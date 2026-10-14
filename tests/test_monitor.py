import dataclasses
from datetime import datetime, timedelta, timezone
from email.message import Message
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor as m

NOW = datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc)


def fixture(p=0.81, reset=None):
    return {
        "schemaVersion": "public-v1",
        "checkedAt": m.iso(NOW),
        "lastRandomResetAt": m.iso(reset or NOW - timedelta(days=2)),
        "dataHealth": {"overall": "ok", "stale": False,
            "generatedAt": m.iso(NOW),
            "sources": {"supabaseSignals": {"state": "ok"}, "openAIStatus": {"state": "ok"}}},
        "viewModel": {"probability24h": p, "probability72h": 0.99,
            "activeWindow": {"active": True, "kind": "official", "source": "https://example.com/fake"}},
    }


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime" / "state.json"
        self.config = m.Config(state_path=self.path, sending_enabled=True)
        # Synthetic values built at runtime; no real webhook or credential fixtures.
        self.env = patch.dict(os.environ, {
            "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/" + "x" * 24,
            "FEISHU_SIGNING_SECRET": "unit" + "-test"})
        self.env.start()
        self.calls = []

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def sender(self, config, alert):
        self.calls.append(alert)
        return "webhook_code_0"

    def run_data(self, data=None, send=False, **kwargs):
        return m.run_once(self.config, send=send, now=NOW, fetcher=lambda: data or fixture(),
                          sender=self.sender, **kwargs)

    def test_threshold_is_strict_and_72h_never_substitutes(self):
        for p in (0, 0.79, 0.8):
            result = self.run_data(fixture(p))
            self.assertEqual(result["status"], "below_threshold")
            self.assertEqual(result["alerts"], [])
        self.assertEqual(self.run_data(fixture(0.8000001))["alerts"][0]["status"], "would_alert")

    def test_dry_run_does_not_send_or_mutate_state(self):
        result = self.run_data()
        self.assertEqual(result["mode"], "dry-run")
        self.assertFalse(self.path.exists())
        self.assertFalse(self.path.parent.exists())
        self.assertEqual(self.calls, [])

    def test_duplicate_across_restart_and_source_refresh(self):
        result = self.run_data(send=True)
        self.assertEqual(result["alerts"][0]["status"], "sent")
        second = fixture(0.92)
        second["checkedAt"] = m.iso(NOW + timedelta(minutes=1))
        second["dataHealth"]["generatedAt"] = second["checkedAt"]
        self.assertEqual(self.run_data(second, send=True)["alerts"][0]["status"], "duplicate_suppressed")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(m.StateStore(self.path).read()["alerts"]), 1)

    def test_same_cycle_stays_deduplicated_after_dip(self):
        self.run_data(send=True)
        self.run_data(fixture(0.1), send=True)
        self.assertEqual(self.run_data(send=True)["alerts"][0]["status"], "duplicate_suppressed")
        self.assertEqual(len(self.calls), 1)

    def test_new_cycle_respects_cooldown(self):
        self.run_data(send=True)
        result = self.run_data(fixture(reset=NOW - timedelta(hours=1)), send=True)
        self.assertEqual(result["alerts"][0]["status"], "cooldown_suppressed")
        self.assertEqual(len(self.calls), 1)

    def test_new_cycle_can_send_after_cooldown(self):
        self.run_data(send=True)
        later = NOW + timedelta(days=1, seconds=1)
        data = fixture(reset=NOW - timedelta(hours=1))
        data["checkedAt"] = data["dataHealth"]["generatedAt"] = m.iso(later)
        result = m.run_once(self.config, send=True, now=later, fetcher=lambda: data, sender=self.sender)
        self.assertEqual(result["alerts"][0]["status"], "sent")
        self.assertEqual(len(self.calls), 2)

    def test_missing_or_bad_probability_fail_closed(self):
        for value in (None, True, "0.9", -0.1, 1.01, float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(m.MonitorError):
                self.run_data(fixture(value), send=True)
        data = fixture()
        del data["viewModel"]["probability24h"]
        with self.assertRaises(m.MonitorError):
            self.run_data(data, send=True)
        self.assertEqual(self.calls, [])

    def test_bad_schema_health_and_missing_fields(self):
        variants = []
        for key, value in (("schemaVersion", "public-v2"), ("checkedAt", None), ("lastRandomResetAt", None)):
            data = fixture(); data[key] = value; variants.append(data)
        for key, value in (("overall", "degraded"), ("stale", True), ("stale", 0), ("generatedAt", None), ("sources", {})):
            data = fixture(); data["dataHealth"][key] = value; variants.append(data)
        data = fixture(); data["dataHealth"]["sources"]["supabaseSignals"]["state"] = "error"; variants.append(data)
        for data in variants:
            with self.subTest(data=data), self.assertRaises(m.MonitorError):
                self.run_data(data, send=True)
        self.assertEqual(self.calls, [])

    def test_stale_and_future_times(self):
        for key in ("checkedAt", "generatedAt"):
            for delta in (timedelta(hours=-3), timedelta(minutes=6)):
                data = fixture()
                target = data if key == "checkedAt" else data["dataHealth"]
                target[key] = m.iso(NOW + delta)
                with self.subTest(key=key, delta=delta), self.assertRaises(m.MonitorError):
                    self.run_data(data, send=True)
        self.assertEqual(self.calls, [])

    def test_timezone_offsets_normalized_and_naive_rejected(self):
        self.assertEqual(m.parse_time("2026-10-14T20:30:00+08:00", "x"), NOW)
        with self.assertRaises(m.MonitorError):
            m.parse_time("2026-10-14T12:30:00", "x")

    def test_network_error_fail_closed(self):
        def fail():
            raise OSError("network offline")
        with self.assertRaises(m.MonitorError):
            m.run_once(self.config, send=True, now=NOW, fetcher=fail, sender=self.sender)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.path.exists())

    def test_send_failure_not_marked_success_and_not_retried(self):
        def fail(config, alert):
            self.calls.append(alert)
            raise TimeoutError("uncertain")
        with self.assertRaises(m.SendUnknown):
            m.run_once(self.config, send=True, now=NOW, fetcher=fixture, sender=fail)
        state = m.StateStore(self.path).read()
        self.assertEqual(next(iter(state["alerts"].values()))["status"], "unknown")
        self.assertIsNone(state["last_sent_at"])
        result = self.run_data(send=True)
        self.assertEqual(result["status"], "blocked_unresolved_delivery")
        self.assertEqual(len(self.calls), 1)

    def test_pending_written_before_send_and_survives_crash(self):
        def crash(config, alert):
            self.assertEqual(m.StateStore(self.path).read()["alerts"][alert.key]["status"], "pending")
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            m.run_once(self.config, send=True, now=NOW, fetcher=fixture, sender=crash)
        self.assertEqual(self.run_data(send=True)["status"], "blocked_unresolved_delivery")
        self.assertEqual(self.calls, [])

    def test_failed_initial_state_write_prevents_send(self):
        store = m.StateStore(self.path)
        with patch.object(store, "write", side_effect=m.StateError("disk full")):
            with self.assertRaises(m.StateError):
                self.run_data(send=True, store=store)
        self.assertEqual(self.calls, [])

    def test_failed_final_state_write_prevents_restart_resend(self):
        store = m.StateStore(self.path)
        original = store.write
        count = 0
        def fail_second(state):
            nonlocal count
            count += 1
            if count == 2:
                raise m.StateError("disk full after delivery")
            original(state)
        with patch.object(store, "write", side_effect=fail_second):
            with self.assertRaises(m.StateError):
                self.run_data(send=True, store=store)
        self.assertEqual(next(iter(store.read()["alerts"].values()))["status"], "pending")
        self.assertEqual(self.run_data(send=True)["status"], "blocked_unresolved_delivery")
        self.assertEqual(len(self.calls), 1)

    def test_corrupt_state_does_not_reset_or_send(self):
        self.path.parent.mkdir()
        self.path.write_text("{corrupt")
        with self.assertRaises(m.StateError):
            self.run_data(send=True)
        self.assertEqual(self.calls, [])

    def test_concurrent_lock_blocks_second_instance(self):
        store = m.StateStore(self.path)
        with store.locked():
            with self.assertRaises(m.StateError):
                self.run_data(send=True)
        self.assertEqual(self.calls, [])

    def test_json_constants_duplicate_keys_and_size_rejected(self):
        for raw in (b'{"p":NaN}', b'{"p":Infinity}', b'{"p":-Infinity}', b'{"p":0,"p":1}', b'{bad'):
            with self.subTest(raw=raw), self.assertRaises(m.MonitorError):
                m.decode_json(raw)
        with self.assertRaises(m.MonitorError):
            m.decode_json(b" " * 10, 9)

    def test_no_send_without_explicit_config_gate(self):
        config = dataclasses.replace(self.config, sending_enabled=False)
        with self.assertRaises(m.MonitorError):
            m.run_once(config, send=True, now=NOW, fetcher=fixture, sender=self.sender)
        self.assertEqual(self.calls, [])

    def test_input_fixture_cannot_send(self):
        with self.assertRaises(m.MonitorError):
            self.run_data(send=True, input_path=Path("anything.json"))

    def test_unverified_active_window_never_independently_triggers(self):
        result = self.run_data(fixture(0.2), send=True)
        self.assertEqual(result["alerts"], [])
        self.assertEqual(self.calls, [])

    def test_optional_official_adapter_rejects_untrusted_sources(self):
        signal_path = Path(self.temp.name) / "signals.json"
        signal = {"verification": "primary_source_manually_verified",
            "source_url": "https://x.com/thsottiaux/status/12345678",
            "event_kind": "banked_reset_notice", "published_at": m.iso(NOW - timedelta(hours=1)),
            "verified_at": m.iso(NOW), "expected_at": m.iso(NOW + timedelta(hours=1))}
        def write_signal(entry):
            signal_path.write_text(json.dumps({"schemaVersion": "verified-official-v1", "signals": [entry]}))
        write_signal(signal)
        self.assertEqual(len(m.official_alerts(signal_path, NOW)), 1)
        for override in ({"verification": "site_classified"},
            {"source_url": "https://x.com.attacker.test/thsottiaux/status/12345678"},
            {"source_url": "https://x.com/random_account/status/12345678"},
            {"source_url": "https://x.com/thsottiaux/status/12345678?redirect=x"},
            {"event_kind": "regular_reset"}):
            write_signal(dict(signal, **override))
            with self.subTest(override=override), self.assertRaises(m.MonitorError):
                m.official_alerts(signal_path, NOW)

    def test_optional_verified_notice_works_without_forecast(self):
        signal_path = Path(self.temp.name) / "signals.json"
        signal_path.write_text(json.dumps({"schemaVersion": "verified-official-v1", "signals": [{
            "verification": "primary_source_manually_verified",
            "source_url": "https://x.com/thsottiaux/status/12345678", "event_kind": "early_reset_notice",
            "published_at": m.iso(NOW - timedelta(hours=1)), "verified_at": m.iso(NOW),
            "expected_at": m.iso(NOW + timedelta(hours=1))}]}))
        config = dataclasses.replace(self.config, official_signals_enabled=True, official_signals_path=signal_path)
        result = m.run_once(config, now=NOW, fetcher=lambda: {})
        self.assertEqual(result["alerts"][0]["status"], "would_alert")
        self.assertIn("source_error", result)

    def test_unknown_config_field_rejected(self):
        path = Path(self.temp.name) / "config.json"
        path.write_text('{"threshold":0.8,"sendng_enabled":true}')
        with self.assertRaises(m.MonitorError):
            m.load_config(path)

    def test_template_discloses_source_limits(self):
        text = self.run_data()["alerts"][0]["message"]
        for phrase in ("未来24小时", "备用重置额度发放", "第三方统计估计", "不是 OpenAI 官方概率", "个人额度", m.SOURCE_URL):
            self.assertIn(phrase, text)
        self.assertIn("UTC+08:00", text)
        self.assertIn("2026-10-14 20:30", text)

    def fake_http(self, body, content_type="application/json", length=None, status=200):
        class Response(io.BytesIO):
            pass
        response = Response(body)
        response.status = status
        response.headers = Message()
        response.headers["Content-Type"] = content_type
        if length is not None:
            response.headers["Content-Length"] = str(length)
        opener = Mock()
        opener.open.return_value = response
        return opener

    def test_http_success_uses_timeout_and_fixed_endpoint(self):
        opener = self.fake_http(json.dumps(fixture()).encode())
        with patch.object(m.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(m.fetch_current()["schemaVersion"], "public-v1")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, m.SOURCE_URL)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)

    def test_http_declared_oversize_and_invalid_length_rejected(self):
        for length in (m.MAX_RESPONSE_BYTES + 1, -1, "not-a-number"):
            opener = self.fake_http(b"{}", length=length)
            with patch.object(m.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(m.MonitorError):
                    m.fetch_current()

    def test_http_streaming_body_limit_rejected(self):
        opener = self.fake_http(b" " * (m.MAX_RESPONSE_BYTES + 1))
        with patch.object(m.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(m.MonitorError):
                m.fetch_current()

    def test_http_wrong_type_status_and_redirect_rejected(self):
        for overrides in ({"content_type": "text/html"}, {"status": 503}):
            opener = self.fake_http(b"{}", **overrides)
            with patch.object(m.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(m.MonitorError):
                    m.fetch_current()
        with self.assertRaises(m.MonitorError):
            m.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.com")

    def test_http_timeout_fails_closed(self):
        opener = Mock()
        opener.open.side_effect = TimeoutError("timed out")
        with patch.object(m.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(m.MonitorError):
                m.fetch_current()

    def test_config_state_path_moves_with_config_directory(self):
        directory = Path(self.temp.name) / "moved-project"
        directory.mkdir()
        config_path = directory / "config.json"
        config_path.write_text('{"state_path":"runtime/state.json"}')
        config = m.load_config(config_path)
        self.assertEqual(config.state_path, directory / "runtime" / "state.json")
        self.assertFalse(config.sending_enabled)

    def test_project_defaults_are_portable_and_dry_run_needs_no_secrets(self):
        config = dataclasses.replace(m.Config(), state_path=self.path)
        with patch.dict(os.environ, {}, clear=True):
            result = m.run_once(config, now=NOW, fetcher=fixture)
        self.assertEqual(result["alerts"][0]["status"], "would_alert")

    def test_missing_credentials_block_before_fetch_and_state(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(m.MonitorError):
            m.run_once(self.config, send=True, fetcher=Mock(side_effect=AssertionError()))
        self.assertFalse(self.path.parent.exists())

    def test_webhook_url_exact_allowlist(self):
        prefix = "https://open.feishu.cn/open-apis/bot/v2/hook/"
        for url in (prefix + "short", "http://open.feishu.cn", prefix + "x"*24 + "?q=x",
                    prefix.replace(".cn", ".cn.evil.test") + "x"*24,
                    prefix.replace("open.feishu", "user@open.feishu") + "x"*24,
                    prefix.replace(".cn", ".cn:443") + "x"*24):
            with self.subTest(url=url), patch.dict(os.environ, {"FEISHU_WEBHOOK_URL": url}):
                with self.assertRaises(m.MonitorError):
                    m.validate_sender_config(self.config)

    def test_signing_secret_required_and_controls_rejected(self):
        for value in ("", "bad\nvalue", "x" * 1025):
            with patch.dict(os.environ, {"FEISHU_SIGNING_SECRET": value}):
                with self.assertRaises(m.MonitorError):
                    m.validate_sender_config(self.config)

    def test_signature_matches_official_demo_algorithm(self):
        import base64, hashlib, hmac
        expected = base64.b64encode(hmac.new(b"1599360473\ndemo", digestmod=hashlib.sha256).digest()).decode()
        self.assertEqual(m.webhook_signature("1599360473", "demo"), expected)

    def test_webhook_exact_signed_payload_and_acknowledgement(self):
        alert = m.forecast_alert(m.validate_snapshot(fixture(), NOW), self.config)
        opener = self.fake_http(b'{"code":0,"data":{},"msg":"success"}')
        with patch.object(m.urllib.request, "build_opener", return_value=opener) as build, patch.object(m.time, "time", return_value=1599360473):
            self.assertEqual(m.webhook_sender(self.config, alert), "webhook_code_0")
        req = opener.open.call_args.args[0]
        body = json.loads(req.data)
        self.assertEqual(req.method, "POST")
        self.assertEqual(body["content"]["text"], alert.text)
        self.assertEqual(body["timestamp"], "1599360473")
        self.assertEqual(body["sign"], m.webhook_signature("1599360473", os.environ["FEISHU_SIGNING_SECRET"]))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 15)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], m.NoRedirect)

    def test_webhook_business_errors_and_legacy_success_are_unconfirmed(self):
        alert = m.forecast_alert(m.validate_snapshot(fixture(), NOW), self.config)
        for code in (9499, 11232, 19021, 19022, 19024, True, "0", None):
            opener = self.fake_http(json.dumps({"code":code}).encode())
            with self.subTest(code=code), patch.object(m.urllib.request, "build_opener", return_value=opener):
                with self.assertRaises(m.SendUnknown):
                    m.webhook_sender(self.config, alert)
                self.assertEqual(opener.open.call_count, 1)
        opener = self.fake_http(b'{"StatusCode":0}')
        with patch.object(m.urllib.request, "build_opener", return_value=opener), self.assertRaises(m.SendUnknown):
            m.webhook_sender(self.config, alert)

    def test_webhook_transport_and_response_errors_redacted(self):
        alert = m.forecast_alert(m.validate_snapshot(fixture(), NOW), self.config)
        sensitive = os.environ["FEISHU_WEBHOOK_URL"] + os.environ["FEISHU_SIGNING_SECRET"]
        opener = Mock(); opener.open.side_effect = OSError(sensitive)
        with patch.object(m.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(m.SendUnknown) as caught:
                m.webhook_sender(self.config, alert)
        self.assertNotIn(sensitive, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        for body, kind, status in ((b'{bad', 'application/json', 200),
            (b'{"code":0}', 'text/html', 200), (b'{"code":0}', 'application/json', 429),
            (b' '*65537, 'application/json', 200)):
            opener = self.fake_http(body, kind, status=status)
            with patch.object(m.urllib.request, "build_opener", return_value=opener), self.assertRaises(m.SendUnknown):
                m.webhook_sender(self.config, alert)

    def test_webhook_payload_limit_blocks_http(self):
        opener = Mock()
        alert = m.Alert("forecast:" + "a"*40, "forecast24h", "中" * 20000)
        with patch.object(m.urllib.request, "build_opener", return_value=opener), self.assertRaises(m.SendUnknown):
            m.webhook_sender(self.config, alert)
        opener.open.assert_not_called()

    def test_old_cli_config_rejected(self):
        path = Path(self.temp.name)/"config.json"
        path.write_text('{"cli_home":"/old/authorization"}')
        with self.assertRaises(m.MonitorError):
            m.load_config(path)

    def test_health_age_boundary_and_future_reset(self):
        for seconds in (7200, 7201):
            data = fixture()
            data["checkedAt"] = m.iso(NOW-timedelta(seconds=seconds))
            if seconds == 7200:
                self.run_data(data)
            else:
                with self.assertRaises(m.MonitorError): self.run_data(data)
        data=fixture(reset=NOW+timedelta(minutes=6))
        with self.assertRaises(m.MonitorError): self.run_data(data)

    def test_config_limits_cannot_weaken_threshold(self):
        path=Path(self.temp.name)/"config.json"
        for values in ({"threshold":0.79}, {"threshold":True}, {"sending_enabled":"yes"},
                       {"max_age_seconds":True}, {"cooldown_seconds":0}, {"state_path":None}):
            path.write_text(json.dumps(values))
            with self.subTest(values=values), self.assertRaises(m.MonitorError): m.load_config(path)

    def test_state_size_and_unknown_version_stop(self):
        store=m.StateStore(self.path)
        with self.assertRaises(m.StateError): store.write({"extra":"x"*m.MAX_STATE_BYTES})
        self.path.write_text('{"version":2,"alerts":{},"last_sent_at":null}')
        with self.assertRaises(m.StateError): store.read()

    def test_unresolved_delivery_blocks_even_below_threshold(self):
        store=m.StateStore(self.path)
        state=m.empty_state()
        state["alerts"]["forecast:"+"a"*40]={"status":"pending","attempted_at":m.iso(NOW)}
        store.write(state)
        self.assertEqual(self.run_data(fixture(0.1),send=True)["status"], "blocked_unresolved_delivery")
        self.assertEqual(self.calls,[])

    def test_cli_remote_state_error_has_documented_code_without_traceback(self):
        import subprocess
        environment = dict(os.environ)
        environment.pop("GH_TOKEN", None)
        completed = subprocess.run([sys.executable, str(Path(m.__file__)),
            "--github-state", "--once", "--dry-run"], env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(completed.returncode, 4)
        self.assertEqual(json.loads(completed.stderr)["status"], "error")
        self.assertNotIn(b"Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()

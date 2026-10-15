#!/usr/bin/env python3
"""Codex reset forecast monitor. Python 3.10+ standard library only.

Default execution is read-only/dry-run. No LLM, scheduler, login, or token access.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import contextlib
import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request

UTC = timezone.utc
DISPLAY_TZ = timezone(timedelta(hours=8))
BASE = Path(__file__).resolve().parent
SOURCE_URL = "https://codex.gussuriworks.com/api/current"
SOURCE_PAGE = "https://codex.gussuriworks.com/en"
MAX_RESPONSE_BYTES = 524288
MAX_STATE_BYTES = 1048576
OFFICIAL_URL_RULES = (
    ("x.com", r"/thsottiaux/status/[0-9]+/?"),
    ("x.com", r"/OpenAI/status/[0-9]+/?"),
    ("openai.com", r"/(?:index|blog)/[a-zA-Z0-9_/-]+/?"),
    ("help.openai.com", r"/(?:en/)?articles/[a-zA-Z0-9_-]+/?"),
)


class MonitorError(Exception):
    pass


class StateError(MonitorError):
    pass


class SendUnknown(MonitorError):
    """Delivery cannot be established; never automatically retry."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def local_time(value: datetime) -> str:
    return value.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M")


def parse_time(value, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise MonitorError(f"Invalid timestamp: {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MonitorError(f"Invalid timestamp: {field}") from exc
    if parsed.tzinfo is None:
        raise MonitorError(f"Timezone required: {field}")
    return parsed.astimezone(UTC)


def fresh(value: datetime, now: datetime, max_age: int, field: str) -> None:
    age = (now - value).total_seconds()
    if age > max_age or age < -300:
        raise MonitorError(f"Stale or future timestamp: {field}")


def object_at(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise MonitorError(f"Expected object: {field}")
    return value


def finite_number(value, field: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MonitorError(f"Expected number: {field}")
    if not math.isfinite(value) or not low <= value <= high:
        raise MonitorError(f"Out-of-range number: {field}")
    return float(value)


def reject_constant(value: str):
    raise MonitorError("Non-finite JSON number rejected")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MonitorError("Duplicate JSON key rejected")
        result[key] = value
    return result


def decode_json(raw: bytes, limit: int = MAX_RESPONSE_BYTES):
    if len(raw) > limit:
        raise MonitorError("JSON exceeds size limit")
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=reject_constant,
                          object_pairs_hook=unique_keys)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise MonitorError("Invalid JSON") from exc


def read_json(path: Path, limit: int = MAX_RESPONSE_BYTES):
    with path.open("rb") as handle:
        return decode_json(handle.read(limit + 1), limit)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MonitorError("HTTP redirect rejected")


def fetch_current(timeout: int = 15) -> dict:
    """Fixed HTTPS endpoint, standard TLS validation, no redirects or retries."""
    request = urllib.request.Request(SOURCE_URL, headers={
        "Accept": "application/json", "Accept-Encoding": "identity",
        "User-Agent": "CodexResetMonitor/1.0 (hourly; no-LLM)",
    })
    deadline = time.monotonic() + 30
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise MonitorError("Unexpected HTTP status")
            if response.headers.get_content_type() != "application/json":
                raise MonitorError("Unexpected HTTP content type")
            size_header = response.headers.get("Content-Length")
            if size_header is not None:
                try:
                    size = int(size_header)
                except ValueError as exc:
                    raise MonitorError("Invalid Content-Length") from exc
                if size < 0 or size > MAX_RESPONSE_BYTES:
                    raise MonitorError("HTTP body exceeds size limit")
            parts, total = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise MonitorError("HTTP total read deadline exceeded")
                part = response.read1(min(16384, MAX_RESPONSE_BYTES + 1 - total))
                if not part:
                    break
                parts.append(part)
                total += len(part)
                if total > MAX_RESPONSE_BYTES:
                    raise MonitorError("HTTP body exceeds size limit")
            return object_at(decode_json(b"".join(parts)), "response")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MonitorError(f"Source fetch failed ({type(exc).__name__})") from exc


@dataclasses.dataclass(frozen=True)
class Snapshot:
    probability24h: float
    checked_at: datetime
    generated_at: datetime
    last_reset_at: datetime


def validate_snapshot(raw: dict, now: datetime, max_age: int = 7200) -> Snapshot:
    raw = object_at(raw, "response")
    if raw.get("schemaVersion") != "public-v1":
        raise MonitorError("Unknown source schemaVersion")
    health = object_at(raw.get("dataHealth"), "dataHealth")
    if health.get("overall") != "ok" or health.get("stale") is not False:
        raise MonitorError("Source health not OK or stale")
    sources = object_at(health.get("sources"), "dataHealth.sources")
    for key in ("supabaseSignals", "openAIStatus"):
        if object_at(sources.get(key), f"sources.{key}").get("state") != "ok":
            raise MonitorError(f"Unhealthy source: {key}")
    checked = parse_time(raw.get("checkedAt"), "checkedAt")
    generated = parse_time(health.get("generatedAt"), "generatedAt")
    fresh(checked, now, max_age, "checkedAt")
    fresh(generated, now, max_age, "generatedAt")
    vm = object_at(raw.get("viewModel"), "viewModel")
    probability = finite_number(vm.get("probability24h"), "probability24h", 0, 1)
    # 12h/48h/72h and textual classifications are deliberately never substituted.
    last_reset = parse_time(raw.get("lastRandomResetAt"), "lastRandomResetAt")
    if last_reset > now + timedelta(minutes=5) or last_reset > generated + timedelta(minutes=5):
        raise MonitorError("Future lastRandomResetAt")
    return Snapshot(probability, checked, generated, last_reset)


@dataclasses.dataclass(frozen=True)
class Config:
    threshold: float = 0.8
    max_age_seconds: int = 7200
    cooldown_seconds: int = 86400
    state_path: Path = BASE / "runtime" / "state.json"
    sending_enabled: bool = False
    official_signals_enabled: bool = False
    official_signals_path: Path | None = None


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    raw = object_at(read_json(path, 32768), "config")
    known = {f.name for f in dataclasses.fields(Config)}
    if raw.keys() - known:
        raise MonitorError("Unknown config field")
    values = dict(raw)
    for key in ("sending_enabled", "official_signals_enabled"):
        if key in values and not isinstance(values[key], bool):
            raise MonitorError(f"Expected boolean: {key}")
    values["threshold"] = finite_number(values.get("threshold", 0.8), "threshold", 0.8, 1)
    for key, default, low, high in (
        ("max_age_seconds", 7200, 600, 14400),
        ("cooldown_seconds", 86400, 3600, 604800),
    ):
        value = values.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise MonitorError(f"Invalid config: {key}")
        values[key] = value
    for key in ("state_path", "official_signals_path"):
        if key in values and values[key] is not None:
            if not isinstance(values[key], str) or not values[key]:
                raise MonitorError(f"Invalid path: {key}")
            candidate = Path(values[key]).expanduser()
            values[key] = candidate if candidate.is_absolute() else path.parent / candidate
    config = Config(**values)
    if config.state_path is None:
        raise MonitorError("state_path is required")
    if config.official_signals_enabled and config.official_signals_path is None:
        raise MonitorError("official_signals_path is required when enabled")
    return config


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]


@dataclasses.dataclass(frozen=True)
class Alert:
    key: str
    kind: str
    text: str


def forecast_alert(snapshot: Snapshot, config: Config) -> Alert | None:
    if snapshot.probability24h <= config.threshold:
        return None
    key = "forecast:" + digest(SOURCE_URL + "|24h|" + iso(snapshot.last_reset_at))
    text = (
        "Codex 提前重置提醒\n"
        f"未来24小时：该站估计 {snapshot.probability24h:.1%}（提醒阈值 >{config.threshold:.0%}）。\n"
        f"窗口：{local_time(snapshot.generated_at)} 至 {local_time(snapshot.generated_at + timedelta(hours=24))}\n"
        f"来源更新：{local_time(snapshot.generated_at)}（以上均为 UTC+08:00）\n"
        "包含广范围提前重置或备用重置额度发放；备用卡不代表个人额度自动恢复。\n"
        "第三方统计估计，未校准，不是 OpenAI 官方概率。\n"
        f"来源：{SOURCE_URL}"
    )
    return Alert(key, "forecast24h", text)


def official_alerts(path: Path, now: datetime) -> list[Alert]:
    """Opt-in local, operator-verified signal adapter; no public DTO inference."""
    raw = object_at(read_json(path, 65536), "official_signals")
    if raw.get("schemaVersion") != "verified-official-v1":
        raise MonitorError("Unknown verified official signal schema")
    entries = raw.get("signals")
    if not isinstance(entries, list) or len(entries) > 20:
        raise MonitorError("Invalid official signals list")
    result = []
    for entry in entries:
        entry = object_at(entry, "official signal")
        if entry.get("verification") != "primary_source_manually_verified":
            raise MonitorError("Official signal lacks primary-source verification")
        source = entry.get("source_url")
        if not isinstance(source, str) or len(source) > 512:
            raise MonitorError("Invalid official source URL")
        try:
            parsed = urllib.parse.urlsplit(source)
            port = parsed.port
        except ValueError as exc:
            raise MonitorError("Invalid official source URL") from exc
        if (parsed.scheme != "https" or parsed.username or parsed.password or port
                or parsed.query or parsed.fragment or not any(
                    parsed.netloc == host and re.fullmatch(pattern, parsed.path)
                    for host, pattern in OFFICIAL_URL_RULES)):
            raise MonitorError("Official source not allowlisted")
        event = entry.get("event_kind")
        labels = {"early_reset_notice": "提前重置预告", "banked_reset_notice": "备用重置额度发放预告"}
        if event not in labels:
            raise MonitorError("Unknown official event kind")
        published = parse_time(entry.get("published_at"), "published_at")
        verified = parse_time(entry.get("verified_at"), "verified_at")
        expected = parse_time(entry.get("expected_at"), "expected_at")
        fresh(published, now, 86400, "official published_at")
        fresh(verified, now, 86400, "official verified_at")
        if verified < published or expected <= now or expected > now + timedelta(hours=24):
            continue
        result.append(Alert("official:" + digest(source.rstrip("/")), "verified_official_notice",
            f"Codex {labels[event]}\n已人工核验的原始来源信号。\n"
            f"预计时间：{local_time(expected)}\n"
            f"来源发布时间：{local_time(published)}\n核验时间：{local_time(verified)}（UTC+08:00）\n"
            f"原始来源：{source}\n这是预告，并不保证你的个人额度届时恢复。"))
    return result


def empty_state() -> dict:
    return {"version": 1, "alerts": {}, "last_sent_at": None}


def validate_state(state: dict) -> dict:
    state = object_at(state, "state")
    if type(state.get("version")) is not int or state.get("version") != 1 or not {"version", "alerts", "last_sent_at"} <= state.keys():
        raise StateError("Unknown state schema; refusing to reset")
    alerts = object_at(state.get("alerts"), "state.alerts")
    for key, entry in alerts.items():
        if not isinstance(key, str) or not re.fullmatch(r"(?:forecast|official):[a-f0-9]{40}", key):
            raise StateError("Invalid state alert key")
        entry = object_at(entry, "state alert")
        if entry.get("status") not in {"pending", "sent", "unknown", "abandoned"}:
            raise StateError("Invalid state alert status")
        parse_time(entry.get("attempted_at"), "state.attempted_at")
    if state.get("last_sent_at") is not None:
        parse_time(state["last_sent_at"], "state.last_sent_at")
    tests = state.get("acceptance_tests", {})
    if not isinstance(tests, dict) or len(tests) > 100:
        raise StateError("Invalid installation test ledger")
    for key, entry in tests.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key) or not isinstance(entry, dict):
            raise StateError("Invalid installation test record")
        if entry.get("status") not in {"pending", "sent", "unknown"}:
            raise StateError("Invalid installation test status")
        parse_time(entry.get("attempted_at"), "test.attempted_at")
    if 'reset_events' in state:
        from reset_events import validate_reset_state
        validate_reset_state(state['reset_events'])
    return state


def unresolved_delivery(state, *, include_reset=True):
    return any(entry["status"] in {"pending", "unknown"}
               for entry in (list(state["alerts"].values()) + list(state.get("acceptance_tests", {}).values())
               + (list(state.get("reset_events", {}).get("records", {}).values()) if include_reset else [])))


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    @contextlib.contextmanager
    def locked(self):
        try:
            import fcntl
        except ImportError:
            raise StateError("Local file locking requires macOS/Linux; use the remote Actions backend") from None
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StateError("Another monitor process holds the lock") from exc
            yield
        finally:
            os.close(fd)

    def read(self) -> dict:
        if not self.path.exists():
            return empty_state()
        try:
            return validate_state(read_json(self.path, MAX_STATE_BYTES))
        except (OSError, MonitorError) as exc:
            raise StateError("State unreadable or invalid; refusing to send") from exc

    def write(self, state: dict) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        raw = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
        if len(raw) > MAX_STATE_BYTES:
            raise StateError("State exceeds limit; manual archival required")
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".state-", dir=self.path.parent)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            dir_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            raise StateError("Atomic state write failed; do not retry delivery") from exc
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    os.unlink(temporary)


def notification_backend() -> str:
    backend = os.environ.get("FEISHU_NOTIFY_BACKEND", "webhook")
    if backend not in {"webhook", "app"}:
        raise MonitorError("Unknown notification backend")
    return backend


def validate_sender_config(config: Config) -> str:
    """Credentials come only from environment; never include values in errors."""
    if not config.sending_enabled:
        raise MonitorError("Sending disabled in config")
    if notification_backend() == "app":
        from feishu_app import environment_credentials
        environment_credentials()
        return "app"
    url = os.environ.get("FEISHU_WEBHOOK_URL", "")
    # Exact Feishu host/path; no userinfo, ports, query, fragments or redirects.
    if not re.fullmatch(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[a-zA-Z0-9_-]{16,128}", url):
        raise MonitorError("Missing or invalid FEISHU_WEBHOOK_URL")
    secret = os.environ.get("FEISHU_SIGNING_SECRET", "")
    if not secret or len(secret) > 1024 or any(ord(c) < 32 for c in secret):
        raise MonitorError("Missing or invalid FEISHU_SIGNING_SECRET")
    return url


def webhook_signature(timestamp: str, secret: str) -> str:
    # Official algorithm: timestamp + newline + secret is the HMAC key;
    # the HMAC message is empty (not the JSON body).
    key = f"{timestamp}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii")


def webhook_sender(config: Config, alert: Alert) -> str:
    if notification_backend() != "webhook":
        raise MonitorError("Webhook sender requires webhook backend")
    url = validate_sender_config(config)
    timestamp = str(int(time.time()))
    payload = {"msg_type": "text", "content": {"text": alert.text},
               "timestamp": timestamp,
               "sign": webhook_signature(timestamp, os.environ["FEISHU_SIGNING_SECRET"])}
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(body) > 20000:
        raise SendUnknown("Webhook payload exceeds safe size limit")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
        "User-Agent": "CodexResetMonitor/2.0"})
    # Disable environment HTTP proxies: they could receive the secret-bearing URL.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status != 200 or response.headers.get_content_type() != "application/json":
                raise SendUnknown("Webhook HTTP result unconfirmed")
            raw = object_at(decode_json(response.read(65537), 65536), "webhook response")
        # HTTP 200 does not establish success. Reject bool and legacy-only fields.
        code = raw.get("code")
        if type(code) is not int:
            raise SendUnknown("Webhook business result missing or invalid")
        if code != 0:
            if -1 <= code <= 1000000000:
                raise SendUnknown(f"Webhook rejected (business code {code}); automatic retries blocked")
            raise SendUnknown("Webhook business result invalid")
    except SendUnknown as exc:
        raise SendUnknown(str(exc)) from None
    except Exception:
        # Never log upstream exception text, response body, request, URL or secret.
        raise SendUnknown("Webhook delivery unconfirmed; no automatic retry") from None
    # Bot v2 returns no usable message ID: record an acknowledgement, not invent one.
    return "webhook_code_0"


def notification_sender(config: Config, alert: Alert) -> str:
    if notification_backend() == "app":
        from feishu_app import app_sender
        return app_sender(config, alert)
    return webhook_sender(config, alert)


def dispatch(alert: Alert, config: Config, store: StateStore, state: dict,
             now: datetime, send: bool, sender: Callable[[Config, Alert], str]) -> dict:
    if unresolved_delivery(state, include_reset=False):
        return {"status": "blocked_unresolved_delivery", "event_key": alert.key}
    if alert.key in state["alerts"]:
        return {"status": "duplicate_suppressed", "event_key": alert.key}
    last = state.get("last_sent_at")
    if alert.kind == "forecast24h" and last is not None:
        elapsed = (now - parse_time(last, "state.last_sent_at")).total_seconds()
        if elapsed < config.cooldown_seconds:
            return {"status": "cooldown_suppressed", "event_key": alert.key}
    if not send:
        return {"status": "would_alert", "event_key": alert.key, "message": alert.text}
    validate_sender_config(config)
    entry = {"status": "pending", "attempted_at": iso(now), "kind": alert.kind}
    state["alerts"][alert.key] = entry
    store.write(state)
    try:
        receipt = sender(config, alert)
    except Exception as exc:
        entry["status"] = "unknown"
        entry["failure_type"] = type(exc).__name__
        store.write(state)
        raise SendUnknown("Delivery not confirmed; automatic retries are blocked") from None
    entry["status"] = "sent"
    entry["receipt"] = receipt
    entry["sent_at"] = iso(now)
    state["last_sent_at"] = iso(now)
    store.write(state)
    return {"status": "sent", "event_key": alert.key, "receipt": receipt}


def run_once(config: Config, *, send: bool = False, input_path: Path | None = None,
             now: datetime | None = None, fetcher=fetch_current,
             sender=notification_sender, store: StateStore | None = None) -> dict:
    now = now or utc_now()
    if input_path is not None and send:
        raise MonitorError("Sending with a fixture/input file is forbidden")
    if send:
        validate_sender_config(config)
    store = store or StateStore(config.state_path)
    with store.locked() if send else contextlib.nullcontext():
        state = store.read()
        candidates, source_error, snapshot = [], None, None
        reset_ready, reset_summary, reset_error = [], {"status": "not_checked"}, None
        if config.official_signals_enabled:
            candidates.extend(official_alerts(config.official_signals_path, now))
        try:
            raw = read_json(input_path) if input_path is not None else fetcher()
            snapshot = validate_snapshot(raw, now, config.max_age_seconds)
            from reset_events import observe
            try:
                changed, reset_ready, reset_summary = observe(raw, snapshot, state, now)
                if send and changed:
                    store.write(state)  # Durable baseline/unsent queue; never erase an existing ledger.
            except StateError:
                raise
            except MonitorError as exc:
                reset_error = str(exc)
            candidate = forecast_alert(snapshot, config)
            if candidate:
                candidates.append(candidate)
        except StateError:
            raise
        except (MonitorError, OSError) as exc:
            source_error = str(exc)
        if source_error and not candidates:
            raise MonitorError(source_error)
        results = []
        for index, alert in enumerate(candidates):
            if send and index:
                time.sleep(1)  # Below bot 5/s and 100/min limits, even with local adapters.
            results.append(dispatch(alert, config, store, state, now, send, sender))
        if snapshot is not None and not reset_error:
            from reset_events import dispatch as dispatch_reset
            for key in reset_ready:
                if send and results:
                    time.sleep(1)
                results.append(dispatch_reset(key, config, store, state, now, send, sender))
        unresolved = unresolved_delivery(state)
        result = {"checked_at": iso(now), "mode": "send" if send else "dry-run",
                  "status": "blocked_unresolved_delivery" if unresolved else ("evaluated" if candidates or reset_ready else "below_threshold"),
                  "alerts": results}
        if snapshot is not None:
            result.update(probability24h=snapshot.probability24h, source_generated_at=iso(snapshot.generated_at),
                          threshold=config.threshold)
        result["reset_events"] = reset_summary
        if reset_error:
            result["reset_source_error"] = reset_error
        if source_error:
            result["source_error"] = source_error
        return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Check once (default)")
    mode.add_argument("--loop", action="store_true", help="Foreground hourly loop; creates no scheduler")
    safety = parser.add_mutually_exclusive_group()
    safety.add_argument("--dry-run", action="store_true", help="Read-only preview (default)")
    safety.add_argument("--send", action="store_true", help="Send only with explicitly enabled/verified config")
    parser.add_argument("--github-state", action="store_true", help="Use pre-initialized remote state branch")
    parser.add_argument("--input-json", type=Path, help="Offline fixture; sending is forbidden")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (MonitorError, OSError) as exc:
        print(json.dumps({"status": "config_error", "error": str(exc)}), file=sys.stderr)
        return 2
    while True:
        started = time.monotonic()
        try:
            if args.github_state:
                from github_state import GitHubStateStore
                store = GitHubStateStore()
            else:
                store = None
            result = run_once(config, send=args.send, input_path=args.input_json, store=store)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
            code = 3 if result["status"] == "blocked_unresolved_delivery" else 0
        except (MonitorError, OSError) as exc:
            code = 4 if isinstance(exc, StateError) else 3 if isinstance(exc, SendUnknown) else 2
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
        if not args.loop:
            return code
        if code in (3, 4):
            return code
        time.sleep(max(0, 3600 - (time.monotonic() - started)))


if __name__ == "__main__":
    # Keep exception classes identical when github_state imports this CLI module.
    sys.modules.setdefault("monitor", sys.modules[__name__])
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

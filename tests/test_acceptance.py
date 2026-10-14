"""One-time user acceptance test with durable reservation; no real messages."""
import dataclasses
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import acceptance as a
import monitor as m
from test_monitor import NOW, fixture

REQUEST='a'*32


class AcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'state.json'
        self.store=m.StateStore(self.path)
        self.env=patch.dict(os.environ,{'FEISHU_NOTIFY_BACKEND':'webhook',
            'FEISHU_WEBHOOK_URL':'https://open.feishu.cn/open-apis/bot/v2/hook/'+'x'*24,
            'FEISHU_SIGNING_SECRET':'synthetic'+'-secret'})
        self.env.start()

    def tearDown(self):self.env.stop();self.temp.cleanup()

    def sender(self,config,alert):
        state=self.store.read()
        self.assertEqual(state['acceptance_tests'][REQUEST]['status'],'pending')
        self.assertIn('安装测试',alert.text)
        self.assertIn('不是提前重置预警',alert.text)
        self.assertIn(REQUEST,alert.text)
        return 'webhook_code_0'

    def test_pending_before_send_and_same_request_never_sends_twice(self):
        sender=Mock(side_effect=self.sender)
        self.assertEqual(a.send_test(REQUEST,self.store,sender=sender,now=NOW)['status'],'sent')
        self.assertEqual(a.send_test(REQUEST,self.store,sender=sender,now=NOW)['status'],'duplicate_suppressed')
        self.assertEqual(sender.call_count,1)
        self.assertEqual(self.store.read()['alerts'],{})
        self.assertIsNone(self.store.read()['last_sent_at'])

    def test_source_threshold_is_irrelevant_to_explicit_test_not_fake_forecast(self):
        a.send_test(REQUEST,self.store,sender=self.sender,now=NOW)
        state=self.store.read()
        self.assertEqual(len(state['acceptance_tests']),1)
        self.assertEqual(state['alerts'],{})

    def test_send_unknown_blocks_same_and_new_request_and_forecasts(self):
        sender=Mock(side_effect=TimeoutError('sensitive upstream'))
        with self.assertRaises(m.SendUnknown):a.send_test(REQUEST,self.store,sender=sender,now=NOW)
        for request in (REQUEST,'b'*32):
            with self.assertRaises(m.SendUnknown):a.send_test(request,self.store,sender=sender,now=NOW)
        self.assertEqual(sender.call_count,1)
        config=m.Config(state_path=self.path,sending_enabled=True)
        result=m.run_once(config,send=True,now=NOW,fetcher=fixture,sender=sender)
        self.assertEqual(result['status'],'blocked_unresolved_delivery')
        self.assertEqual(sender.call_count,1)

    def test_initial_write_failure_prevents_send(self):
        sender=Mock()
        with patch.object(self.store,'write',side_effect=m.StateError('disk full')),self.assertRaises(m.StateError):
            a.send_test(REQUEST,self.store,sender=sender,now=NOW)
        sender.assert_not_called()

    def test_failed_final_write_keeps_pending_and_blocks_restart(self):
        original=self.store.write;count=0
        def write(state):
            nonlocal count
            count+=1
            if count==2:raise m.StateError('write acknowledgement lost')
            original(state)
        sender=Mock(side_effect=self.sender)
        with patch.object(self.store,'write',side_effect=write),self.assertRaises(m.StateError):
            a.send_test(REQUEST,self.store,sender=sender,now=NOW)
        self.assertEqual(self.store.read()['acceptance_tests'][REQUEST]['status'],'pending')
        with self.assertRaises(m.SendUnknown):a.send_test(REQUEST,self.store,sender=sender,now=NOW)
        self.assertEqual(sender.call_count,1)

    def test_invalid_request_or_unknown_forecast_never_sends_test(self):
        sender=Mock()
        with self.assertRaises(m.MonitorError):a.send_test('bad',self.store,sender=sender)
        state=m.empty_state();state['alerts']['forecast:'+'a'*40]={'status':'unknown','attempted_at':m.iso(NOW)}
        self.store.write(state)
        with self.assertRaises(m.SendUnknown):a.send_test(REQUEST,self.store,sender=sender,now=NOW)
        sender.assert_not_called()

    def test_confirmed_test_does_not_suppress_normal_forecast_cooldown(self):
        a.send_test(REQUEST,self.store,sender=self.sender,now=NOW)
        sender=Mock(return_value='webhook_code_0')
        result=m.run_once(m.Config(state_path=self.path,sending_enabled=True),send=True,now=NOW,fetcher=fixture,sender=sender)
        self.assertEqual(result['alerts'][0]['status'],'sent')
        sender.assert_called_once()


if __name__=='__main__':unittest.main()

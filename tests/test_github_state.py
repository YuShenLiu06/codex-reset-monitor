"""No network: emulate a remote ledger shared by fresh hourly runners."""
import base64
import copy
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import github_state as g
import monitor as m
from test_monitor import NOW, fixture


class Remote:
    def __init__(self):
        self.raw=json.dumps(m.empty_state()).encode()
        self.writes=[]
        self.fail_before=False
        self.lose_ack=False
        self.fail_final=False

    @property
    def sha(self):
        return hashlib.sha1(self.raw).hexdigest()

    def request(self, method, url, payload=None):
        if method == 'GET':
            return {'encoding':'base64','content':base64.b64encode(self.raw).decode(),
                    'size':len(self.raw),'sha':self.sha}
        if self.fail_before or payload['sha'] != self.sha:
            raise m.StateError('CAS conflict or offline; refuse delivery')
        if self.fail_final and len(self.writes) == 1:
            raise m.StateError('final write failed')
        self.raw=base64.b64decode(payload['content']); self.writes.append(copy.deepcopy(payload))
        if self.lose_ack:
            self.lose_ack=False
            raise m.StateError('write accepted but acknowledgement lost')
        return {'content':{'sha':self.sha}}


class GitHubStateTest(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'GH_TOKEN':'synthetic'+'-token',
            'FEISHU_WEBHOOK_URL':'https://open.feishu.cn/open-apis/bot/v2/hook/'+'x'*24,
            'FEISHU_SIGNING_SECRET':'unit'+'-test'})
        self.env.start()
        self.remote=Remote()
        self.calls=[]
        self.config=m.Config(sending_enabled=True)

    def tearDown(self):
        self.env.stop()

    def store(self):
        store=g.GitHubStateStore()
        store._request=self.remote.request
        return store

    def sender(self,config,alert):
        self.calls.append(alert)
        state=json.loads(self.remote.raw)
        self.assertEqual(state['alerts'][alert.key]['status'],'pending')
        return 'webhook_code_0'

    def run_monitor(self,store=None,sender=None):
        return m.run_once(self.config,send=True,now=NOW,fetcher=fixture,
                          sender=sender or self.sender,store=store or self.store())

    def test_new_runner_restores_dedup_without_local_files(self):
        self.assertEqual(self.run_monitor()['alerts'][0]['status'],'sent')
        self.assertEqual(self.run_monitor()['alerts'][0]['status'],'duplicate_suppressed')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(self.remote.writes),2)

    def test_remote_pending_failure_prevents_http_attempt(self):
        self.remote.fail_before=True
        with self.assertRaises(m.StateError): self.run_monitor()
        self.assertEqual(self.calls,[])

    def test_lost_pending_ack_blocks_now_and_next_runner(self):
        self.remote.lose_ack=True
        with self.assertRaises(m.StateError): self.run_monitor()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.run_monitor()['status'],'blocked_unresolved_delivery')

    def test_final_remote_write_failure_leaves_pending(self):
        self.remote.fail_final=True
        with self.assertRaises(m.StateError): self.run_monitor()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.run_monitor()['status'],'blocked_unresolved_delivery')

    def test_webhook_unknown_persisted_and_blocks_future_runs(self):
        def fail(config,alert):
            self.calls.append(alert); raise m.SendUnknown('timeout')
        with self.assertRaises(m.SendUnknown): self.run_monitor(sender=fail)
        self.assertEqual(next(iter(json.loads(self.remote.raw)['alerts'].values()))['status'],'unknown')
        self.assertEqual(self.run_monitor()['status'],'blocked_unresolved_delivery')
        self.assertEqual(len(self.calls),1)

    def test_stale_concurrent_reader_loses_cas(self):
        first,second=self.store(),self.store()
        state1,state2=first.read(),second.read()
        key='forecast:'+'a'*40
        state1['alerts'][key]={'status':'pending','attempted_at':m.iso(NOW)}
        state2['alerts'][key]={'status':'pending','attempted_at':m.iso(NOW.replace(second=1))}
        first.write(state1)
        with self.assertRaises(m.StateError): second.write(state2)
        self.assertEqual(len(self.remote.writes),1)

    def test_missing_token_fails_closed(self):
        with patch.dict(os.environ,{},clear=True), self.assertRaises(m.StateError): g.GitHubStateStore()

    def test_missing_branch_or_auth_failure_never_initializes(self):
        store=g.GitHubStateStore()
        opener=Mock(); opener.open.side_effect=OSError('private upstream text')
        with patch.object(g.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(m.StateError) as caught: store.read()
        self.assertNotIn('private upstream text',str(caught.exception))
        self.assertEqual(opener.open.call_count,1)
        self.assertIsNone(store.sha)

    def test_corrupt_remote_state_refuses_reset(self):
        for raw in (b'{bad',b'{"version":2,"alerts":{},"last_sent_at":null}',
                    b'{"version":1,"alerts":{"invalid":{}},"last_sent_at":null}'):
            self.remote.raw=raw
            with self.subTest(raw=raw),self.assertRaises(m.StateError): self.store().read()
        self.assertEqual(self.remote.writes,[])

    def test_read_requires_valid_encoding_size_and_sha(self):
        valid=self.remote.request('GET','unused')
        for override in ({'encoding':'none'},{'size':True},{'size':m.MAX_STATE_BYTES+1},
                         {'size':1},{'sha':'invalid'},{'content':'not-base64!'}):
            store=g.GitHubStateStore(); store._request=Mock(return_value=dict(valid,**override))
            with self.subTest(override=override),self.assertRaises(m.StateError): store.read()

    def test_write_requires_read_and_confirmed_sha(self):
        store=self.store()
        with self.assertRaises(m.StateError): store.write(m.empty_state())
        store.read(); store._request=Mock(return_value={'content':{'sha':'wrong'}})
        with self.assertRaises(m.StateError): store.write(m.empty_state())

    def test_remote_transport_fixed_host_headers_and_no_proxy(self):
        class Response(io.BytesIO): pass
        response=Response(b'{"content":{"sha":"'+b'a'*40+b'"}}'); response.status=200
        opener=Mock(); opener.open.return_value=response
        store=g.GitHubStateStore(); store.sha='b'*40
        with patch.object(g.urllib.request,'build_opener',return_value=opener) as build:
            store.write(m.empty_state())
        req=opener.open.call_args.args[0]
        self.assertEqual(req.full_url,f'https://api.github.com/repos/{g.REPOSITORY}/contents/state.json')
        self.assertEqual(req.method,'PUT')
        payload=json.loads(req.data)
        self.assertEqual(payload['branch'],'monitor-state')
        self.assertEqual(payload['sha'],'b'*40)
        self.assertEqual(build.call_args.args[0].proxies,{})
        self.assertEqual(opener.open.call_args.kwargs['timeout'],15)
        self.assertNotIn(os.environ['GH_TOKEN'],req.data.decode())
        self.assertNotIn(os.environ['FEISHU_SIGNING_SECRET'],req.data.decode())

    def test_remote_response_size_limited(self):
        class Response(io.BytesIO): pass
        response=Response(b' '*(2*m.MAX_STATE_BYTES+1)); response.status=200
        opener=Mock(); opener.open.return_value=response
        store=g.GitHubStateStore()
        with patch.object(g.urllib.request,'build_opener',return_value=opener),self.assertRaises(m.StateError): store.read()


if __name__=='__main__': unittest.main()

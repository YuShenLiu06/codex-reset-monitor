"""Synthetic credentials only. Every network boundary is replaced or denied."""
import dataclasses
from datetime import timedelta
from email.message import Message
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import feishu_app as a
import monitor as m
from test_monitor import NOW, fixture


class FakeClock:
    def __init__(self): self.now=0; self.waits=[]
    def clock(self): return self.now
    def sleep(self,seconds): self.waits.append(seconds); self.now+=seconds


class AppTest(unittest.TestCase):
    def setUp(self):
        self.credentials=a.AppCredentials('cli_'+'x'*16,'synthetic'+'-secret','ou_'+'y'*24)
        self.temp=tempfile.TemporaryDirectory()
        self.config=m.Config(state_path=Path(self.temp.name)/'state.json',sending_enabled=True)
        self.record=a.readiness_record(self.credentials,self_confirmed=True,published_bot_confirmed=True,
            self_availability_confirmed=True,send_scope='im:message:send_as_bot')
        self.env=patch.dict(os.environ,{'FEISHU_NOTIFY_BACKEND':'app',
            'FEISHU_APP_ID':self.credentials.app_id,'FEISHU_APP_SECRET':self.credentials.app_secret,
            'FEISHU_SELF_OPEN_ID':self.credentials.open_id,'FEISHU_APP_READINESS':json.dumps(self.record)})
        self.env.start()
        # A forgotten HTTP mock must never call the network in this suite.
        self.network=patch.object(a.urllib.request,'build_opener',side_effect=AssertionError('Network forbidden'))
        self.network.start()

    def tearDown(self): self.network.stop(); self.env.stop(); self.temp.cleanup()

    def success(self):
        return {'client_id':self.credentials.app_id,'client_secret':self.credentials.app_secret,
                'user_info':{'open_id':self.credentials.open_id,'tenant_brand':'feishu'}}

    def alert(self): return m.forecast_alert(m.validate_snapshot(fixture(),NOW),self.config)

    def responses(self):
        return [{'code':0,'tenant_access_token':'synthetic'+'-tat','expire':7200},
                {'code':0,'data':{'message_id':'om_unit_response'}}]

    def test_repr_never_contains_credentials_or_device_code(self):
        for value in (repr(self.credentials),str(self.credentials)):
            self.assertNotIn(self.credentials.app_secret,value)
            self.assertNotIn(self.credentials.open_id,value)
        session=a.Registration('sensitive'+'-device','usercode',600,5)
        self.assertNotIn(session.device_code,repr(session))

    def test_bad_credentials_are_safe_errors(self):
        for overrides in ({'app_id':None},{'app_id':'bad'},{'app_secret':'short'},
                          {'app_secret':'x'*1025},{'app_secret':'bad\nsecret'},{'open_id':'oc_'+'x'*24}):
            with self.subTest(overrides=overrides),self.assertRaises(m.MonitorError):
                a.validate_credentials(dataclasses.replace(self.credentials,**overrides))

    def test_registration_does_not_create_readiness_automatically(self):
        for flags in ({'self_confirmed':False},{'published_bot_confirmed':False},
                      {'self_availability_confirmed':1},{'send_scope':'im:message'}):
            values=dict(self_confirmed=True,published_bot_confirmed=True,
                        self_availability_confirmed=True,send_scope='im:message:send_as_bot')
            values.update(flags)
            with self.subTest(flags=flags),self.assertRaises(m.MonitorError):
                a.readiness_record(self.credentials,**values)

    def test_environment_requires_bound_self_and_readiness(self):
        for override in ({'open_id':'ou_'+'z'*24},{'app_id':'cli_'+'z'*16},
            {'version':True},{'self_confirmed':1},{'published_bot_confirmed':False},
            {'self_availability_confirmed':False},{'identity_source':'guessed'},
            {'send_scope':'im:message:send_as_user'}):
            with patch.dict(os.environ,{'FEISHU_APP_READINESS':json.dumps(dict(self.record,**override))}):
                with self.subTest(override=override),self.assertRaises(m.MonitorError):a.environment_credentials()
        with patch.dict(os.environ,{'FEISHU_APP_READINESS':'{bad'}),self.assertRaises(m.MonitorError):a.environment_credentials()

    def test_begin_exact_form_endpoint_and_protocol_defaults(self):
        request=Mock(return_value={'device_code':'synthetic-device','user_code':'ABC-123'})
        session=a.begin_registration(request)
        self.assertEqual(session.expires,600);self.assertEqual(session.interval,5)
        self.assertEqual(request.call_args.args[0],a.REGISTRATION_URL)
        self.assertEqual(request.call_args.args[1],{'action':'begin','archetype':'PersonalAgent',
            'auth_method':'client_secret','request_user_info':'open_id tenant_brand'})
        self.assertTrue(request.call_args.kwargs['form'])
        self.assertEqual(request.call_args.kwargs['timeout'],30)
        self.assertTrue(session.verification_url.startswith('https://open.feishu.cn/page/cli?user_code=ABC-123&'))

    def test_begin_ignores_upstream_verification_url(self):
        response={'device_code':'synthetic-device','user_code':'ABC',
                  'verification_uri':'https://attacker.invalid','expire_in':7000,'interval':1000}
        session=a.begin_registration(Mock(return_value=response))
        self.assertTrue(session.verification_url.startswith('https://open.feishu.cn/page/cli?'))
        self.assertEqual((session.expires,session.interval),(600,60))

    def test_sdk_addons_minimal_template_and_explicit_reuse(self):
        import base64,gzip,urllib.parse
        request=Mock(return_value={'device_code':'device','user_code':'ABC'})
        session=a.begin_registration(request,create_only=True)
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(session.verification_url).query)
        encoded=query['addons'][0]
        addons=json.loads(gzip.decompress(base64.urlsafe_b64decode(encoded+'='*((4-len(encoded)%4)%4))))
        self.assertEqual(addons,{'preset':False,'scopes':{'tenant':['im:message:send_as_bot']}})
        self.assertEqual(query['createOnly'],['true'])
        session=a.begin_registration(request,existing_app_id=self.credentials.app_id)
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(session.verification_url).query)
        self.assertEqual(query['clientID'],[self.credentials.app_id])
        with self.assertRaises(m.MonitorError):a.begin_registration(request,create_only=True,existing_app_id=self.credentials.app_id)

    def test_legacy_expiry_and_invalid_defaults(self):
        base={'device_code':'synthetic-device','user_code':'ABC'}
        session=a.begin_registration(Mock(return_value=dict(base,expires_in=120,interval=10)))
        self.assertEqual((session.expires,session.interval),(120,10))
        session=a.begin_registration(Mock(return_value=dict(base,expire_in=True,interval=-1)))
        self.assertEqual((session.expires,session.interval),(600,5))

    def test_begin_invalid_fields_and_errors_never_echo_server(self):
        base={'device_code':'synthetic-device','user_code':'ABC'}
        for override in ({'error':'denied','error_description':self.credentials.app_secret},
                         {'device_code':None},{'user_code':'bad&redirect=evil'}):
            with self.subTest(override=override),self.assertRaises(m.MonitorError) as caught:
                a.begin_registration(Mock(return_value=dict(base,**override)))
            self.assertNotIn(self.credentials.app_secret,str(caught.exception))

    def test_poll_pending_slowdown_and_complete_identity(self):
        clock=FakeClock(); request=Mock(side_effect=[{'error':'authorization_pending'},
            {'error':'slow_down'},self.success()])
        result=a.poll_registration(a.Registration('device','ABC',600,5),request,clock.clock,clock.sleep)
        self.assertEqual(result.open_id,self.credentials.open_id)
        self.assertEqual(clock.waits,[5,10])
        self.assertEqual(request.call_args.args[1],{'action':'poll','device_code':'device'})
        self.assertEqual(request.call_count,3)

    def test_poll_first_request_immediate_and_no_begin_retry(self):
        clock=FakeClock();request=Mock(return_value=self.success())
        a.poll_registration(a.Registration('device','ABC',600,5),request,clock.clock,clock.sleep)
        self.assertEqual(clock.waits,[]);self.assertEqual(request.call_count,1)
        self.assertEqual(request.call_args.args[1]['action'],'poll')

    def test_poll_incomplete_credentials_keep_waiting(self):
        clock=FakeClock();request=Mock(side_effect=[{'client_id':self.credentials.app_id},self.success()])
        a.poll_registration(a.Registration('device','ABC',600,5),request,clock.clock,clock.sleep)
        self.assertEqual(clock.waits,[5])

    def test_poll_network_backoff_and_deadline_are_bounded(self):
        clock=FakeClock(); request=Mock(side_effect=m.MonitorError('synthetic upstream error'))
        with self.assertRaises(m.MonitorError):
            a.poll_registration(a.Registration('device','ABC',13,5),request,clock.clock,clock.sleep)
        self.assertEqual(clock.now,13)
        self.assertEqual(clock.waits,[6,7])
        self.assertEqual(request.call_count,2)

    def test_denied_expired_unknown_fail_without_description(self):
        for error in ('access_denied','expired_token','invalid_grant','unknown_error'):
            request=Mock(return_value={'error':error,'error_description':self.credentials.app_secret})
            with self.subTest(error=error),self.assertRaises(m.MonitorError) as caught:
                a.poll_registration(a.Registration('device','ABC',600,5),request)
            self.assertNotIn(self.credentials.app_secret,str(caught.exception))
            self.assertEqual(request.call_count,1)

    def test_lark_brand_or_missing_self_identity_is_rejected(self):
        for override in ({'user_info':{'tenant_brand':'lark','open_id':self.credentials.open_id}},
                         {'user_info':{'tenant_brand':'feishu'}},{'user_info':None}):
            with self.subTest(override=override),self.assertRaises(m.MonitorError):
                a.poll_registration(a.Registration('device','ABC',600,5),Mock(return_value=dict(self.success(),**override)))

    def test_token_code_type_expiry_and_value_validate(self):
        valid=self.responses()[0]
        for override in ({'code':True},{'code':999},{'expire':None},{'expire':True},{'expire':0},
                         {'tenant_access_token':'short'},{'tenant_access_token':'bad\ntoken'}):
            with self.subTest(override=override),self.assertRaises(m.MonitorError):
                a.tenant_token(self.credentials,Mock(return_value=dict(valid,**override)))

    def test_token_only_proves_authentication(self):
        request=Mock(return_value=self.responses()[0])
        self.assertEqual(a.tenant_token(self.credentials,request),'synthetic-tat')
        self.assertEqual(request.call_args.args,(a.TOKEN_URL,{'app_id':self.credentials.app_id,
            'app_secret':self.credentials.app_secret}))
        with patch.dict(os.environ,{'FEISHU_APP_READINESS':''}),patch.object(a,'request_json') as request:
            with self.assertRaises(m.SendUnknown):a.app_sender(self.config,self.alert())
            request.assert_not_called()

    def test_signed_app_sender_exact_self_destination_and_uuid(self):
        with patch.object(a,'request_json',side_effect=self.responses()) as request:
            self.assertEqual(a.app_sender(self.config,self.alert()),'om_unit_response')
        call=request.call_args_list[1]
        self.assertEqual(call.args[0],a.MESSAGE_URL)
        self.assertEqual(call.args[1]['receive_id'],self.credentials.open_id)
        self.assertEqual(call.args[1]['msg_type'],'text')
        self.assertEqual(json.loads(call.args[1]['content'])['text'],self.alert().text)
        self.assertEqual(call.args[1]['uuid'],m.digest(self.alert().key))
        self.assertEqual(call.kwargs['bearer'],'synthetic-tat')
        self.assertNotIn(self.credentials.app_secret,json.dumps(call.args[1]))

    def test_message_business_errors_or_no_receipt_are_unknown_no_retry(self):
        for response in ({'code':230013,'msg':self.credentials.app_secret},{'code':True},
                         {'code':0,'data':{}},{'code':0,'data':{'message_id':'invalid'}}):
            with patch.object(a,'request_json',side_effect=[self.responses()[0],response]) as request:
                with self.assertRaises(m.SendUnknown) as caught:a.app_sender(self.config,self.alert())
            self.assertNotIn(self.credentials.app_secret,str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)
            self.assertEqual(request.call_count,2)

    def test_send_disabled_backend_mismatch_and_oversize_never_authenticate(self):
        with patch.object(a,'request_json') as request:
            with self.assertRaises(m.SendUnknown):a.app_sender(dataclasses.replace(self.config,sending_enabled=False),self.alert())
            with self.assertRaises(m.SendUnknown):a.app_sender(self.config,m.Alert(self.alert().key,'forecast24h','中'*20000))
            request.assert_not_called()
        with patch.dict(os.environ,{'FEISHU_NOTIFY_BACKEND':'webhook'}),patch.object(a,'request_json') as request:
            with self.assertRaises(m.SendUnknown):a.app_sender(self.config,self.alert())
            request.assert_not_called()
        with self.assertRaises(m.MonitorError):m.webhook_sender(self.config,self.alert())

    def test_monitor_keeps_existing_cycle_dedup_across_backend_switch(self):
        sender=Mock(return_value='webhook_code_0')
        m.run_once(self.config,send=True,now=NOW,fetcher=fixture,sender=sender)
        with patch.object(a,'request_json') as request:
            result=m.run_once(self.config,send=True,now=NOW,fetcher=fixture)
        self.assertEqual(result['alerts'][0]['status'],'duplicate_suppressed')
        request.assert_not_called()

    def test_real_dispatch_reserves_before_app_http_and_deduplicates(self):
        def request(url,payload,**kwargs):
            state=m.StateStore(self.config.state_path).read()
            self.assertEqual(state['alerts'][self.alert().key]['status'],'pending')
            return self.responses()[0] if url==a.TOKEN_URL else self.responses()[1]
        with patch.object(a,'request_json',side_effect=request) as calls:
            result=m.run_once(self.config,send=True,now=NOW,fetcher=fixture)
            self.assertEqual(result['alerts'][0]['receipt'],'om_unit_response')
            m.run_once(self.config,send=True,now=NOW+timedelta(minutes=1),fetcher=fixture)
        self.assertEqual(calls.call_count,2)
        raw=self.config.state_path.read_text()
        self.assertNotIn(self.credentials.app_secret,raw)
        self.assertNotIn(self.credentials.open_id,raw)
        self.assertNotIn('synthetic-tat',raw)

    def test_unknown_app_delivery_blocks_future_http(self):
        with patch.object(a,'request_json',side_effect=[self.responses()[0],m.MonitorError('secret response')]):
            with self.assertRaises(m.SendUnknown):m.run_once(self.config,send=True,now=NOW,fetcher=fixture)
        with patch.object(a,'request_json') as request:
            self.assertEqual(m.run_once(self.config,send=True,now=NOW,fetcher=fixture)['status'],'blocked_unresolved_delivery')
            request.assert_not_called()

    def test_app_dry_run_does_not_access_credentials_or_network(self):
        with patch.dict(os.environ,{},clear=True),patch.object(a,'environment_credentials',side_effect=AssertionError()) as creds:
            result=m.run_once(self.config,now=NOW,fetcher=fixture)
        self.assertEqual(result['alerts'][0]['status'],'would_alert');creds.assert_not_called()

    def test_transport_post_headers_no_proxy_or_redirect(self):
        class Response(io.BytesIO):pass
        response=Response(b'{"code":0}');response.status=200;response.headers=Message();response.headers['Content-Type']='application/json'
        opener=Mock();opener.open.return_value=response
        with patch.object(a.urllib.request,'build_opener',return_value=opener) as build:
            a.request_json(a.MESSAGE_URL,{'text':'synthetic'},bearer='synthetic-tat')
        req=opener.open.call_args.args[0]
        self.assertEqual(req.method,'POST');self.assertEqual(req.full_url,a.MESSAGE_URL)
        self.assertEqual(req.get_header('Authorization'),'Bearer synthetic-tat')
        self.assertEqual(build.call_args.args[0].proxies,{})
        self.assertIsInstance(build.call_args.args[1],m.NoRedirect)

    def test_transport_sensitive_upstream_errors_redacted(self):
        opener=Mock();opener.open.side_effect=OSError(self.credentials.app_secret)
        with patch.object(a.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(m.MonitorError) as caught:a.request_json(a.TOKEN_URL,{'app_secret':self.credentials.app_secret})
        self.assertNotIn(self.credentials.app_secret,str(caught.exception));self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(opener.open.call_count,1)

    def test_transport_malformed_oversized_http_errors_rejected(self):
        class Response(io.BytesIO):pass
        for body,status,content_type in ((b'{bad',200,'application/json'),(b'[]',200,'application/json'),
            (b' '*65537,200,'application/json'),(b'{}',403,'application/json'),(b'{}',200,'text/html')):
            response=Response(body);response.status=status;response.headers=Message();response.headers['Content-Type']=content_type
            opener=Mock();opener.open.return_value=response
            with patch.object(a.urllib.request,'build_opener',return_value=opener),self.assertRaises(m.MonitorError):
                a.request_json(a.TOKEN_URL,{})
        with self.assertRaises(m.MonitorError):a.request_json('https://attacker.invalid',{})


if __name__=='__main__':unittest.main()

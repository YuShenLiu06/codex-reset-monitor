"""Failure boundaries and redaction before device authorization. No real OAuth."""
import io
import json
import socket
import ssl
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import urllib.error
from installer import github_api as api
from installer.core import device_authorize
from installer.gui_worker import GUI
from installer.tls import bootstrap_tls,configure_native_trust

URL='https://github.com/login/device/code'
SECRET='private-device-token-cookie-value'

class Response:
    def __init__(self,body=b'{}',status=200,headers=None):
        self.body=io.BytesIO(body);self.status=status;self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*unused):pass
    def read(self,size):return self.body.read(size)

class OAuthTransportTest(unittest.TestCase):
    def request(self,response=None,error=None):
        opener=Mock()
        if error is not None:opener.open.side_effect=error
        else:opener.open.return_value=response or Response()
        with patch.object(api.urllib.request,'build_opener',return_value=opener):
            try:return api.oauth_request(URL,{'client_id':'Iv1.PublicID'})
            finally:self.assertEqual(opener.open.call_count,1)
    def error(self,exc,code,category):
        with self.assertRaises(api.SetupError) as caught:self.request(error=exc)
        error=caught.exception
        self.assertEqual(error.code,code);self.assertEqual(error.diagnostics['category'],category)
        self.assertEqual(error.diagnostics['phase'],'github_device_code')
        self.assertNotIn(SECRET,str(error));self.assertNotIn(SECRET,json.dumps(error.diagnostics))
        return error
    def test_packaged_certificate_failure_is_distinct_and_redacted(self):
        cause=ssl.SSLCertVerificationError(1,SECRET);cause.verify_code=20
        error=self.error(urllib.error.URLError(cause),'DEVICE_TLS_CERTIFICATE','certificate')
        self.assertEqual(error.diagnostics['verify_code'],20)
        self.assertEqual(error.diagnostics['exception_type'],'SSLCertVerificationError')
    def test_timeout_dns_connection_and_tls_do_not_retry_or_echo_reason(self):
        for cause,code,category in ((TimeoutError(SECRET),'DEVICE_REQUEST_TIMEOUT','timeout'),
            (socket.gaierror(-2,SECRET),'DEVICE_DNS_FAILED','dns'),
            (ConnectionRefusedError(SECRET),'DEVICE_CONNECTION_FAILED','connection'),
            (ssl.SSLError(1,SECRET),'DEVICE_TLS_FAILED','tls')):
            with self.subTest(code=code):self.error(urllib.error.URLError(cause),code,category)
    def test_http_status_and_safe_request_id_without_body(self):
        exc=urllib.error.HTTPError(URL,403,SECRET,{'X-GitHub-Request-Id':'ABCD:1234:EF'},io.BytesIO(SECRET.encode()))
        error=self.error(exc,'DEVICE_HTTP_ERROR','http')
        self.assertEqual(error.diagnostics['http_status'],403);self.assertEqual(error.diagnostics['request_id'],'ABCD:1234:EF')
    def test_invalid_json_and_oversize_response_have_bounded_safe_errors(self):
        for body,code in ((SECRET.encode(),'DEVICE_INVALID_JSON'),(b'x'*65537,'DEVICE_RESPONSE_TOO_LARGE'),(b'[]','DEVICE_INVALID_RESPONSE')):
            with self.subTest(code=code),self.assertRaises(api.SetupError) as caught:self.request(Response(body,headers={'X-GitHub-Request-Id':'TEST-123'}))
            self.assertEqual(caught.exception.code,code);self.assertNotIn(SECRET,str(caught.exception))
    def test_metadata_never_accepts_credentials_or_arbitrary_keys(self):
        value=api.safe_diagnostics({'phase':SECRET,'category':SECRET,'exception_type':SECRET,'request_id':'ghu_'+SECRET,
            'http_status':True,'elapsed_ms':-1,'token':SECRET,'body':SECRET,'url':SECRET})
        self.assertEqual(value,{})
    def test_cancellation_is_not_folded_into_network_error(self):
        with self.assertRaises(KeyboardInterrupt):self.request(error=KeyboardInterrupt())
    def test_dribbling_response_hits_total_deadline_instead_of_waiting_forever(self):
        clock=[0]
        class Slow(Response):
            def read(self,size):clock[0]+=1;return b' '
        opener=Mock();opener.open.return_value=Slow()
        with patch.object(api.urllib.request,'build_opener',return_value=opener),patch.object(api.time,'monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(api.SetupError) as caught:api.oauth_request(URL,{},timeout=3)
        self.assertEqual(caught.exception.code,'DEVICE_REQUEST_TIMEOUT');self.assertEqual(clock[0],3)
        self.assertEqual(opener.open.call_count,1)
    def test_config_rejection_before_device_page_is_specific_and_never_retried(self):
        request=Mock(return_value={'error':'device_flow_disabled','error_description':SECRET})
        emit=Mock()
        with self.assertRaises(api.SetupError) as caught:device_authorize('Iv1.PublicID',emit,request=request)
        self.assertEqual(caught.exception.code,'DEVICE_FLOW_DISABLED');self.assertEqual(request.call_count,1)
        self.assertNotIn(SECRET,str(caught.exception));self.assertNotIn('login/device',str(emit.call_args_list))
    def test_failure_ui_exposes_safe_phase_and_support_id_only(self):
        out=io.StringIO();GUI(writer=out).failure(api.SetupError('DEVICE_HTTP_ERROR',SECRET,diagnostics={
            'phase':'github_device_code','category':'http','http_status':429,'request_id':'TEST-ID','token':SECRET}))
        value=json.loads(out.getvalue());self.assertTrue(value['retry_device']);self.assertIn('429',value['text'])
        self.assertIn('TEST-ID',value['text']);self.assertNotIn(SECRET,out.getvalue());self.assertEqual(value['user_code'],'')
    def test_token_poll_failures_never_offer_automatic_or_new_device_retry(self):
        out=io.StringIO();GUI(writer=out).failure(api.SetupError('DEVICE_REQUEST_TIMEOUT',diagnostics={
            'phase':'github_device_poll','category':'timeout'}))
        self.assertFalse(json.loads(out.getvalue())['retry_device'])

class NativeTrustTest(unittest.TestCase):
    def test_source_and_actions_do_not_require_desktop_dependency(self):
        self.assertEqual(configure_native_trust(),'platform-default')
    def test_frozen_entry_uses_native_system_trust_and_keeps_verification(self):
        module=SimpleNamespace(inject_into_ssl=Mock())
        with patch.object(sys,'frozen',True,create=True),patch.dict(sys.modules,{'truststore':module}):
            self.assertEqual(configure_native_trust(),'native-system')
        module.inject_into_ssl.assert_called_once()
    def test_tls_bootstrap_failure_uses_safe_ipc_instead_of_traceback(self):
        output=io.StringIO();module=SimpleNamespace(inject_into_ssl=Mock(side_effect=RuntimeError(SECRET)))
        with patch.object(sys,'frozen',True,create=True),patch.dict(sys.modules,{'truststore':module}),patch('sys.stdout',output):
            self.assertFalse(bootstrap_tls())
        self.assertNotIn(SECRET,output.getvalue());messages=[json.loads(s) for s in output.getvalue().splitlines()]
        self.assertEqual(messages[-1],{'type':'done','code':2})
        self.assertIn('TLS_TRUST_UNAVAILABLE',messages[0]['text'])

if __name__=='__main__':unittest.main()

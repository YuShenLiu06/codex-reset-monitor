"""Explicit native artifact tests. No real OAuth, account API or browser calls."""
import io
import json
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request
import webbrowser


def run(mode,root):
    if mode not in {'certificate','http','json','slow','retry','device','post-device','post-device-missing'}:raise ValueError('Unsupported offline device mode')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    import installer.cli as cli
    from installer.gui_worker import GUI
    post_device=mode in {'post-device','post-device-missing'}
    api=None
    if post_device:
        from installer.offline_preflight import SnapshotAPI
        from installer.core import SOURCE_REPOSITORY,REQUIRED_PERMISSIONS
        class API(SnapshotAPI):
            token='synthetic-offline-only'
            def close(self):self.token=None
            def request(self,method,path,payload=None):
                assert method=='GET' and payload is None
                owner=SOURCE_REPOSITORY.split('/')[0]
                if path=='/user':return {'login':owner,'id':123}
                if path.startswith('/user/installations?'):
                    return {'total_count':1,'installations':[{'id':1,'app_slug':'codex-reset-installer','account':{'login':owner},'repository_selection':'selected','permissions':dict(REQUIRED_PERMISSIONS)}]}
                if path.startswith('/user/installations/1/repositories?'):
                    return {'total_count':1,'repositories':[{'full_name':SOURCE_REPOSITORY}]}
                return super().request(method,path,payload)
        api=API(json.loads((root/'public-source.json').read_text()),'missing' if mode.endswith('missing') else 'paired')
    attempt=root/'attempts.json';number=json.loads(attempt.read_text())+1 if attempt.exists() else 1
    attempt.write_text(json.dumps(number))
    requests=[]
    class Response:
        status=200;headers={'X-GitHub-Request-Id':'OFFLINE-SYNTHETIC'}
        def __init__(self,body):self.body=io.BytesIO(body)
        def read(self,size):return self.body.read(size)
        def __enter__(self):return self
        def __exit__(self,*unused):pass
    class Opener:
        def open(self,request,timeout=None):
            assert request.full_url in ('https://github.com/login/device/code','https://github.com/login/oauth/access_token')
            requests.append({'phase':'device' if request.full_url.endswith('/device/code') else 'poll'})
            (root/('requests-'+str(number)+'.json')).write_text(json.dumps(requests))
            if mode=='slow':time.sleep(5);raise TimeoutError('synthetic slow response')
            if mode=='certificate' or (mode=='retry' and number==1):
                error=ssl.SSLCertVerificationError(1,'synthetic private response omitted');error.verify_code=20
                raise urllib.error.URLError(error)
            if mode=='http':raise urllib.error.HTTPError(request.full_url,403,'synthetic private response omitted',{'X-GitHub-Request-Id':'OFFLINE-REQUEST-ID'},io.BytesIO(b'PRIVATE-OFFLINE-RESPONSE'))
            if mode=='json':return Response(b'<html>PRIVATE-OFFLINE-RESPONSE</html>')
            if request.full_url.endswith('/device/code'):
                return Response(json.dumps({'device_code':'synthetic-offline-device','user_code':'TEST-0001','expires_in':900,'interval':1 if post_device else 5}).encode())
            if post_device:return Response(b'{"access_token":"ghu_SYNTHETICOFFLINE0000","token_type":"bearer","expires_in":3600}')
            return Response(b'{"error":"authorization_pending"}')
    gui=GUI()
    # All HTTP and browser boundaries are replaced before cli.main executes.
    previous_opener=urllib.request.build_opener;previous_browser=webbrowser.open;previous_api=cli.GitHubAPI
    def browser_forbidden(*args,**kwargs):raise AssertionError('Offline browser forbidden')
    urllib.request.build_opener=lambda *args,**kwargs:Opener()
    webbrowser.open=browser_forbidden
    if post_device:cli.GitHubAPI=lambda token:api
    try:
        arguments=['--progress',str(root/'state.local.json'),'--public-config',str(root/'app.local.json')]
        if post_device:arguments+=['--target',SOURCE_REPOSITORY]
        code=cli.main(arguments,ui=gui)
    finally:
        urllib.request.build_opener=previous_opener;webbrowser.open=previous_browser;cli.GitHubAPI=previous_api
        if post_device:(root/'offline-report.json').write_text(json.dumps({'http_requests':0,'oauth_requests':0,'api_reads':len(api.calls),'writes':0,'identity':'synthetic','fixture_commit':api.snapshot['commit']}))
    gui.send({'type':'done','code':code})
    return code

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
    if mode not in {'certificate','http','json','slow','retry','device'}:raise ValueError('Unsupported offline device mode')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    import installer.cli as cli
    from installer.gui_worker import GUI
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
                return Response(json.dumps({'device_code':'synthetic-offline-device','user_code':'TEST-0001','expires_in':900,'interval':5}).encode())
            return Response(b'{"error":"authorization_pending"}')
    gui=GUI()
    # All HTTP and browser boundaries are replaced before cli.main executes.
    previous_opener=urllib.request.build_opener;previous_browser=webbrowser.open
    def browser_forbidden(*args,**kwargs):raise AssertionError('Offline browser forbidden')
    urllib.request.build_opener=lambda *args,**kwargs:Opener()
    webbrowser.open=browser_forbidden
    try:
        code=cli.main(['--progress',str(root/'state.local.json'),'--public-config',str(root/'app.local.json')],ui=gui)
    finally:
        urllib.request.build_opener=previous_opener;webbrowser.open=previous_browser
    gui.send({'type':'done','code':code})
    return code

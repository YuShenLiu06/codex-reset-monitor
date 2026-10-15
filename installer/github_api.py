"""Fixed-host, bounded, redacted GitHub API; no persisted authentication."""
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from monitor import NoRedirect, decode_json


class SetupError(Exception):
    def __init__(self, code, hint='', *, diagnostics=None):
        self.code=code
        self.hint=hint
        self.diagnostics=safe_diagnostics(diagnostics or {})
        super().__init__(code+(': '+hint if hint else ''))


def safe_diagnostics(value):
    """An allowlist of support metadata, never exception text or response data."""
    result={}
    enums={
        'phase':{'github_device_code','github_device_poll','tls_initialization'},
        'category':{'certificate','tls','timeout','dns','connection','http','json','response','config','redirect','trust_setup'},
        'exception_type':{'SSLCertVerificationError','SSLError','TimeoutError','gaierror','ConnectionRefusedError',
            'ConnectionResetError','URLError','OSError','JSONDecodeError','UnicodeDecodeError','MonitorError',
            'ValueError','ImportError','RuntimeError','Exception'}}
    for key,allowed in enums.items():
        if isinstance(value.get(key),str) and value[key] in allowed:result[key]=value[key]
    for key,low,high in (('http_status',100,599),('verify_code',0,9999),('elapsed_ms',0,600000)):
        item=value.get(key)
        if type(item) is int and low<=item<=high:result[key]=item
    request_id=value.get('request_id')
    if isinstance(request_id,str) and re.fullmatch(r'[A-Za-z0-9:-]{1,100}',request_id):result['request_id']=request_id
    return result


class GitHubAPI:
    def __init__(self, token): self.token=token
    def __repr__(self): return '<GitHubAPI credentials hidden>'
    def close(self): self.token=None

    def request(self, method, path, payload=None):
        if not self.token: raise SetupError('AUTH_REQUIRED')
        if not path.startswith('/') or path.startswith('//') or any(c in path for c in ('\r','\n','#')):
            raise SetupError('INVALID_API_PATH')
        body=None if payload is None else json.dumps(payload).encode()
        try:
            req=urllib.request.Request('https://api.github.com'+path,data=body,method=method,headers={
                'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
                'Content-Type':'application/json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'CodexResetInstaller/1'})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            with opener.open(req,timeout=15) as response:
                raw=response.read(2*1024*1024+1)
                if response.status==204: return None
                if response.status not in (200,201,202): raise SetupError('GITHUB_HTTP_ERROR')
                return decode_json(raw,2*1024*1024)
        except urllib.error.HTTPError as exc:
            code={401:'AUTH_REVOKED',403:'PERMISSION_OR_POLICY',404:'NOT_FOUND',409:'CONFLICT',429:'RATE_LIMITED'}.get(exc.code,'GITHUB_HTTP_ERROR')
            raise SetupError(code,'check GitHub official settings; do not expand permissions automatically') from None
        except SetupError: raise
        except Exception: raise SetupError('GITHUB_RESPONSE_UNKNOWN','inspect remote outcome before retrying writes') from None


def oauth_request(url,payload,timeout=15):
    if url not in ('https://github.com/login/device/code','https://github.com/login/oauth/access_token'):
        raise SetupError('INVALID_OAUTH_ENDPOINT')
    phase='github_device_code' if url.endswith('/device/code') else 'github_device_poll'
    started=time.monotonic();metadata={'phase':phase}
    def failure(code,category,exc=None):
        detail=dict(metadata,category=category,elapsed_ms=min(600000,max(0,int((time.monotonic()-started)*1000))))
        if exc is not None:
            reason=getattr(exc,'reason',exc)
            detail['exception_type']=type(reason).__name__
            if isinstance(reason,ssl.SSLCertVerificationError):detail['verify_code']=getattr(reason,'verify_code',None)
        return SetupError(code,'no automatic authorization retry; sensitive response omitted',diagnostics=detail)
    try:
        req=urllib.request.Request(url,data=urllib.parse.urlencode(payload).encode(),method='POST',headers={
            'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(req,timeout=timeout) as response:
            metadata.update(http_status=response.status,request_id=response.headers.get('X-GitHub-Request-Id'))
            if response.status!=200:raise failure('DEVICE_HTTP_ERROR','http')
            parts=[];total=0
            while True:
                if time.monotonic()-started>=timeout:raise TimeoutError()
                part=getattr(response,'read1',response.read)(min(8192,65537-total))
                if not part:break
                total+=len(part)
                if total>65536:raise failure('DEVICE_RESPONSE_TOO_LARGE','response')
                parts.append(part)
            try:data=decode_json(b''.join(parts),65536)
            except Exception as exc:raise failure('DEVICE_INVALID_JSON','json',exc.__cause__ or exc) from None
            if not isinstance(data,dict):raise failure('DEVICE_INVALID_RESPONSE','response')
            return data
    except SetupError:raise
    except urllib.error.HTTPError as exc:
        metadata.update(http_status=exc.code,request_id=exc.headers.get('X-GitHub-Request-Id') if exc.headers else None)
        raise failure('DEVICE_HTTP_ERROR','http') from None
    except Exception as exc:
        reason=getattr(exc,'reason',exc)
        if isinstance(reason,ssl.SSLCertVerificationError):code,category='DEVICE_TLS_CERTIFICATE','certificate'
        elif isinstance(reason,ssl.SSLError):code,category='DEVICE_TLS_FAILED','tls'
        elif isinstance(reason,(TimeoutError,socket.timeout)):code,category='DEVICE_REQUEST_TIMEOUT','timeout'
        elif isinstance(reason,socket.gaierror):code,category='DEVICE_DNS_FAILED','dns'
        elif str(exc)=='HTTP redirect rejected':code,category='DEVICE_REDIRECT_REJECTED','redirect'
        elif isinstance(reason,(OSError,urllib.error.URLError)):code,category='DEVICE_CONNECTION_FAILED','connection'
        else:code,category='DEVICE_RESPONSE_UNKNOWN','response'
        raise failure(code,category,exc) from None

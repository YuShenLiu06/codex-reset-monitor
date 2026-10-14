"""Fixed-host, bounded, redacted GitHub API; no persisted authentication."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from monitor import NoRedirect, decode_json


class SetupError(Exception):
    def __init__(self, code, hint=''):
        self.code=code
        self.hint=hint
        super().__init__(code+(': '+hint if hint else ''))


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
    try:
        req=urllib.request.Request(url,data=urllib.parse.urlencode(payload).encode(),method='POST',headers={
            'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(req,timeout=timeout) as response:
            if response.status!=200: raise ValueError()
            data=decode_json(response.read(65537),65536)
            if not isinstance(data,dict):raise ValueError()
            return data
    except Exception: raise SetupError('DEVICE_REQUEST_FAILED','sensitive response omitted') from None

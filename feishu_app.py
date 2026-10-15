"""Small, redacted Feishu application-bot transport and user-run registration.

Registration follows the official CLI implementation, not a separately promised
public API. No credential files, automatic login, refresh storage or retries of sends.
"""
from __future__ import annotations
import dataclasses
import gzip
import base64
import json
import os
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from monitor import MonitorError, SendUnknown, NoRedirect, decode_json, digest

REGISTRATION_URL = 'https://accounts.feishu.cn/oauth/v1/app/registration'
TOKEN_URL = 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'
MESSAGE_URL = 'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id'
APP_SECRET_NAMES = ('FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_SELF_OPEN_ID',
                    'FEISHU_APP_READINESS', 'FEISHU_NOTIFY_BACKEND')
DEFAULT_APP_NAME = 'Codex 提醒'


def validate_app_name(value):
    # A small installer input limit, not a claim about the platform's maximum.
    if not isinstance(value,str):raise MonitorError('Invalid app name')
    value=value.strip()
    if not 1<=len(value)<=20 or any(unicodedata.category(c).startswith('C') for c in value):
        raise MonitorError('App name must contain 1–20 characters without control characters')
    return value


@dataclasses.dataclass(repr=False)
class AppCredentials:
    app_id: str
    app_secret: str
    open_id: str
    identity_source: str = 'registration'


def validate_credentials(credentials):
    if not isinstance(credentials.app_id, str) or not re.fullmatch(r'cli_[a-zA-Z0-9_-]{8,128}', credentials.app_id):
        raise MonitorError('Invalid app ID')
    secret = credentials.app_secret
    if not isinstance(secret, str) or not 8 <= len(secret) <= 1024 or any(ord(c)<32 for c in secret):
        raise MonitorError('Missing or invalid app secret')
    if not isinstance(credentials.open_id, str) or not re.fullmatch(r'ou_[a-zA-Z0-9_-]{8,128}', credentials.open_id):
        raise MonitorError('Invalid self open_id; never use a name or chat ID')


def readiness_record(credentials, *, self_confirmed, published_bot_confirmed,
                     self_availability_confirmed, send_scope):
    """Caller must supply explicit operator checks; registration alone is insufficient."""
    validate_credentials(credentials)
    if (any(value is not True for value in (self_confirmed, published_bot_confirmed,
                                           self_availability_confirmed))
            or send_scope != 'im:message:send_as_bot'
            or credentials.identity_source not in ('registration', 'operator_verified_reuse')):
        raise MonitorError('Explicit self, published bot, scope and availability verification required')
    # This records explicit operator verification, not an invented API proof.
    return {'version':1, 'app_id':credentials.app_id, 'open_id':credentials.open_id,
            'identity_source':credentials.identity_source, 'self_confirmed':True,
            'send_scope':'im:message:send_as_bot', 'published_bot_confirmed':True,
            'self_availability_confirmed':True}


def environment_credentials():
    credentials=AppCredentials(os.environ.get('FEISHU_APP_ID',''),
        os.environ.get('FEISHU_APP_SECRET',''), os.environ.get('FEISHU_SELF_OPEN_ID',''))
    validate_credentials(credentials)
    try:
        record=decode_json(os.environ.get('FEISHU_APP_READINESS','').encode(),8192)
        if (not isinstance(record,dict) or type(record.get('version')) is not int or record['version'] != 1
            or record.get('app_id') != credentials.app_id or record.get('open_id') != credentials.open_id
            or record.get('identity_source') not in ('registration','operator_verified_reuse')
            or record.get('send_scope') != 'im:message:send_as_bot'
            or any(record.get(key) is not True for key in ('self_confirmed','published_bot_confirmed','self_availability_confirmed'))):
            raise ValueError()
    except Exception:
        raise MonitorError('App readiness/self-recipient confirmation missing or mismatched') from None
    return credentials


def request_json(url, payload, *, form=False, bearer=None, timeout=15):
    if url not in (REGISTRATION_URL,TOKEN_URL,MESSAGE_URL):
        raise MonitorError('Feishu endpoint not allowed')
    body=urllib.parse.urlencode(payload).encode() if form else json.dumps(payload,ensure_ascii=False).encode()
    headers={'Content-Type':'application/x-www-form-urlencoded' if form else 'application/json; charset=utf-8',
             'Accept':'application/json','User-Agent':'CodexResetMonitor/3.0'}
    if bearer is not None: headers['Authorization']='Bearer '+bearer
    try:
        req=urllib.request.Request(url,data=body,method='POST',headers=headers)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(req,timeout=timeout) as response:
            if response.status != 200 or response.headers.get_content_type() != 'application/json':
                raise ValueError()
            data=decode_json(response.read(65537),65536)
            if not isinstance(data,dict): raise ValueError()
            return data
    except Exception:
        # Upstream error bodies can contain app_secret, TAT or device_code.
        raise MonitorError('Feishu request failed or response invalid; sensitive details omitted') from None


@dataclasses.dataclass(repr=False)
class Registration:
    device_code: str
    user_code: str
    expires: int
    interval: int
    create_only: bool = False
    existing_app_id: str | None = None
    app_name: str | None = None

    @property
    def verification_url(self):
        # Same public addons encoding used by official node-sdk/registerApp:
        # JSON -> gzip -> URL-safe Base64 without padding. No SDK dependency.
        addons={'preset':False,'scopes':{'tenant':['im:message:send_as_bot']}}
        encoded=base64.urlsafe_b64encode(gzip.compress(json.dumps(addons,separators=(',',':')).encode(),mtime=0)).decode().rstrip('=')
        query={'user_code':self.user_code,'from':'sdk','source':'codex-reset-monitor','tp':'sdk','addons':encoded}
        if self.create_only:query['createOnly']='true'
        if self.existing_app_id:query['clientID']=self.existing_app_id
        # Official SDK appPreset.name is a separate query param, not an addon.
        # It pre-fills creation only; the user can edit it on the official page.
        if self.app_name is not None:query['name']=validate_app_name(self.app_name)
        return 'https://open.feishu.cn/page/cli?'+urllib.parse.urlencode(query)


def begin_registration(request=None, *, create_only=False, existing_app_id=None, app_name=None):
    if type(create_only) is not bool or (existing_app_id is not None and (not isinstance(existing_app_id,str) or not re.fullmatch(r'cli_[a-zA-Z0-9_-]{8,128}',existing_app_id))):
        raise MonitorError('Invalid registration mode or existing app ID')
    if create_only and existing_app_id is not None:raise MonitorError('New-app and reuse modes cannot be combined')
    if app_name is not None:
        if not create_only:raise MonitorError('Name preset is only supported when creating a new app')
        app_name=validate_app_name(app_name)
    request = request or request_json
    data=request(REGISTRATION_URL,{'action':'begin','archetype':'PersonalAgent',
        'auth_method':'client_secret','request_user_info':'open_id tenant_brand'},form=True,timeout=30)
    if 'error' in data: raise MonitorError('App registration rejected; no credentials stored')
    device,user=data.get('device_code'),data.get('user_code')
    if not isinstance(device,str) or not 1<=len(device)<=2048 or not isinstance(user,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}',user):
        raise MonitorError('Invalid registration session')
    expiry=data.get('expire_in',data.get('expires_in',600)); interval=data.get('interval',5)
    if type(expiry) is not int or expiry <= 0: expiry=600
    if type(interval) is not int or interval <= 0: interval=5
    return Registration(device,user,min(expiry,600),max(5,min(interval,60)),create_only,existing_app_id,app_name)


def poll_registration(session,request=None,clock=time.monotonic,sleep=time.sleep):
    request = request or request_json
    deadline=clock()+session.expires
    interval=session.interval
    while clock()<deadline:
        try:
            data=request(REGISTRATION_URL,{'action':'poll','device_code':session.device_code},
                         form=True,timeout=max(0.1,min(30,deadline-clock())))
        except MonitorError:
            interval=min(interval+1,60)
            data={'error':'authorization_pending'}
        if clock()>=deadline: break
        user=data.get('user_info',{})
        if not isinstance(user,dict): raise MonitorError('Invalid registration identity response')
        if user.get('tenant_brand') not in (None,'','feishu'):
            raise MonitorError('This guide supports Feishu only; Lark brand requires its official setup')
        error=data.get('error')
        if not error and data.get('client_id') and data.get('client_secret'):
            credentials=AppCredentials(data['client_id'],data['client_secret'],user.get('open_id',''))
            validate_credentials(credentials)
            if session.existing_app_id is not None and credentials.app_id!=session.existing_app_id:
                raise MonitorError('Authorized app does not match explicitly selected reuse app')
            return credentials
        if error == 'slow_down': interval=min(interval+5,60)
        elif error in ('access_denied','expired_token','invalid_grant'):
            raise MonitorError('App registration denied or expired; reuse an existing app rather than automatically recreate')
        elif error not in (None,'','authorization_pending'):
            raise MonitorError('App registration failed; sensitive server description omitted')
        sleep(min(interval,max(0,deadline-clock())))
    raise MonitorError('App registration timed out; no automatic new registration')


def tenant_token(credentials,request=None):
    request = request or request_json
    validate_credentials(credentials)
    data=request(TOKEN_URL,{'app_id':credentials.app_id,'app_secret':credentials.app_secret})
    token=data.get('tenant_access_token')
    if type(data.get('expire')) is not int or not 0 < data['expire'] <= 86400:
        raise MonitorError('App token expiry missing or invalid')
    if type(data.get('code')) is not int or data['code'] != 0 or not isinstance(token,str) or not 8<=len(token)<=4096 or any(ord(c)<32 for c in token):
        raise MonitorError('App authentication unconfirmed; check selected app credentials')
    return token


def app_sender(config,alert):
    try:
        from monitor import notification_backend
        if not config.sending_enabled or notification_backend() != 'app':
            raise MonitorError('App sender requires explicitly enabled app backend')
        credentials=environment_credentials()
        # Check payload size before authenticating; no unnecessary credential request.
        payload={'receive_id':credentials.open_id,'msg_type':'text',
                 'content':json.dumps({'text':alert.text},ensure_ascii=False),'uuid':digest(alert.key)}
        if len(json.dumps(payload,ensure_ascii=False).encode())>20000: raise ValueError()
        token=tenant_token(credentials)
        data=request_json(MESSAGE_URL,payload,bearer=token)
        message=data.get('data',{}).get('message_id') if isinstance(data.get('data'),dict) else None
        if type(data.get('code')) is not int or data['code'] != 0 or not isinstance(message,str) or not re.fullmatch(r'om_[a-zA-Z0-9_-]{1,128}',message):
            raise ValueError()
        return message
    except Exception:
        raise SendUnknown('App delivery unconfirmed; automatic retries blocked; sensitive details omitted') from None

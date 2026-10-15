"""Shared Release/Skill installation core. Default is offline plan only."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import subprocess
import sys
import tempfile
import time

from installer.github_api import GitHubAPI, SetupError, oauth_request
from monitor import decode_json
from feishu_app import APP_SECRET_NAMES

SOURCE_REPOSITORY='YuShenLiu06/codex-reset-monitor'
PROJECT_ID='codex-reset-monitor:24h-feishu:v1'
REQUIRED_PERMISSIONS={'contents':'read','metadata':'read','actions':'write','secrets':'write','actions_variables':'write'}
TRUSTED_FILES=('project.json','reset_events.py','monitor.py','github_state.py','feishu_app.py','bootstrap_state.py',
               '.github/workflows/monitor.yml','.github/workflows/prepare.yml','.github/workflows/ci.yml',
               'acceptance.py','.github/workflows/acceptance.yml','.github/workflows/release.yml')
STAGES=('target','github','compatibility','feishu','secrets','prepare','preview','complete','test','enabled')


def validate_target(target):
    if not isinstance(target,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}',target):
        raise SetupError('INVALID_TARGET')
    return target


def expected_manifest(root):
    root=Path(root)
    names=set(TRUSTED_FILES)
    names.update(path.relative_to(root).as_posix() for path in (root/'.github/workflows').glob('*.y*ml'))
    for path in root.rglob('*.py'):
        relative=path.relative_to(root)
        if not set(relative.parts)&{'runtime','.git','__pycache__','.codex-reset-setup'}:
            names.add(relative.as_posix())
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(names)}


class Progress:
    """Only a strictly bounded metadata schema is allowed on disk."""
    def __init__(self,path,target):
        self.path=Path(path);self.target=validate_target(target)
        self.data={'version':1,'target':target,'completed':[],'runs':{},'app_created':False,'pending':None,'dispatch_ids':{},'github_user_id':None,'run_history':{},'test_request_id':None,'received_confirmed':False}
        if self.path.exists():
            try:
                value=decode_json(self.path.read_bytes(),32768)
                if not isinstance(value,dict) or set(value)!=set(self.data) or type(value['version']) is not int or value['version']!=1 or value['target']!=target:
                    raise ValueError()
                if not isinstance(value['completed'],list) or any(s not in STAGES for s in value['completed']):raise ValueError()
                if type(value['app_created']) is not bool or value['pending'] not in (None,'secrets','prepare','preview','test','enable'):raise ValueError()
                if not isinstance(value['runs'],dict) or any(k not in ('prepare','preview','test') or type(v) is not int or v<=0 for k,v in value['runs'].items()):raise ValueError()
                if not isinstance(value['dispatch_ids'],dict) or any(k not in ('prepare','preview','test') or not isinstance(v,str) or not re.fullmatch(r'[0-9a-f]{32}',v) for k,v in value['dispatch_ids'].items()):raise ValueError()
                if value['github_user_id'] is not None and (type(value['github_user_id']) is not int or value['github_user_id']<=0):raise ValueError()
                if not isinstance(value['run_history'],dict) or any(k not in ('prepare','preview','test') or not isinstance(v,list) or len(v)>20 or any(type(n) is not int or n<=0 for n in v) for k,v in value['run_history'].items()):raise ValueError()
                if type(value['received_confirmed']) is not bool or (value['test_request_id'] is not None and (not isinstance(value['test_request_id'],str) or not re.fullmatch(r'[0-9a-f]{32}',value['test_request_id']))):raise ValueError()
                self.data=value
            except Exception:raise SetupError('INVALID_RESUME_STATE','do not silently discard installation history') from None

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,name=tempfile.mkstemp(prefix='.progress-',dir=self.path.parent)
        try:
            with os.fdopen(fd,'w') as handle:
                json.dump(self.data,handle);handle.flush();os.fsync(handle.fileno())
            os.replace(name,self.path)
        finally:
            if os.path.exists(name):os.unlink(name)

    def mark(self,stage):
        if stage not in STAGES:raise SetupError('INVALID_STAGE')
        if stage not in self.data['completed']:self.data['completed'].append(stage)
        self.save()


def device_authorize(client_id,emit,*,request=oauth_request,clock=time.monotonic,sleep=time.sleep):
    if not isinstance(client_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{8,128}',client_id):
        raise SetupError('CLIENT_ID_REQUIRED','register a real installer GitHub App with device flow enabled')
    emit('github','正在向 GitHub 申请连接码…')
    begin=request('https://github.com/login/device/code',{'client_id':client_id,'scope':'offline_access'})
    if begin.get('error') is not None:
        error=begin.get('error')
        code={'device_flow_disabled':'DEVICE_FLOW_DISABLED','incorrect_client_credentials':'CLIENT_ID_REJECTED',
              'invalid_client':'CLIENT_ID_REJECTED'}.get(error,'DEVICE_SESSION_REJECTED') if isinstance(error,str) else 'INVALID_DEVICE_SESSION'
        raise SetupError(code,'verify the existing public App configuration; do not create another App',
            diagnostics={'phase':'github_device_code','category':'config','http_status':200})
    device,user=begin.get('device_code'),begin.get('user_code')
    interval,expiry=begin.get('interval'),begin.get('expires_in')
    if (not isinstance(device,str) or not 1<=len(device)<=128 or not isinstance(user,str)
        or not re.fullmatch(r'[A-Z0-9]{4}-[A-Z0-9]{4}',user) or type(interval) is not int
        or not 1<=interval<=60 or type(expiry) is not int or not 1<=expiry<=900):
        raise SetupError('INVALID_DEVICE_SESSION')
    # Fixed official page; do not honor an upstream redirect URL.
    emit('github','在 https://github.com/login/device 输入 '+user+'；仅本人使用，勿共享。')
    deadline=clock()+expiry
    while clock()<deadline:
        wait_until=min(clock()+interval,deadline)
        while clock()<wait_until:
            sleep(min(30,wait_until-clock()))
        if clock()>=deadline:break
        data=request('https://github.com/login/oauth/access_token',{
            'client_id':client_id,'device_code':device,'grant_type':'urn:ietf:params:oauth:grant-type:device_code'},
            timeout=max(0.1,min(15,deadline-clock())))
        if clock()>=deadline:raise SetupError('DEVICE_TIMED_OUT')
        error=data.get('error')
        if error=='authorization_pending':continue
        if error=='slow_down':
            interval+=5
            continue
        if error:raise SetupError('DEVICE_DENIED_OR_EXPIRED','no automatic new authorization')
        token=data.get('access_token');expiry=data.get('expires_in')
        if (not isinstance(token,str) or not re.fullmatch(r'ghu_[A-Za-z0-9]{8,256}',token)
            or data.get('token_type')!='bearer' or type(expiry) is not int or not 0<expiry<=28800):
            raise SetupError('INVALID_USER_TOKEN','expiring GitHub App user token required')
        # Refresh token is neither returned nor persisted. No private key/secret.
        return token
    raise SetupError('DEVICE_TIMED_OUT')


# Values here are only official permission names and access levels. Never retain
# the raw installation response in an error or diagnostic.
DIAGNOSTIC_PERMISSION_NAMES=set(REQUIRED_PERMISSIONS)|{
    'administration','workflows','checks','issues','pull_requests','deployments',
    'statuses','security_events','members','organization_administration',
    'email_addresses','single_file','pages','packages','discussions'}


def verify_installation_permissions(actual):
    differences=[]
    if not isinstance(actual,dict):
        differences=[{'name':'permissions','actual':'invalid','expected':'read/write map'}]
    else:
        for name,expected in REQUIRED_PERMISSIONS.items():
            value=actual.get(name)
            if value!=expected:
                differences.append({'name':name,'actual':value if value in ('read','write','none') else ('missing' if name not in actual else 'invalid'),'expected':expected})
        for name,value in actual.items():
            if name not in REQUIRED_PERMISSIONS:
                differences.append({'name':name if isinstance(name,str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}',name) else 'unknown_permission',
                    'actual':value if value in ('read','write','none') else 'invalid','expected':'absent'})
    if differences:
        if len(differences)>32:
            differences=differences[:32]+[{'name':'additional_permissions','actual':'invalid','expected':'absent'}]
        exc=SetupError('INSTALLATION_PERMISSIONS_MISMATCH',
            '; '.join(v['name']+'='+v['actual']+' (expected '+v['expected']+')' for v in differences))
        exc.permission_diff=differences
        raise exc


def verify_installation(api,target,slug):
    user=api.request('GET','/user')
    if not isinstance(user.get('login'),str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}',user['login']):
        raise SetupError('INVALID_GITHUB_IDENTITY')
    if type(user.get('id')) is not int or user['id']<=0:raise SetupError('INVALID_GITHUB_IDENTITY')
    installations=api.request('GET','/user/installations?per_page=100')
    if installations.get('total_count',0)>100:raise SetupError('INSTALLATION_LIST_TOO_LARGE')
    matches=[]
    for installation in installations.get('installations',[]):
        if installation.get('app_slug')!=slug:continue
        account=installation.get('account')
        if not isinstance(account,dict) or not isinstance(account.get('login'),str):raise SetupError('INVALID_INSTALLATION_ACCOUNT')
        if account['login'].casefold()!=target.split('/',1)[0].casefold():continue
        if installation.get('repository_selection')!='selected':raise SetupError('BROAD_INSTALLATION_REJECTED','select only the target repository')
        verify_installation_permissions(installation.get('permissions'))
        repositories=api.request('GET',f"/user/installations/{installation['id']}/repositories?per_page=100")
        names=[r.get('full_name') for r in repositories.get('repositories',[])]
        if names!=[target] or repositories.get('total_count')!=1:raise SetupError('OTHER_REPOSITORIES_ACCESSIBLE','select only the target repository for this installer')
        matches.append(installation)
    if len(matches)!=1:raise SetupError('TARGET_INSTALLATION_REQUIRED','install the selected GitHub App on only this repository')
    return {'login':user['login'],'id':user['id']}


def verify_repository(api,target,manifest,*,allow_enabled=False):
    metadata=api.request('GET','/repos/'+target)
    if metadata.get('full_name')!=target or metadata.get('default_branch')!='main' or metadata.get('archived') or metadata.get('disabled'):
        raise SetupError('INCOMPATIBLE_REPOSITORY')
    if metadata.get('permissions',{}).get('admin') is not True:raise SetupError('TARGET_ADMIN_REQUIRED','independent GitHub repository permission check failed')
    if target!=SOURCE_REPOSITORY and (not metadata.get('fork') or metadata.get('source',{}).get('full_name')!=SOURCE_REPOSITORY):
        raise SetupError('PROJECT_ORIGIN_MISMATCH','same name alone is insufficient; repository will not be overwritten')
    commit=api.request('GET','/repos/'+target+'/git/ref/heads/main')['object']['sha']
    if not isinstance(commit,str) or not re.fullmatch(r'[0-9a-f]{40}',commit):raise SetupError('INVALID_TARGET_REVISION')
    if not set(TRUSTED_FILES)<=set(manifest):raise SetupError('INVALID_PACKAGE_MANIFEST')
    tree=api.request('GET','/repos/'+target+'/git/trees/'+commit+'?recursive=1')
    if tree.get('truncated'):raise SetupError('INCOMPLETE_REPOSITORY_TREE')
    executable={entry['path'] for entry in tree.get('tree',[]) if entry['path'].endswith(('.py','.pyz','.so'))
                or entry['path'].startswith('.github/workflows/')}
    if not executable<=set(manifest):raise SetupError('UNREVIEWED_EXECUTABLE_FILES')
    for entry in tree.get('tree',[]):
        if entry['path'] in manifest and entry.get('mode') not in ('100644','100755'):
            raise SetupError('UNSAFE_REPOSITORY_FILE_TYPE')
    for name,expected in manifest.items():
        data=api.request('GET','/repos/'+target+'/contents/'+name+'?ref='+commit)
        try:
            content=base64.b64decode(data['content'].replace('\n',''),validate=True)
            if data.get('encoding')!='base64' or hashlib.sha256(content).hexdigest()!=expected:raise ValueError()
            if name=='project.json' and decode_json(content).get('project_id')!=PROJECT_ID:raise ValueError()
        except Exception:raise SetupError('PROJECT_FILES_MISMATCH','use a reviewed matching release; no files overwritten') from None
    for workflow in ('ci.yml','monitor.yml','prepare.yml','acceptance.yml'):
        api.request('GET','/repos/'+target+'/actions/workflows/'+workflow)
    api.request('GET','/repos/'+target+'/actions/secrets/public-key')
    try:gate=api.request('GET','/repos/'+target+'/actions/variables/MONITOR_ENABLED')
    except SetupError as exc:
        if exc.code!='NOT_FOUND':raise
        gate=None
    if gate is not None:
        if not isinstance(gate,dict) or gate.get('name')!='MONITOR_ENABLED' or not isinstance(gate.get('value'),str):
            raise SetupError('INVALID_VARIABLE_RESPONSE')
        if not allow_enabled and gate['value']=='true':
            raise SetupError('MONITOR_ALREADY_ENABLED','disable and stop running notifications before replacing credentials')
    return commit


@contextmanager
def external_process_dll_path():
    # PyInstaller adds its private DLL directory on Windows. Do not inject that
    # directory into the separately bundled official GitHub CLI process.
    if sys.platform!='win32' or not getattr(sys,'frozen',False):
        yield;return
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    buffer=ctypes.create_unicode_buffer(32768)
    if kernel.GetDllDirectoryW(len(buffer),buffer)>=len(buffer):raise SetupError('DLL_DIRECTORY_INVALID')
    previous=buffer.value
    if not kernel.SetDllDirectoryW(None):raise SetupError('DLL_DIRECTORY_INVALID')
    try:yield
    finally:kernel.SetDllDirectoryW(previous or None)


def upload_secrets(token,target,values,runner=None):
    runner = runner or subprocess.run
    if set(values)!=set(APP_SECRET_NAMES):raise SetupError('INVALID_SECRET_PLAN')
    executable=shutil.which('gh')
    if not executable:raise SetupError('GH_CLI_REQUIRED','install GitHub official CLI before continuing')
    # An empty temporary HOME/config isolates existing login files. Only CLI
    # configuration may touch this directory; secrets and token are stdin/env.
    with tempfile.TemporaryDirectory(prefix='codex-reset-gh-') as directory:
        environment={k:os.environ[k] for k in ('PATH','LANG','LC_ALL','SYSTEMROOT','SystemRoot','WINDIR','TEMP','TMP') if k in os.environ}
        environment.update(GH_TOKEN=token,GH_HOST='github.com',GH_PROMPT_DISABLED='1',
                           GH_CONFIG_DIR=directory,HOME=directory,USERPROFILE=directory,
                           APPDATA=directory,LOCALAPPDATA=directory,GH_NO_UPDATE_NOTIFIER='1',
                           GH_NO_EXTENSION_UPDATE_NOTIFIER='1',GH_PAGER='cat')
        options={'creationflags':0x08000000} if sys.platform=='win32' else {}
        for name in APP_SECRET_NAMES:
            try:
                with external_process_dll_path():
                    result=runner([executable,'secret','set',name,'--repo',target,'--app','actions'],input=values[name].encode(),
                        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,check=False,env=environment,**options)
                if result.returncode:raise ValueError()
            except Exception:raise SetupError('SECRET_UPLOAD_UNCONFIRMED','partial upload possible; inspect names and reuse same app, gate stays disabled') from None



class Installer:
    def __init__(self,api,progress,manifest,emit,clock=time.monotonic,sleep=time.sleep):
        self.api=api;self.progress=progress;self.manifest=manifest;self.emit=emit
        self.clock=clock;self.sleep=sleep;self.commit=None
        self.base='/repos/'+progress.target

    def check(self,slug):
        identity=verify_installation(self.api,self.progress.target,slug)
        previous=self.progress.data['github_user_id']
        if previous is not None and previous!=identity['id']:
            raise SetupError('RESUME_IDENTITY_MISMATCH','use the original GitHub account or a separate setup progress file')
        self.github_login=identity['login']
        self.progress.data['github_user_id']=identity['id'];self.progress.save()
        self.emit('github','当前授权 GitHub 用户：'+identity['login']+'；目标：'+self.progress.target)
        self.commit=verify_repository(self.api,self.progress.target,self.manifest,allow_enabled=self.progress.data['pending']=='enable' and self.progress.data['received_confirmed'])
        self.progress.mark('compatibility');self.emit('compatibility','目标身份、代码及权限已核验；不会覆盖源码。')

    def set_variable(self,name,value):
        try:self.api.request('GET',self.base+'/actions/variables/'+name)
        except SetupError as exc:
            if exc.code!='NOT_FOUND':raise
            self.api.request('POST',self.base+'/actions/variables',{'name':name,'value':value})
        else:self.api.request('PATCH',self.base+'/actions/variables/'+name,{'name':name,'value':value})

    def configure(self,values,upload=None):
        upload = upload or upload_secrets
        if not self.commit:raise SetupError('CHECK_REQUIRED')
        if self.progress.data['pending']=='secrets':
            raise SetupError('PREVIOUS_SECRET_UPLOAD_UNKNOWN','explicitly inspect GitHub then resolve; no blind retry')
        self.emit('secrets','明确保持发送关闭；开始本地加密上传，值不会显示。')
        self.set_variable('MONITOR_ENABLED','false')
        old_run=self.progress.data['runs'].get('test')
        if old_run is not None:
            history=self.progress.data['run_history'].setdefault('test',[])
            if len(history)>=20:raise SetupError('TEST_HISTORY_FULL','manual review required')
            history.append(old_run)
        self.progress.data['runs'].pop('test',None)
        self.progress.data['dispatch_ids'].pop('test',None)
        self.progress.data['test_request_id']=None
        self.progress.data['received_confirmed']=False
        self.progress.data['completed']=[s for s in self.progress.data['completed'] if s not in ('test','enabled')]
        self.progress.data['pending']='secrets';self.progress.save()
        upload(self.api.token,self.progress.target,values)
        self.progress.data['pending']=None;self.progress.mark('secrets')

    def workflow(self,name,stage,inputs):
        if not self.commit:raise SetupError('CHECK_REQUIRED')
        self.emit(stage,'核验固定 workflow 并等待结果；'+('执行用户明确授权的安装测试。' if stage=='test' else '不发送通知。'))
        path=self.base+'/actions/workflows/'+name
        if stage in self.progress.data['runs']:
            return self.wait_run(self.progress.data['runs'][stage],stage)
        if self.progress.data['pending']==stage:
            raise SetupError('PREVIOUS_DISPATCH_UNKNOWN','inspect Actions and record its run ID; do not dispatch again blindly')
        self.api.request('PUT',path+'/enable')
        before=self.api.request('GET',path+'/runs?event=workflow_dispatch&per_page=100')
        if before.get('total_count',0)>100:raise SetupError('RUN_HISTORY_TOO_LARGE','manual run selection needed')
        known={r['id'] for r in before.get('workflow_runs',[])}
        self.progress.data['dispatch_ids'][stage]=secrets.token_hex(16)
        self.progress.data['pending']=stage;self.progress.save()
        # Recheck branch head before dispatch, which must run reviewed source.
        head=self.api.request('GET',self.base+'/git/ref/heads/main')['object']['sha']
        if head!=self.commit:raise SetupError('TARGET_CHANGED','rerun compatibility check')
        inputs=dict(inputs,installation_id=self.progress.data['dispatch_ids'][stage])
        self.api.request('POST',path+'/dispatches',{'ref':'main','inputs':inputs})
        deadline=self.clock()+120
        while self.clock()<deadline:
            runs=self.api.request('GET',path+'/runs?event=workflow_dispatch&per_page=100').get('workflow_runs',[])
            candidates=[r for r in runs if r['id'] not in known and r.get('head_sha')==self.commit and r.get('display_title')=='install-'+self.progress.data['dispatch_ids'][stage]]
            if len(candidates)>1:raise SetupError('AMBIGUOUS_DISPATCH','select the correct run on official Actions page')
            if candidates:
                run=candidates[0]['id'];self.progress.data['runs'][stage]=run;self.progress.data['pending']=None;self.progress.save()
                return self.wait_run(run,stage)
            self.sleep(5)
        raise SetupError('DISPATCH_RESULT_UNKNOWN','do not redispatch automatically')

    def wait_run(self,run,stage):
        self.emit(stage,'等待 https://github.com/'+self.progress.target+'/actions/runs/'+str(run))
        deadline=self.clock()+600
        while self.clock()<deadline:
            result=self.api.request('GET',self.base+'/actions/runs/'+str(run))
            if result.get('head_sha')!=self.commit or result.get('event')!='workflow_dispatch' or result.get('display_title')!='install-'+self.progress.data['dispatch_ids'].get(stage,''):raise SetupError('RUN_IDENTITY_MISMATCH')
            if result.get('status')=='completed':
                if result.get('conclusion')!='success':raise SetupError('WORKFLOW_FAILED','inspect official Actions logs; no credential logs requested')
                jobs=self.api.request('GET',self.base+'/actions/runs/'+str(run)+'/jobs?per_page=100')
                expected={'prepare':'prepare','preview':'preview','test':'acceptance'}[stage]
                matches=[job for job in jobs.get('jobs',[]) if job.get('name')==expected]
                if len(matches)!=1 or matches[0].get('conclusion')!='success':raise SetupError('REQUIRED_JOB_NOT_SUCCESSFUL')
                self.progress.mark(stage);return run
            self.sleep(10)
        raise SetupError('WORKFLOW_TIMEOUT','run ID retained for resume')

    def retry_failed_stage(self,stage):
        """Only explicit user-confirmed revalidation; old run IDs remain auditable."""
        if stage not in ('prepare','preview') or self.progress.data['pending'] is not None:
            raise SetupError('RETRY_REQUIRES_RESOLVED_OPERATION')
        run=self.progress.data['runs'].get(stage)
        if run is None:raise SetupError('FAILED_RUN_REQUIRED')
        result=self.api.request('GET',self.base+'/actions/runs/'+str(run))
        if (result.get('head_sha')!=self.commit or result.get('event')!='workflow_dispatch'
            or result.get('display_title')!='install-'+self.progress.data['dispatch_ids'].get(stage,'')
            or result.get('status')!='completed' or result.get('conclusion') in (None,'success')):
            raise SetupError('FAILED_RUN_IDENTITY_REQUIRED')
        history=self.progress.data['run_history'].setdefault(stage,[])
        if len(history)>=20:raise SetupError('RETRY_HISTORY_LIMIT')
        history.append(run)
        self.progress.data['runs'].pop(stage)
        self.progress.data['dispatch_ids'].pop(stage,None)
        self.progress.data['completed']=[s for s in self.progress.data['completed'] if s not in (stage,'complete')]
        self.progress.save()

    def test_notification(self, *, user_authorized=False):
        if user_authorized is not True:raise SetupError('TEST_SEND_CONFIRMATION_REQUIRED')
        if not {'prepare','preview','complete'}<=set(self.progress.data['completed']):raise SetupError('VALIDATION_REQUIRED_BEFORE_TEST')
        if self.progress.data['test_request_id'] is None:
            self.progress.data['test_request_id']=secrets.token_hex(16);self.progress.save()
        return self.workflow('acceptance.yml','test',{'confirm_test_send':'true',
            'test_request_id':self.progress.data['test_request_id']})

    def enable_after_received(self, *, received_confirmed=False, enable_authorized=False):
        if received_confirmed is not True or enable_authorized is not True:
            raise SetupError('RECEIPT_AND_ENABLE_CONFIRMATIONS_REQUIRED')
        if not {'prepare','preview','test','complete'}<=set(self.progress.data['completed']) or self.progress.data['pending'] not in (None,'enable'):
            raise SetupError('SUCCESSFUL_ACCEPTANCE_REQUIRED')
        head=self.api.request('GET',self.base+'/git/ref/heads/main')['object']['sha']
        if head!=self.commit:raise SetupError('TARGET_CHANGED')
        state=self.api.request('GET',self.base+'/contents/state.json?ref=monitor-state')
        from monitor import validate_state, unresolved_delivery
        try:state=validate_state(decode_json(base64.b64decode(state['content']),1048576))
        except Exception:raise SetupError('STATE_VALIDATION_FAILED') from None
        record=state.get('acceptance_tests',{}).get(self.progress.data['test_request_id'],{})
        if record.get('status')!='sent' or unresolved_delivery(state):raise SetupError('TEST_DELIVERY_NOT_CONFIRMED')
        self.progress.data['received_confirmed']=True
        self.progress.data['pending']='enable';self.progress.save()
        # A lost variable-write acknowledgement is resolved by readback, never
        # by another test send. This enabling step is explicitly authorized.
        self.set_variable('MONITOR_ENABLED','true')
        actual=self.api.request('GET',self.base+'/actions/variables/MONITOR_ENABLED')
        if actual.get('value')!='true':raise SetupError('ENABLE_UNCONFIRMED')
        self.progress.data['pending']=None;self.progress.mark('enabled')
        self.emit('enabled','已核验开启门控；实际 schedule 时效仍取决于 GitHub。')

    def validate(self):
        self.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        self.set_variable('MONITOR_INITIALIZED','true')
        state=self.api.request('GET',self.base+'/contents/state.json?ref=monitor-state')
        from monitor import validate_state
        try:validate_state(decode_json(base64.b64decode(state['content']),1048576))
        except Exception:raise SetupError('STATE_VALIDATION_FAILED') from None
        self.workflow('monitor.yml','preview',{'dry_run':'true'})
        self.progress.mark('complete')
        gate=self.api.request('GET',self.base+'/actions/variables/MONITOR_ENABLED').get('value')
        self.emit('complete','准备及安全预览通过；此阶段不发送消息，当前 MONITOR_ENABLED='+str(gate)+'。')

"""Explicit CI-only synthetic services; never authorize, browse, upload or send."""
import base64
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import patch


def run(mode,gui,args):
    if mode not in ('full','cancel','resume'):raise ValueError('Unknown offline fixture')
    import installer.cli as cli
    import installer.core as core
    from installer.github_api import SetupError
    from feishu_app import AppCredentials
    from monitor import empty_state
    from reset_events import identity
    assert identity("bundled-check").startswith("reset:")
    target='ExampleUser/codex-reset-monitor';sha='a'*40
    contents={name:b'offline reviewed fixture\n' for name in core.TRUSTED_FILES}
    contents['project.json']=json.dumps({'project_id':core.PROJECT_ID}).encode()
    manifest={k:hashlib.sha256(v).hexdigest() for k,v in contents.items()}
    class API:
        token='ghu_'+'x'*16
        def __init__(self):self.variables={};self.runs={};self.records={};self.ids=100
        def close(self):self.token=None
        def request(self,method,path,payload=None):
            base='/repos/'+target
            if path=='/user':return {'login':'ExampleUser','id':123}
            if path.startswith('/user/installations?'):
                return {'total_count':1,'installations':[{'id':1,'app_slug':'codex-reset-installer','account':{'login':'ExampleUser'},'repository_selection':'selected','permissions':dict(core.REQUIRED_PERMISSIONS)}]}
            if path.startswith('/user/installations/1/repositories?'):return {'total_count':1,'repositories':[{'full_name':target}]}
            if path==base:return {'full_name':target,'default_branch':'main','fork':True,'source':{'full_name':core.SOURCE_REPOSITORY},'permissions':{'admin':True}}
            if '/git/ref/heads/' in path:return {'object':{'sha':sha}}
            if '/git/trees/' in path:return {'truncated':False,'tree':[{'path':n,'mode':'100644'} for n in manifest]}
            if '/contents/state.json?' in path:
                return {'content':base64.b64encode(json.dumps(dict(empty_state(),acceptance_tests=self.records)).encode()).decode()}
            if '/contents/' in path:
                name=path.split('/contents/')[1].split('?')[0]
                return {'encoding':'base64','content':base64.b64encode(contents[name]).decode()}
            if path.endswith('/actions/secrets/public-key'):return {'key':'offline','key_id':'offline'}
            if '/actions/variables/' in path:
                name=path.rsplit('/',1)[1]
                if method=='GET':
                    if name not in self.variables:raise SetupError('NOT_FOUND')
                    return {'name':name,'value':self.variables[name]}
                self.variables[name]=payload['value'];return None
            if path.endswith('/actions/variables'):
                self.variables[payload['name']]=payload['value'];return None
            if '/actions/runs/' in path:
                number=int(path.split('/actions/runs/')[1].split('/')[0])
                if path.endswith('/jobs?per_page=100'):
                    return {'jobs':[{'name':{'prepare.yml':'prepare','monitor.yml':'preview','acceptance.yml':'acceptance'}[self.runs[number]['workflow']],'conclusion':'success'}]}
                return self.runs[number]
            if '/actions/workflows/' in path:
                tail=path.split('/actions/workflows/')[1];workflow=tail.split('/')[0]
                if tail.endswith('/enable'):return None
                if '/runs?' in tail:
                    runs=[r for r in self.runs.values() if r['workflow']==workflow]
                    return {'total_count':len(runs),'workflow_runs':runs}
                if tail.endswith('/dispatches'):
                    self.ids+=1
                    self.runs[self.ids]={'id':self.ids,'head_sha':sha,'event':'workflow_dispatch','workflow':workflow,'display_title':'install-'+payload['inputs']['installation_id'],'status':'completed','conclusion':'success'}
                    if workflow=='acceptance.yml':self.records[payload['inputs']['test_request_id']]={'status':'sent','attempted_at':'2026-10-15T12:00:00Z','receipt':'om_offline'}
                    return None
                return {'state':'active'}
            raise AssertionError('Unexpected offline API route')
    api=API()
    def authorize(client,emit):
        emit('github','在 https://github.com/login/device 输入 ABCD-1234；仅本人使用。')
        return api.token
    def upload(*unused):
        if mode=='cancel':
            gui.send({'type':'notice','stage':'offline-cancel-ready','title':'离线暂停测试','text':'等待取消；没有访问真实账号。','links':[]})
            while True:time.sleep(0.05)
    credentials=AppCredentials('cli_'+'x'*16,'synthetic-secret','ou_'+'y'*24)
    # Every service boundary is replaced; the CLI, progress and installer core
    # still execute through real stdin/stdout and real UI response handlers.
    with patch.object(cli,'GitHubAPI',return_value=api),patch.object(cli,'device_authorize',side_effect=authorize),patch.object(cli,'manifest',return_value=manifest),patch.object(cli,'begin_registration',return_value=SimpleNamespace(verification_url='https://open.feishu.cn/offline-fixture')),patch.object(cli,'poll_registration',return_value=credentials),patch.object(cli,'tenant_token',return_value='synthetic-token'),patch.object(core,'upload_secrets',side_effect=upload),patch('webbrowser.open',side_effect=AssertionError('Offline browser forbidden')):
        return cli.main(args,ui=gui)

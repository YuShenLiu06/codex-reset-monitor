"""Simulated end-to-end installation. No live account/app/Secret operations."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import installer.core as c
import installer.cli as cli
from installer.github_api import SetupError, GitHubAPI
import bootstrap_state as b
from monitor import empty_state
from test_feishu_app import FakeClock

ROOT=Path(__file__).resolve().parents[1]
TARGET='ExampleUser/codex-reset-monitor'
SHA='a'*40


class FakeAPI:
    def __init__(self):
        self.token='ghu_'+'x'*16;self.calls=[];self.variables={};self.runs={};self.ids=100
        self.permissions={'actions':'write','contents':'read','metadata':'read','secrets':'write','actions_variables':'write'};self.selection='selected';self.names=[TARGET]
        self.corrupt=None;self.origin=c.SOURCE_REPOSITORY;self.extra=[];self.run_fail=False
        self.dispatch_lost=False;self.run_pending=False;self.has_state=False;self.test_records={}
        self.manifest=c.expected_manifest(ROOT)

    def close(self):self.token=None

    def request(self,method,path,payload=None):
        self.calls.append((method,path,payload))
        if path=='/user':return {'login':'ExampleUser','id':123}
        if path.startswith('/user/installations?'):
            return {'total_count':1,'installations':[{'id':1,'app_slug':'unit-installer','account':{'login':'ExampleUser'},'repository_selection':self.selection,'permissions':self.permissions}]}
        if path.startswith('/user/installations/1/repositories?'):
            return {'total_count':len(self.names),'repositories':[{'full_name':name} for name in self.names]}
        base='/repos/'+TARGET
        if path==base:return {'full_name':TARGET,'default_branch':'main','fork':True,'source':{'full_name':self.origin},'permissions':{'admin':True}}
        if path==base+'/git/ref/heads/main':return {'object':{'sha':SHA}}
        if '/git/trees/' in path:
            return {'truncated':False,'tree':[{'path':name,'mode':'100644'} for name in self.manifest]+self.extra}
        if '/contents/state.json?' in path:
            if not self.has_state:raise SetupError('NOT_FOUND')
            return {'content':base64.b64encode(json.dumps(dict(empty_state(),acceptance_tests=self.test_records)).encode()).decode()}
        if '/contents/' in path:
            name=path.split('/contents/')[1].split('?')[0]
            raw=(ROOT/name).read_bytes() if name!=self.corrupt else b'changed'
            return {'encoding':'base64','content':base64.b64encode(raw).decode()}
        if path.endswith('/actions/secrets/public-key'):return {'key':'public','key_id':'public-id'}
        if path.endswith('/actions/variables?per_page=100'):
            return {'total_count':len(self.variables),'variables':[{'name':k,'value':v} for k,v in self.variables.items()]}
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
                return {'jobs':[{'name':{'prepare.yml':'prepare','monitor.yml':'preview','acceptance.yml':'acceptance'}[self.runs[number]['workflow']],
                                 'conclusion':'failure' if self.run_fail else 'success'}]}
            return self.runs[number]
        if '/actions/workflows/' in path:
            tail=path.split('/actions/workflows/')[1];workflow=tail.split('/')[0]
            if tail.endswith('/enable'):return None
            if '/runs?' in tail:
                runs=[value for value in self.runs.values() if value['workflow']==workflow]
                return {'total_count':len(runs),'workflow_runs':runs}
            if tail.endswith('/dispatches'):
                self.ids+=1
                self.runs[self.ids]={'id':self.ids,'head_sha':SHA,'event':'workflow_dispatch','workflow':workflow,
                    'display_title':'install-'+payload['inputs']['installation_id'],
                    'status':'in_progress' if self.run_pending else 'completed',
                    'conclusion':'failure' if self.run_fail else 'success'}
                if workflow=='prepare.yml':self.has_state=True
                if workflow=='acceptance.yml':self.test_records[payload['inputs']['test_request_id']]={'status':'sent','attempted_at':'2026-10-14T12:00:00Z','receipt':'om_test'}
                if self.dispatch_lost:raise SetupError('GITHUB_RESPONSE_UNKNOWN')
                return None
            return {'state':'active'}
        raise AssertionError('Unexpected simulated API path '+path)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'progress.json'
        self.api=FakeAPI();self.progress=c.Progress(self.path,TARGET);self.clock=FakeClock()
        self.emit=Mock();self.core=c.Installer(self.api,self.progress,self.api.manifest,self.emit,self.clock.clock,self.clock.sleep)
        self.values={name:'synthetic-'+name.lower() for name in c.APP_SECRET_NAMES}

    def tearDown(self):self.temp.cleanup()

    def test_new_secret_configuration_invalidates_prior_acceptance(self):
        self.core.check('unit-installer');self.core.configure(self.values,Mock());self.core.validate()
        self.core.test_notification(user_authorized=True)
        old_run=self.progress.data['runs']['test']
        self.core.configure(self.values,Mock())
        self.assertIsNone(self.progress.data['test_request_id'])
        self.assertNotIn('test',self.progress.data['runs'])
        self.assertIn(old_run,self.progress.data['run_history']['test'])
        with self.assertRaises(SetupError):
            self.core.enable_after_received(received_confirmed=True,enable_authorized=True)

    def test_simulated_e2e_configure_prepare_preview_without_enable_or_fork(self):
        self.core.check('unit-installer')
        upload=Mock();self.core.configure(self.values,upload);self.core.validate()
        self.assertIn('complete',self.progress.data['completed'])
        self.assertEqual(self.api.variables['MONITOR_ENABLED'],'false')
        self.assertEqual(self.api.variables['MONITOR_INITIALIZED'],'true')
        self.assertEqual(upload.call_count,1)
        self.assertEqual(len(self.api.runs),2)
        writes=[(method,path,payload) for method,path,payload in self.api.calls if method!='GET']
        self.assertFalse(any('/forks' in path or '/contents/' in path or '/git/' in path for _,path,_ in writes))
        self.assertFalse(any(payload and payload.get('value')=='true' and payload.get('name')=='MONITOR_ENABLED' for _,_,payload in writes))
        self.assertTrue(all(run['workflow'] in ('prepare.yml','monitor.yml') for run in self.api.runs.values()))
        state=self.path.read_text()
        for value in self.values.values():self.assertNotIn(value,state)
        self.assertNotIn(self.api.token,state)

    def test_installation_rejects_all_repositories_extra_permissions_or_other_targets(self):
        for selection,permissions,names in [('all',c.REQUIRED_PERMISSIONS,[TARGET]),
            ('selected',dict(c.REQUIRED_PERMISSIONS,administration='write'),[TARGET]),
            ('selected',dict(c.REQUIRED_PERMISSIONS,contents='write'),[TARGET]),
            ('selected',c.REQUIRED_PERMISSIONS,[TARGET,'ExampleUser/unrelated'])]:
            self.api.selection=selection;self.api.permissions=permissions;self.api.names=names
            with self.subTest(selection=selection,permissions=permissions,names=names),self.assertRaises(SetupError):self.core.check('unit-installer')
        self.assertFalse(any(method!='GET' for method,_,_ in self.api.calls))

    def test_same_named_unrelated_origin_or_modified_code_rejected(self):
        self.api.origin='someone/unrelated'
        with self.assertRaises(SetupError):self.core.check('unit-installer')
        self.api.origin=c.SOURCE_REPOSITORY;self.api.corrupt='monitor.py'
        with self.assertRaises(SetupError):self.core.check('unit-installer')
        self.assertFalse(any(method!='GET' for method,_,_ in self.api.calls))

    def test_unknown_executable_and_symlink_rejected(self):
        for entry in ({'path':'json.py','mode':'100644'},{'path':'monitor.py','mode':'120000'},
                      {'path':'.github/workflows/steal.yml','mode':'100644'}):
            self.api.extra=[entry]
            with self.subTest(entry=entry),self.assertRaises(SetupError):self.core.check('unit-installer')

    def test_enabled_monitor_blocks_configuration(self):
        self.api.variables['MONITOR_ENABLED']='true'
        with self.assertRaises(SetupError):self.core.check('unit-installer')
        self.assertFalse(any(method!='GET' for method,_,_ in self.api.calls))

    def test_partial_secret_write_is_reserved_and_requires_user_review(self):
        self.core.check('unit-installer')
        with self.assertRaises(SetupError):self.core.configure(self.values,Mock(side_effect=SetupError('UNKNOWN')))
        restored=c.Progress(self.path,TARGET)
        self.assertEqual(restored.data['pending'],'secrets')
        upload=Mock()
        with self.assertRaises(SetupError):self.core.configure(self.values,upload)
        upload.assert_not_called()
        self.assertEqual(self.api.variables['MONITOR_ENABLED'],'false')

    def test_lost_dispatch_ack_never_redispatches_automatically(self):
        self.core.check('unit-installer');self.api.dispatch_lost=True
        with self.assertRaises(SetupError):self.core.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        self.api.dispatch_lost=False
        with self.assertRaises(SetupError):self.core.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        self.assertEqual(len(self.api.runs),1)
        self.assertEqual(c.Progress(self.path,TARGET).data['pending'],'prepare')

    def test_resume_waits_existing_run_without_second_dispatch(self):
        self.core.check('unit-installer');self.api.run_pending=True
        with self.assertRaises(SetupError):self.core.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        run=self.progress.data['runs']['prepare'];self.api.runs[run]['status']='completed'
        restored=c.Progress(self.path,TARGET)
        core=c.Installer(self.api,restored,self.api.manifest,self.emit,self.clock.clock,self.clock.sleep)
        core.check('unit-installer');core.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        self.assertEqual(len(self.api.runs),1)

    def test_workflow_failure_not_completed(self):
        self.core.check('unit-installer');self.api.run_fail=True
        with self.assertRaises(SetupError):self.core.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        self.assertNotIn('prepare',self.progress.data['completed'])


    def test_user_authorized_test_then_confirmed_receive_can_enable(self):
        self.core.check('unit-installer');self.core.configure(self.values,Mock());self.core.validate()
        with self.assertRaises(SetupError):self.core.test_notification()
        self.core.test_notification(user_authorized=True)
        with self.assertRaises(SetupError):self.core.enable_after_received(received_confirmed=True)
        self.core.enable_after_received(received_confirmed=True,enable_authorized=True)
        self.assertEqual(self.api.variables['MONITOR_ENABLED'],'true')
        self.assertIn('enabled',self.progress.data['completed'])
        self.assertEqual(len(self.api.runs),3)
        self.core.test_notification(user_authorized=True)
        self.assertEqual(len(self.api.runs),3)


    def test_cli_confirmed_receipt_and_enable_simulated_e2e(self):
        stream=io.StringIO()
        def user_answer(prompt):
            if prompt.startswith('目标仓库策略'):return 'reuse'
            if '当前授权 GitHub 用户' in prompt:return 'ExampleUser'
            if '是否明确授权一次' in prompt:return 'SEND_TEST'
            if '确认本人收到' in prompt:return 'RECEIVED '+json.loads(self.path.read_text())['test_request_id']
            if '明确开启此仓库' in prompt:return 'ENABLE '+TARGET
            return TARGET
        with contextlib.redirect_stdout(stream),patch.object(sys.stdin,'isatty',return_value=True),patch.object(sys.stdout,'isatty',return_value=True),             patch.object(cli,'device_authorize',return_value=self.api.token),patch.object(cli,'GitHubAPI',return_value=self.api),             patch.object(cli,'manifest',return_value=self.api.manifest),patch.object(cli,'collect_feishu',return_value=self.values),             patch.object(c,'upload_secrets'),patch('builtins.input',side_effect=user_answer):
            code=cli.main(['--run','--client-id','Iv1.UnitPublicID','--app-slug','unit-installer','--target',TARGET,'--progress',str(self.path)])
        self.assertEqual(code,0)
        self.assertEqual(self.api.variables['MONITOR_ENABLED'],'true')
        self.assertEqual(len(self.api.runs),3)
        self.assertIsNone(self.api.token)

    def test_gui_buttons_use_same_validated_installation_core(self):
        ui=Mock();ui.recover.return_value={};ui.ask.side_effect=['reuse','SEND_TEST']
        with patch.object(cli,'device_authorize',return_value=self.api.token),patch.object(cli,'GitHubAPI',return_value=self.api),patch.object(cli,'manifest',return_value=self.api.manifest),patch.object(cli,'collect_feishu',return_value=self.values),patch.object(c,'upload_secrets'),patch('builtins.input',side_effect=AssertionError('GUI must not read terminal')):
            result=cli.main(['--run','--client-id','Iv1.UnitPublicID','--app-slug','unit-installer','--target',TARGET,'--progress',str(self.path)],ui=ui)
        self.assertEqual(result,0);self.assertEqual(self.api.variables['MONITOR_ENABLED'],'true')
        self.assertEqual(len(self.api.runs),3)
        confirmations=[call.args[1] for call in ui.confirm.call_args_list]
        self.assertIn('ENABLE '+TARGET,confirmations)
        self.assertTrue(any(value.startswith('RECEIVED ') for value in confirmations))

    def test_default_feishu_new_uses_official_scan_without_manual_secret(self):
        import feishu_app
        credentials=feishu_app.AppCredentials('cli_'+'x'*16,'synthetic-secret','ou_'+'y'*24)
        session=Mock();session.verification_url='https://open.feishu.cn/page/cli?user_code=TEST'
        with patch('builtins.input',side_effect=['','','CREATE','VERIFIED']),patch.object(cli,'begin_registration',return_value=session) as begin,             patch.object(cli,'poll_registration',return_value=credentials),patch.object(cli,'tenant_token',return_value='synthetic-tat'),             patch.object(cli.getpass,'getpass',side_effect=AssertionError('No manual secret required')):
            values=cli.collect_feishu(self.progress,self.emit)
        self.assertEqual(values['FEISHU_APP_ID'],credentials.app_id)
        self.assertEqual(begin.call_args.kwargs,{'create_only':True,'existing_app_id':None,'app_name':'Codex 提醒'})

    def test_unknown_test_ledger_cannot_enable_or_generate_new_send(self):
        self.core.check('unit-installer');self.core.configure(self.values,Mock());self.core.validate()
        self.core.test_notification(user_authorized=True)
        request=self.progress.data['test_request_id'];self.api.test_records[request]['status']='unknown'
        with self.assertRaises(SetupError):self.core.enable_after_received(received_confirmed=True,enable_authorized=True)
        self.assertEqual(self.api.variables['MONITOR_ENABLED'],'false')
        self.assertEqual(len(self.api.runs),3)

    def test_resume_github_user_id_mismatch_stops_before_mutations(self):
        self.core.check('unit-installer')
        original=self.api.request
        def changed(method,path,payload=None):
            return {'login':'OtherUser','id':456} if path=='/user' else original(method,path,payload)
        self.api.request=changed
        with self.assertRaises(SetupError) as caught:self.core.check('unit-installer')
        self.assertEqual(caught.exception.code,'RESUME_IDENTITY_MISMATCH')
        self.assertFalse(any(method!='GET' for method,_,_ in self.api.calls))

    def test_explicit_failed_stage_retry_keeps_history_and_only_reruns_safe_job(self):
        self.core.check('unit-installer');self.api.run_fail=True
        with self.assertRaises(SetupError):self.core.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        failed=self.progress.data['runs']['prepare']
        self.api.run_fail=False
        self.core.retry_failed_stage('prepare')
        self.core.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        self.assertEqual(self.progress.data['run_history']['prepare'],[failed])
        self.assertEqual(len(self.api.runs),2)
        self.assertNotIn('MONITOR_ENABLED',self.api.variables)

    def test_successful_or_running_job_cannot_be_retried_as_failed(self):
        self.core.check('unit-installer')
        self.core.workflow('prepare.yml','prepare',{'confirm_first_setup':'true'})
        with self.assertRaises(SetupError):self.core.retry_failed_stage('prepare')
        self.assertEqual(len(self.api.runs),1)

    def test_device_token_arriving_after_session_expiry_is_rejected(self):
        clock=FakeClock()
        def request(url,payload,**kwargs):
            if 'device/code' in url:return {'device_code':'x'*40,'user_code':'ABCD-EFGH','expires_in':6,'interval':5}
            clock.now=7
            return {'access_token':'ghu_'+'x'*16,'token_type':'bearer','expires_in':28800}
        with self.assertRaises(SetupError) as caught:c.device_authorize('Iv1.UnitPublicID',self.emit,request=request,clock=clock.clock,sleep=clock.sleep)
        self.assertEqual(caught.exception.code,'DEVICE_TIMED_OUT')

    def test_progress_corruption_target_mismatch_and_credentials_schema_rejected(self):
        self.progress.save()
        with self.assertRaises(SetupError):c.Progress(self.path,'OtherOwner/repo')
        value=json.loads(self.path.read_text());value['token']='synthetic-credential';self.path.write_text(json.dumps(value))
        with self.assertRaises(SetupError):c.Progress(self.path,TARGET)
        self.path.write_text('{bad')
        with self.assertRaises(SetupError):c.Progress(self.path,TARGET)

    def test_secret_upload_stdin_only_debug_removed_and_unknown_redacted(self):
        runner=Mock(return_value=subprocess.CompletedProcess(['gh'],0,b'',b''))
        with patch.object(c.shutil,'which',return_value='/unit/gh'),patch.dict(os.environ,{'GH_DEBUG':'api','GITHUB_TOKEN':'parent-credential'}):
            c.upload_secrets(self.api.token,TARGET,self.values,runner)
        self.assertEqual(runner.call_count,len(c.APP_SECRET_NAMES))
        for call,name in zip(runner.call_args_list,c.APP_SECRET_NAMES):
            self.assertEqual(call.kwargs['input'],self.values[name].encode())
            self.assertNotIn(self.api.token,str(call.args[0]));self.assertNotIn(self.values[name],str(call.args[0]))
            self.assertNotIn('GH_DEBUG',call.kwargs['env']);self.assertNotIn('GITHUB_TOKEN',call.kwargs['env'])
        runner=Mock(side_effect=OSError(self.api.token))
        with patch.object(c.shutil,'which',return_value='/unit/gh'),self.assertRaises(SetupError) as caught:
            c.upload_secrets(self.api.token,TARGET,self.values,runner)
        self.assertNotIn(self.api.token,str(caught.exception))

    def test_user_cli_simulated_e2e_same_core_and_cleanup(self):
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream),patch.object(sys.stdin,'isatty',return_value=True),patch.object(sys.stdout,'isatty',return_value=True),\
             patch.object(cli,'device_authorize',return_value=self.api.token),patch.object(cli,'GitHubAPI',return_value=self.api),\
             patch.object(cli,'manifest',return_value=self.api.manifest),patch.object(cli,'collect_feishu',return_value=self.values),\
             patch.object(c,'upload_secrets') as upload,patch('builtins.input',side_effect=['reuse',TARGET,'ExampleUser',TARGET,'']):
            code=cli.main(['--run','--client-id','Iv1.UnitPublicID','--app-slug','unit-installer','--target',TARGET,'--progress',str(self.path)])
        self.assertEqual(code,0);upload.assert_called_once()
        self.assertIsNone(self.api.token)
        self.assertEqual(self.values,{})
        self.assertIn('当前 MONITOR_ENABLED=false',stream.getvalue())

    def test_cli_default_plan_never_accesses_accounts_or_credentials(self):
        with contextlib.redirect_stdout(io.StringIO()),patch.object(cli,'device_authorize',side_effect=AssertionError()),\
             patch.object(cli,'collect_feishu',side_effect=AssertionError()):
            self.assertEqual(cli.main([]),0)
        self.assertFalse(self.path.exists())

    def test_missing_real_app_stops_before_auth(self):
        with contextlib.redirect_stdout(io.StringIO()),patch.object(sys.stdin,'isatty',return_value=True),patch.object(sys.stdout,'isatty',return_value=True),\
             patch.object(cli,'device_authorize') as auth:
            self.assertEqual(cli.main(['--run']),2)
        auth.assert_not_called()

    def test_github_device_flow_minimum_interval_and_expiring_token_no_client_secret(self):
        clock=FakeClock();request=Mock(side_effect=[{'device_code':'x'*40,'user_code':'ABCD-EFGH','expires_in':900,'interval':5},
            {'error':'authorization_pending'},{'error':'slow_down'},{'access_token':'ghu_'+'x'*16,'token_type':'bearer','expires_in':28800,'refresh_token':'synthetic-refresh'}])
        token=c.device_authorize('Iv1.UnitPublicID',self.emit,request=request,clock=clock.clock,sleep=clock.sleep)
        self.assertEqual(token,'ghu_'+'x'*16);self.assertEqual(clock.waits,[5,5,10])
        payloads=[call.args[1] for call in request.call_args_list]
        self.assertNotIn('client_secret',json.dumps(payloads));self.assertNotIn('repo',payloads[0].get('scope',''))
        self.assertNotIn('synthetic-refresh',repr(self.emit.call_args_list))

    def test_device_rejects_denial_nonexpiring_token_and_wrong_app_token(self):
        for response in ({'error':'access_denied','error_description':'sensitive'},
            {'access_token':'ghu_'+'x'*16,'token_type':'bearer'},
            {'access_token':'ghp_'+'x'*16,'token_type':'bearer','expires_in':28800}):
            clock=FakeClock();request=Mock(side_effect=[{'device_code':'x'*40,'user_code':'ABCD-EFGH','expires_in':900,'interval':5},response])
            with self.subTest(response=response),self.assertRaises(SetupError) as caught:
                c.device_authorize('Iv1.UnitPublicID',self.emit,request=request,clock=clock.clock,sleep=clock.sleep)
            self.assertNotIn('sensitive',str(caught.exception))

    def test_device_timeout_does_not_start_second_flow(self):
        clock=FakeClock();request=Mock(return_value={'device_code':'x'*40,'user_code':'ABCD-EFGH','expires_in':5,'interval':5})
        with self.assertRaises(SetupError):c.device_authorize('Iv1.UnitPublicID',self.emit,request=request,clock=clock.clock,sleep=clock.sleep)
        self.assertEqual(request.call_count,1)

    def test_prepare_preserves_existing_state_and_fails_corrupt_or_deleted(self):
        api=Mock();api.request.return_value={'content':base64.b64encode(json.dumps(empty_state()).encode()).decode()}
        self.assertEqual(b.prepare(api,TARGET),'preserved');self.assertEqual(api.request.call_count,1)
        api.request.return_value={'content':base64.b64encode(b'{bad').decode()}
        with self.assertRaises(SetupError):b.prepare(api,TARGET)
        api.request.side_effect=SetupError('NOT_FOUND')
        with self.assertRaises(SetupError):b.prepare(api,TARGET,initialized=True)

    def test_prepare_first_setup_only_writes_git_state_no_credentials_or_send(self):
        api=Mock();api.request.side_effect=[SetupError('NOT_FOUND'),SetupError('NOT_FOUND'),{'sha':'tree'},{'sha':'commit'},None]
        self.assertEqual(b.prepare(api,TARGET),'initialized')
        methods=[call.args[0] for call in api.request.call_args_list]
        self.assertEqual(methods,['GET','GET','POST','POST','POST'])
        self.assertIn('refs/heads/monitor-state',repr(api.request.call_args_list))
        self.assertNotIn('MONITOR_ENABLED',repr(api.request.call_args_list))

    def test_api_redacts_http_and_token_and_closes_reference(self):
        import urllib.error
        api=GitHubAPI('synthetic-token')
        opener=Mock();opener.open.side_effect=urllib.error.HTTPError('https://api.github.com/user',403,'synthetic-token',{},None)
        with patch('installer.github_api.urllib.request.build_opener',return_value=opener):
            with self.assertRaises(SetupError) as caught:api.request('GET','/user')
        self.assertEqual(caught.exception.code,'PERMISSION_OR_POLICY');self.assertNotIn('synthetic-token',str(caught.exception))
        self.assertNotIn('synthetic-token',repr(api));api.close();self.assertIsNone(api.token)

    def test_release_build_allowlist_and_skill_share_identical_zipapp(self):
        import importlib.util,zipfile
        spec=importlib.util.spec_from_file_location('builder',ROOT/'scripts/build_release.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        out=Path(self.temp.name)/'release';builder.build(out)
        with zipfile.ZipFile(out/'codex-reset-install.pyz') as archive:
            names=archive.namelist()
            self.assertFalse(any('runtime' in name or '.git/' in name or 'config.json' in name for name in names))
            self.assertIn('installer/trusted_files.json',names)
            self.assertIn('reset_events.py',names)
            self.assertIn('reset_events.py',json.loads(archive.read('installer/trusted_files.json')))
        with zipfile.ZipFile(out/'codex-reset-install-skill.zip') as archive:
            self.assertEqual(archive.read('codex-reset-install/assets/codex-reset-install.pyz'),(out/'codex-reset-install.pyz').read_bytes())
        environment={k:os.environ[k] for k in ('SystemRoot','SYSTEMROOT','WINDIR','TEMP','TMP') if k in os.environ}
        environment.update(PATH=os.environ.get('PATH',''),HOME=self.temp.name)
        done=subprocess.run([sys.executable,'-I','-S',str(out/'codex-reset-install.pyz')],capture_output=True,check=False,env=environment)
        self.assertEqual(done.returncode,0,done.stderr.decode('utf-8',errors='replace'));self.assertIn(b'[plan]',done.stdout)


if __name__=='__main__':unittest.main()

class PermissionContractTest(unittest.TestCase):
    def test_exact_documented_permission_levels_and_default_metadata(self):
        c.verify_installation_permissions(dict(c.REQUIRED_PERMISSIONS))
        for key in c.REQUIRED_PERMISSIONS:
            actual=dict(c.REQUIRED_PERMISSIONS);actual.pop(key)
            with self.subTest(key=key),self.assertRaises(SetupError) as caught:c.verify_installation_permissions(actual)
            self.assertEqual(caught.exception.permission_diff,[{'name':key,'actual':'missing','expected':c.REQUIRED_PERMISSIONS[key]}])
    def test_official_installations_response_permission_subset_is_not_our_app(self):
        # Unmodified permissions subset of GitHub's documented response example;
        # this is an official schema fixture, not the user's live installation.
        actual={'checks':'write','metadata':'read','contents':'read'}
        with self.assertRaises(SetupError) as caught:c.verify_installation_permissions(actual)
        diff=caught.exception.permission_diff
        self.assertIn({'name':'checks','actual':'write','expected':'absent'},diff)
        self.assertIn({'name':'actions_variables','actual':'missing','expected':'write'},diff)
    def test_changed_extra_none_and_invalid_values_fail_closed_without_raw_echo(self):
        for actual in (dict(c.REQUIRED_PERMISSIONS,contents='write'),dict(c.REQUIRED_PERMISSIONS,administration='write'),dict(c.REQUIRED_PERMISSIONS,issues='none'),dict(c.REQUIRED_PERMISSIONS,secrets='private-value'),{'private-key':'private-value'},None,[]):
            with self.subTest(actual=actual),self.assertRaises(SetupError) as caught:c.verify_installation_permissions(actual)
            self.assertNotIn('private-value',str(caught.exception));self.assertNotIn('private-key',str(caught.exception))
    def test_other_owner_stale_install_does_not_block_target_owner(self):
        api=FakeAPI();original=api.request
        def response(method,path,payload=None):
            if path=='/user/installations?per_page=100':
                good=original(method,path,payload)['installations'][0]
                bad=dict(good,id=2,account={'login':'OtherOwner'},repository_selection='all',permissions={'administration':'write'})
                return {'total_count':2,'installations':[bad,good]}
            return original(method,path,payload)
        api.request=response
        self.assertEqual(c.verify_installation(api,TARGET,'unit-installer')['id'],123)
    def test_target_owner_broad_or_extra_permissions_still_rejected(self):
        for selection,permissions in [('all',dict(c.REQUIRED_PERMISSIONS)),('selected',dict(c.REQUIRED_PERMISSIONS,administration='write'))]:
            api=FakeAPI();api.selection=selection;api.permissions=permissions
            with self.assertRaises(SetupError):c.verify_installation(api,TARGET,'unit-installer')
    def test_retry_only_reads_with_same_authorization(self):
        api=FakeAPI();api.permissions=dict(c.REQUIRED_PERMISSIONS,actions_variables='read');ui=Mock()
        with tempfile.TemporaryDirectory() as directory:
            progress=c.Progress(Path(directory)/'state.json',TARGET)
            installer=c.Installer(api,progress,api.manifest,Mock())
            def fix(error,slug):
                self.assertEqual(error.permission_diff,[{'name':'actions_variables','actual':'read','expected':'write'}])
                api.permissions=dict(c.REQUIRED_PERMISSIONS);return 'retry'
            ui.installation_issue.side_effect=fix
            cli.check_installation(installer,'unit-installer',ui)
            self.assertEqual(ui.installation_issue.call_count,1)
            self.assertTrue(all(method=='GET' for method,_,_ in api.calls))
    def test_single_variable_read_cannot_miss_enabled_gate_on_later_page(self):
        api=FakeAPI();api.variables['MONITOR_ENABLED']='true'
        with self.assertRaises(SetupError) as caught:c.verify_repository(api,TARGET,api.manifest)
        self.assertEqual(caught.exception.code,'MONITOR_ALREADY_ENABLED')
        self.assertFalse(any('variables?' in path for _,path,_ in api.calls))
        self.assertTrue(any(path.endswith('/variables/MONITOR_ENABLED') for _,path,_ in api.calls))

class ObservedPermissionRegressionTest(unittest.TestCase):
    def test_live_observed_missing_variables_normalized_from_github_ui(self):
        # 2026-10-15 read-only App and installation settings inspection.
        # This is a normalized UI observation, NOT a captured authenticated API
        # response; no account identity, token, Secret or installation ID retained.
        actual={'actions':'write','contents':'read','secrets':'write','metadata':'read'}
        with self.assertRaises(SetupError) as caught:c.verify_installation_permissions(actual)
        self.assertEqual(caught.exception.permission_diff,[{'name':'actions_variables','actual':'missing','expected':'write'}])

class BoundedPermissionDiagnosticTest(unittest.TestCase):
    def test_large_unknown_permission_response_remains_bounded_and_rejected(self):
        actual=dict(c.REQUIRED_PERMISSIONS)
        actual.update({'unknown_'+str(i):'private-value' for i in range(1000)})
        with self.assertRaises(SetupError) as caught:c.verify_installation_permissions(actual)
        self.assertLessEqual(len(caught.exception.permission_diff),33)
        self.assertLess(len(str(caught.exception)),2500)
        self.assertNotIn('private-value',str(caught.exception))

class CanonicalGitHubPermissionKeysTest(unittest.TestCase):
    def test_independent_contract_matches_observed_registration_fields(self):
        expected=json.loads((ROOT/'tests/fixtures/github-variable-permissions.json').read_text())['permissions']
        self.assertEqual(c.REQUIRED_PERMISSIONS,expected)
        c.verify_installation_permissions(expected)
    def test_wrong_legacy_key_is_rejected_and_raw_key_remains_diagnosable(self):
        actual={'actions':'write','contents':'read','metadata':'read','secrets':'write','variables':'write'}
        with self.assertRaises(SetupError) as caught:c.verify_installation_permissions(actual)
        self.assertEqual(caught.exception.permission_diff,[
            {'name':'actions_variables','actual':'missing','expected':'write'},
            {'name':'variables','actual':'write','expected':'absent'}])
    def test_canonical_permission_still_rejects_extra_sensitive_access(self):
        actual={'actions':'write','contents':'read','metadata':'read','secrets':'write','actions_variables':'write','administration':'write'}
        with self.assertRaises(SetupError) as caught:c.verify_installation_permissions(actual)
        self.assertEqual(caught.exception.permission_diff,[{'name':'administration','actual':'write','expected':'absent'}])
    def test_form_manifest_and_registration_use_same_exact_official_keys(self):
        from installer.registration import registration_url
        from urllib.parse import urlsplit,parse_qs
        expected={'actions':'write','contents':'read','metadata':'read','secrets':'write','actions_variables':'write'}
        manifest=json.loads((ROOT/'docs/app-manifest.template.json').read_text())['default_permissions']
        self.assertEqual(manifest,expected)
        query=parse_qs(urlsplit(registration_url()).query)
        self.assertEqual({k:query[k][0] for k in expected if k!='metadata'}, {k:v for k,v in expected.items() if k!='metadata'})
        self.assertNotIn('variables',query)

class OfficialVariableEndpointFixtureTest(unittest.TestCase):
    def test_official_variables_endpoint_fixture_is_bound_to_actions_variables(self):
        fixture=json.loads((ROOT/'tests/fixtures/github-variable-permissions.json').read_text())
        self.assertEqual(fixture['actions_variables']['title'],'Variables')
        routes={(p['verb'],p['requestPath']):p['access'] for p in fixture['actions_variables']['permissions']}
        self.assertEqual(routes[('post','/repos/{owner}/{repo}/actions/variables')],'write')
        self.assertEqual(routes[('patch','/repos/{owner}/{repo}/actions/variables/{name}')],'write')
        self.assertEqual(routes[('get','/repos/{owner}/{repo}/actions/variables/{name}')],'read')
    def test_real_field_mock_cannot_be_silently_renamed_with_production_constant(self):
        self.assertEqual(FakeAPI().permissions,json.loads((ROOT/'tests/fixtures/github-variable-permissions.json').read_text())['permissions'])

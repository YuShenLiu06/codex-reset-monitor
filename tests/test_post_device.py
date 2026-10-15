"""Post-device regressions. Public source snapshots and synthetic service boundaries only."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
import urllib.error
from installer import cli, core
from installer.github_api import GitHubAPI,SetupError,API_ROUTES
from installer.gui_worker import GUI
from installer.offline_preflight import check,SnapshotAPI
from scripts.deployment_manifest import build_manifest
from scripts.verify_deployment_pair import snapshot

ROOT=Path(__file__).resolve().parents[1]
DEPLOYMENT='519c2696a5286a388e524d575393acbfe59f070d'
PRIVATE='private-token-cookie-device-code'

class PostDeviceTest(unittest.TestCase):
    def test_unpublished_installer_pairs_with_real_released_deployment(self):
        manifest,commit=build_manifest(ROOT,DEPLOYMENT)
        self.assertEqual(commit,DEPLOYMENT)
        self.assertNotIn('.github/workflows/mac-device-test.yml',manifest)
        report=check(snapshot(commit),manifest)
        self.assertTrue(all(row['passed'] for row in report['cases']))
        self.assertEqual(report['writes'],0);self.assertEqual(report['http_requests'],0)

    def test_old_test_package_bug_is_rejected_before_contents_404(self):
        manifest,_=build_manifest(ROOT,DEPLOYMENT)
        manifest['.github/workflows/mac-device-test.yml']='a'*64
        api=SnapshotAPI(snapshot(DEPLOYMENT))
        with self.assertRaises(SetupError) as caught:core.verify_repository(api,core.SOURCE_REPOSITORY,manifest)
        self.assertEqual(caught.exception.code,'PROJECT_FILES_MISSING')
        self.assertEqual(caught.exception.diagnostics['operation'],'manifest_pairing')
        self.assertFalse(any('/contents/' in path for path in api.calls))

    def test_explicit_reviewed_commit_cannot_be_a_moving_ref(self):
        for value in ('main','origin/main','519c269','a'*40+';anything'):
            with self.subTest(value=value),self.assertRaises(ValueError):build_manifest(ROOT,value)

    def test_default_build_keeps_current_source_manifest(self):
        manifest,commit=build_manifest(ROOT)
        self.assertEqual(manifest,core.expected_manifest(ROOT))
        self.assertEqual(commit,subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())

    def test_each_preflight_404_keeps_precise_safe_operation_and_request_id(self):
        paths={
            '/user':'user','/user/installations?per_page=100':'installations',
            '/user/installations/42/repositories?per_page=100':'installation_repositories',
            '/repos/owner/repo':'repository','/repos/owner/repo/git/ref/heads/main':'main_revision',
            '/repos/owner/repo/git/trees/'+('a'*40)+'?recursive=1':'repository_tree',
            '/repos/owner/repo/contents/'+PRIVATE+'?ref='+('a'*40):'reviewed_file',
            '/repos/owner/repo/actions/workflows/monitor.yml':'workflow_monitor',
            '/repos/owner/repo/actions/secrets/public-key':'secrets_public_key',
            '/repos/owner/repo/actions/variables/MONITOR_ENABLED':'variable'}
        for path,operation in paths.items():
            with self.subTest(operation=operation):
                opener=Mock();opener.open.side_effect=urllib.error.HTTPError('https://api.github.com'+path,404,PRIVATE,
                    {'X-GitHub-Request-Id':'TEST:404:ABC','Set-Cookie':PRIVATE},io.BytesIO(PRIVATE.encode()))
                with patch('installer.github_api.urllib.request.build_opener',return_value=opener),self.assertRaises(SetupError) as caught:
                    GitHubAPI(PRIVATE).request('GET',path)
                error=caught.exception;self.assertEqual(error.code,'NOT_FOUND')
                self.assertEqual(error.diagnostics['operation'],operation)
                self.assertEqual(error.diagnostics['endpoint'],API_ROUTES[operation])
                self.assertEqual(error.diagnostics['request_id'],'TEST:404:ABC')
                output=io.StringIO();GUI(writer=output).failure(error)
                message=json.loads(output.getvalue());self.assertIn(operation,message['text']);self.assertIn('404',message['text'])
                self.assertNotIn(PRIVATE,output.getvalue());self.assertFalse(message['retry_device'])
                self.assertEqual(opener.open.call_count,1)

    def test_identity_confirmation_precedes_repository_checks_and_cancel_preserves_progress(self):
        events=[]
        class UI:
            def emit(self,*args):pass
            def ask(self,prompt):return 'reuse'
            def confirm(self,prompt,word):
                events.append(('confirm',prompt))
                if '当前授权 GitHub 用户' in prompt:raise SetupError('USER_CANCELLED')
            def failure(self,error):self.error=error
        def request(method,path,payload=None):
            events.append(('api',path));self.assertEqual(path,'/user')
            return {'login':'ExampleUser','id':123}
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'state.local.json';progress=core.Progress(path,'ExampleUser/codex-reset-monitor')
            progress.mark('target');ui=UI();api=Mock();api.request.side_effect=request
            with patch.object(cli.shutil,'which',return_value='/synthetic/gh'),\
                 patch.object(cli,'configure_public',return_value={'client_id':'Iv1.PublicID','app_slug':'unit-installer'}),\
                 patch.object(cli,'device_authorize',return_value='ghu_synthetic'),patch.object(cli,'GitHubAPI',return_value=api):
                self.assertEqual(cli.main(['--run','--progress',str(path)],ui),2)
            self.assertEqual(ui.error.code,'USER_CANCELLED');api.close.assert_called_once()
            self.assertEqual([v for kind,v in events if kind=='api'],['/user'])
            saved=json.loads(path.read_text());self.assertEqual(saved['completed'],['target','github'])
            self.assertIsNone(saved['pending']);self.assertNotIn('ghu_',path.read_text())

if __name__=='__main__':unittest.main()

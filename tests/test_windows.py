"""Windows portability checks and offline service fixtures. No account access."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from installer.gui_worker import GUI,progress_directory
import installer.core as core
import installer.cli as cli

ROOT=Path(__file__).resolve().parents[1]


class WindowsCompatibilityTest(unittest.TestCase):
    def test_local_app_data_supports_chinese_and_spaces(self):
        with patch('installer.gui_worker.sys.platform','win32'),patch.dict(os.environ,{'LOCALAPPDATA':'C:/用户 空格/Local'}):
            self.assertEqual(progress_directory(),Path('C:/用户 空格/Local')/'Codex Reset Installer')
    def test_windows_preflight_reaches_local_prompt_and_cancel_without_auth(self):
        output=io.StringIO();gui=GUI(io.StringIO('{"id":1,"value":"cancel"}\n'),output)
        with tempfile.TemporaryDirectory() as directory,patch.object(cli.sys,'platform','win32'),patch.object(cli.shutil,'which',return_value='C:/tools/gh.exe'),patch.object(cli,'device_authorize') as authorize:
            result=cli.main(['--client-id','Iv1.TestPublicID','--app-slug','codex-reset-installer','--progress',str(Path(directory)/'state.json')],ui=gui)
            self.assertEqual(result,2);authorize.assert_not_called()
            self.assertIn('设置放在哪里？',output.getvalue());self.assertFalse(list(Path(directory).iterdir()))
    def test_gh_hidden_process_and_isolated_windows_user_configuration(self):
        runner=Mock(return_value=subprocess.CompletedProcess(['gh'],0,b'',b''))
        values={name:'synthetic-'+name for name in core.APP_SECRET_NAMES}
        with patch.object(core.sys,'platform','win32'),patch.object(core.shutil,'which',return_value='C:/包 空格/gh.exe'),patch.dict(os.environ,{'GH_ENTERPRISE_TOKEN':'must-not-inherit','GIT_CONFIG_GLOBAL':'must-not-inherit'}):
            core.upload_secrets('synthetic-token','ExampleUser/repo',values,runner)
        self.assertEqual(runner.call_count,5)
        for call in runner.call_args_list:
            options=call.kwargs;environment=options['env']
            self.assertEqual(options['creationflags'],0x08000000)
            self.assertNotIn('GH_ENTERPRISE_TOKEN',environment);self.assertNotIn('GIT_CONFIG_GLOBAL',environment)
            self.assertEqual(environment['USERPROFILE'],environment['GH_CONFIG_DIR'])
            self.assertEqual(environment['APPDATA'],environment['HOME'])
            self.assertIsInstance(options['input'],bytes)
    def test_manifest_includes_new_windows_workflow_and_no_build_outputs(self):
        manifest=core.expected_manifest(ROOT)
        self.assertIn('.github/workflows/windows.yml',manifest)
        self.assertFalse(any('/obj/' in name or '/bin/' in name or name.startswith('runtime/') for name in manifest))
        for name,digest in manifest.items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest)
            self.assertNotIn(b'\r\n',(ROOT/name).read_bytes(),name)
    def test_full_offline_fixture_executes_shared_cli_and_installer(self):
        from windows.offline_fixture import run
        messages=[]
        class Writer:
            def write(self,line):messages.append(json.loads(line))
            def flush(self):pass
        class Reader:
            def readline(self,limit):
                prompt=messages[-1]
                if prompt['choices']:
                    value='reuse' if prompt['title']=='准备存放设置的地方' else prompt['choices'][0]['value']
                else:value='我的 Codex 小助手' if prompt['title']=='给提醒机器人起个名字' else 'ExampleUser/codex-reset-monitor'
                return json.dumps({'id':prompt['id'],'value':value},ensure_ascii=False)+'\n'
        with tempfile.TemporaryDirectory(prefix='中文 空格 ') as directory:
            state=Path(directory)/'state.json'
            result=run('full',GUI(Reader(),Writer()),['--progress',str(state),'--client-id','Iv1.TestPublicID','--app-slug','codex-reset-installer'])
            self.assertEqual(result,0)
            saved=json.loads(state.read_text(encoding='utf-8'));self.assertIn('enabled',saved['completed'])
            self.assertNotIn('synthetic-secret',state.read_text(encoding='utf-8'))
            self.assertTrue(any(m.get('user_code')=='ABCD-1234' for m in messages))


if __name__=='__main__':unittest.main()

import io
import json
import unittest
from installer.gui_worker import GUI
from installer.github_api import SetupError

class GUIWorkerTest(unittest.TestCase):
    def test_button_confirms_exact_received_request(self):
        nonce='a'*32;word='RECEIVED '+nonce
        output=io.StringIO();gui=GUI(io.StringIO(json.dumps({'id':1,'value':word})+'\n'),output)
        self.assertEqual(gui.confirm('确认本人收到对应标识',word),word)
        prompt=json.loads(output.getvalue())
        self.assertIn(nonce,prompt['text']);self.assertEqual(prompt['choices'][0]['label'],'我收到了这条测试')
    def test_wrong_prompt_response_stops(self):
        gui=GUI(io.StringIO('{"id":99,"value":"CREATE"}\n'),io.StringIO())
        with self.assertRaises(SetupError):gui.confirm('create','CREATE')
    def test_cancel_does_not_confirm_enable(self):
        gui=GUI(io.StringIO('{"id":1,"value":"cancel"}\n'),io.StringIO())
        with self.assertRaises(SetupError):gui.confirm('enable','ENABLE User/repo')
    def test_notices_hide_sensitive_body_and_untrusted_links(self):
        output=io.StringIO();gui=GUI(writer=output)
        gui.emit('secrets','PRIVATE_APP_SECRET')
        gui.emit('github','https://github.com.attacker.test/PRIVATE_APP_SECRET')
        gui.emit('stopped','private_credential: original response')
        self.assertNotIn('PRIVATE_APP_SECRET',output.getvalue())
        self.assertNotIn('private_credential',output.getvalue())
        self.assertNotIn('original response',output.getvalue())
    def test_official_device_page_and_code_display_only_in_current_window(self):
        output=io.StringIO();GUI(writer=output).emit('github','在 https://github.com/login/device 输入 ABCD-1234；仅本人')
        value=json.loads(output.getvalue());self.assertEqual(value['links'],['https://github.com/login/device'])
        self.assertEqual(value['user_code'],'ABCD-1234')
        self.assertIn('复制下方授权码',value['text'])
    def test_device_copy_value_requires_the_exact_official_page(self):
        output=io.StringIO();gui=GUI(writer=output)
        gui.emit('github','https://github.com/login/device/other ABCD-1234')
        value=json.loads(output.getvalue());self.assertEqual(value['user_code'],'')
    def test_unknown_ui_step_fails_closed(self):
        with self.assertRaises(SetupError):GUI(reader=io.StringIO(),writer=io.StringIO()).ask('unsupported')

class GUIRecoveryTest(unittest.TestCase):
    def gui(self,values):
        return GUI(io.StringIO(''.join(json.dumps({'id':i,'value':v})+'\n' for i,v in enumerate(values,1))),io.StringIO())
    def test_partial_secrets_requires_review(self):
        from types import SimpleNamespace
        progress=SimpleNamespace(data={'pending':'secrets'})
        self.assertEqual(self.gui(['reviewed']).recover(progress),{'secrets':True})
        with self.assertRaises(SetupError):self.gui(['cancel']).recover(progress)
    def test_unknown_test_dispatch_only_explicit_run_resolution(self):
        from types import SimpleNamespace
        progress=SimpleNamespace(data={'pending':'test'})
        self.assertEqual(self.gui(['123']).recover(progress),{'resolve':('test','123')})
        with self.assertRaises(SetupError):self.gui(['invalid']).recover(progress)
    def test_failed_prepare_can_wait_or_explicitly_retry(self):
        from types import SimpleNamespace
        progress=SimpleNamespace(data={'pending':None,'runs':{'prepare':123},'completed':[]})
        self.assertEqual(self.gui(['wait']).recover(progress),{})
        self.assertEqual(self.gui(['retry']).recover(progress),{'retry':'prepare'})

class GUIEndToEndTest(unittest.TestCase):
    def test_real_protocol_with_mock_services_through_received_and_enable(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch, Mock
        import installer.cli as cli
        import installer.core as core
        from test_installer import FakeAPI, TARGET
        from feishu_app import AppCredentials
        messages=[]
        class Writer:
            def write(self,line):messages.append(json.loads(line))
            def flush(self):pass
        class Reader:
            def readline(self,limit):
                prompt=messages[-1]
                if prompt['choices']:
                    value=prompt['choices'][0]['value']
                    if prompt['title']=='准备存放设置的地方':value='reuse'
                else:value='我的 Codex 小助手' if prompt['title']=='给提醒机器人起个名字' else TARGET
                return json.dumps({'id':prompt['id'],'value':value})+'\n'
        api=FakeAPI();gui=GUI(Reader(),Writer())
        session=Mock(verification_url='https://open.feishu.cn/safe-mock')
        credentials=AppCredentials('cli_'+'x'*16,'synthetic-secret','ou_'+'y'*24)
        with tempfile.TemporaryDirectory() as directory, patch.object(cli,'GitHubAPI',return_value=api), patch.object(cli,'device_authorize',return_value=api.token), patch.object(cli,'manifest',return_value=api.manifest), patch.object(cli,'begin_registration',return_value=session) as begin, patch.object(cli,'poll_registration',return_value=credentials), patch.object(cli,'tenant_token',return_value='synthetic-token'), patch.object(core,'upload_secrets') as upload:
            result=cli.main(['--run','--client-id','Iv1.UnitPublicID','--app-slug','unit-installer','--progress',str(Path(directory)/'state.json')],ui=gui)
        self.assertEqual(result,0);self.assertEqual(api.variables['MONITOR_ENABLED'],'true')
        self.assertEqual(len(api.runs),3);upload.assert_called_once()
        titles=[v['title'] for v in messages if v['type']=='prompt']
        self.assertIn('你收到测试了吗？',titles);self.assertIn('开启提醒',titles)
        self.assertNotIn('synthetic-secret',json.dumps(messages))
        self.assertIsNone(api.token)
        self.assertEqual(begin.call_args.kwargs['app_name'],'我的 Codex 小助手')

class GUIInputRepairTest(unittest.TestCase):
    def gui(self,values):
        output=io.StringIO()
        reader=io.StringIO(''.join(json.dumps({'id':i,'value':v})+'\n' for i,v in enumerate(values,1)))
        return GUI(reader,output),output
    def test_app_url_retries_settings_repo_query_and_empty_without_exit(self):
        gui,out=self.gui(['Iv1.PublicID','https://github.com/settings/apps/demo/installations','https://github.com/user/repo','https://github.com/apps/demo?q=x','','demo'])
        value=gui.public_fields()
        self.assertEqual(value['app_slug'],'demo')
        prompts=[json.loads(v) for v in out.getvalue().splitlines()]
        self.assertEqual(len(prompts),6)
        self.assertTrue(all(p['error'] for p in prompts[2:]))
        self.assertTrue(all(p['type']=='prompt' for p in prompts))
    def test_back_preserves_valid_client_and_can_correct_it(self):
        gui,out=self.gui(['Iv1.PublicID','__back__','Iv1.CorrectedID','https://github.com/apps/demo/'])
        self.assertEqual(gui.public_fields()['client_id'],'Iv1.CorrectedID')
        prompts=[json.loads(v) for v in out.getvalue().splitlines()]
        self.assertTrue(prompts[1]['back']);self.assertEqual(prompts[2]['initial'],'Iv1.PublicID')
    def test_invalid_client_retries_and_does_not_echo_invalid_value(self):
        gui,out=self.gui(['accidentally-pasted-private-value','Iv1.PublicID','demo'])
        self.assertEqual(gui.public_fields()['app_slug'],'demo')
        self.assertNotIn('accidentally-pasted-private-value',out.getvalue())
    def test_other_public_fields_are_correctable(self):
        gui,out=self.gui(['https://github.com/user/repo','user/repo','bad','cli_abcdefgh'])
        self.assertEqual(gui.ask('目标仓库 OWNER/REPO：'),'user/repo')
        self.assertEqual(gui.ask('要复用的公开 App ID：'),'cli_abcdefgh')
        self.assertEqual(len(out.getvalue().splitlines()),4)
    def test_name_is_editable_and_invalid_names_are_correctable(self):
        gui,out=self.gui(['','x'*21,'提醒\n机器人','我的 Codex 小助手'])
        self.assertEqual(gui.ask('提醒机器人名称 [默认 Codex 提醒]：'),'我的 Codex 小助手')
        prompts=[json.loads(v) for v in out.getvalue().splitlines()]
        self.assertEqual(prompts[0]['initial'],'Codex 提醒')
        self.assertTrue(all(p['error'] for p in prompts[1:]))
    def test_cancelling_name_does_not_start_registration_or_mark_app_created(self):
        from installer.cli import collect_feishu
        from types import SimpleNamespace
        from unittest.mock import Mock,patch
        progress=SimpleNamespace(data={'app_created':False},save=Mock())
        gui,out=self.gui(['new','cancel'])
        with patch('installer.cli.begin_registration') as begin,self.assertRaises(SetupError):
            collect_feishu(progress,gui.emit,ui=gui)
        begin.assert_not_called();progress.save.assert_not_called()
    def test_registration_only_saves_after_successful_correction(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from installer.registration import configure
        gui,out=self.gui(['existing','Iv1.PublicID','https://github.com/settings/apps/demo/installations','demo'])
        with tempfile.TemporaryDirectory() as directory,patch('installer.registration.read_bundled',return_value=None),patch('installer.registration.webbrowser.open',side_effect=AssertionError('no browser')):
            path=Path(directory)/'app.local.json'
            result=configure(path,gui.emit,ui=gui)
            self.assertEqual(result['app_slug'],'demo');self.assertEqual(json.loads(path.read_text()),result)

class GUISettingsURLTest(unittest.TestCase):
    def test_actual_user_input_shows_normalized_address_without_claiming_verified(self):
        helper=GUIInputRepairTest();gui,out=helper.gui(['Iv1.PublicID','https://github.com/settings/apps/codex-reset-installer','continue'])
        self.assertEqual(gui.public_fields()['app_slug'],'codex-reset-installer')
        prompt=json.loads(out.getvalue().splitlines()[-1])
        self.assertIn('https://github.com/apps/codex-reset-installer',prompt['text'])
        self.assertIn('仍需后续核验',prompt['text'])
    def test_normalization_can_go_back_and_correct_public_input(self):
        helper=GUIInputRepairTest();gui,out=helper.gui(['Iv1.PublicID','https://github.com/settings/apps/demo','back','corrected'])
        self.assertEqual(gui.public_fields()['app_slug'],'corrected')

class GUIInstallationRepairTest(unittest.TestCase):
    def test_permission_difference_and_official_recovery_links_are_actionable(self):
        from installer.core import REQUIRED_PERMISSIONS,verify_installation_permissions
        try:verify_installation_permissions(dict(REQUIRED_PERMISSIONS,actions_variables='read'))
        except SetupError as error:
            helper=GUIInputRepairTest();gui,out=helper.gui(['retry'])
            self.assertEqual(gui.installation_issue(error,'codex-reset-installer'),'retry')
            messages=[json.loads(v) for v in out.getvalue().splitlines()]
            self.assertIn('Variables',messages[-1]['text']);self.assertIn('现在 只读 → 需要 读写',messages[-1]['text'])
            self.assertIn('不重新授权',messages[-1]['text'])
            self.assertEqual(messages[0]['links'][0],'https://github.com/settings/apps/codex-reset-installer/permissions')
    def test_scope_error_is_not_misrepresented_as_missing_permission(self):
        helper=GUIInputRepairTest();gui,out=helper.gui(['cancel'])
        self.assertEqual(gui.installation_issue(SetupError('BROAD_INSTALLATION_REJECTED'),'codex-reset-installer'),'cancel')
        self.assertIn('Only select repositories',out.getvalue())

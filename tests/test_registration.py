import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from installer import registration as r,cli
from installer.github_api import SetupError

class RegistrationTest(unittest.TestCase):
    def test_official_url_only_minimal_parameters(self):
        url=urlsplit(r.registration_url());self.assertEqual(url.netloc,'github.com')
        query=parse_qs(url.query)
        for key,value in {'contents':'read','actions':'write','secrets':'write','actions_variables':'write','webhook_active':'false','public':'false'}.items():self.assertEqual(query[key],[value])
        self.assertEqual(parse_qs(urlsplit(r.registration_url(public=True)).query)['public'],['true'])
        self.assertNotIn('device_flow',query);self.assertNotIn('default_permissions',query)
    def test_validate_public_config_rejects_secrets_and_bad_urls(self):
        self.assertEqual(r.public_config('Iv1.PublicID','https://github.com/apps/test-installer')['app_slug'],'test-installer')
        for client,app in [('12345','test'),('ghp_fakecredential','test'),('Iv1.PublicID','https://github.com.evil/apps/test'),('Iv1.PublicID','https://github.com/apps/test?q=x')]:
            with self.assertRaises(SetupError):r.public_config(client,app)
    def test_create_save_and_resume_only_public_fields(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'app.local.json'
            with patch('builtins.input',side_effect=['','','Iv1.PublicID','https://github.com/apps/test-installer']),patch.object(r.webbrowser,'open',return_value=True) as browser:
                result=r.configure(path,lambda *a:None)
            browser.assert_called_once();self.assertEqual(set(result),{'version','client_id','app_slug'})
            with patch('builtins.input',side_effect=AssertionError('resume must not prompt')),patch.object(r.webbrowser,'open',side_effect=AssertionError('no recreate')):
                self.assertEqual(r.configure(path,lambda *a:None),result)
    def test_cancel_then_existing_never_recreates(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'app.local.json'
            with patch('builtins.input',return_value='cancel'),patch.object(r.webbrowser,'open') as browser:
                with self.assertRaises(SetupError):r.configure(path,lambda *a:None)
                browser.assert_not_called();self.assertFalse(path.exists())
            with patch('builtins.input',side_effect=['existing','Iv1.PublicID','test-installer']),patch.object(r.webbrowser,'open') as browser:
                r.configure(path,lambda *a:None);browser.assert_not_called()
    def test_plan_and_noninteractive_never_prompt_or_open(self):
        for args in [[],['--plan'],['--plan','--run']]:
            with patch.object(cli.sys.stdin,'isatty',return_value=False),patch('builtins.input',side_effect=AssertionError('no prompt')),patch.object(r.webbrowser,'open',side_effect=AssertionError('no browser')):
                self.assertEqual(cli.main(args),0)
    def test_interactive_default_cancel(self):
        with patch.object(cli.sys.stdin,'isatty',return_value=True),patch.object(cli.sys.stdout,'isatty',return_value=True),patch('builtins.input',return_value='n'),patch.object(cli,'configure_public',side_effect=AssertionError('no setup')):
            self.assertEqual(cli.main([]),0)
    def test_interactive_default_enters_configuration(self):
        with patch.object(cli.sys.stdin,'isatty',return_value=True),patch.object(cli.sys.stdout,'isatty',return_value=True),patch('builtins.input',return_value=''),patch.object(cli.shutil,'which',return_value='/trusted/gh'),patch.object(cli,'configure_public',side_effect=SetupError('USER_CANCELLED')) as configure:
            self.assertEqual(cli.main([]),2);configure.assert_called_once()
    def test_trusted_bundle_skips_maintainer_prompts(self):
        import json
        public=r.public_config('Iv1.PublicID','shared-installer')
        with tempfile.TemporaryDirectory() as d,patch.object(r,'read_bundled',return_value=json.dumps(public).encode()),patch('builtins.input',side_effect=AssertionError('no registration prompt')),patch.object(r.webbrowser,'open',side_effect=AssertionError('no browser')):
            self.assertEqual(r.configure(Path(d)/'none.json',lambda *a:None),public)
    def test_build_embeds_only_validated_public_configuration(self):
        import zipfile,json
        from scripts.build_release import build
        with tempfile.TemporaryDirectory() as d:
            build(d,{'client_id':'Iv1.PublicID','app_slug':'shared-installer'})
            with zipfile.ZipFile(Path(d)/'codex-reset-install.pyz') as archive:
                self.assertEqual(json.loads(archive.read('installer/public_app.json'))['app_slug'],'shared-installer')
    def test_unexpected_error_shows_stage_type_without_payload(self):
        import io,contextlib
        stream=io.StringIO();stream.isatty=lambda:True
        with patch.object(cli.sys.stdin,'isatty',return_value=True),patch.object(cli.sys.stdout,'isatty',return_value=True),patch('builtins.input',return_value='Y'),patch.object(cli.shutil,'which',return_value='/trusted/gh'),patch.object(cli,'configure_public',side_effect=OSError('PRIVATE_UPSTREAM_PAYLOAD')),contextlib.redirect_stdout(stream):
            self.assertEqual(cli.main([]),2)
        self.assertIn('阶段=public-app-config',stream.getvalue())
        self.assertIn('类型=OSError',stream.getvalue())
        self.assertNotIn('PRIVATE_UPSTREAM_PAYLOAD',stream.getvalue())

class AppAddressNormalizationTest(unittest.TestCase):
    def test_user_reported_personal_settings_url_and_public_forms(self):
        for value in ('https://github.com/settings/apps/codex-reset-installer',
                      'https://github.com/settings/apps/codex-reset-installer/',
                      'https://github.com/apps/codex-reset-installer',
                      'codex-reset-installer'):
            self.assertEqual(r.public_config('Iv1.PublicID',value)['app_slug'],'codex-reset-installer')
    def test_only_exact_allowed_paths_without_auth_query_or_fragment(self):
        for value in ('https://github.com/settings/apps/',
                      'https://github.com/settings/apps/demo/permissions',
                      'https://github.com/settings/apps/demo?code=private',
                      'https://github.com/settings/apps/demo#private',
                      'https://github.com/settings/apps/%64emo',
                      'https://github.com/user/demo',
                      'https://github.com/organizations/team/settings/apps/demo',
                      'https://github.com/settings/apps/a/../demo',
                      'https://github.com.evil/settings/apps/demo',
                      'https://user@github.com/settings/apps/demo',
                      'https://github.com:443/settings/apps/demo',
                      'http://github.com/settings/apps/demo'):
            with self.subTest(value=value),self.assertRaises(SetupError):r.public_config('Iv1.PublicID',value)

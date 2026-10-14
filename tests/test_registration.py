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
        for key,value in {'contents':'read','actions':'write','secrets':'write','variables':'write','webhook_active':'false','public':'false'}.items():self.assertEqual(query[key],[value])
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

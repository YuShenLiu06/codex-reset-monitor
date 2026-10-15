"""Publication boundary checks against synthetic ZIPs, no account or network."""
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile
from scripts.build_macos import write_app_archive
from scripts.verify_macos_release import APP, PUBLIC_RESOURCE, inspect_archive, validate_public
from installer.core import expected_manifest


class ReleaseArchiveTest(unittest.TestCase):
    def inspect(self,extra_name=None,data=b'',mode=None,public=False,approved=None):
        with tempfile.TemporaryDirectory() as directory:
            archive=Path(directory)/'preview.zip'
            with zipfile.ZipFile(archive,'w') as bundle:
                bundle.writestr(APP+'/Contents/Resources/BUILD-INFO.json',json.dumps({
                    'source_commit':'a'*40,'architecture':'arm64','public_app_bundled':public,'public_app':approved}))
                for name in ('Python.txt','GitHub-CLI.txt'):
                    bundle.writestr(APP+'/Contents/Resources/licenses/'+name,'License text '*20)
                if extra_name:
                    info=zipfile.ZipInfo(extra_name)
                    if mode is not None:info.external_attr=mode<<16
                    bundle.writestr(info,data)
            return inspect_archive(archive,'a'*40,approved)

    def test_rejects_personal_configuration_and_embedded_tokens(self):
        for name in ('public_app.json','state.local.json','.env','config.json'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                self.inspect(APP+'/Contents/Resources/'+name,b'private')
        with self.assertRaises(ValueError):self.inspect(APP+'/Contents/Resources/data',b'ghp_'+b'x'*36)
        with self.assertRaises(ValueError):self.inspect(APP+'/Contents/Resources/data',b'/Users/private-person/private')
        with self.assertRaises(ValueError):self.inspect(public=True)

    def test_rejects_traversal_and_escaping_symlinks(self):
        for path,target in (('../outside',b''),(APP+'/Contents/link',b'../../../outside'),(APP+'/link',b'/outside')):
            with self.subTest(path=path),self.assertRaises(ValueError):
                self.inspect(path,target,stat.S_IFLNK|0o777)

    def test_allows_internal_framework_symlink(self):
        self.inspect(APP+'/Contents/Resources/worker/Python.framework/Versions/Current',b'3.12',stat.S_IFLNK|0o777)

    def test_only_approved_public_fields_are_accepted_at_exact_resource_path(self):
        public={'version':1,'client_id':'Iv1.UnitPublicID','app_slug':'unit-installer'}
        self.inspect(PUBLIC_RESOURCE,json.dumps(public).encode(),public=True,approved=public)
        for extra in ('client_secret','private_key','token'):
            with self.subTest(extra=extra),self.assertRaises(ValueError):
                validate_public(dict(public,**{extra:'do-not-publish'}))
        with self.assertRaises(ValueError):
            self.inspect(PUBLIC_RESOURCE,json.dumps(dict(public,app_slug='another-app')).encode(),public=True,approved=public)
        with self.assertRaises(ValueError):
            self.inspect(APP+'/public_app.json',json.dumps(public).encode(),public=True,approved=public)

    def test_release_workflow_is_in_installers_trusted_manifest(self):
        self.assertIn('.github/workflows/release.yml',expected_manifest(Path(__file__).resolve().parents[1]))

    def test_writer_preserves_utf8_names_links_and_executable_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);app=root/APP;app.mkdir()
            executable=app/'worker';executable.write_bytes(b'synthetic');executable.chmod(0o755)
            (app/'alias').symlink_to('worker')
            archive=root/'app.zip';write_app_archive(app,archive)
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(bundle.read(APP+'/worker'),b'synthetic')
                self.assertTrue(bundle.getinfo(APP+'/worker').flag_bits & 0x800)
                self.assertEqual(bundle.getinfo(APP+'/worker').external_attr>>16 & 0o777,0o755)
                self.assertTrue(stat.S_ISLNK(bundle.getinfo(APP+'/alias').external_attr>>16))
                self.assertEqual(bundle.read(APP+'/alias'),b'worker')

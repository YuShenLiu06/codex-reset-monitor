"""Publication boundary checks against synthetic ZIPs, no account or network."""
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile
from scripts.verify_macos_release import APP, inspect_archive
from installer.core import expected_manifest


class ReleaseArchiveTest(unittest.TestCase):
    def inspect(self,extra_name=None,data=b'',mode=None,public=False):
        with tempfile.TemporaryDirectory() as directory:
            archive=Path(directory)/'preview.zip'
            with zipfile.ZipFile(archive,'w') as bundle:
                bundle.writestr(APP+'/Contents/Resources/BUILD-INFO.json',json.dumps({
                    'source_commit':'a'*40,'architecture':'arm64','public_app_bundled':public}))
                for name in ('Python.txt','GitHub-CLI.txt'):
                    bundle.writestr(APP+'/Contents/Resources/licenses/'+name,'License text '*20)
                if extra_name:
                    info=zipfile.ZipInfo(extra_name)
                    if mode is not None:info.external_attr=mode<<16
                    bundle.writestr(info,data)
            return inspect_archive(archive,'a'*40)

    def test_rejects_personal_configuration_and_embedded_tokens(self):
        for name in ('public_app.json','state.local.json','.env','config.json'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                self.inspect(APP+'/Contents/Resources/'+name,b'private')
        with self.assertRaises(ValueError):self.inspect(APP+'/Contents/Resources/data',b'ghp_'+b'x'*36)
        with self.assertRaises(ValueError):self.inspect(APP+'/Contents/Resources/data',b'/Users/yushen/private')
        with self.assertRaises(ValueError):self.inspect(public=True)

    def test_rejects_traversal_and_escaping_symlinks(self):
        for path,target in (('../outside',b''),(APP+'/Contents/link',b'../../../outside'),(APP+'/link',b'/outside')):
            with self.subTest(path=path),self.assertRaises(ValueError):
                self.inspect(path,target,stat.S_IFLNK|0o777)

    def test_allows_internal_framework_symlink(self):
        self.inspect(APP+'/Contents/Resources/worker/Python.framework/Versions/Current',b'3.12',stat.S_IFLNK|0o777)

    def test_release_workflow_is_in_installers_trusted_manifest(self):
        self.assertIn('.github/workflows/release.yml',expected_manifest(Path(__file__).resolve().parents[1]))

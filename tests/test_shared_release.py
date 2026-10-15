"""Prevent mixing commits, unverified Windows runs or private release payloads."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from scripts.release_assets import (MAC_ZIP, WINDOWS_ZIP, WINDOWS_EXE, WINDOWS_ROOT, MAC_CHECKS,
                                   assemble, check_windows_report, inspect_windows)
from scripts.verify_macos_release import APP, PUBLIC_RESOURCE

SHA='a'*40
PUBLIC={'version':1,'client_id':'Iv1.UnitPublicID','app_slug':'unit-installer'}
LOGO=b'original synthetic logo'
LOGO_SHA=hashlib.sha256(LOGO).hexdigest()


def windows_report(archive_sha):
    modes=[{'mode':mode,'passed':True,'forced':False,'doneCode':0,'finished':mode in ('full','resume'),
            'sawResume':mode=='resume'} for mode in ('startup','full','cancel','resume')]
    from scripts.windows_payload import inspect_exe
    return {'source_commit':SHA,'archive_sha256':archive_sha,'tests':modes}


class SharedReleaseTest(unittest.TestCase):
    def test_rejects_wrong_commit_missing_resume_and_forced_cancellation(self):
        for mutation in ('source','missing','forced','unfinished','no-resume'):
            report=windows_report('f'*64)
            if mutation=='source':report['source_commit']='b'*40
            if mutation=='missing':report['tests'].pop()
            if mutation=='forced':report['tests'][2]['forced']=True
            if mutation=='unfinished':report['tests'][1]['finished']=False
            if mutation=='no-resume':report['tests'][3]['sawResume']=False
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):check_windows_report(report,SHA,'f'*64)

    def windows_zip(self,path,extra=None,public=PUBLIC):
        with zipfile.ZipFile(path,'w') as bundle:
            bundle.writestr(WINDOWS_ROOT+'/BUILD-INFO.json',json.dumps({'source_commit':SHA,'architecture':'x64',
                'public_app':public,'logo_sha256':LOGO_SHA}))
            bundle.writestr(WINDOWS_ROOT+'/ProjectLogo.png',LOGO)
            bundle.writestr(WINDOWS_ROOT+'/worker/_internal/installer/public_app.json',json.dumps(public))
            if extra:bundle.writestr(*extra)

    def test_rejects_private_files_credentials_and_other_app_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'windows.zip'
            for name,data in [('state.local.json',b'private'),('tools/data',b'ghp_'+b'x'*40),('../outside',b'bad')]:
                self.windows_zip(path,(WINDOWS_ROOT+'/'+name,data))
                with self.subTest(name=name),self.assertRaises(ValueError):inspect_windows(path,SHA,PUBLIC,LOGO_SHA)
            self.windows_zip(path,public=dict(PUBLIC,app_slug='another-app'))
            with self.assertRaises(ValueError):inspect_windows(path,SHA,PUBLIC,LOGO_SHA)

    def test_combined_manifest_contains_only_same_source_selected_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);mac=root/'mac';windows=root/'windows';mac.mkdir();windows.mkdir()
            self.windows_zip(windows/WINDOWS_ZIP)
            with zipfile.ZipFile(mac/MAC_ZIP,'w') as bundle:
                bundle.writestr(APP+'/Contents/Resources/BUILD-INFO.json',json.dumps({'source_commit':SHA,
                    'architecture':'arm64','public_app_bundled':True,'public_app':PUBLIC}))
                bundle.writestr(PUBLIC_RESOURCE,json.dumps(PUBLIC))
                for name in ('Python.txt','GitHub-CLI.txt'):
                    bundle.writestr(APP+'/Contents/Resources/licenses/'+name,b'license '*30)
            from scripts.windows_payload import manifest,FOOTER,MAGIC,inspect_exe
            entries=[];packed=bytearray()
            with zipfile.ZipFile(windows/WINDOWS_ZIP) as bundle:
                for name in bundle.namelist():
                    data=bundle.read(name);entries.append((name.removeprefix(WINDOWS_ROOT+'/'),len(data),len(data),len(packed),hashlib.sha256(data).hexdigest()));packed+=data
            metadata=manifest(entries);prefix=b'MZsynthetic-native-launcher'
            (windows/WINDOWS_EXE).write_bytes(prefix+packed+metadata+FOOTER.pack(len(prefix)+len(packed),len(metadata),len(prefix),len(packed),MAGIC))
            for directory,name,sums in ((mac,MAC_ZIP,'SHA256SUMS'),(windows,WINDOWS_EXE,'WINDOWS-SHA256SUMS')):
                value=hashlib.sha256((directory/name).read_bytes()).hexdigest()
                (directory/sums).write_text(value+'  '+name+'\n')
            (mac/'BUILD-CHECKS.json').write_text(json.dumps({'source_commit':SHA,'public_app':PUBLIC,
                'public_app_bundled':True,'logo_sha256':LOGO_SHA,'checks':sorted(MAC_CHECKS)}))
            win_sha=hashlib.sha256((windows/WINDOWS_EXE).read_bytes()).hexdigest()
            report=windows_report(win_sha)
            report.update(asset=WINDOWS_EXE,manifest_sha256=inspect_exe(windows/WINDOWS_EXE)['manifest_sha256'],comparison={'measured':True},
                single_exe_checks=['embedded-files-match-scanned-package','empty-working-directory','Chinese-and-space-paths',
                    'clean-PATH-no-Python-gh-dotnet','first-and-repeat-launch','cache-corruption-repair','prepare-cancel-cleanup-retry',
                    'stale-preparation-cleanup','previous-version-cache-retained','config-and-progress-retained','concurrent-launch',
                    'embedded-manifest-corruption-rejected','reparse-point-rejected','bundled-gh'])
            (windows/'WINDOWS-BUILD-CHECKS.json').write_text(json.dumps(report))
            # Evidence screenshots stay in the workflow artifact, outside public assets.
            (windows/'not-for-release.txt').write_text('synthetic evidence')
            output=root/'release';assets=assemble(mac,windows,output,SHA,PUBLIC,LOGO_SHA,'https://example.test/run')
            self.assertEqual(len(assets),8)
            self.assertFalse((output/'not-for-release.txt').exists())
            self.assertEqual(len((output/'SHA256SUMS').read_text().splitlines()),2)
            manifest=json.loads((output/'RELEASE-MANIFEST.json').read_text())
            self.assertEqual(manifest['source_commit'],SHA)
            self.assertEqual(set(manifest['platforms']),{'macos-arm64','windows-x64'})
            report=json.loads((windows/'WINDOWS-BUILD-CHECKS.json').read_text());report['source_commit']='b'*40
            (windows/'WINDOWS-BUILD-CHECKS.json').write_text(json.dumps(report))
            with self.assertRaises(ValueError):assemble(mac,windows,root/'mixed',SHA,PUBLIC,LOGO_SHA,'run')

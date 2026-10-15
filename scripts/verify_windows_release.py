#!/usr/bin/env python3
"""Extract the actual ZIP and run its actual WPF -> frozen worker integration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile


def pe(path,subsystem=None):
    raw=path.read_bytes();assert raw[:2]==b'MZ',path.name
    offset=struct.unpack_from('<I',raw,60)[0]
    assert raw[offset:offset+4]==b'PE\0\0' and struct.unpack_from('<H',raw,offset+4)[0]==0x8664,path.name
    if subsystem is not None:assert struct.unpack_from('<H',raw,offset+24+68)[0]==subsystem,path.name


def verify(directory,commit):
    assert sys.platform=='win32','Run the actual release on Windows'
    directory=Path(directory).resolve();archive=directory/'codex-reset-installer-windows-x64-preview.zip'
    checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (directory/'WINDOWS-SHA256SUMS').read_text(encoding='utf-8').split()[0]==checksum
    evidence=directory/'windows-evidence';evidence.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='Codex 中文 测试 ') as temporary:
        root=Path(temporary)
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                path=Path(item.filename)
                assert not path.is_absolute() and '..' not in path.parts
                assert not item.filename.endswith(('.pem','.key','.local.json','.pdb','.py','.spec'))
            bundle.extractall(root/'解压 文件')
        package=next((root/'解压 文件').iterdir());info=json.loads((package/'BUILD-INFO.json').read_text(encoding='utf-8'))
        assert info['source_commit']==commit and info['architecture']=='x64'
        assert info['public_app']['app_slug']=='codex-reset-installer'
        assert (package/'licenses/Python.txt').stat().st_size>1000
        assert (package/'licenses/Microsoft-ThirdPartyNotices.txt').stat().st_size>1000
        assert (package/'tools/GitHub-CLI-LICENSE.txt').is_file()
        for name in ('coreclr.dll','hostfxr.dll','hostpolicy.dll','PresentationFramework.dll','System.Private.CoreLib.dll'):
            assert (package/name).is_file(),name
        pe(package/'CodexResetInstaller.exe',2)  # GUI app, no terminal.
        assert b'level="asInvoker"' in (package/'CodexResetInstaller.exe').read_bytes(), 'Must not request administrator'
        pe(package/'worker/reset-worker.exe',3)  # Console worker retains stdin/out.
        pe(package/'tools/gh.exe')
        assert list((package/'worker').rglob('python313.dll'))
        # A clean process has no Python/gh/dotnet on PATH; all runtimes must come
        # from the ZIP. No actual user auth/config/proxy is inherited.
        environment={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP')}
        environment.update(PATH=os.path.join(os.environ['SystemRoot'],'System32'),
                           DOTNET_ROOT=str(root/'no-installed-dotnet'),DOTNET_MULTILEVEL_LOOKUP='0',
                           USERPROFILE=str(root/'空 用户'),APPDATA=str(root/'空 用户'/'Roaming'))
        results=[]
        for mode,profile in [('startup','启动'),('full','完整'),('cancel','恢复'),('resume','恢复')]:
            local=root/profile;local.mkdir(exist_ok=True)
            environment['LOCALAPPDATA']=str(local)
            output=evidence/mode
            result=subprocess.run([str(package/'CodexResetInstaller.exe'),'--offline-ui-test',mode,'--test-output',str(output)],
                                  cwd=root,env=environment,timeout=65,capture_output=True)
            report=json.loads((output/'RESULT.json').read_text(encoding='utf-8')) if (output/'RESULT.json').is_file() else {}
            assert result.returncode==0 and report.get('passed'),(mode,result.returncode,report)
            progress=local/'Codex Reset Installer'/'state.local.json'
            if mode=='startup':assert not progress.exists()
            else:
                state=json.loads(progress.read_text(encoding='utf-8'))
                assert 'synthetic-secret' not in progress.read_text(encoding='utf-8')
                assert 'ghu_' not in progress.read_text(encoding='utf-8')
                if mode=='cancel':assert state['pending']=='secrets' and 'enabled' not in state['completed']
                else:assert 'enabled' in state['completed'] and state['pending'] is None
            results.append(report)
        # Packaged gh starts without a user installation or console shell.
        gh=subprocess.run([str(package/'tools/gh.exe'),'--version'],env=environment,capture_output=True,text=True,timeout=15)
        assert gh.returncode==0 and gh.stdout.startswith('gh version 2.95.0 ')
    checks={'source_commit':commit,'archive_sha256':checksum,'platform':'real Windows x64 runner',
            'tests':results,'limits':['synthetic service boundaries; not live account E2E','unsigned; SmartScreen not evaluated','public GitHub App verified; full cross-account installation not yet tested']}
    (directory/'WINDOWS-BUILD-CHECKS.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    print('Actual ZIP, native WPF, frozen worker, Chinese/space paths, clean PATH, cancel/resume passed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('commit')
    args=parser.parse_args();verify(args.directory,args.commit)

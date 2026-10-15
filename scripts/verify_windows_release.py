#!/usr/bin/env python3
"""Run the final single EXE on Windows, with isolated state and no tool PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.windows_payload import inspect_exe,decompress
from scripts.release_assets import inspect_windows,digest,WINDOWS_ZIP

BASELINE='https://github.com/YuShenLiu06/codex-reset-monitor/releases/download/v0.1.4-preview.1/codex-reset-installer-windows-x64-preview.zip'
BASELINE_SHA='5cb2ba2edfee5b489fc23137a56bc98b348f75f2ef950d18b0e8da1929d85a2f'
EXE='codex-reset-installer-windows-x64-preview.exe'


def pe(path,subsystem=None):
    raw=path.read_bytes();assert raw[:2]==b'MZ',path.name
    offset=struct.unpack_from('<I',raw,60)[0]
    assert raw[offset:offset+4]==b'PE\0\0' and struct.unpack_from('<H',raw,offset+4)[0]==0x8664,path.name
    if subsystem is not None:assert struct.unpack_from('<H',raw,offset+24+68)[0]==subsystem,path.name


def wait_file(path,process,timeout=90):
    deadline=time.monotonic()+timeout
    while not path.is_file():
        assert process.poll() is None,('process ended before marker',process.returncode,str(path))
        if time.monotonic()>deadline:process.kill();raise AssertionError('marker timeout: '+str(path))
        time.sleep(.01)


def ui(executable,mode,output,root,environment):
    began=time.perf_counter()
    process=subprocess.Popen([str(executable),'--offline-ui-test',mode,'--test-output',str(output)],cwd=root,env=environment)
    visible=None
    if executable.name!='CodexResetInstaller.exe':
        wait_file(output/'preparing.png',process)
        visible=round(time.perf_counter()-began,4)
    wait_file(output/'welcome.png',process)
    interactive=time.perf_counter()-began
    code=process.wait(timeout=65)
    report=json.loads((output/'RESULT.json').read_text(encoding='utf-8'))
    assert code==0 and report.get('passed'),(mode,code,report)
    report['interactive_seconds']=round(interactive,4)
    if visible is not None:report['preparation_visible_seconds']=visible
    return report


def bootstrap(executable,mode,output,root,environment,expected=0):
    result=subprocess.run([str(executable),'--bootstrap-test',mode,'--test-output',str(output)],cwd=root,env=environment,timeout=90)
    report=json.loads((output/'BOOTSTRAP.json').read_text())
    assert result.returncode==expected,(mode,result.returncode,report)
    return report



def normal_launch(executable,root,environment):
    import ctypes
    from ctypes import wintypes
    user=ctypes.WinDLL('user32',use_last_error=True)
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    user.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    found=[]
    def visit(handle,unused):
        text=ctypes.create_unicode_buffer(256);user.GetWindowTextW(handle,text,256)
        if text.value=='Codex 提醒设置 · Windows 测试版':found.append(handle)
        return True
    callback=callback_type(visit);user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    process=subprocess.Popen([str(executable)],cwd=root,env=environment)
    deadline=time.monotonic()+90
    try:
        while not found:
            assert process.poll() is None,('normal launch ended',process.returncode)
            user.EnumWindows(callback,0)
            if time.monotonic()>deadline:raise AssertionError('normal window timeout')
            time.sleep(.02)
        user.PostMessageW(found[0],0x0010,0,0)  # Close untouched welcome; no Start click or account access.
        assert process.wait(timeout=15)==0
    finally:
        if process.poll() is None:process.kill()


def verify(directory,commit):
    assert sys.platform=='win32','Run the actual release on Windows'
    directory=Path(directory).resolve();executable=directory/EXE;checksum=digest(executable)
    assert (directory/'WINDOWS-SHA256SUMS').read_text(encoding='utf-8').strip()==checksum+'  '+EXE
    evidence=directory/'windows-evidence';evidence.mkdir(exist_ok=True)
    pe(executable,2);raw=executable.read_bytes();assert b'level="asInvoker"' in raw
    import ctypes
    version=ctypes.WinDLL('version',use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes=[ctypes.c_wchar_p,ctypes.c_void_p]
    version.GetFileVersionInfoW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    version.VerQueryValueW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_uint)]
    length=version.GetFileVersionInfoSizeW(str(executable),None);assert length>0
    info=ctypes.create_string_buffer(length);assert version.GetFileVersionInfoW(str(executable),0,length,info)
    fixed=ctypes.c_void_p();size=ctypes.c_uint()
    assert version.VerQueryValueW(info,'\\',ctypes.byref(fixed),ctypes.byref(size))
    values=struct.unpack('<13I',ctypes.string_at(fixed,52));assert values[0]==0xFEEF04BD and values[2:4]==(1,5<<16)
    payload=inspect_exe(executable)
    public=json.loads((ROOT/'macos/public-app.json').read_text())
    inspect_windows(directory/WINDOWS_ZIP,commit,public,digest(ROOT/'assets/logo-v1.png'))
    inspection=tempfile.TemporaryDirectory(prefix='inspected-payload-')
    with zipfile.ZipFile(directory/WINDOWS_ZIP) as source:source.extractall(inspection.name)
    package=next(Path(inspection.name).iterdir())
    # Compare every embedded/decompressed file with the scanned package, not just a marker.
    with executable.open('rb') as source:
        for name,size,packed,offset,sha in payload['files']:
            source.seek(payload['payload_offset']+offset);data=decompress(source.read(packed),size)
            assert hashlib.sha256(data).hexdigest()==sha==digest(package/name),name
    pe(package/'worker/reset-worker.exe',3);pe(package/'tools/gh.exe')
    assert list((package/'worker').rglob('python313.dll'))
    with tempfile.TemporaryDirectory(prefix='Codex 中文 测试 ') as temporary:
        root=Path(temporary);download=root/'下载 空格';download.mkdir();app=download/'Codex 提醒.exe';shutil.copy2(executable,app)
        empty=root/'空 工作目录';empty.mkdir()
        environment={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP')}
        environment.update(PATH=os.path.join(os.environ['SystemRoot'],'System32'),DOTNET_ROOT=str(root/'no-installed-dotnet'),
            DOTNET_MULTILEVEL_LOOKUP='0',USERPROFILE=str(root/'空 用户'),APPDATA=str(root/'空 用户'/'Roaming'))
        local=root/'当前 用户';local.mkdir();environment['LOCALAPPDATA']=str(local)
        state_dir=local/'Codex Reset Installer';state_dir.mkdir();config=state_dir/'app.local.json'
        config.write_text(json.dumps(public));original_config=config.read_bytes()
        results=[];first=ui(app,'startup',evidence/'startup',empty,environment);results.append(first)
        cold=json.loads((evidence/'startup/BOOTSTRAP.json').read_text());assert not cold['reused']
        cached=next((state_dir/'packages').iterdir());assert (cached/'BUILD-INFO.json').is_file()
        assert json.loads((cached/'BUILD-INFO.json').read_text())['source_commit']==commit
        assert not (state_dir/'state.local.json').exists()
        normal_launch(app,empty,environment)
        assert not (state_dir/'state.local.json').exists()
        warm=[ui(app,'startup',evidence/f'warm-{i}',empty,environment)['interactive_seconds'] for i in range(2)]
        assert all(json.loads((evidence/f'warm-{i}/BOOTSTRAP.json').read_text())['reused'] for i in range(2))
        for mode in ('full','cancel','resume'):
            if mode=='cancel':(state_dir/'state.local.json').unlink()  # Fresh setup, then interrupt shared worker.
            report=ui(app,mode,evidence/mode,empty,environment);results.append(report)
            state=json.loads((state_dir/'state.local.json').read_text())
            assert 'synthetic-secret' not in json.dumps(state) and 'ghu_' not in json.dumps(state)
            if mode=='cancel':assert state['pending']=='secrets' and 'enabled' not in state['completed']
            else:assert 'enabled' in state['completed'] and state['pending'] is None
        progress=(state_dir/'state.local.json').read_bytes()
        assert config.read_bytes()==original_config
        # Damage one cached binary. Same final EXE repairs files, retains config/progress.
        (cached/'worker/reset-worker.exe').write_bytes(b'broken')
        repair=bootstrap(app,'prepare',evidence/'cache-repair',empty,environment)
        assert repair['repaired'] and not repair['reused'] and digest(cached/'worker/reset-worker.exe')==digest(package/'worker/reset-worker.exe')
        assert (state_dir/'state.local.json').read_bytes()==progress and config.read_bytes()==original_config
        # A leftover staging tree is cleaned. Another version's cache is retained.
        sibling=state_dir/'packages'/'previous-version';sibling.mkdir();(sibling/'keep').write_text('old version')
        staging=Path(str(cached)+'.preparing');staging.mkdir();(staging/'partial').write_text('interrupted')
        recovered=bootstrap(app,'prepare',evidence/'stale-preparation',empty,environment)
        assert recovered['reused'] and not staging.exists() and (sibling/'keep').is_file()
        # Cancel extraction, not the worker. No completed cache or staging remains; retry works.
        cancel_local=root/'准备 取消';cancel_local.mkdir();environment['LOCALAPPDATA']=str(cancel_local)
        cancelled=bootstrap(app,'cancel',evidence/'prepare-cancel',empty,environment,130)
        assert cancelled['status']=='cancelled' and not list((cancel_local/'Codex Reset Installer/packages').iterdir())
        bootstrap(app,'prepare',evidence/'prepare-retry',empty,environment)
        # Concurrent launches in the preparation window share one owner. The second exits promptly.
        concurrent=root/'并发 用户';concurrent.mkdir();environment['LOCALAPPDATA']=str(concurrent)
        first_output=evidence/'concurrent-first';second_output=evidence/'concurrent-second'
        process=subprocess.Popen([str(app),'--bootstrap-test','prepare','--test-output',str(first_output)],cwd=empty,env=environment)
        wait_file(first_output/'preparing.png',process)
        second=bootstrap(app,'prepare',second_output,empty,environment,2)
        assert second['status']=='already-open' and process.wait(timeout=90)==0
        # Tampering with the embedded manifest is rejected before extracting or starting code.
        damaged=download/'损坏.exe';shutil.copy2(app,damaged)
        with damaged.open('r+b') as target:
            target.seek(-41,2);value=target.read(1);target.seek(-1,1);target.write(bytes([value[0]^1]))
        bad=bootstrap(damaged,'prepare',evidence/'damaged-exe',empty,environment,1)
        assert bad['status']=='failed'
        # Junctions/symlinks are rejected without following or deleting their targets.
        link_local=root/'链接 用户';link_local.mkdir();external=root/'不可 修改';external.mkdir();sentinel=external/'keep';sentinel.write_text('safe')
        junction=link_local/'Codex Reset Installer'
        result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(junction),str(external)],capture_output=True)
        assert result.returncode==0,result.stderr
        environment['LOCALAPPDATA']=str(link_local)
        link=bootstrap(app,'prepare',evidence/'reparse-rejection',empty,environment,1)
        assert link['status']=='failed' and sentinel.read_text()=='safe' and not (external/'packages').exists()
        junction.rmdir()
        # Use the cached bundled gh with no user install.
        gh=subprocess.run([str(cached/'tools/gh.exe'),'--version'],env=environment,capture_output=True,text=True,timeout=15)
        assert gh.returncode==0 and gh.stdout.startswith('gh version 2.95.0 ')
        # Actual published old ZIP; same machine, path shape, clean environment and UI marker.
        old_zip=root/'old.zip'
        with urllib.request.urlopen(BASELINE,timeout=60) as response,old_zip.open('wb') as target:shutil.copyfileobj(response,target)
        assert digest(old_zip)==BASELINE_SHA
        started=time.perf_counter()
        with zipfile.ZipFile(old_zip) as source:source.extractall(root/'旧版 空格')
        unzip=time.perf_counter()-started;old_app=next((root/'旧版 空格').iterdir())/'CodexResetInstaller.exe'
        old_local=root/'旧 用户';old_local.mkdir();environment['LOCALAPPDATA']=str(old_local)
        old=[ui(old_app,'startup',evidence/f'baseline-{i}',empty,environment)['interactive_seconds'] for i in range(3)]
        ui(old_app,'cancel',evidence/'upgrade-old-cancel',empty,environment)
        upgraded=ui(app,'resume',evidence/'upgrade-new-resume',empty,environment)
        assert upgraded['sawResume'] and upgraded['finished']
        assert json.loads((old_local/'Codex Reset Installer/state.local.json').read_text())['pending'] is None
        comparison={'environment':{'os':platform.platform(),'cpu':platform.processor(),'logical_cpus':os.cpu_count(),
            'runner':os.environ.get('ImageOS'),'image_version':os.environ.get('ImageVersion')},
            'method':'same Windows runner; isolated LOCALAPPDATA; System32-only PATH; launch to welcome.png after loaded interactive WPF welcome; 10ms polling; prepare-window timing includes process launch and capture after visible paint. First run has empty app cache, not a cold OS disk cache. Download time excluded.',
            'single_exe':{'bytes':executable.stat().st_size,'first_visible_seconds':first['preparation_visible_seconds'],
                'first_interactive_seconds':first['interactive_seconds'],'repeat_interactive_seconds':warm},
            'baseline_zip':{'tag':'v0.1.4-preview.1','sha256':BASELINE_SHA,'bytes':old_zip.stat().st_size,
                'unzip_seconds':round(unzip,4),'launch_interactive_seconds':old,
                'first_unzip_plus_launch_seconds':round(unzip+old[0],4)}}
    checks={'source_commit':commit,'archive_sha256':checksum,'asset':EXE,'manifest_sha256':payload['manifest_sha256'],
        'platform':'real Windows x64 standard runner','tests':results,'comparison':comparison,
        'single_exe_checks':['embedded-files-match-scanned-package','normal-no-argument-launch-and-close','empty-working-directory','Chinese-and-space-paths',
            'clean-PATH-no-Python-gh-dotnet','first-and-repeat-launch','cache-corruption-repair','prepare-cancel-cleanup-retry',
            'stale-preparation-cleanup','old-ZIP-to-EXE-progress-resume','previous-version-cache-retained','config-and-progress-retained','concurrent-launch',
            'embedded-manifest-corruption-rejected','reparse-point-rejected','bundled-gh'],
        'limits':['synthetic service boundaries; not live account E2E','unsigned; SmartScreen and antivirus false positives not evaluated',
            'public GitHub App unchanged; cross-account installation not tested','runner timings do not predict user computer speed']}
    (directory/'WINDOWS-BUILD-CHECKS.json').write_text(json.dumps(checks,indent=2),encoding='utf-8')
    inspection.cleanup()
    print(json.dumps(comparison,indent=2));print('Final EXE and affected cache/UI-worker paths passed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('commit')
    args=parser.parse_args();verify(args.directory,args.commit)

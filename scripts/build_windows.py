#!/usr/bin/env python3
"""Build Windows x64 single EXE on Windows only. Never publish, authenticate or send."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.deployment_manifest import build_manifest
from installer.registration import load as load_public


def run(command,**options):
    return subprocess.run([str(v) for v in command],cwd=ROOT,check=True,**options)


def main(deps,output,deployment_commit=None):
    if sys.platform!='win32' or platform.machine().lower() not in ('amd64','x86_64'):
        raise SystemExit('Build on a real standard Windows x64 runner')
    if sys.version_info[:3]!=(3,13,16):raise SystemExit('Use pinned Python 3.13.16 x64')
    deps=Path(deps).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    work=ROOT/'runtime/windows-build';work.mkdir(parents=True,exist_ok=True)
    dotnet=deps/'dotnet/dotnet.exe'
    if run([dotnet,'--version'],capture_output=True,text=True).stdout.strip()!='10.0.402':raise ValueError('SDK version mismatch')
    gh=deps/'tools/gh.exe'
    if not run([gh,'--version'],capture_output=True,text=True).stdout.startswith('gh version 2.95.0 '):raise ValueError('gh version mismatch')
    public=load_public(ROOT/'macos/public-app.json')
    if public is None:raise ValueError('Reviewed public App configuration required')
    reviewed,deployment_commit=build_manifest(ROOT,deployment_commit)
    manifest=work/'trusted_files.json';manifest.write_text(json.dumps(reviewed,sort_keys=True),encoding='utf-8')
    public_file=work/'public_app.json';public_file.write_text(json.dumps(public),encoding='utf-8')
    run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--console',
         '--name','reset-worker','--distpath',work/'dist','--workpath',work/'pyinstaller',
         '--specpath',work,'--paths',ROOT,'--add-data',str(manifest)+':installer',
         '--add-data',str(public_file)+':installer',ROOT/'windows/worker.py'])
    # Resize the approved PNG using Windows' built-in drawing library, then wrap
    # the PNG in a standard 256px ICO. No generated artwork or third-party codec.
    icon_png=work/'icon-256.png'
    environment=dict(os.environ,CODEX_LOGO=str(ROOT/'assets/logo-v1.png'),CODEX_ICON_PNG=str(icon_png))
    run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
         'Add-Type -AssemblyName System.Drawing; $image=[System.Drawing.Image]::FromFile($env:CODEX_LOGO); '
         '$bitmap=New-Object System.Drawing.Bitmap 256,256; $graphics=[System.Drawing.Graphics]::FromImage($bitmap); '
         '$graphics.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic; '
         '$graphics.DrawImage($image,0,0,256,256); $bitmap.Save($env:CODEX_ICON_PNG,[System.Drawing.Imaging.ImageFormat]::Png); '
         '$graphics.Dispose(); $bitmap.Dispose(); $image.Dispose()'],env=environment)
    png=icon_png.read_bytes()
    (ROOT/'windows/app.ico').write_bytes(struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',0,0,0,0,1,32,len(png),22)+png)
    package=work/'Codex 提醒设置 Windows 测试版'
    if package.exists():shutil.rmtree(package)
    run([dotnet,'restore',ROOT/'windows/CodexResetInstaller.csproj','--runtime','win-x64','--locked-mode',
         '--source',deps/'nuget','--packages',work/'nuget-packages'])
    run([dotnet,'publish',ROOT/'windows/CodexResetInstaller.csproj','--configuration','Release',
         '--runtime','win-x64','--self-contained','true','--no-restore','--output',package])
    shutil.copytree(work/'dist/reset-worker',package/'worker')
    shutil.copytree(deps/'tools',package/'tools')
    shutil.copy2(ROOT/'assets/logo-v1.png',package/'ProjectLogo.png')
    shutil.copy2(ROOT/'docs/windows-preview.md',package/'先读我.md')
    licenses=package/'licenses';licenses.mkdir()
    shutil.copy2(deps/'Python-LICENSE.txt',licenses/'Python.txt')
    for name in ('LICENSE.txt','ThirdPartyNotices.txt'):
        source=deps/'dotnet'/name
        if not source.is_file():raise ValueError('Missing Microsoft license notice')
        shutil.copy2(source,licenses/('Microsoft-'+name))
    for package_input in (deps/'nuget').glob('*.nupkg'):
        with zipfile.ZipFile(package_input) as source:
            for name in source.namelist():
                if Path(name).name.lower().startswith(('license','thirdpartynotice','notice')) and not name.endswith('/'):
                    destination=licenses/package_input.stem/Path(name).name
                    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(source.read(name))
    for name in ('truststore','pyinstaller','altgraph','packaging','pyinstaller-hooks-contrib','pefile','pywin32-ctypes','setuptools'):
        distribution=importlib.metadata.distribution(name);found=False
        for item in distribution.files or []:
            if item.name.lower().startswith(('license','copying','notice')):
                source=Path(distribution.locate_file(item))
                if source.is_file():
                    destination=licenses/name/str(item);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination);found=True
        if not found:raise ValueError('Missing license: '+name)
    commit=run(['git','rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()
    info={'purpose':'unsigned Windows x64 preview; no live account E2E','source_commit':commit,'deployment_manifest_commit':deployment_commit,
          'architecture':'x64','python':platform.python_version(),'dotnet_sdk':'10.0.402','dotnet_runtime':'10.0.13',
          'gh':'2.95.0','public_app':public,'logo_sha256':hashlib.sha256((package/'ProjectLogo.png').read_bytes()).hexdigest(),
          'dependencies':json.loads((ROOT/'windows/dependencies.json').read_text(encoding='utf-8'))}
    (package/'BUILD-INFO.json').write_text(json.dumps(info,indent=2),encoding='utf-8')
    archive=output/'codex-reset-installer-windows-x64-preview.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(package.rglob('*')):
            if path.is_file():bundle.write(path,path.relative_to(package.parent).as_posix())
    # Keep the equivalent ZIP only as an internal inspection/comparison artifact.
    from scripts.windows_payload import build_exe
    executable=output/'codex-reset-installer-windows-x64-preview.exe'
    build_exe(package,work,executable,commit)
    (output/'WINDOWS-SHA256SUMS').write_text(hashlib.sha256(executable.read_bytes()).hexdigest()+'  '+executable.name+'\n',encoding='utf-8')
    print(executable)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deps',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--deployment-commit',help='Full reviewed deployment commit for an unpublished installer test')
    args=parser.parse_args();main(args.deps,args.output,args.deployment_commit)

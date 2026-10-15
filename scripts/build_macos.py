#!/usr/bin/env python3
"""Build a local arm64 .app with bundled runtime and gh; no publishing/auth."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from installer.core import expected_manifest
from installer.registration import public_config

ROOT=Path(__file__).resolve().parents[1]

def run(command):
    environment=dict(os.environ,PYINSTALLER_CONFIG_DIR=str(ROOT/'runtime/macos-build/cache'))
    subprocess.run([str(v) for v in command],check=True,cwd=ROOT,env=environment)

def build(output,gh,public=None):
    if sys.platform!='darwin' or os.uname().machine!='arm64':raise SystemExit('This local test build supports macOS arm64 only')
    output=Path(output).resolve();work=ROOT/'runtime/macos-build';work.mkdir(parents=True,exist_ok=True)
    gh=Path(gh).resolve()
    version=subprocess.run([str(gh),'--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    if version!='gh version 2.95.0 (2026-06-17)':raise SystemExit('Expected reviewed gh 2.95.0; review and update before changing the bundled dependency')
    manifest=work/'trusted_files.json';manifest.write_text(json.dumps(expected_manifest(ROOT),sort_keys=True))
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','reset-worker',
        '--distpath',work/'dist','--workpath',work/'pyinstaller','--specpath',work,
        '--paths',ROOT,'--add-data',str(manifest)+':installer',ROOT/'macos/worker.py']
    if public:
        value=public_config(public['client_id'],public['app_slug']);path=work/'public_app.json';path.write_text(json.dumps(value))
        command[-1:-1]=['--add-data',str(path)+':installer']
    run(command)
    app=output/'Codex 提醒设置.app';contents=app/'Contents';resources=contents/'Resources';binary=contents/'MacOS'
    if app.exists():
        with (contents/'Info.plist').open('rb') as handle:previous=plistlib.load(handle)
        if previous.get('CFBundleIdentifier')!='com.yushen.codex-reset-installer.local':raise SystemExit('Refusing to replace an unrelated app')
        shutil.rmtree(app)
    binary.mkdir(parents=True);resources.mkdir()
    shutil.copytree(work/'dist/reset-worker',resources/'worker')
    (resources/'tools').mkdir();shutil.copy2(gh,resources/'tools/gh');(resources/'tools/gh').chmod(0o755)
    licenses=resources/'licenses';licenses.mkdir()
    for name,path in [('GitHub-CLI',gh.parent.parent/'LICENSE'),('Python',Path(sys.base_prefix).parents[3]/'LICENSE')]:
        if path.is_file():shutil.copy2(path,licenses/(name+'.txt'))
    import importlib.metadata
    for name in ('pyinstaller','altgraph','macholib','packaging','pyinstaller-hooks-contrib','setuptools'):
        distribution=importlib.metadata.distribution(name)
        for item in distribution.files or []:
            if item.name.lower().startswith(('license','copying','notice')):
                path=Path(distribution.locate_file(item))
                if path.is_file():
                    destination=licenses/name/str(item);destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
    info={'CFBundleName':'Codex 提醒设置','CFBundleDisplayName':'Codex 提醒设置','CFBundleIdentifier':'com.yushen.codex-reset-installer.local',
          'CFBundleExecutable':'CodexResetInstaller','CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.1',
          'CFBundleVersion':'2','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True}
    with (contents/'Info.plist').open('wb') as handle:plistlib.dump(info,handle)
    run(['swiftc','-parse-as-library','-target','arm64-apple-macos13.0','-module-cache-path',work/'module-cache',
         ROOT/'macos/InstallerApp.swift','-o',binary/'CodexResetInstaller'])
    (resources/'BUILD-INFO.json').write_text(json.dumps({'purpose':'local unsigned-distribution test; ad-hoc signature only',
        'architecture':'arm64','source_commit':subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip(),'python':sys.version.split()[0],'gh':version,'public_app_bundled':public is not None},indent=2))
    # Finder may decorate generated .app/framework folders in Documents. Remove
    # only build-output Finder metadata; never remove quarantine or security attrs.
    subprocess.run(['xattr','-dr','com.apple.FinderInfo',str(app)],check=False,capture_output=True)
    try:run(['codesign','--force','--sign','-',app])
    except subprocess.CalledProcessError:
        # File provider can add FinderInfo during assembly; retry once after
        # clearing only that non-security metadata on the generated bundle.
        subprocess.run(['xattr','-dr','com.apple.FinderInfo',str(app)],check=False,capture_output=True)
        run(['codesign','--force','--sign','-',app])
    # ZIP stores no Finder extended attributes; verify an extracted copy before sharing.
    archive=output/'codex-reset-installer-macos-arm64-local.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(app.rglob('*')):
            if path.is_file():bundle.write(path,path.relative_to(output))
    (output/'SHA256SUMS').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
    print(app)
    return app

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'runtime/macos-build/output')
    parser.add_argument('--gh',required=True,type=Path);parser.add_argument('--client-id');parser.add_argument('--app-slug')
    args=parser.parse_args()
    if bool(args.client_id)!=bool(args.app_slug):parser.error('Public client ID and slug are required together')
    build(args.output,args.gh,{'client_id':args.client_id,'app_slug':args.app_slug} if args.client_id else None)

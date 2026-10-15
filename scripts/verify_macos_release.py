#!/usr/bin/env python3
"""Inspect and extract the ZIP, verify signatures, smoke the frozen worker offline."""
import argparse
import hashlib
import json
import os
import plistlib
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

APP='Codex 提醒设置.app'
ARCHIVE='codex-reset-installer-macos-arm64-preview.zip'
PUBLIC_RESOURCE=APP+'/Contents/Resources/worker/_internal/installer/public_app.json'
FORBIDDEN={'.git','.codex-reset-setup','.env','credentials','secrets','lark-home','__pycache__'}
CREDENTIAL=re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def validate_public(value):
    if (not isinstance(value,dict) or set(value)!={'version','client_id','app_slug'} or type(value['version']) is not int or value['version']!=1
        or not isinstance(value['client_id'],str) or not re.fullmatch(r'Iv[0-9A-Za-z.]{8,126}',value['client_id'])
        or not isinstance(value['app_slug'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}',value['app_slug'])):
        raise ValueError('Expected reviewed public fields only; no credentials')
    return value


def inspect_archive(archive,source_sha,public=None):
    if public is not None:validate_public(public)
    runtime=json.loads((Path(__file__).resolve().parents[1]/'macos/python-runtime-hashes.json').read_text())
    if runtime.get('python')!='3.13.16':raise ValueError('Unexpected reviewed runtime version')
    vendor_prefix=APP+'/Contents/Resources/worker/'
    with zipfile.ZipFile(archive) as bundle:
        names=bundle.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive paths')
        for item in bundle.infolist():
            path=PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=APP:
                raise ValueError('Unexpected archive path')
            if FORBIDDEN.intersection(path.parts) or path.name.endswith(('.local.json','.pem','.key','.log')) or path.name in ('config.json','config.local.json'):
                raise ValueError('Private configuration/cache in package')
            data=bundle.read(item)
            if path.name=='public_app.json':
                if public is None or path.as_posix()!=PUBLIC_RESOURCE or validate_public(json.loads(data))!=public:
                    raise ValueError('Unreviewed public App configuration')
            if stat.S_ISLNK(item.external_attr>>16):
                target=PurePosixPath(data.decode())
                # Resolve lexically without following links outside the extraction.
                parts=list(path.parent.parts)
                if target.is_absolute():raise ValueError('Absolute bundle symlink')
                for part in target.parts:
                    if part=='..':
                        if len(parts)<=1:raise ValueError('Escaping bundle symlink')
                        parts.pop()
                    elif part!='.':parts.append(part)
            elif CREDENTIAL.search(data):raise ValueError('Credential-shaped data in package')
            if re.search(rb'/Users/(?!runner/|Shared/)[A-Za-z0-9_.-]+/',data):
                relative=path.as_posix().removeprefix(vendor_prefix)
                # Official CPython binaries include upstream release-builder paths.
                # Exempt only exact reviewed native-file hashes, never arbitrary paths.
                if not path.as_posix().startswith(vendor_prefix) or runtime['files'].get(relative)!=hashlib.sha256(data).hexdigest():
                    raise ValueError('Unreviewed personal/build path in package file: '+path.as_posix())
        metadata=json.loads(bundle.read(APP+'/Contents/Resources/BUILD-INFO.json'))
        if (metadata.get('source_commit')!=source_sha or metadata.get('architecture')!='arm64'
            or metadata.get('public_app_bundled') is not (public is not None) or metadata.get('public_app')!=public):
            raise ValueError('Build identity/configuration mismatch')
        if public is not None and validate_public(json.loads(bundle.read(PUBLIC_RESOURCE)))!=public:
            raise ValueError('Missing approved public App resource')
        for name in ('Python.txt','GitHub-CLI.txt'):
            if len(bundle.read(APP+'/Contents/Resources/licenses/'+name))<100:
                raise ValueError('Missing runtime license')
    return metadata


def verify(directory,source_sha,public=None):
    directory=Path(directory);archive=directory/ARCHIVE
    expected=hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+ARCHIVE+'\n'
    if (directory/'SHA256SUMS').read_text()!=expected:raise ValueError('Archive checksum mismatch')
    metadata=inspect_archive(archive,source_sha,public)
    with tempfile.TemporaryDirectory(prefix='release-unpack-') as temporary:
        root=Path(temporary)
        subprocess.run(['/usr/bin/ditto','-x','-k',str(archive.resolve()),str(root)],check=True)
        app=root/APP
        subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
        info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
        if info.get('CFBundleIconFile')!='AppIcon.icns':raise ValueError('Bundle icon is not configured')
        logo=Path(__file__).resolve().parents[1]/'assets/logo-v1.png'
        if ((app/'Contents/Resources/ProjectLogo.png').read_bytes()!=logo.read_bytes()
            or metadata.get('logo_sha256')!=hashlib.sha256(logo.read_bytes()).hexdigest()):
            raise ValueError('Original project logo identity mismatch')
        iconset=root/'verified.iconset'
        subprocess.run(['/usr/bin/iconutil','-c','iconset',app/'Contents/Resources/AppIcon.icns','-o',iconset],check=True)
        if not (iconset/'icon_16x16.png').is_file() or not (iconset/'icon_512x512@2x.png').is_file():
            raise ValueError('Missing required small/retina icon sizes')
        for executable in (app/'Contents/MacOS/CodexResetInstaller',app/'Contents/Resources/worker/reset-worker',app/'Contents/Resources/tools/gh'):
            if not os.access(executable,os.X_OK):raise ValueError('Lost executable permission')
            architectures=subprocess.check_output(['lipo','-archs',str(executable)],text=True).strip()
            if architectures!='arm64':raise ValueError('Unexpected executable architecture')
        gh=app/'Contents/Resources/tools/gh'
        version=subprocess.check_output([str(gh),'--version'],text=True).splitlines()[0]
        if version!='gh version 2.95.0 (2026-06-17)':raise ValueError('Bundled gh version mismatch')
        env={key:value for key,value in os.environ.items() if key not in ('GH_TOKEN','GITHUB_TOKEN','PYTHONPATH','PYTHONHOME')}
        env['PATH']=str(gh.parent)+':/usr/bin:/bin'
        icon_check=subprocess.run([str(app/'Contents/MacOS/CodexResetInstaller'),'--check-icon'],env=env,cwd=root,
                                  capture_output=True,text=True,timeout=30)
        if icon_check.returncode or 'application/Dock icon loaded' not in icon_check.stdout:
            raise ValueError('Actual bundled application icon did not load')
        print(icon_check.stdout.strip())
        # macOS sandbox explicitly denies network; no real auth, HOME override or UI.
        result=subprocess.run(['/usr/bin/sandbox-exec','-p','(version 1)(allow default)(deny network*)',
                               str(app/'Contents/Resources/worker/reset-worker'),'--smoke-test'],
                              env=env,cwd=root,capture_output=True,text=True,timeout=45)
        if result.returncode or 'safe cancellation passed' not in result.stdout:
            raise ValueError('Offline frozen worker smoke failed: '+result.stderr[-2000:])
        print(result.stdout.strip())
    (directory/'BUILD-CHECKS.json').write_text(json.dumps(dict(metadata,checks=[
        'archive-allowlist-and-credential-scan','sha256','ditto-unpack','deep-signature-verification',
        'arm64-executable-modes','bundled-gh-version','network-denied-frozen-worker-initialization-cancel',
        'original-logo-icns-and-actual-application-dock-icon-load']),indent=2)+'\n')
    print('Archive scan, extracted signature and offline worker checks passed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('source_sha')
    parser.add_argument('--public-config',type=Path)
    args=parser.parse_args();public=validate_public(json.loads(args.public_config.read_text())) if args.public_config else None
    verify(args.directory,args.source_sha,public)

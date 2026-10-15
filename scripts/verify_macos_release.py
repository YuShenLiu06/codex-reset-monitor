#!/usr/bin/env python3
"""Inspect and extract the ZIP, verify signatures, smoke the frozen worker offline."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

APP='Codex 提醒设置.app'
ARCHIVE='codex-reset-installer-macos-arm64-preview.zip'
FORBIDDEN={'.git','.codex-reset-setup','.env','credentials','secrets','lark-home','__pycache__'}
CREDENTIAL=re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{60,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def inspect_archive(archive,source_sha):
    with zipfile.ZipFile(archive) as bundle:
        names=bundle.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive paths')
        for item in bundle.infolist():
            path=PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!=APP:
                raise ValueError('Unexpected archive path')
            if FORBIDDEN.intersection(path.parts) or path.name.endswith(('.local.json','.pem','.key','.log')) or path.name in ('public_app.json','config.json','config.local.json'):
                raise ValueError('Private configuration/cache in package')
            data=bundle.read(item)
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
            if b'/Users/yushen/' in data:raise ValueError('Personal local path in package')
        metadata=json.loads(bundle.read(APP+'/Contents/Resources/BUILD-INFO.json'))
        if metadata.get('source_commit')!=source_sha or metadata.get('architecture')!='arm64' or metadata.get('public_app_bundled') is not False:
            raise ValueError('Build identity/configuration mismatch')
        for name in ('Python.txt','GitHub-CLI.txt'):
            if len(bundle.read(APP+'/Contents/Resources/licenses/'+name))<100:
                raise ValueError('Missing runtime license')
    return metadata


def verify(directory,source_sha):
    directory=Path(directory);archive=directory/ARCHIVE
    expected=hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+ARCHIVE+'\n'
    if (directory/'SHA256SUMS').read_text()!=expected:raise ValueError('Archive checksum mismatch')
    metadata=inspect_archive(archive,source_sha)
    with tempfile.TemporaryDirectory(prefix='release-unpack-') as temporary:
        root=Path(temporary)
        subprocess.run(['/usr/bin/ditto','-x','-k',str(archive.resolve()),str(root)],check=True)
        app=root/APP
        subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
        for executable in (app/'Contents/MacOS/CodexResetInstaller',app/'Contents/Resources/worker/reset-worker',app/'Contents/Resources/tools/gh'):
            if not os.access(executable,os.X_OK):raise ValueError('Lost executable permission')
            architectures=subprocess.check_output(['lipo','-archs',str(executable)],text=True).strip()
            if architectures!='arm64':raise ValueError('Unexpected executable architecture')
        gh=app/'Contents/Resources/tools/gh'
        version=subprocess.check_output([str(gh),'--version'],text=True).splitlines()[0]
        if version!='gh version 2.95.0 (2026-06-17)':raise ValueError('Bundled gh version mismatch')
        env={key:value for key,value in os.environ.items() if key not in ('GH_TOKEN','GITHUB_TOKEN','PYTHONPATH','PYTHONHOME')}
        env['PATH']=str(gh.parent)+':/usr/bin:/bin'
        # macOS sandbox explicitly denies network; no real auth, HOME override or UI.
        result=subprocess.run(['/usr/bin/sandbox-exec','-p','(version 1)(allow default)(deny network*)',
                               str(app/'Contents/Resources/worker/reset-worker'),'--smoke-test'],
                              env=env,cwd=root,capture_output=True,text=True,timeout=45)
        if result.returncode or 'safe cancellation passed' not in result.stdout:
            raise ValueError('Offline frozen worker smoke failed: '+result.stderr[-2000:])
        print(result.stdout.strip())
    (directory/'BUILD-CHECKS.json').write_text(json.dumps(dict(metadata,checks=[
        'archive-allowlist-and-credential-scan','sha256','ditto-unpack','deep-signature-verification',
        'arm64-executable-modes','bundled-gh-version','network-denied-frozen-worker-initialization-cancel']),indent=2)+'\n')
    print('Archive scan, extracted signature and offline worker checks passed')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('source_sha')
    args=parser.parse_args();verify(args.directory,args.source_sha)

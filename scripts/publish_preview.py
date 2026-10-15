#!/usr/bin/env python3
"""Publish only a new preview release after checking the build identity/hash."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.verify_macos_release import validate_public
from scripts.release_assets import assemble, digest, MAC_ZIP, WINDOWS_EXE

def main(mac_directory,windows_directory):
    tag=os.environ['RELEASE_TAG'];sha=os.environ['GITHUB_SHA'];repository=os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'v\d+\.\d+\.\d+-preview\.\d+',tag) or not re.fullmatch(r'[0-9a-f]{40}',sha):
        raise SystemExit('Invalid preview identity')
    if repository!='YuShenLiu06/codex-reset-monitor':raise SystemExit('Unexpected publishing repository')
    public=validate_public(json.loads(Path('macos/public-app.json').read_text()))
    run_url=f'https://github.com/{repository}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    assets=assemble(mac_directory,windows_directory,Path('runtime/publish-assets'),sha,public,
                    digest('assets/logo-v1.png'),run_url)
    # Refuse overwrites, including tags pointing to another source revision.
    releases=json.loads(subprocess.check_output(['gh','api','--paginate','--slurp',f'repos/{repository}/releases'],text=True))
    if any(release['tag_name']==tag for page in releases for release in page):raise SystemExit('Release already exists; use a new preview version')
    ref=subprocess.run(['gh','api',f'repos/{repository}/git/ref/tags/{tag}'],capture_output=True,text=True)
    if ref.returncode==0:
        # Workflow tags must be lightweight or annotated tags resolved by git.
        target=subprocess.check_output(['git','ls-remote','origin',f'refs/tags/{tag}',f'refs/tags/{tag}^{{}}'],text=True)
        lines=target.strip().splitlines()
        resolved=next((line.split()[0] for line in lines if line.endswith('^{}')),lines[0].split()[0])
        if resolved!=sha:raise SystemExit('Tag points to another commit')
    elif '404' not in ref.stderr:raise SystemExit('Cannot verify release tag')
    # Each version supplies real user-facing changes; never recycle old claims.
    notes=Path('docs/release-notes',tag+'.md').read_text().strip()
    if not notes or len(notes)>5000 or '{{DOWNLOADS}}' not in notes:
        raise SystemExit('Write concise version-specific notes with a DOWNLOADS placeholder')
    download=f'https://github.com/{repository}/releases/download/{tag}/'
    notes=notes.replace('{{DOWNLOADS}}',f'- [Mac Apple 芯片（ZIP）]({download}{MAC_ZIP})\n- [Windows 64 位（单 EXE）]({download}{WINDOWS_EXE})')
    with tempfile.TemporaryDirectory() as temporary:
        body=Path(temporary)/'notes.md';body.write_text(notes)
        subprocess.run(['gh','release','create',tag,'--repo',repository,'--target',sha,
                        '--title',tag+' · Mac / Windows 测试版','--notes-file',str(body),
                        '--prerelease','--draft','--latest=false',
                        *map(str,assets)],check=True)
    subprocess.run(['gh','release','edit',tag,'--repo',repository,'--draft=false','--prerelease','--latest=false'],check=True)


if __name__=='__main__':main(sys.argv[1],sys.argv[2])

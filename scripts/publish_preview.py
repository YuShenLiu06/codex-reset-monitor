#!/usr/bin/env python3
"""Publish only a new preview release after checking the build identity/hash."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from verify_macos_release import validate_public

ARCHIVE='codex-reset-installer-macos-arm64-preview.zip'


def main(directory):
    directory=Path(directory)
    tag=os.environ['RELEASE_TAG'];sha=os.environ['GITHUB_SHA'];repository=os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'v\d+\.\d+\.\d+-preview\.\d+',tag) or not re.fullmatch(r'[0-9a-f]{40}',sha):
        raise SystemExit('Invalid preview identity')
    if repository!='YuShenLiu06/codex-reset-monitor':raise SystemExit('Unexpected publishing repository')
    checksum=hashlib.sha256((directory/ARCHIVE).read_bytes()).hexdigest()+'  '+ARCHIVE+'\n'
    if (directory/'SHA256SUMS').read_text()!=checksum:raise SystemExit('Downloaded artifact checksum mismatch')
    report=json.loads((directory/'BUILD-CHECKS.json').read_text())
    public=validate_public(json.loads(Path('macos/public-app.json').read_text()))
    if (report.get('source_commit')!=sha or report.get('public_app_bundled') is not True
        or report.get('public_app')!=public or len(report.get('checks',[]))!=8):
        raise SystemExit('Missing verified build identity')
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
    if not notes or len(notes)>5000:raise SystemExit('Write concise version-specific user release notes')
    notes+=f'''

<details>
<summary>构建与校验详情（维护者）</summary>

包内含 Python 与 GitHub CLI；仅有 ad-hoc 签名，没有 Developer ID 签名或 Apple 公证。

经所有者授权，内置现有安装器 App 的公开 Client ID / slug，不含凭据。[公开配置](https://github.com/{repository}/blob/{sha}/macos/public-app.json)可审查。公开 API 查询返回 404，现有 CLI 授权不能读取 App 安装记录；Any account、Device Flow 和当前注册权限仍待本人核验。程序保留最小权限检查，不自动扩大安装范围。

[构建和解包检查](https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}) · [对应源码](https://github.com/{repository}/tree/{sha})

下方 SHA256SUMS 用于核对完整性；BUILD-CHECKS.json 记录源码、架构和自动检查项。自动检查不代替真实账号验收。

</details>
'''
    with tempfile.TemporaryDirectory() as temporary:
        body=Path(temporary)/'notes.md';body.write_text(notes)
        subprocess.run(['gh','release','create',tag,'--repo',repository,'--target',sha,
                        '--title',tag+' · Mac Apple 芯片测试版','--notes-file',str(body),
                        '--prerelease','--draft','--latest=false',
                        str(directory/ARCHIVE),str(directory/'SHA256SUMS'),str(directory/'BUILD-CHECKS.json')],check=True)
    subprocess.run(['gh','release','edit',tag,'--repo',repository,'--draft=false','--prerelease','--latest=false'],check=True)


if __name__=='__main__':main(sys.argv[1])

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
    notes=f'''Mac Apple 芯片（arm64）App 所有者预览版 · 未公证

下载 `{ARCHIVE}`，它包含独立 .app、Python 与 GitHub CLI，无需另装 Python/gh。请同时下载 SHA256SUMS 核验完整性。

这是 ad-hoc 签名的开发预览包，**没有 Apple Developer ID 签名、没有 Apple 公证**。macOS 可能阻止打开；遇到系统拦截请暂停，不要关闭系统安全保护。完整真实账号端到端验收尚未完成，不能视为稳定版。

本包经 App 所有者授权，内置 `codex-reset-installer` 的公开 Client ID 和 slug，不含 Secret、私钥或 token。配置可在源码 `macos/public-app.json` 审查，首次使用无需再创建安装器 App。

**当前仅供该 App 所有者测试**：公开 API 查询返回 404，现有 CLI 授权不能读取该 App 安装记录，因此尚未核实 Any account、Device Flow 和当前注册权限。程序仍逐项核验五项最小安装权限（actions/actions_variables/secrets:write、contents/metadata:read）；不扩权、不更改 App 可安装范围。其他账号暂不能视为可用。

预测来自第三方，不是 OpenAI 保证。安装测试消息也不是重置预告。

源码提交：{sha}
构建与解包检查：[Actions run](https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']})。BUILD-CHECKS.json 记录架构、源码与检查项；哈希用于核对完整性，不替代发行者身份认证。
'''
    with tempfile.TemporaryDirectory() as temporary:
        body=Path(temporary)/'notes.md';body.write_text(notes)
        subprocess.run(['gh','release','create',tag,'--repo',repository,'--target',sha,
                        '--title',tag+' · Mac arm64 所有者预览 · 未公证','--notes-file',str(body),
                        '--prerelease','--draft','--latest=false',
                        str(directory/ARCHIVE),str(directory/'SHA256SUMS'),str(directory/'BUILD-CHECKS.json')],check=True)
    subprocess.run(['gh','release','edit',tag,'--repo',repository,'--draft=false','--prerelease','--latest=false'],check=True)


if __name__=='__main__':main(sys.argv[1])

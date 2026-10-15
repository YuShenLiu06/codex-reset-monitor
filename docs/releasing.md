# 用 Actions 发布 Mac 与 Windows 预览版

[返回首页](../README.md) · [macOS 构建设计](macos-installer-plan.md)

维护者先为新版本填写 `docs/release-notes/版本号.md`，只写普通用户能理解的实际变化、使用步骤和当前限制，下载位置保留 `{{DOWNLOADS}}`。随后在 **Actions → Mac and Windows preview release → Run workflow** 选择 `main`，输入该版本号，例如 `v0.1.4-preview.1`。也可向 main 已包含的提交推送同格式 tag。缺少本版说明会在构建前停止；每个版本只发布一次，不覆盖旧资产。技术记录自动放到发布说明末尾的折叠详情。

变更按 **Feat（新增）**、**Bugfix（修复）**、**Change（更改）** 分类，每条用简单中文描述用户能感知的本版变化。没有内容的分类省略，不把历史改动或构建日志当作本次变化。可参考[本版说明](release-notes/v0.1.4-preview.1.md)。

## 自动完成哪些步骤

1. Ubuntu 运行离线测试，核对版本格式与源码属于 main。
2. 同时在标准 `macos-15` arm64 和 `windows-2025` x64 runner 构建，Windows 直接复用 [windows.yml](../.github/workflows/windows.yml)；两平台 checkout 同一提交。
3. Mac 扫描并解压 ZIP，检查签名、架构和执行权限，在断网 sandbox 中运行打包 worker，核对公开配置与安全取消。
4. Mac 从[原始 logo](../assets/logo-v1.png)生成 ICNS，让实际 App 在不显示窗口的模式中验证 application/Dock 图标加载。
5. Windows 解压真实 ZIP，在无预装运行时的环境运行原生界面、完整离线模拟流程、取消及同进度恢复；保存模拟界面证据，不连接真实账号。
6. 两平台全部成功后，发布 job 再核对 ZIP 与报告的源码 SHA、公开 App 配置和原图一致，且 Windows 四模式全部通过、未强杀，才公开共同测试版。只发布明确选择的资产，不发布证据目录或任意构建文件。

标准 `macos-15` 当前为 M1 arm64、3 核、7 GB 内存。[GitHub 官方 runner 表](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)列出该配置；公开仓库使用标准 runner 免费。[计费规则](https://docs.github.com/en/billing/concepts/product-billing/github-actions)不包含免费 larger runner。本 workflow 不使用 larger runner、缓存或付费服务；中转 artifact 仅保留 3 天，并限制为本公开仓库执行。

Windows 使用标准 `windows-2025` x64 runner，同样适用公开仓库标准 runner 免费规则。其固定依赖和测试范围见 [Windows 说明](windows-preview.md)；Windows 中转 artifact 保留 7 天。

## 固定的构建输入

| 输入 | 固定方式 |
|---|---|
| 源码 | 触发 run 的提交；写入 BUILD-INFO / BUILD-CHECKS |
| Python | 3.13.16，官方 setup-python，固定 action SHA |
| PyInstaller 与依赖 | [requirements](../macos/build-requirements.txt)锁版本与 wheel SHA256；只安装二进制 wheel |
| GitHub CLI | 官方 2.95.0 arm64 ZIP，代码内固定 SHA256 |
| Python 许可证 | 官方 CPython v3.13.16 文本，固定 SHA256 |
| Actions | checkout、setup-python、upload/download-artifact 固定完整 commit SHA |

这是固定输入、可重复执行的构建流程，不承诺 ZIP 字节完全一致。GitHub runner 镜像、Xcode/Swift 工具链和构建时间仍会变化；首次构建结果与 worker 检查以对应 run 为准。

## 公开包的边界

Actions 从干净 checkout 构建，通过 `--public-config macos/public-app.json` 读取已提交并可审查的公开标识，不读取个人机器。App 内加入生成的可信文件清单和经所有者授权的[公开配置](../macos/public-app.json)，只允许 `version/client_id/app_slug` 三项；拒绝 Secret、私钥、token、其他配置、本地进度与账号截图。扫描用于拦截常见泄漏，不能证明任意形式的秘密都不存在。

官方 CPython 原生运行时含上游发布者的编译路径。仅对[逐文件固定 SHA256](../macos/python-runtime-hashes.json)完全匹配的官方二进制保留该公开元数据，项目数据与其他文件不豁免。运行时升级或字节变化会要求重新审查。

两平台复用所有者明确授权的 `codex-reset-installer`，首次无需重复创建 App。只读公开 API 现已核对 Client ID、slug 和五项权限一致；该接口不返回 Device Flow 设置，跨账号完整安装仍待实际验收。程序继续检查实际安装范围及 actions/actions_variables/secrets:write、contents/metadata:read，不扩权。更改 App 可安装范围需所有者另行确认。缺配置的开发构建仍保留初始化引导，见[注册与权限说明](github-app-maintainer.md)。

Mac 包只有 ad-hoc 签名，没有 Developer ID 和 Apple 公证；Windows 包尚未签名。系统可能拦截，保留系统安全保护。离线模拟和自动检查不能替代真实 GitHub/飞书账号的[完整验收](user-acceptance.md)。

## 同一发布页的资产

两份 ZIP 分别配 `MACOS-SHA256SUMS` / `WINDOWS-SHA256SUMS` 和平台报告；共同 `SHA256SUMS` 包含两份 ZIP，`RELEASE-MANIFEST.json` 记录共同源码及全部选定资产的哈希。发布说明自动提供两个直接下载入口，旧 Release 及其资产保留。

测试和构建 job 只有 `contents: read`；发布 job 才有 `contents: write`，使用本次临时 GITHUB_TOKEN，无新凭据。发布先建 draft，资产上传成功后才公开；若发布阶段失败留下 draft，核对对应 run 后处理，不覆盖既有预览版本。

# 用 Actions 发布 Mac 预览版

[返回首页](../README.md) · [macOS 构建设计](macos-installer-plan.md)

维护者可在 **Actions → macOS preview release → Run workflow** 选择 `main`，输入新的版本号，例如 `v0.1.3-preview.1`。也可向 main 已包含的提交推送同格式 tag。每个版本只发布一次，不覆盖旧资产。

## 自动完成哪些步骤

1. Ubuntu 运行离线测试，核对版本格式与源码属于 main。
2. 标准 `macos-15` arm64 runner 测试原生界面协议，构建自包含 `.app`。
3. 扫描 ZIP，检查私有配置、常见凭据格式与个人路径；解压后核验签名、架构和执行权限。
4. 在禁止网络的 sandbox 中启动打包后的 worker，验证公开配置载入与安全取消，不打开窗口或连接真实账号。
5. 由原始[项目 logo](../assets/logo-v1.png)只做系统尺寸转换生成 ICNS，检查 bundle 图标配置，并让真实 App 在不显示窗口的模式加载 application/Dock 图标。
6. 全部通过后，用独立发布 job 创建标为 **Pre-release、未公证** 的 GitHub Release，附 ZIP、SHA256SUMS 和 BUILD-CHECKS.json。

标准 `macos-15` 当前为 M1 arm64、3 核、7 GB 内存。[GitHub 官方 runner 表](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)列出该配置；公开仓库使用标准 runner 免费。[计费规则](https://docs.github.com/en/billing/concepts/product-billing/github-actions)不包含免费 larger runner。本 workflow 不使用 larger runner、缓存或付费服务；中转 artifact 仅保留 3 天，并限制为本公开仓库执行。

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

当前包复用所有者明确授权的 `codex-reset-installer`，首次无需重复创建 App。只读公开 API 返回 404，现有 CLI 授权不能读取 App 安装记录；Any account、Device Flow 和当前注册权限尚未核实，所以本包定位为 **App 所有者预览**，不宣称所有账号可安装。实际使用仍核验 actions/actions_variables/secrets:write、contents/metadata:read，不扩权。更改 App 可安装范围需所有者另行确认。缺配置的开发构建仍保留初始化引导，见[注册与权限说明](github-app-maintainer.md)。

包只有 ad-hoc 签名，没有 Developer ID 和 Apple 公证。系统可能拦截；不要关闭 Gatekeeper。构建、解包和离线 worker smoke 不能替代真实 GitHub/飞书账号的[完整验收](user-acceptance.md)。

测试和构建 job 只有 `contents: read`；发布 job 才有 `contents: write`，使用本次临时 GITHUB_TOKEN，无新凭据。发布先建 draft，资产上传成功后才公开；若发布阶段失败留下 draft，核对对应 run 后处理，不覆盖既有预览版本。

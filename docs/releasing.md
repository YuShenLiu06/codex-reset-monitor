# 用 Actions 发布 Mac 预览版

[返回首页](../README.md) · [macOS 构建设计](macos-installer-plan.md)

维护者可在 **Actions → macOS preview release → Run workflow** 选择 `main`，输入新的版本号，例如 `v0.1.3-preview.1`。也可向 main 已包含的提交推送同格式 tag。每个版本只发布一次，不覆盖旧资产。

## 自动完成哪些步骤

1. Ubuntu 运行离线测试，核对版本格式与源码属于 main。
2. 标准 `macos-15` arm64 runner 测试原生界面协议，构建自包含 `.app`。
3. 扫描 ZIP，检查私有配置、常见凭据格式与个人路径；解压后核验签名、架构和执行权限。
4. 在禁止网络的 sandbox 中启动打包后的 worker，验证缺 App 配置的初始化和取消，不打开窗口或连接真实账号。
5. 全部通过后，用独立发布 job 创建标为 **Pre-release、未公证** 的 GitHub Release，附 ZIP、SHA256SUMS 和 BUILD-CHECKS.json。

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

Actions 从干净 checkout 构建，命令不传 `--client-id` / `--app-slug`，不读取个人机器。App 内只加入生成的可信文件清单；禁止捆绑 `public_app.json`、本地进度、配置、账号截图或密钥。扫描用于拦截常见泄漏，不能证明任意形式的秘密都不存在。

公开包缺 App 配置时显示现有初始化引导，由维护者按[注册与权限说明](github-app-maintainer.md)操作。共享 App 配置尚未公开，因此不能承诺普通用户只授权就能安装完成。未来内置共享配置需单独审查，不从用户个人 App 自动复制。

包只有 ad-hoc 签名，没有 Developer ID 和 Apple 公证。系统可能拦截；不要关闭 Gatekeeper。构建、解包和离线 worker smoke 不能替代真实 GitHub/飞书账号的[完整验收](user-acceptance.md)。

测试和构建 job 只有 `contents: read`；发布 job 才有 `contents: write`，使用本次临时 GITHUB_TOKEN，无新凭据。发布先建 draft，资产上传成功后才公开；若发布阶段失败留下 draft，核对对应 run 后处理，不覆盖既有预览版本。

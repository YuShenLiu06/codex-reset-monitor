# 维护者首次创建安装器 GitHub App

本项目尚未创建真实安装器 App。以下步骤只需维护者完成一次，不是每位安装用户都创建 App；终端安装器仅使用公开 client_id 和 App slug。

## 最少注册步骤

1. 以 App 所有者账号打开 [GitHub New App](https://github.com/settings/apps/new)（组织 App 可从组织设置进入 Developer settings → GitHub Apps）。填写全球唯一、最长 34 字符的名称，例如 `Codex Reset Installer`，以及 Homepage URL `https://github.com/YuShenLiu06/codex-reset-monitor`。名称示例是否可用须由 GitHub 确认。
2. 保留 **Expire user authorization tokens**，勾选 **Enable Device Flow**。不勾选安装时 OAuth 请求，不配置 Callback URL 或 Setup URL；device flow 不使用 callback，不需要长期 OAuth 服务。
3. 取消 Webhook 的 **Active**；不配置 Webhook URL/Secret、不订阅事件。本安装器不处理 webhook。
4. Repository permissions 仅设置：**Actions: Read & write、Secrets: Read & write、Variables: Read & write、Contents: Read-only**。Metadata 为 GitHub 自动必需的 Read-only。其他仓库权限、组织权限及用户权限均不申请；不申请 Administration、Workflows 或 Contents 写入。
5. 若供其他账号使用，安装范围选 **Any account**（公开 App）；仅自己测试可选 **Only on this account**，但不能将它宣称为所有用户可安装。维护者本人核对所有字段后点击 Create GitHub App。
6. 从 App 设置页面取得公开 **Client ID**（不是数字 App ID），从 `https://github.com/apps/APP_SLUG` 取得 slug，写入可信发布说明。无需生成/分发 client_secret 或 App 私钥；若使用 manifest flow 产生了这些字段，仍不得放入 Release、Skill、代码或日志。

实际安装用户仍须在官方 App 安装页面选择 **Only select repositories**，仅勾选目标仓库，再进行 device 授权。组织策略或 Actions 关闭时由用户/管理员在官方页面处理，安装器不能扩权绕过。

## 预填 manifest 能做什么

[app-manifest.template.json](app-manifest.template.json) 和 [app-manifest-preview.html](app-manifest-preview.html) 提供仅代码的权限预填草稿；页面不会提交表单或自动创建 App。它可用于审查和复制 manifest，当前推荐以上手工注册步骤。

GitHub 官方 manifest flow 可以向 `https://github.com/settings/apps/new` POST `manifest` JSON，但完整流程还需要控制回调、核验 state、在一小时内兑换临时 code；兑换结果包含 App 私钥、client_secret、Webhook Secret 等敏感字段。一个静态预填页面不能诚实地宣称已经完成该闭环，本项目不为它建立凭据托管服务。

当前官方 manifest 参数表未提供 Enable Device Flow 字段，因此即使未来实现安全的 manifest 回调，维护者仍需在真实 App 设置页面启用 device flow，并保持用户 token 过期。无需为本地 device 安装器引入共享 client_secret。

官方依据：[注册 GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)、[manifest flow 与参数表](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest)、[device flow](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app#using-the-device-flow-to-generate-a-user-access-token)。（字段与权限应在提交前再次核对官方页面。）

安装器现在提供首次交互引导并打开[官方支持的 URL 预填注册页](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-using-url-parameters)，权限使用 contents=read、actions=write、secrets=write、variables=write。Device Flow 与 token 过期仍需本人核对。只保存公开 Client ID/slug，无 manifest code 交换、私钥或 client secret 采集。

真实共享 App 创建后，维护者可用 build_release.py 的 --client-id 与 --app-slug 将已审查的公开两项内置进发行包；普通用户无需填写它们，也不会进入 App 注册向导。包内配置优先于本机旧配置，实际安装权限仍由 GitHub API 校验。当前包未内置虚假配置。

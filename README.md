<p align="center"><img src="assets/logo-v1.png" width="112" alt="Codex 提前重置提醒：重置箭头与铃铛"></p>

# Codex 提前重置提醒

**Codex 额度可能提前重置时，给你发一条飞书提醒。**

预测来自第三方，**不是 OpenAI 的保证**。收到提醒，也不代表你的额度已经恢复。预测也包含备用重置额度的发放，仍需你去 Codex 查看。

[开始](#start) · [使用](#use) · [求助](#help)

## 这是什么

像一个“小闹钟”：帮你留意 Codex 有没有可能提前恢复额度。

- 每小时看一次[第三方预测](https://codex.gussuriworks.com/api/current)。
- 数据正常、够新，且未来 24 小时的概率**超过 80%**，才提醒。
- 同一轮通常只提醒一次，避免重复打扰。

通过窗口安装器设置后，提醒发给你自己的飞书账号。它不会帮你重置额度，也不需要你提供 OpenAI 密码。

<a id="start"></a>

## 怎么下载／开始

**已有开发预览包：[下载 Mac Apple 芯片版（ZIP）](https://github.com/YuShenLiu06/codex-reset-monitor/releases/download/v0.1.3-preview.1/codex-reset-installer-macos-arm64-preview.zip)**。版本限制与校验文件见[发布页](https://github.com/YuShenLiu06/codex-reset-monitor/releases/tag/v0.1.3-preview.1)。GitHub 的「Download ZIP」是源码，不是安装包。

适用于 **Mac Apple 芯片（arm64）**，内置 Python / gh。它没有 Developer ID 签名或 Apple 公证，完整真实流程仍待验收，可能被系统拦截。当前版本缺 App 配置，需要维护者首次初始化；普通用户请等待完整发行版。Intel Mac、Windows 暂无安装包。

预览版开始方式（先完成维护者配置，详见[验收清单](docs/user-acceptance.md)）：

```mermaid
flowchart TD
    A[① 从 Releases 下载安装包] -->|解压后| B[② 双击打开]
    B -->|跟着窗口提示| C[③ 在 GitHub 和飞书官方页面授权]
    C -->|收到安装测试消息后| D[④ 确认收到，再开启提醒]
```

正在参与本机测试？使用维护者交给你的测试包，按[本人验收清单](docs/user-acceptance.md)逐步核对。不要把测试包当正式版转发。

<a id="use"></a>

## 怎么用

安装并确认开启后，提醒任务会在 GitHub 上定时运行。你平时看飞书消息就好。

| 看到什么 | 你要做什么 |
|---|---|
| “安装测试”消息 | 核对窗口中的标识；它只是测试，不是重置预告。 |
| 概率提醒 | 把它当参考，去 Codex 查看自己的实际额度。 |
| 一直没有消息 | 可能没达到提醒条件；也可能任务停了，去你自己的仓库 Actions 页面看最近运行。 |

定时任务可能延迟或漏跑。建议每月检查一次 Actions，确认它还在运行。

想停止提醒：在你自己的仓库 **Actions → Hourly monitor → ⋯ → Disable workflow** 停用工作流。已经开始的任务需另行取消。熟悉终端的用户也可看[关闭与恢复说明](docs/operations.md)。

<a id="help"></a>

## 卡住怎么办

| 问题 | 下一步 |
|---|---|
| 下载后找不到 .app | 解压上方预览版 ZIP；不要把源码 ZIP 当安装包。 |
| Mac 拦住测试包 | 暂停，向维护者确认包的来源与状态；不要关闭系统安全保护。 |
| 窗口要求填 Client ID 或 App 地址 | 测试包缺少公开配置，联系维护者；普通用户不必自行注册安装器 App。 |
| 授权或权限检查没通过 | 按窗口提示核对目标账号、仓库和权限；详细见[验收与恢复](docs/user-acceptance.md#失败恢复)。 |
| 测试消息没收到／结果不明 | 先停下，核对飞书和 Actions 记录，不要反复点击发送。 |

反馈时只提供失败阶段、错误码和脱敏后的说明。**不要发送密码、密钥、授权码或完整账号截图。** 可在 [Issues](https://github.com/YuShenLiu06/codex-reset-monitor/issues)反馈。

<details>
<summary>维护者与高级用户文档</summary>

- [GitHub App 注册与权限](docs/github-app-maintainer.md)
- [用 Actions 构建与发布](docs/releasing.md)
- [macOS 构建方案与当前测试状态](docs/macos-installer-plan.md)
- [安装器授权、安全与打包审查](docs/installer-review.md)
- [手动部署、去重机制与故障恢复](docs/operations.md)
- [本人验收清单](docs/user-acceptance.md)

</details>

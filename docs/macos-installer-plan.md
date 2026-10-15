# macOS 双击版最小实施选择

当前已有 Actions 构建的开发预览包，下载见[首页](../README.md#start)，构建与核验见[发布流程](releasing.md)。新包含用户授权的公开 App 标识和项目图标；App 可安装范围尚待本人核验。没有 Apple 公证，完整真实流程仍待验收。下文保留实施与本机测试记录。

选择：SwiftUI 薄窗口 + 复用 installer/core.py 的本地工作进程，`.app` 内打包受支持 Python、固定版本 gh 及可信公开 App 配置。图形入口以按钮/字段取代 CLI input，不模拟键盘输入或解析终端输出。授权仍打开官方页面；后台任务结果通过固定消息协议返回。现有仓库、权限、去重与验收检查保留。

用户路径：下载 Release → 解压双击 → 官方授权/必要配置 → 点击测试 → 确认收到并开启。包内已包含所有者授权的公开 App 配置；缺配置的开发包才显示初始化页。无需用户安装 Python/gh，无 sudo，不写系统目录。SwiftUI 窗口及 stdin/stdout 适配已实现，当前为开发预览。GUI 复用相同安装核心；格式输入可原页纠正，暂停后按保存进度恢复，未知提交须本人核对。

构建依赖：macOS Swift/Command Line Tools、本机受支持 Python、固定版本 PyInstaller（打包运行时）、固定版本官方 gh 分发包。本机为 arm64，已有 Swift/CLT 与 gh，默认系统 Python 3.9.6，无 PyInstaller。项目隔离构建环境已配置 Python 3.12.13 与固定版本依赖，包内 gh 为 2.95.0。为降低变量先出 arm64；Intel 需独立架构构建/实测，不能把 arm64 包称 universal2。

发行限制：Developer ID 签名、hardened runtime、Apple notarization/stapling 应由维护者在其受控环境完成；本次未读取/创建签名身份或苹果凭据。PyInstaller 的 ad-hoc 签名不能替代 Developer ID 和公证。未签名本地工程不能宣称已达到公开 Release 下载双击体验，不指导关闭 Gatekeeper。还需审计打包运行时和 gh 的许可证、校验下载、确认所有嵌套可执行文件签名。

优先完成当前启动 bug 和实际包回归，再实现 GUI 适配层；不重写远端部署架构，不引入常驻服务或共享敏感凭据。

依据：[PyInstaller macOS .app 与架构/签名参数](https://pyinstaller.org/en/stable/usage.html)、[Apple 软件公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)。

## 2026-10-15 本机测试状态

真实 GUI 安全启动到首次配置页；完整模拟服务及消息协议测试通过，不代表真实账户端到端已验收。用户真实输入个人应用设置页 `https://github.com/settings/apps/codex-reset-installer` 曾触发终止错误；源码现精确接受该路径并显示转换后的公开应用地址，再继续安装/权限核验。组织设置页和带 query/fragment 的地址不接受。非法输入留在当前页，可重填或返回；不回显无效输入以避免误粘贴密钥。

当前公开 client ID / slug 配置未随包发布。用户本人可能已在官方创建应用，本项目没有替其注册或读取私人配置；不得把“包内缺少公开配置”等同“用户没有创建”。源码提交推送并与目标仓库匹配之前，新包的可信清单核验会拒绝旧版本仓库。

新测试包独立放于 `runtime/macos-build/versions/20261015-settings-url-fix/`，不覆盖用户当前 `output/` 包。没有启动新包的 GUI，不抢当前窗口焦点。公开发行还缺 Developer ID 签名、公证和本人真实端到端验收。ZIP 和 SHA256SUMS 是本地测试产物，不是正式 Release。

## 0.1.2 界面完善

用户已报告 0.1.1 真实安装跑通；本地非敏感进度记录完成至 enabled。0.1.2 增加内置 AppIcon.icns 和 Dock 图标、当前 GitHub 授权码的复制按钮，以及新建飞书提醒机器人前的名称输入（默认“Codex 提醒”）。名称通过[官方 SDK 的 appPreset.name](https://github.com/larksuite/node-sdk/blob/main/scene/registration/index.ts)独立 `name` 查询参数预填到官方创建页，用户仍可在那里修改；复用已有应用不传名称，也不改名。安装器输入限制为 1–20 个字符，不代表平台最大长度。

授权码只在当前界面提供复制，进入下一步、暂停、出错和结束时移除；不记录凭据或读取系统剪贴板。原生复制回归使用合成码和独立私有剪贴板。图标由仓库内 AppKit 绘制代码生成，构建时转为标准多分辨率 icns。本地测试包仍只有 ad-hoc 签名，未公证。

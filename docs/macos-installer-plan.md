# macOS 双击版最小实施选择

选择：SwiftUI 薄窗口 + 复用 installer/core.py 的本地工作进程，`.app` 内打包受支持 Python、固定版本 gh 及可信公开 App 配置。图形入口以按钮/字段取代 CLI input，不模拟键盘输入或解析终端输出。授权仍打开官方页面；后台任务结果通过固定消息协议返回。现有仓库、权限、去重与验收检查保留。

用户路径：下载 Release → 解压双击 → 官方授权/必要配置 → 点击测试 → 确认收到并开启。普通用户包必须内置维护者公开 App 配置；缺配置才显示维护者初始化页。无需用户安装 Python/gh，无 sudo，不写系统目录。SwiftUI 窗口及 stdin/stdout 适配已实现，属于本机开发测试版，尚未发布。GUI 复用相同安装核心；格式输入可原页纠正，暂停后按保存进度恢复，未知提交须本人核对。

构建依赖：macOS Swift/Command Line Tools、本机受支持 Python、固定版本 PyInstaller（打包运行时）、固定版本官方 gh 分发包。本机为 arm64，已有 Swift/CLT 与 gh，默认系统 Python 3.9.6，无 PyInstaller。项目隔离构建环境已配置 Python 3.12.13 与固定版本依赖，包内 gh 为 2.95.0。为降低变量先出 arm64；Intel 需独立架构构建/实测，不能把 arm64 包称 universal2。

发行限制：Developer ID 签名、hardened runtime、Apple notarization/stapling 应由维护者在其受控环境完成；本次未读取/创建签名身份或苹果凭据。PyInstaller 的 ad-hoc 签名不能替代 Developer ID 和公证。未签名本地工程不能宣称已达到公开 Release 下载双击体验，不指导关闭 Gatekeeper。还需审计打包运行时和 gh 的许可证、校验下载、确认所有嵌套可执行文件签名。

优先完成当前启动 bug 和实际包回归，再实现 GUI 适配层；不重写远端部署架构，不引入常驻服务或共享敏感凭据。

依据：[PyInstaller macOS .app 与架构/签名参数](https://pyinstaller.org/en/stable/usage.html)、[Apple 软件公证](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)。

## 2026-10-15 本机测试状态

真实 GUI 安全启动到首次配置页；完整模拟服务及消息协议测试通过，不代表真实账户端到端已验收。用户真实输入个人应用设置页 `https://github.com/settings/apps/codex-reset-installer` 曾触发终止错误；源码现精确接受该路径并显示转换后的公开应用地址，再继续安装/权限核验。组织设置页和带 query/fragment 的地址不接受。非法输入留在当前页，可重填或返回；不回显无效输入以避免误粘贴密钥。

当前公开 client ID / slug 配置未随包发布。用户本人可能已在官方创建应用，本项目没有替其注册或读取私人配置；不得把“包内缺少公开配置”等同“用户没有创建”。源码提交推送并与目标仓库匹配之前，新包的可信清单核验会拒绝旧版本仓库。

新测试包独立放于 `runtime/macos-build/versions/20261015-settings-url-fix/`，不覆盖用户当前 `output/` 包。没有启动新包的 GUI，不抢当前窗口焦点。公开发行还缺 Developer ID 签名、公证和本人真实端到端验收。ZIP 和 SHA256SUMS 是本地测试产物，不是正式 Release。

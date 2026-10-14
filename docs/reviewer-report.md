# 发布前独立审查记录

独立 reviewer 完成只读 diff/新文件审查，并两次运行离线测试。第一轮 116 项成功，未发现直接凭据泄漏、自动误发、覆盖源码或清空旧 ledger 的阻塞问题；提出三项改善。作者修复后，reviewer 复核 120 项测试成功，确认无新增阻塞级问题，可提交审查后源码。

已修复：

- 显示并要求用户确认当前 GitHub login；非敏感进度绑定 numeric user ID，换账号续接停止。
- 延迟导入 fcntl，live 安装器明确 macOS/Linux；离线计划不依赖 POSIX 锁模块。
- 明确失败 workflow 经用户确认可重新 prepare/安全 preview，验证原 run 身份并保留旧 run 历史，不能盲目重试未知操作。
- 额外拒绝轮询请求完成时已经过期的 device session token。

关键安全测试结论：

| 范围 | 自动化结果 |
|---|---|
| GitHub/飞书 device flow | 待授权、降速、拒绝、过期、缺真实 client_id 安全停止；不保存 refresh token。 |
| 身份与仓库 | 本人身份确认、续接用户 ID 绑定、仅目标仓库、权限过宽拒绝、同名来源/代码不符拒绝。 |
| Secrets | stdin 上传、空临时 HOME/config、无明文凭据文件、原始 stderr/HTTP 错误脱敏。 |
| 防重与续接 | 远端 pending 先写；未知发送阻塞；Secret/dispatch 未确认需人工核对；旧 webhook 去重保留。 |
| workflow | 初始化显式 gating、无飞书凭据、保留 ledger；preview 绑定 SHA/事件/关联 ID和成功 job；不自动启用通知。 |
| 包和 Skill | 显式构建 allowlist、无 runtime/配置、同字节 pyz；隔离 Python 且禁用 site-packages 可运行默认计划；缺 gh 在授权前停止。 |

未验证真实授权、真实 Secrets 上传、组织策略、飞书实际送达或长期调度。真实安装器 GitHub App 尚未注册，不能发布宣称开箱可用的 Release。

闭环追加审查：reviewer 在 132 项测试通过后发现替换 Secrets 可沿用旧验收资格。已修复为配置上传前失效本地测试资格、归档旧 run、保留远端 ledger，并新增回归测试。测试阶段文案与开启回执丢失后的实际门控显示亦修正；真实授权、发送与开启未执行。

默认交互安装入口追加：140 项离线测试覆盖默认开始/取消、非交互与 --plan、首次浏览器引导、公开配置验证及续接。官方预填 URL 不含臆造 device flow 参数，不采集私钥或 secret；用途默认仅本人，公开用途须显式选择。

真实启动回归：Mac 默认 /usr/bin/python3 为 3.9.6，主目录 cwd 下 zipimport 对缺失可选资源抛 OSError，而旧代码只捕获 FileNotFoundError。现改用 zip 中央目录确认存在后读取，损坏归档不当作“无配置”。实际 PTY 输入 Y → 新建 → 用途提示成功，随后 cancel，未打开注册页且主目录未创建配置。补入真正打包后 subprocess 与 PTY 测试，不以资源加载 mock 替代。意外错误仅显示固定阶段和异常类名，不显示响应或异常正文。

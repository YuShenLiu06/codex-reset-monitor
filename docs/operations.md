# 手动部署与运行参考

[返回首页](../README.md) · 普通用户先看[开始方式](../README.md#start)。

本页供维护者和熟悉终端的用户使用，保留群 Webhook 部署、状态恢复和服务器运行细节。窗口安装器的权限与打包见[安装器审查稿](installer-review.md)。

## 手动部署：GitHub Actions

这是按时启动、运行后退出的脚本任务，不是常驻云电脑。适合容忍延迟的轻量提醒；无需购买服务器或开通付费服务。

1. 在你要接收消息的**飞书群**中，打开「设置 → 群机器人 → 添加机器人 → 自定义机器人」。开启**签名校验**并保存，取得 Webhook 和签名密钥。自定义机器人只向所在群发送，不能直接私信；不要把密钥贴到聊天、代码、issue 或日志。如果还设置关键词，可使用 `Codex`；GitHub 托管 runner 出口 IP 不固定，固定 IP 白名单通常不适用。
2. 在已登录 GitHub CLI 的本地终端克隆仓库，执行一次准备：

   ```bash
   git clone https://github.com/YuShenLiu06/codex-reset-monitor.git
   cd codex-reset-monitor
   python3 scripts/deploy.py --prepare
   ```

   该命令初始化 `monitor-state` 分支并请求默认 dry-run，不发飞书。已有状态时保留；已有初始化标记但状态分支丢失时停止。它**仅用于首次准备**，不能拿来清空发送历史。
3. 安全输入你刚创建的机器人凭据：

   ```bash
   python3 scripts/deploy.py --configure
   ```

   两次输入均隐藏，仅经 stdin 交给 `gh secret set`，不存本地文件、不进入命令参数。脚本不会登录或扩大 OAuth 权限；若已有授权不足，应由用户自己处理。也可在[仓库 Actions Secrets 设置](https://github.com/YuShenLiu06/codex-reset-monitor/settings/secrets/actions)手动建立 `FEISHU_WEBHOOK_URL` 和 `FEISHU_SIGNING_SECRET`。首次建飞书机器人和录入 Secrets **必须由用户完成，无法自动省略**。
4. 在 [Actions 页面](https://github.com/YuShenLiu06/codex-reset-monitor/actions/workflows/monitor.yml)选择「Run workflow」，保留 `dry_run=true`，检查测试和实时数据检查结果。预览 job 不注入飞书密钥、不写状态；它用只读临时 token 验证远端状态并预览去重后的候选消息。
5. 核对目标群和签名设置后，显式开启：

   ```bash
   python3 scripts/deploy.py --enable
   ```

   脚本检查两项 Secret 名称和有效状态，再设置仓库变量 `MONITOR_ENABLED=true`。每小时 UTC 第 17 分钟唤起；没有 Secret、源数据不健康或同类发送结果未解决时不会发消息。预测要求概率超过 80%；新公共重置记录不受该阈值限制。开启后可以手动 `dry_run=false` 做一次真实检查，仍只有符合对应提醒条件才发送，**不会为了测试捏造预测提醒**。到时需在飞书群确认实际消息；仅看到 workflow 成功不证明收件人已阅读。

停止：`python3 scripts/deploy.py --disable`。此命令关闭后续发送门控；已在执行的任务需在 Actions 页面取消或等待结束。保留所有去重记录。更换凭据前先关闭并等待现有任务结束，再运行 `--configure`。


## 为什么使用 Actions

| 项目 | 本项目的做法与限制 |
|---|---|
| 持久化 | `monitor-state/state.json` 保存非敏感去重元数据。每次发送前通过 GitHub Contents API 写入 `pending` 并等待确认；不依赖 runner 磁盘、cache 或到期 artifact。 |
| 并发 | workflow 共用 concurrency group，`cancel-in-progress: false`；SHA 条件更新防止两个旧快照同时抢到发送权。排队任务可能被 GitHub 替换，不承诺每小时都执行。 |
| 权限 | CI、预览仅 `contents: read`；发送 job 仅增加 `contents: write`，使用临时 `GITHUB_TOKEN`，无需新增 PAT。GitHub 无分支粒度 token 权限，因此写权限覆盖该仓库内容，需信任主分支代码及写入者。 |
| 公开信息 | workflow 日志、状态分支及其历史公开；只记录事件哈希、时间、类型、状态和固定回执，不保存 Webhook、签名密钥、消息原文、群成员或认证令牌。去重时间仍属公开运维信息。 |
| Secrets | 只在发送步骤环境注入；不向 PR/预览 job 注入，不打印上游响应或异常原文，禁用代理及重定向。GitHub 掩码只是额外保护；仓库写入者能修改 workflow 取得 Secret，不能向不可信协作者开放写权限。 |
| 依赖与成本 | 唯一外部 action 是 SHA 固定的官方 checkout；使用 `ubuntu-24.04` 自带 Python，无 pip/npm 安装。runner 镜像和 Python 补丁仍由 GitHub 更新。普通公开仓库托管运行，不自动申请付费 runner；以你的 GitHub 账户规则为准。 |
| 时效与失败 | schedule 可延迟、漏跑；公开仓库 60 天无活动可能自动停用。检查 Actions 最近运行时间及失败通知设置；无新提醒不能证明系统仍在线。不能通过同一失效通道保证故障告警。 |

保持每月人工检查 Actions 最近运行和 workflow 启用状态；不要通过无意义提交假装保障在线。需要严格可控调度、固定出口 IP 或私密状态时，使用下方服务器 cron 方案。

## 安全校验和去重

- 只接受 `public-v1`，`dataHealth.overall == "ok"`、`stale is False`，`supabaseSignals` / `openAIStatus` 均为 `ok`。
- `checkedAt` 和 `dataHealth.generatedAt` 默认不超过两小时，不超前本机 UTC 五分钟；时间必须带时区，通知显示 UTC+08:00。`lastRandomResetAt` 必须有效且不能在未来。
- 拒绝非数值/布尔概率、NaN/Inf、越界值、重复 JSON key、错误 schema、降级/过期数据。固定 HTTPS 源、TLS 校验、无跳转/自动重试，15 秒 socket timeout，源响应最多 512 KiB，读取循环目标总时限 30 秒（阻塞一次读最多再占一个 timeout）。
- 预测事件 key 用固定源 + `24h` + 标准化 `lastRandomResetAt`；同一重置周期最多一次，新周期也遵守默认 24 小时冷却。`checkedAt` 刷新不能成为新事件。
- 状态先从远端读出，条件写入 `pending` 成功后才 POST 飞书；`code` 为整数 0 才记录成功回执。HTTP 200、旧 `StatusCode`、超时、业务错误或无法确认的结果均不触发自动重试。Webhook 没有本项目可依赖的远端幂等键，成功回执也没有消息 ID，因此使用 `webhook_code_0`，不伪造消息 ID。
- 发送后状态写入失败时，远端 `pending` 会阻止下一 runner 重发；写入已成功但回应丢失也停止发送。同类 `pending/unknown` 暂停该类自动通知；安装测试的未解决结果阻止两类通知。这是保守的**至多一次尝试**，可能漏送，不承诺严格恰好一次交付。
- 状态损坏、分支缺失、GitHub 403/404/409、网络错误或超过 1 MiB 均停止；监测过程绝不自动创建空状态。首次初始化只由部署脚本显式执行。

```mermaid
flowchart LR
  A[健康数据且24h严格大于80%] -->|事件未发且冷却结束| B[远端条件写入pending]
  B -->|确认写入成功| C[签名POST飞书]
  B -->|失败或回应未知| X[停止并人工核对]
  C -->|code为整数0| D[远端更新sent]
  C -->|失败或结果未知| U[保留pending或unknown]
  D -->|更新未确认| U
  U -->|下次运行| X
```

处理未解决状态：先关闭门控、等待/停止所有实例，查飞书群记录并备份状态。查到消息可将记录标为 `sent`，填实际 `sent_at`（预测另更新 `last_sent_at`）和 `receipt: "manually_verified"`；确定未送达且选择放弃可标为 `abandoned`，事件仍不重发；无法确认则保留。使用正常条件更新提交到状态分支，不能强推、删分支或回滚状态后重发。没有自动清空/重试命令。

## 公共重置记录提醒

只使用同一个 `public-v1` 健康响应中的 `viewModel.recentHistory`：`recordKind=confirmed_global`、`sourceKind=direct_post`，且引用是固定允许的官方 X 账号帖子。依据[上游历史语义](https://github.com/gussuri/codex-reset-observatory/blob/main/docs/reset-history-normalization.md)，预告、备用重置卡配发、常规重置和参考记录不等于这类公共重置。概率下降、窗口滚动和 `lastRandomResetAt` 变化也不能单独触发事件提醒。

- 沿用每小时检查、当前飞书后端和收件人、`MONITOR_ENABLED` 门控；纯 Python 规则，不调用大模型。
- 首次有有效历史时，在原状态中增加独立的 `reset_events` 账本，将现有记录设为基线。以后只考虑比已见事件时间更新、距现在不超过 24 小时的新记录。离线超过这个范围可能漏报，不追补旧历史。
- 使用上游稳定 key 去重，并按引用链接识别改名别名；保留首次记录时间。改标题、修正时间、历史回填、源快照回滚或同一检查时间内改写历史均不作为新事件。未来时间暂不视为完成；待后续健康快照确认。
- 先持久保存未发送队列，再将单条事件条件写入 `pending`，得到确认后才发送。崩溃、超时、回执不明或最终写入失败保留 `pending/unknown`，不自动重试。同类后续事件等待人工核对，预测账本及 24 小时冷却独立保留。
- 旧状态和安装验收记录原样保留，不清空、不重建。队列记录被来源删除或修改时不发送；超过 24 小时标为过期。账本最多 1,000 条、整体最多 1 MiB，达到限制停止并要求人工归档，不能直接删历史以重发。

消息明确写“数据源记录到公共重置事件”。时间精度为 `announcement_fallback` 时标作完成公告时间，不能当作精确执行时间；无论精度如何，都不证明本人账户额度已恢复。

## 本地运行与服务器备选

```bash
# 离线测试，不调用网络或飞书
python3 -m unittest discover -s tests -v
# 默认安全实时预览
python3 monitor.py --once --dry-run
# 使用不含密钥的示例配置
python3 monitor.py --config config.example.json --once --dry-run
```

`--input-json PATH` 可读离线数据，但与 `--send` 同用会拒绝。`--loop` 是每 3600 秒检查的前台循环，终端退出就停止，不会安装调度。

服务器需要 Linux/macOS、Python 3.10+。先关闭 Actions 并等待所有发送任务结束，将状态分支的 `state.json` 单独迁移到服务器 `runtime/state.json`（权限 0600），避免换机丢去重记录。不要同时运行 Actions 和本地状态实例；本地 `flock` 只保护同一机器、同一状态文件。

服务器可安全输入环境凭据，或由管理员配置仓库外、权限 0600 的环境文件。不要把值写进 crontab、shell 命令参数、仓库文件或日志。复制示例到被忽略的 `config.json` 并设 `sending_enabled: true`；不再需要 lark-cli、飞书 OAuth 或 open_id。

在管理员维护的 wrapper 中读取仓库外凭据并导出两项环境变量，再执行：

```bash
python3 /ABSOLUTE/codex-reset-monitor/monitor.py \
  --config /ABSOLUTE/codex-reset-monitor/config.json --once --send
```

用户自行安装一条 cron（替换绝对路径；本项目未安装）：

```cron
17 * * * * /ABSOLUTE/secure-monitor-wrapper >> /ABSOLUTE/private-monitor.log 2>&1
```

本地状态采用临时文件、fsync 和原子 replace；服务器须维护在线状态、时钟同步、日志轮转及非零退出告警。cron 不补停机漏跑。若迁回 Actions，先停服务器并把最新状态迁回远端，不能重新初始化空状态。

## 故障定位与官方依据

退出码：`0` 正常检查/预览/确认回执；`2` 配置或源数据错误；`3` 发送未知/已有未解决发送；`4` 状态/锁错误；`130` 中断。飞书业务错误也持久标为未知、保守暂停：9499 请求错误、19021 签名/时间问题、19022 IP 校验、19024 关键词校验、11232 限流。只检查安全元数据，勿开启 `set -x`、HTTP debug 或输出完整环境。

程序保留原先可选的本地人工核验官方预告适配器，默认关闭，Actions 不启用。它不联网验证；站点 `activeWindow` / 文本分类不能独立触发官方提醒。

- [飞书自定义机器人官方指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)：签名是 `timestamp + "\n" + secret` 作 HMAC-SHA256 key，对空消息求摘要后 Base64，秒级时间戳距发送不超过一小时；单机器人 5/s、100/min，请求体 ≤20 KB。本项目每批通知间隔一秒并设 20,000 字节上限，不重试限流。
- [GitHub schedule 官方限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)：默认分支、延迟/漏跑、60 天无活动停用。
- [GitHub Contents 条件更新 API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents)和[临时 GITHUB_TOKEN 权限](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)。
- [来源 API 项目](https://github.com/gussuri/codex-reset-observatory)及[模型治理说明](https://github.com/gussuri/codex-reset-observatory/blob/main/docs/probability/published-model-governance.md)。本项目消费公开 DTO，不复制模型或采集器。


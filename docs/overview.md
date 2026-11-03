# 全局总览（后端候选，未部署）

新增 `GET /api/v1/overview`，只提供微信、阿里国际站、WhatsApp 的接入、实时采集、保存、进知识库四个环节，以及模型预算和阿里历史范围。没有修改生产 `v3.py`、演练服务器、`web/` 或 `deploy/`，没有挂载、部署、启停服务、运行模型或请求余额 API。

开发前已阅读 `src/console/pipeline/`、`docs/pipeline-health.md`、`docs/kb-live-recovery.md`、`docs/channels.md`、`src/console/v3_pipeline_api.py` 和 `patches/r7f/` 的两个挂载补丁；另只读核对生产 `/opt/imgw/wechat/console/v3.py` 的实际挂载上下文。工作区没有 Git 元数据。

## 固定输出契约

以下为结构说明，`state` 的实际值见后文。所有键始终存在，不添加聊天内容或身份字段。

```text
{
  generated_at: ISO UTC,
  summary: {state, headline, problems: [{channel, text, next_step}]},
  channels: [{
    key: wechat|alibaba|whatsapp, label, state, headline,
    stages: [{
      key: connected|collecting|stored|knowledge, label, state, detail,
      last_arrival_at, age_seconds, count_24h, total,
      processed, pending, last_processed_at
    }],
    notes: []
  }],
  model_budget: {
    deepseek_official: {currency, balance_micros, observed_at} | null,
    free_pools: [{model, remaining_tokens, observed_at, expires_on}],
    note
  },
  history: {alibaba: {
    messages, conversations, earliest_sent_at, latest_sent_at,
    by_month: [{month, count}], completeness: unproven, detail
  }}
}
```

渠道按微信、阿里、WhatsApp 排序，环节按接入、实时采集、保存、进知识库排序。`*_at` 均为 ISO 8601 UTC（`Z`）；`expires_on` 是账本观察的日期，`month` 是消息发送时间的 UTC `YYYY-MM`。`age_seconds` 是距相应来源最新证据的实际秒数；知识环节为距最后成功处理的秒数。微信知识处理的**消息与处理时间之差**另外明确写入 `detail`，不把它混成到达年龄。

不适用或读不到的数值和时间为 `null`。实际查询出的空表总数/成功数/未完成数可以是 `0`，但状态为未知；不能用 `0` 填充缺失数据。缺少月份来源时 `by_month=[]`，缺少免费池观察时 `free_pools=[]`，均附未知说明。`notes` 是字符串数组。问题的 `channel` 使用三个渠道 key 或 `model_budget`。

状态严格复用 `console.pipeline.service.STATUS/SEVERITY`：`ok`、`slow`、`interrupted`、`never`、`not_connected`、`unavailable`，不新增 `unknown` 状态词。**本接口以 `unavailable` 表示 unknown/未知**，并在 `detail` 中说明。零分母、缺文件/表/列、无效时间、读失败不显示正常。严重度沿用 `interrupted > unavailable > slow > never > not_connected > ok`；每个渠道取四个环节最严重状态，问题列表保留所有非正常环节。

总摘要同时纳入预算未知/零余额和历史完整性未知。目前阿里固定 `unproven`，所以即使四个环节都是 `ok`，总摘要仍至少是 `unavailable`；红色环节优先。摘要不把“暂时没报错”表述成全部历史和预算已验收。

## 真实来源结构与复用

以下业务库已在本轮用 pipeline 的 `read_db`、`PRAGMA table_info` 只读核对真实表与列。未查询客户正文、模型响应、身份值或真实业务计数，没有备份或复制业务库到测试目录。仅把结构和源码契约用于实现；测试全部是临时虚构库。

| 默认来源 | 本接口使用的真实表、列 | 口径与复用 |
| --- | --- | --- |
| `/var/lib/imgw/icbu/inbound.sqlite3` | `inbound(created_at, payload, archive_reason)` | 直接复用 `pipeline.readers.READERS['alibaba_inbound']`。接收时间为 `created_at`；实时/历史分类复用现行实现，不另写到达统计。当前源码要求 `history=false` 且 `live_provenance` 为 object 才记实时，`archive_reason=history_import` 优先记历史，其余为未标明。 |
| `/var/lib/imgw/icbu/probe-status.sqlite3` | `probe_state(received_at, snapshot_json)`；已核实另有 `account, first_seen_at, last_complete_at` | 直接复用 `READERS['alibaba_probe']`，只保留版本、允许的 state 和安全错误码，不返回 snapshot。`first_seen_at` 是首次见到探针，**不是暂停开始时间**。 |
| 同上 | `heartbeat_log(id, received_at, instance_id, state)`；另有 `version,last_error,cycle_complete,counters_json,range_start,range_end` | 新增连续暂停时长查询，只返回暂停起点。既有 reader 的心跳连续性查询继续复用；实例标识不输出。 |
| `/var/lib/imgw/wechat/api_state.sqlite3` | `messages(created_at,status,updated_at)`；附件关联用 `messages(app_id,message_id)` 与 `company_media(app_id,message_id)` | 复用 `READERS['wechat_messages']`；发生时间不能冒充接收时间。附件关联直接调用既有 `pipeline.coverage.coverage/wechat_attachments`，不重写相同 SQL、不计算翻译覆盖。 |
| 同上 | `received_callback_archive(received_at)` | 复用 `READERS['wechat_callbacks']`，作为接入/采集的本地回调证据。不会读取 `payload_json` 正文。 |
| `/var/lib/imgw/wechat/kb-model-runtime/calls.sqlite3` | `calls(state,updated_at)` | 真实表另有 `cache_key,attempt,prompt_sha256,model,error_code,day,reserved_tokens,response_json,response_sha256,created_at`；本接口只读状态和时间，绝不读响应。`complete` 为成功，取其有效 `MAX(updated_at)`。 |
| `/var/lib/imgw/icbu/conversation-analysis/analysis.sqlite3` | `windows(version,state,updated_at)`；`current_conversations(analysis_version)` | 用 `EXISTS` 限定当前版本，不把旧版本 pending 审计记录算当前积压。已核实 windows 另有 `window_index,window_sha256,plan_json,result_json,error_code,attempts`，均不读正文/计划。当前映射缺失时不退回全部版本。 |
| `/var/lib/imgw/whatsapp/messages.sqlite3` | `messages(received)`，`recipients(chat_id)`；`events(event_type,received,raw_json)` | 复用 `READERS['whatsapp_messages']`、`READERS['whatsapp_session']` 及 pipeline `_classify/_session_state`。事件 JSON 只在 SQL 中提取允许的会话状态和 `me` 是否存在；不输出身份或原 JSON。 |
| 同上 | `knowledge_jobs(status,updated)` | 真实表另有 `message_key,asset_id,error_code`。状态分布复用 pipeline `job_counts`；仅新增完成时间查询，不读取资产或消息正文。 |
| `/var/lib/imgw/ops-app/kb-recovery-coordinator/physical.sqlite3` | `gate_balances(bucket,balance_json)` | 最初检查文件不存在，后续复核文件出现，已只读确认该表的真实列，以及 `gate_audit(seq,kind,payload_json,previous_sha256,event_sha256)`。当时没有可枚举的余额 JSON 字段；解析格式另依据已读协调器 `physical_gate.record_balance` 与 `official_deepseek.read_balance` 源码核对，不能声称已有可用余额。只从 `gate_balances` 在 SQL 内提取 `provider,currency,total_micros,observed_at`，不读 gate_calls、gate_audit 的回执/响应正文。 |

`payload` 和 `raw_json` 仅作为既有 metadata JSON 提取的 SQL 输入；没有 `SELECT *`、消息文本选择或 Python 消息对象反序列化。阿里历史另从 `payload` 在 SQL 内只提取 `account/conversation_id/sent_at`，只让汇总数量和时间离开 SQLite，账号与会话标识不离开 SQL。

## 四个环节的判定

| 渠道 | 接入 connected | 实时采集 collecting | 保存 stored | 进知识库 knowledge |
| --- | --- | --- | --- | --- |
| 微信 | 本地回调接收证据，沿用回调阈值 | 同一回调到达统计，明确不等于消息已保存 | 已保存消息总数及按发生时间的 24h 数，`last_arrival_at=null`；缺 pending 检查字段为未知，pending 最久不推进超过既有阈值报红 | calls 成功时间对比最新消息发生时间，见下文 |
| 阿里 | 心跳实际接收时间，超过 5 分钟报红 | 复用实时消息统计与作息口径，再叠加暂停/心跳硬阈值；历史不刷新实时最后到达 | inbound 当前保留的全部已保存行；有有效非零样本才能正常，不把实时中断混成没有保存 | 当前版本 windows 的成功/未完成统计 |
| WhatsApp | 复用 pipeline 会话判定 | `messages.received` 的消息到达统计，不用服务事件代替 | 当前保留消息行与接收时间 | knowledge_jobs 的成功/未完成统计 |

保存正常只表示当前本地记录可读且有有效样本，不证明远端消息全部持久化；接入证据也不是实时登录或发送探测。微信附件关联结果只作为 notes：分母是全部消息，分子为按 app_id/message_id 能关联 company_media 的消息，不代表所有附件都已采集。覆盖率来源缺失、空分母或失败明确显示未知，不冒充 100%。

### 阿里实时采集

- 距最近有效心跳**严格超过 300 秒**即 `interrupted`，按墙上时间计算，不享夜间折扣；即使快照的 state 无法识别，已知的心跳断流仍报红。文件缺失或没有有效心跳时间则未知。
- 最新快照 `state=paused`：用 `heartbeat_log` 的连续 paused 段计算观察下界。未知状态、非 paused、instance_id 变化、相邻心跳空档超过 300 秒均切断连续段；日志最新时间必须与快照接收时间一致。缺流水或无法对齐时，暂停时长未知，不能变绿。
- 连续暂停**严格超过 1800 秒**报红；超过 600 秒、直到 1800 秒报黄。较短但明确暂停也保守报黄，表示暂停待核对。`detail` 必须含 `state=paused`、暂停至少多少秒/分钟与 `last_error`。
- `last_error` 复用 pipeline 的小写 snake_case 安全代码过滤，最多 96 字符；无值/非法值只写“未提供或不符合安全错误码格式”，不透传异常正文。保留既有 `pairing_reconnect` 的断流判定。
- 采集环节取实时统计与探针证据的最严重状态，因此新消息不能掩盖暂停，历史导入不能掩盖实时断流。

### 进知识库

- **微信**：`processed=COUNT(state='complete')`，`pending=total-processed`，单位是模型调用记录，不是消息覆盖数。`last_processed_at=MAX(有效 updated_at WHERE state='complete')`。以 `MAX(messages.created_at)-last_processed_at` 算滞后（成功晚于消息时滞后为 0）。**严格超过 86400 秒**为 `interrupted`，detail 同时写两个 UTC 时间、滞后秒数和小时数；恰好 24h 不触发该红线。近期 hold 不能更新最后成功时间。无成功、无有效最新消息、零样本或读取失败为未知。调用成功不是逐消息索引完成证明。
- **阿里**：只统计当前 `analysis_version` 对应的 windows；`validated` 计 processed，其余计 pending。`pending/retry_wait` 为黄色未完成，`infra_blocked/needs_repair` 为红色阻断；未识别状态、无效时间、零分母为未知。最后成功时间是 validated 窗口的有效 `MAX(updated_at)`。不把全部旧版本窗口、历史冻结记录混成当前待执行任务；不声称 validated 代表发布索引或全部实时富消息已分析。
- **WhatsApp**：`indexed_claims/indexed_no_supported_claims` 计 processed；`pending/raw_indexed/waiting_model/indexed_media_pending` 计未完成并报黄，`budget_wait/model_hold` 报红，未识别状态报未知。成功时间为两种完成状态的有效 `MAX(updated)`。只表示登记作业完成情况，不证明未登记消息或所有媒体覆盖。零分母一律未知。

### WhatsApp 会话

直接沿用 pipeline 的 WORKING 最长有效年龄（默认 21600 秒）、非 WORKING 立即黄色、连续非可用严格超过 300 秒红色，以及完整近 10 分钟内至少 20 次 FAILED 且没有 WORKING 的重启风暴规则。状态无法识别、没有记录或 WORKING 记录过旧为未知，采样范围外的失败不臆造。默认最多读最近 50000 条状态事件，可使用 pipeline 已验证的配置边界。

接入环节 detail 固定追加：**“静止期不产生状态事件，未必是故障”**。不把会话状态事件时间填进消息 `last_arrival_at`。零消息的采集环节仍为未知，不能用新号等待说明冒充非零采集证据。

## 阿里历史完整性

`completeness` 固定为 **`unproven`**。当前没有逐会话到顶证据来源或自动改为 proven 的开关；若未来接入证据，必须先另行核对其表结构、会话覆盖和到顶语义。

`messages` 是 inbound 当前保留的物理归档行，含实时/历史/未标明，不是平台远端总数，也不根据客户正文去重。`conversations` 是有效 account+conversation_id 组合的 SQL 去重数量；存在缺失会话元数据时为 null，不用部分数假装完整。发送时间只接受带时区、可解析、正数且不在未来的 sent_at；最早/最新和 by_month 仅统计这些有效发送时间，不拿接收时间替代。缺失/无效数量写入 detail，零消息时会话数和时间未知。月份数、消息数和最早时间都不构成完整性证据。

## 模型预算：只读 ai-task 已落盘数据

已只读检查 `/usr/local/libexec/imgw-ai-task`：它把 `/usr/local/libexec/imgw-ai-task-lib` 加入路径，入口为 `ai_task_gateway_candidate.main`。已阅读该入口及 `ai_task_core`、`tools/qwen_probe.snapshot` 和矩阵账本 `_read` 源码，**没有执行/导入网关、matrix、模型客户端或凭据加载器**，没有打开 `/etc/imgw/swarm.json`、媒体策略配置、EnvironmentFile 或 token 文件。

已仅核对以下非秘密文件的顶层键和模型记录键/类型，没有把真实值带入测试、日志或文档：

| ai-task 来源 | 格式与本接口读取方式 |
| --- | --- |
| `/etc/imgw/claude-free-model-snapshot.json` | 顶层 `provider,verified_at,valid_until,source,confidence,note,models`；每个模型为 `remaining_tokens,expires_on,free_only`。只选择源码已登记的 6 个百炼模型，不输出 note 等任意文本。 |
| `/var/lib/imgw/cpq-batch-qwen/ledger.json` | 下述 version=1 用量格式 |
| `/var/lib/imgw/wechat/kb-model-runtime/usage.json` | 同上 |
| `/var/lib/imgw/whatsapp/model-runtime/usage.json` | 同上 |
| `/var/lib/imgw/icbu/live-drafts/usage.json` | 同上 |
| `/var/lib/imgw/icbu/conversation-analysis/quota-pool-20261024/usage.json` | 同上 |
| `/var/lib/imgw/video_knowledge_auto/equipment/qwen-free-quota-protocol-20261024.json` | 同上；这是用量账本，不是含配置的媒体 policy 文件 |

用量账本顶层为 `version,policy_sha256,models`；每个模型有 `accounted_tokens,actual_prompt_tokens,actual_completion_tokens,actual_total_tokens,unknown_reserved_tokens,calls,completed_calls,blocked_reason,in_flight`。实现检查版本、摘要格式、非负计数及 `prompt+completion=total`、`accounted=actual_total+unknown_reserved`。不修改任何预留、hold 或阻断。

免费池的 model 名单直接依据已读 ai-task 源码：`deepseek-v4-pro-0813`、`deepseek-v4.1-flash`、`deepseek-v4.1-flash-1017`、`glm-5.3`、`kimi-k3`、`qwen3.8-2.4t-a95b`。要求观察 provider=dashscope、source=owner_browser_observation、confidence=masked_key_match、free_only=true，verified_at/valid_until 有效且观察尚未过期。expires_on 保守按上海时区当日零点到期，valid_until 不得超过该时点。

只有观察与六本账中该模型记录均有效且没有阻断/in_flight，才显示 `max(0,观察 remaining_tokens - 六本账 unknown_reserved_tokens 之和)`；observed_at 保留观察时间。任一账缺失、模型未登记、读失败、计数非法、观察/模型过期或有阻断，`remaining_tokens=null` 并写明未知。不能把缺模型当成零消耗，也不把 blocked_reason 原文输出。

此数字是**账本观察口径**，不是实时云端可用余额。本接口不读含凭据的 swarm 配置，因而不核验配置中的本地 cap，也不提供 ai-task 准入决策；已消费变化、其他消费者和跨文件非原子快照都可能影响当前实际可用额度。说明固定放入 model_budget.note，禁止据此授权调用。

官方余额只读协调器 `gate_balances` 中 bucket=`deepseek_official` 的本地解析结果。要求 provider 精确匹配、currency 为 CNY/USD、total_micros 为非负整数、observed_at 有效且不在未来，输出该币种百万分之一金额；不换汇、不拿配置的初始资源/费用 cap 充余额、不读原始回执正文。缺文件、缺表、空表、坏 JSON 或无有效余额记录均返回 `deepseek_official=null`。有值也仅代表 observed_at 的观察，note 明确不等同当前余额。**请求路径没有任何外部 API、命令或凭据读取。**

## 超时、大小、鉴权和隔离

- SQLite 复用 pipeline `read_db`：mode=ro、query_only、20ms busy timeout、短读事务、进度截止时间、必定关闭；无 WAL 时使用 immutable 并核对前后快照，有 WAL 时要求已有 SHM，存在非空回滚日志时降级。新包装另拒绝非普通文件/末级软链，主库及每个旁文件最多 2 GiB，单个 SQLite 值最多 2 MiB，临时聚合放内存。
- 每次 SQLite 来源查询预算沿用 pipeline `query_timeout_seconds`，默认 1 秒，配置范围 0.01–3 秒。每个来源独立捕获失败，失败不让其他渠道失去数据。新查询只有固定聚合和窗口计算，不输出动态数量的消息对象。月份最多 1200 项，超出整体降级；既有小时格固定 48 项，会话采样受 pipeline 配置上限约束。
- JSON 每文件最多 32 KiB，O_RDONLY/O_NOFOLLOW/O_NONBLOCK 打开，只允许普通文件，拒绝重复键、NaN/Infinity、超大小和读前后修改；每个文件独立使用同一秒数预算并检查截止时间。没有子进程、外部客户端或网络请求。异常只用固定中文说明，不泄露路径、SQL、身份、凭据或异常原文。
- HTTP 完全复用 `inbox.http.get_handler`：先验证 password principal、旧 session、recheck 能力；线程池读取；读取后重新验权，撤权则 403；no-store、nosniff。仅 GET/HEAD，其余方法 405；任何查询参数均 400，且拒绝参数前不读来源。没有新增审计写入、CSRF 豁免或写接口。
- `Overview(paths=..., config=..., clock=...)` 仅为服务端装配/测试参数，不接受 HTTP 路径或 SQL。config 用既有 pipeline.validate；默认使用 pipeline 默认阈值，不自动打开生产配置文件。显式注入任意 paths 后，**所有**未指定来源（含余额、免费观察和六本账）都隔离到同一临时目录的 disabled 占位路径，不回落生产、不创建缺失文件。

## 挂载与验证

新增模块：`src/console/overview/{__init__,safety,readers,budget,service}.py`、`src/console/v3_overview_api.py`。新增测试仅在 `tests/overview/`，文档为本文件。

- `patches/overview/production-mount.patch` 参照 r7f 的独立 import/build 两行写法，生产目标是 `console/v3.py`，上下文为已存在的 v3_pipeline_api 挂载。部署时由维护者审查并应用，本轮未应用到生产文件。
- `patches/overview/rehearsal-mount.patch` 只作为补丁交付，不修改演练服务器。使用 `paths={'inbound': tmp + '/overview-inbound.sqlite3'}`，所有省略来源保持隔离；不创建该文件、不读取真实业务源。需要展示数据时由演练装配显式提供虚构来源，不能用伪鉴权默认访问生产。

测试命令（无网络、仅虚构数据）：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest tests/overview -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider
```

测试覆盖固定键契约、暂停 10/30 分钟与心跳 5 分钟边界、暂停连续性、微信滞后 24h 边界、当前版本窗口、WhatsApp 状态/风暴/静止期说明、零分母、缺表列、超时/超大小、非法 JSON/软链/FIFO、预算观察与预留、认证/读后撤权、GET/HEAD 与写方法拒绝。测试拦截生产 SQLite/JSON、网络和子进程；另用 SQLite authorizer 禁止正文列读取和写操作，核对读取前后所有虚构源的字节/mtime 不变、连接均关闭。

最终验证结果：`tests/overview` **85 passed in 21.40s**；既有 `tests/pipeline` **122 passed in 16.17s**。两个挂载补丁均以 `--batch --fuzz=0 -p1 --dry-run` 在代码临时副本上通过，随后仅对临时副本实际套用并通过 AST 语法检查；新模块和测试也通过 AST 检查。开发中只读安全测试曾因测试连接包装器的参数名与 SQLite `uri=` 重名而失败，已修正测试包装器，未放宽只读或正文拦截规则。

本候选**未部署、未挂载、未实测线上接口或模型/历史完整性**；虚构测试通过不表示真实渠道或知识链路已恢复。没有业务库写入、网络请求、真实模型调用或真实发送，没有修改 `web/`、`deploy/`、生产 `v3.py` 或工作区演练服务器。

# R7-A：微信待处理与三个后台作业的只读诊断

2026-11-01。**未部署、未补跑、未改生产状态、未发送、未调用模型。** 施工目录是调度器提供的独立工作区，不是 `/srv/imgw-dev/ops` 本体；后者和 `/opt/imgw` 只读。工作区及生产源码目录没有可核对的 Git 元数据，不能提供生产提交号；补丁以本文和 `deploy/r7a/baseline.json` 的文件 SHA-256 为准，不能拿开发副本的 HEAD 代替生产版本。

结论：微信 200 条是**暂停闸门下没有被消费的主回复队列记录**，200 来自入队上限，不是每次只扫描 200 条。日报有可追溯的共享模型隔离不匹配：现行代码只接受两条未知记录，实际账本有三条。历史原文索引在读取服务属性时最近一次已成功，现存来源均有扫描登记。名称刷新仍失败，但旧程序只留下 `ValueError` 类型，当前权限不足以还原历史具体异常。

本轮交付两个补丁：修正只收不发模式下新消息的入队状态；给历史索引、名称刷新增加固定错误码和中文提示。**不清除暂停、不重放原 200 条、不改未知隔离、不删除索引的审核记录。** 日报需要总指挥合并已有 R3-C 隔离修复，不能只把常量 2 改成 3。

## 取证方法和限制

先读 `CLAUDE.md`、master-plan、data-pipelines、workflow-gaps 的 WG-01 全文及相关渠道、知识恢复文档；再核对真实模块、SQL 和 schema。没有读取 api_config、WAHA 配置、EnvironmentFile、模型配置及任何凭据文件的内容，没有查看进程环境。

生产库只以 SQLite `mode=ro`、`query_only=ON`、`backup()` 打开。副本在当前用户持有的 `/tmp/r7a-private-*` 0700 目录，完成后文件设为 0400，分析时使用 `mode=ro&immutable=1`；没有复制 `-wal/-shm`，没有向生产库发送写 SQL。各库不是跨库原子快照。

| 副本名 | 实际来源 | 备份完成时间 UTC |
| --- | --- | --- |
| wechat.sqlite3 | `/var/lib/imgw/wechat/api_state.sqlite3` | 2026-11-01 15:18:47 |
| digest.sqlite3 | `/var/lib/imgw/knowledge_digest/state.sqlite3` | 2026-11-01 15:20:52 |
| history.sqlite3 | `/var/lib/imgw/whatsapp-history-private/history.sqlite3` | 2026-11-01 15:20:53 |
| raw-index.sqlite3 | `/var/lib/imgw/whatsapp-text-analysis/history-raw/catalog.sqlite3` | 2026-11-01 15:20:53 |
| whatsapp.sqlite3 | `/var/lib/imgw/whatsapp/messages.sqlite3` | 2026-11-01 15:20:53 |

`systemctl cat` 定位了历史索引、名称刷新入口；日报的 unit 文件拒绝读取，改从 `systemctl show` 的 ExecStart 提取解释器、脚本路径和已知开关，不输出完整命令行。真实入口分别为：

| 作业 | 入口及读取到的状态 |
| --- | --- |
| imgw-knowledge-daily | `/usr/bin/python3 /opt/imgw/scripts/knowledge_digest_generate.py`，带 `--execute --allow-paused-generation`；2026-10-31 21:30:00–01 PDT 退出 1 |
| imgw-whatsapp-history-raw-index | `/opt/imgw/venv/bin/python /opt/imgw/console-waha-history-raw-v1/history_index_worker.py`；属性读取时最近一次为 2026-11-01 07:18:07–10 PST，退出 0、Result=success |
| waha-names-refresh | `/opt/imgw/venv/bin/python /opt/imgw/waha-names-refresh-v1/capture_names_worker.py --refresh`；2026-11-01 07:08:02–03 PST 退出 1 |

微信实际 unit 是 `wechat-receiver.service`，为 active/running，最近启动 2026-10-29 01:40:51 PDT。没有把不存在的 `imgw-wechat-api.service` 当成接收器。日报 timer 为 `America/Los_Angeles` 每天 21:30；下一次为 11 月 1 日 21:30 PST，换夏令时后对应 UTC 时间与前一天不同。

三份 journal 都没有读到，原始错误为：

```text
No journal files were opened due to insufficient permissions.
```

因此题目提供的 `index_unavailable` 和 `ValueError` 只作为现场提供的日志线索，不能写成“本轮已读到完整日志”。本环境多个系统源码文件显示 UID=65534，而安全检查要求 UID=0；这不能证明宿主机运行时属主错误，尤其索引服务已有成功记录。**不要据此执行 chown 或降低属主、哈希、绑定检查。** 四个索引依赖文件的字节哈希均与 `query_worker.PINS` 相符，BGE socket 在挂载中存在；没有连接它。

## 微信 200 条的根因和分类

真实 `messages` 主键是 `(app_id,message_id)`，列为 app_id、message_id、group_id、sender_id、created_at、payload、status、rule、updated_at；payload 是规范化消息，不是原始回调 envelope。`created_at` 是消息发生时间，不冒充接收时间。

| 脱敏统计 | 结果 |
| --- | --- |
| pending | 200；同一个应用、4 个群、32 个发送者，仅输出数量 |
| 发生时间，America/Los_Angeles | 2026-10-24 23:59:12 至 10-25 07:48:51，均为 PDT |
| 对应 UTC | 2026-10-25 06:59:12 至 14:48:51 |
| updated_at 范围 UTC | 2026-10-25 06:59:20 至 14:48:56，此后未推进；首次入队比发生时间晚约 4.14–10.40 秒 |
| 主队列处理证据 | rule 为空 200；关联 actions、analysis_archive、reply_archive 均为 0 |
| 来源证据 | 200 条均按账号 SHA、消息 ID、源时间精确匹配原始回调；均有关联的不可变角色观察 |
| 绑定、路由 | 200 条的已存 scope 与已存 memory_binding 一致，route 均为 group；这不证明当前动态授权有效 |
| 其他队列 | owner_messages、onboarding_requests 没有 pending/processing；原 200 条没有 receive_collection_jobs |
| 全局暂停 | metadata 中 paused=true；本轮未改动，也不能从这个布尔值确定最初暂停的时间或原因 |

证据链，行号均针对生产基线 `api_groupbot.py`：

1. `:38` 定义 `MAX_QUEUE=200`；`:897–900` 合计主消息、老板消息、接入请求的 pending/processing，达到上限即满。
2. `:1140–1163` 的 `run_once()` 在选择主消息之前检查 `store.paused()`，为真就返回。`:1183–1186` 的实际消费是按 rowid 每次取 **1 条**，不存在 LIMIT 200 的批扫描器。
3. `:907–917` 只在“队列已经满 + collection_only + 群视图 silent”时直接写 `collected_silent`，未满时同样的静默来源仍写 pending。这解释了为什么前 200 条固定，而之后仍能采集。副本中上限到达后另有 **4467 条** collected_silent；它证明有后续归档，不证明全部消息都采齐。
4. `:315–326` 的 recover 只处理 sending/processing，保留 pending；服务重启不会清掉这 200 条。`:1395–1402` 的 worker 还会吞掉未预期异常，因此也不能单凭队列长期不动证明线程崩溃。现在能够直接证实的阻断是暂停闸门，不是线程失联。
5. `collection_policy.py:444–489` 会按采集开关生成 silent 群视图；`receive_only.py:16–30` 要求只收模式同时关闭发送并开启静默采集。**没有读取含凭据的配置，不能宣称现时这些开关已经现场核验。**

按题目三个概念做互斥判定，不把候选冒充执行许可：

| 主回复队列判定 | 数量 | 含义及建议 |
| --- | ---: | --- |
| 已证实现在该自动执行 | 0 | 本轮没有当前动态授权、规则和独立模型调用账本的完整准入证据；不要重新入队 |
| 已证实由旧规则判为忽略 | 0 | rule 为空、无分析记录，不能编造“旧规则忽略”或批量标 expired/ignored |
| 待处理状态确实未推进 | 200 | 已证实卡在暂停保护下；是否仍需要业务处理未定。当前全部保持，暂停也保持 |

这里“卡住”是队列状态，**不等于应该恢复自动回复**。还可按持久来源给 200 条提出分流建议，以下互斥合计仍为 200：

| 历史登记范围 | 条数 | 纯文本 / 带附件 | 安全决定 |
| --- | ---: | ---: | --- |
| learning | 170，两个群分别 169、1 | 136 / 34 | 核对当前学习范围及 KB 已完成/可靠响应/未知隔离后，再决定知识补处理。若确认原任务只是静默采集，可逐条审核标 collected_silent；不能为清卡重放回调或重新发模型 |
| internal | 20，一个群 | 2 / 18 | 核对内部工作授权及老板工作队列。不能把内部资料转为客户回复；若确认只需保留原件，可审核标 collected_silent |
| unclassified | 10，一个群 | 0 / 10 | 保持、先确认分类和授权；不据附件或群名自动给模型、建工作任务或发送 |
| 合计 | 200 | 138 / 62 | 本轮无任何终态更新或重排 |

角色来自 message_scopes，并与逐条原始回调对应的 `channel_role_intake.observation_json.registered_business_role` 交叉一致，不靠群名猜测。62 个附件描述中，image=38、reference=18、video=1、其余=5；这是**描述数量**，没有证明文件存在、可解析或已入知识库。没有把 receive_collection_jobs=0 判为丢失：`video_knowledge/receive_collection.py:30–69` 的独立收集授权只准 supplier/customer，本次历史角色均不属于这两类。

需要清理旧状态时，应由总指挥在当前授权核实后冻结逐条清单，检查 `(app_id,message_id)`、原 payload SHA、旧 status/rule/updated_at、scope/绑定和无发送计划，再通过现有单写者事务做比较后更新，另在独立可配置审计库留原值。回滚只对仍保持本次新状态且指纹未变的记录反向更新，不能回灌整个 api_state。**本轮不提供 UPDATE 全部 pending 的命令，也不把 190 条终态候选当已批准清单；当前保持 200 是执行决定。**

## 日报：隔离清单过时，保护性阻断

真实 digest_runs 副本共有 13 条 daily：model_failed=8、notification_ready=3、accepted=1、notify_rejected=1。这些是历史状态，不能把旧 notification_ready 当成当前待发送授权。

| period_id | status / error_code | UTC 更新时间 |
| --- | --- | --- |
| 2026-10-29 | model_failed / qwen_matrix_unavailable | 2026-10-30 04:30:01 |
| 2026-10-30 | model_failed / qwen_matrix_unavailable | 2026-10-31 04:30:01 |
| 2026-10-31 | model_failed / qwen_matrix_unavailable | 2026-11-01 04:30:01 |

10 月 31 日这条与 systemd 的 21:30 PDT 失败时间一致；result_json、archive_json 均无结果，notification_attempts=0。更早五条失败分别涉及数字证据校验、类别代表性校验和 model_exception，不能用这次隔离修复宣布它们也都修好。

实际代码链：

- `scripts/knowledge_digest_generate.py:111` 调 daily worker；`:130–143` 会输出结构化结果并对未成功归档返回 1，不是因为“日志少”就能认定没有运行。
- `daily_worker_candidate.py:91–97` 的 daily 专用池经 `daily_pool_proxy.py:17–28` 调用子进程。`daily_pool_child.py:48–59,83–85` 实际载入 `/opt/imgw/wechat-kb-dual-live-v1/live_loader.py` 的 overlay，然后建立隔离矩阵；不是直接使用另一份同名基础模块。
- overlay 的 `video_knowledge/kb_model_runtime.py:292–310` 先调用 quota_isolation.install，再 preflight。实际 `quota_isolation.py:9–11` 要求 holds 恰为 2，且 usage_uncertain 的模型集合与其完全相等。
- 只读检查 usage 账本得到：**usage_uncertain=3，均 in_flight=false 且仍有未知预留**；另外 free_tier_exhausted=1、无封锁记录=4。没有输出模型名、金额、账本原文或任何凭据。现行“恰好 2”的代码无论拿两条还是三条清单，都不能放行这个三条未知集合。

这是当前代码和账本组合的确定阻断，且与三天持久错误码吻合。最初产生第三条未知的原始网络原因、实际计费及云端可用额度没有由本轮重新验证；不能把 qwen_matrix_unavailable 改写为“免费额度已用完”。

**丢失及补跑：**原始微信库、日报输入快照仍保留；失败造成派生日报缺口，没有看到该 worker 删除来源的代码。不能据此证明所有来源、NAS 或其他采集器完整。`daily_worker_candidate.py:60–68` 使已失败周期直接返回、不重调；calling 会转 model_usage_unknown 并禁止重试。`:165–166` 只选最近到期周期，没有自动补齐全部历史的循环。单纯 restart 或修改系统日期不能补齐这些失败日报。

**修复归属：**复用既有 [R3-C 方案](kb-live-recovery.md)、[原隔离补丁](../deploy/kb/unknown-isolation-v2.patch) 及 [基线清单](../deploy/kb/baseline.json)。必须连同精确三条隔离审核、来源隔离和后续补丁链由原负责人合并；保留未知预留及 prompt/source 的禁止重发，禁止清账、改 blocked_reason 或只改数量。R7-A 不覆盖这些他人模块。已有 `tests/kb_recovery/test_isolation.py` 本轮 21 项通过，只验证虚构记录。

修复后：先核对持久响应，generated/nas_pending 只续本地归档；model_failed/unknown 不改回 prepared。历史失败若确需新报告，必须另有经审查的修订周期/调用身份、原失败审计和授权预算，不覆盖原行或换 policy SHA 绕过去。本轮没有交付这种新模型补跑入口。总指挥应先完成 R3-C/R5 的协调方案，再明确日报是否另行恢复；这里没有可以安全执行的“一条补跑命令”。

## WhatsApp 历史原文索引：当前已恢复，旧失败原因未知

`history_index_worker.py:6–10` 把所有非锁异常统一压成 index_unavailable。真正可能阻断的位置包括 `history_runtime.py:16–29` 的源码/原库身份/BGE socket/账号授权检查，以及 `:45–62` 的扫描；**generic error 本身不能区分这些原因**。没有日志权限，不能把此前的错误归因于某个具体检查。

副本中历史原消息 **390 条**，扫描状态 **269 indexed + 121 review = 390**，pending=0、retry=0；Catalog 为 283 份 asset、324 个 chunk，不能直接把 asset 数当当前已完成消息数。没有删除多出的历史资产，也没有实际调用检索器逐条证明其可用。

| 121 条 review 的原因 | 数量 | 决定 |
| --- | ---: | --- |
| text_empty | 94 | 不具原文文本，不该强塞进文本索引；保留原消息和媒体链路 |
| encoded_or_binary | 9 | 编码或二进制过滤，保留审核状态 |
| unsupported_message_type | 4 | 当前原文索引不支持，保留审核状态 |
| evidence_missing_or_invalid | 9 | 来源证据不足，人工核对原归档/证据；不能删除状态强行通过 |
| source_conflict | 5 | 来源冲突，保持并审核，不能自动覆盖 |

即 107 条是当前文本索引规则的正常拒绝，14 条需要证据审核；不是 121 条都“卡住”。最近一次服务已成功，不安排为了消红而再次启动。

**丢失及补跑：**`history_index.py:45–84` 只读来源库；`:207–256` 按 message_key 保存扫描进度，在 `:233–239` 核对已索引内容、来源及 provenance 后跳过；`:245–248` 分别保存资产和扫描状态。Catalog 的 `put_asset:131`、`put_chunk:194–198` 用稳定 ID 和 INSERT OR IGNORE。已提交进度能复用，失败不会要求清库重建；review 也不能当成应该自动重试的许可。若未来再失败，总指挥按固定错误码修依赖/绑定，保留来源和 Catalog 后可单次启动同一 unit；**该启动会访问真实 WAHA 的授权 GET 和 BGE embedding，是模型动作，本轮没有执行。** 新补丁不会新增自动启动或重试。

## WhatsApp 名称刷新：保护性失败，具体检查仍待日志

`capture_names_worker.py:20–43` 先持有目录锁，检查原缓存、配置/账号绑定，逐页 GET chats，每次请求前后用 `private_history._session:48–51` 核对 WORKING 与精确账号，再原子替换缓存。`:47` 只打印异常类名。题目给的是准确的 ValueError 时，不能据此认定是另一个子类 NameErrorSafe 所代表的空列表/冲突，更不能猜成字段改名。

当前只读证据：缓存文件存在，0600、单链接，schema 正确，共 109 个条目且 109 个有名称；捕获时间为 2026-11-01 01:51 UTC。缓存 binding 与私有历史 binding 一致，私有 binding 与原 WhatsApp binding 一致。最新已归档 session.status 在 13:46:47 UTC 为 WORKING，me.id 与原 binding 对应账号一致；**这是历史事件，不是 15:08 失败时或现在的实时探测**。

10 月 25 日以来副本记录 STARTING=37073、FAILED=37085、STOPPED=11、SCAN_QR_CODE=58、WORKING=18，说明存在严重会话不稳。它支持“会话检查曾可能失败”的候选解释，不能证明 07:08 PST 的 ValueError 就是 binding_mismatch。仍可能是早于请求的配置/祖先路径/绑定检查、缓存前置条件或根目录变化。**历史根因未定案**，本轮没有调用 WAHA 或读取其配置来补猜。

**丢失及补跑：**此脚本不请求 messages、不发送、不删除聊天。`wa_names.py:64–103` 保留旧名，先写 0600 临时文件、fsync、保存 chat-names.previous.json 再原子替换。登录失败发生在替换前时原缓存不变；替换后若根目录校验失败，结果可能已改变，所以补丁提示“刷新未确认完成”，不谎称没有写入。没有迹象证明消息丢失，名称可能过旧。

总指挥确认同一账号已登录、绑定与缓存仍匹配后，可执行**一次** `systemctl start waha-names-refresh.service`，原 unit 自带 --refresh 和目录锁；这是会访问真实 WAHA 的 GET 运维动作，**本轮没有执行**。失败或结果不明时先核对现缓存与 previous，不盲目循环。若错误码为 cache_missing，不得把 --refresh 改成初次采集来绕过，先恢复经核对的缓存或另审初始化。

## 补丁、验证和复现

| 补丁 | 修改生产候选 | 行为及范围 |
| --- | --- | --- |
| `deploy/r7a/wechat-silent-admission.patch` | `wechat/api_groupbot.py` | 仅 receive_only_mode=true、send_enabled=false、collection_only 且实际群视图 silent 时，新条目直接 collected_silent。保留原满队列绕行、绑定/时间/关闭群校验，不改旧 pending。非只收模式和公开回复的原入队行为保持 |
| `deploy/r7a/job-errors.patch` | `console-waha-history-raw-v1/history_index_worker.py`、`waha-names-refresh-v1/capture_names_worker.py` | 增加固定允许列表错误码、中文下一步及可测试 main；历史索引保留 error=index_unavailable 兼容字段，锁 busy 仍退出 0；不放宽任何身份检查，不重试 |

基线与候选在 `baseline.json` 均有 SHA，源文件路径相对于 `/opt/imgw`：

| 文件 | 生产基线 SHA-256 |
| --- | --- |
| wechat/api_groupbot.py | `a52b7e93ed6ac577c25f78ee42c494915854b6ab052468e2e65148cd643e8367` |
| console-waha-history-raw-v1/history_index_worker.py | `047086c04c24d5877920cb3f959434dbd41ccbcfd3a0227890a7e09542ebd40f` |
| waha-names-refresh-v1/capture_names_worker.py | `dd3b74e05e017d376eb7ab55f213d719cc7328b7f5c48618088ee51492facb67` |
| waha-names-refresh-v1/wa_names.py | `800081a5817cbbca554bfc00d24e010ec8d20c07e46703ae0882e1816c02b2e7` |
| waha-names-refresh-v1/private_history.py | `e8f978759f517b5cdba381025e72435ac91698b09c5ccdb7a42c628d0fbc1907` |
| console-waha-history-raw-v1/history_index.py | `5bf374fba458876097368a35b2d2be9658148acf2c889d43b9f3dd63c0626610` |
| wechat-kb-dual-live-v1/video_knowledge/quota_isolation.py | `27fc420444a491a57586e0d2cc0c744af5c4374746987253dac13d49d484f1b3` |
| video-broker/20261012/knowledge_digest/daily_worker_candidate.py | `c2144b295b01b7d296fe773d5337f5759d4161c54bf9252fd7faf28cff07d538` |

仅前三个文件有 R7-A 生产补丁，其余是测试/证据基线。两份补丁 SHA 分别为 `ea312f77a9e108b2c0e67ad83c71880518b5a1c66537b1ed63604b8cf6c0eb61`、`c24a9dc8348bf06dd0b08efe4132842f90b2cee8380174aaf4a15ef3fc00457f`。若总指挥先合入 R3-C 等其他任务，引用基线变化时必须重新审查、更新基线并验证，不强行忽略校验。

只读复现方式：以下备份命令只在 /tmp 创建新目录，不读取配置，也不输出库内容。它不复制 WAL/SHM；副本完成后设为只读。需要足够的临时磁盘空间，各库不保证同一时点。

```sh
R7A_SNAPSHOT="$(mktemp -d /tmp/r7a-private.XXXXXX)"
export R7A_SNAPSHOT
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python - <<'PY'
from contextlib import closing
import os
from pathlib import Path
import sqlite3
root = Path(os.environ['R7A_SNAPSHOT'])
assert root.stat().st_uid == os.geteuid() and root.stat().st_mode & 0o777 == 0o700
sources = [
    ('/var/lib/imgw/wechat/api_state.sqlite3', 'wechat.sqlite3'),
    ('/var/lib/imgw/knowledge_digest/state.sqlite3', 'digest.sqlite3'),
    ('/var/lib/imgw/whatsapp-history-private/history.sqlite3', 'history.sqlite3'),
    ('/var/lib/imgw/whatsapp-text-analysis/history-raw/catalog.sqlite3', 'raw-index.sqlite3'),
    ('/var/lib/imgw/whatsapp/messages.sqlite3', 'whatsapp.sqlite3'),
]
for source, name in sources:
    target = root / name
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(Path(source).as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.execute('PRAGMA query_only=ON')
        with closing(sqlite3.connect(target)) as dest:
            db.backup(dest, pages=256, sleep=0.05)
    target.chmod(0o400)
print('五份私有只读副本已完成')
PY
```

再运行：

```sh
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/r7a/diagnose_snapshot.py --snapshot "$R7A_SNAPSHOT"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/r7a/verify_patch.py
```

`diagnose_snapshot.py` 拒绝生产路径、..、软硬链接、非当前用户、非 0700 目录、非 0400/0600 文件及带 WAL/SHM 的副本，仅输出固定枚举和计数；缺表不能当零。`verify_patch.py` 只读清单固定的源码，校验哈希，在新建 `/tmp/r7a-code-*` 目录 dry-run 再应用补丁，核对候选 SHA 并无 pyc 地编译，没有生产目标参数。

总指挥取得 journal 权限后可用下面命令筛选证据，**不要直接输出原始 journal**。筛选器只保留固定错误码/类型、固定源码路径和行号，丢弃其他消息字段和异常正文。首次日志若原本只有类型，不能靠新补丁恢复过去已经丢失的错误码。

```sh
set -o pipefail
sudo -n journalctl -u waha-names-refresh.service -u imgw-whatsapp-history-raw-index.service -u imgw-knowledge-daily.service --since '2026-10-24 00:00:00 UTC' --until '2026-11-01 15:21:00 UTC' --no-pager -o json |
  env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/r7a/journal_summary.py
```

### 虚构测试与已有回归

当前进程为 imgw；sudo 探测原始失败如下，按共同约定直接以当前身份运行：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

实际命令和最终结果：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/r7a" /opt/imgw/venv/bin/python -m pytest tests/r7a -q -p no:cacheprovider --tb=short
# 62 passed in 6.44s
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/pipeline" /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider --tb=short
# 117 passed in 22.82s
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python -m pytest tests/kb_recovery/test_isolation.py -q -p no:cacheprovider --tb=short
# 21 passed in 3.93s
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/audit" /opt/imgw/venv/bin/python -m pytest tests/audit -q -p no:cacheprovider --tb=short
# 裸快照：收集失败，见下面原文
```

新套件只摘出经过哈希验证的生产定义，不执行其模块启动代码；输入来自虚构 dataclass、fake WAHA、临时 SQLite。网络和外部子进程被禁止，只有补丁核对可执行 patch。覆盖原版“首 200 pending、溢出静默归档”的复现、新版连续 205 条静默来源、去重/原 scope/暂停不变、旧模式/公开模式/满队列/时间边界，以及错误码不泄露、失败仅一次、会话失败保留缓存、替换后报错不重试、两条隔离对三条未知的拒绝、失败日报不重调和私有路径校验。没有真实模型或真实渠道质量验收。

首跑新套件因我漏注入 fake record_media_progress 而失败，随后只补齐测试替身；原始结果和异常为：

```text
E           NameError: name 'record_media_progress' is not defined
8 failed, 46 passed in 3.82s
```

裸 audit 收集失败的异常及完整收集错误列表原文：

```text
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/audit/test_api.py
ERROR tests/audit/test_archive.py
ERROR tests/audit/test_channels.py
ERROR tests/audit/test_integration.py
ERROR tests/audit/test_migration.py
ERROR tests/audit/test_plan.py
ERROR tests/audit/test_review.py
ERROR tests/audit/test_service.py
ERROR tests/audit/test_wechat.py
!!!!!!!!!!!!!!!!!!! Interrupted: 9 errors during collection !!!!!!!!!!!!!!!!!!!!
9 errors in 0.86s
```

为核对相关功能，只将 `/srv/imgw-dev/ops/src/console/media/` 的源码复制进 `/tmp/r7a-audit-deps-*`，通过临时 sitecustomize 扩展包路径，未修改任何已有仓库文件。先补三个旧文件仍缺新增 alibaba 模块，第二次失败原文：

```text
E   ImportError: cannot import name 'alibaba' from 'console.media' (/tmp/r7a-audit-deps-hys93p7l/console/media/__init__.py)
!!!!!!!!!!!!!!!!!!! Interrupted: 9 errors during collection !!!!!!!!!!!!!!!!!!!!
9 errors in 0.74s
```

补全第四份源码后，临时闸门禁止读取真实业务库/配置、网络和子进程，并设置 OPS_MEDIA_KEY_PATH 到该临时目录的虚构密钥路径；没有读取、打印或复制任何真实媒体密钥。命令与结果：

```sh
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r7a-audit-deps-hys93p7l/fiction-media.key PYTHONPATH="/tmp/r7a-audit-deps-hys93p7l:src:tests/audit" /opt/imgw/venv/bin/python -m pytest tests/audit -q -p no:cacheprovider --tb=short
# 204 passed in 18.03s
```

四份临时依赖 SHA-256：__init__.py=`ced5f3a0f932bbaaa6140ae8ceb081161800f896b998260729deb97fef5880f8`，tokens.py=`25440e4ce6ad69f2e952aaa2c1d55e2d0070807f9731f898faba828ee58ed184`，files.py=`7c6a84e3819dc6a50e3e11c8e9f92ba6e5f941c656ba6eda9b6e808b5a21917b`，alibaba.py=`282823a19e141425fa2f352eccc9dff2ab019721f67d06a48cc13e2811f76c93`。它们是开发副本源码组合，不保证与无 Git 工作区同一提交。最新有效组合为 **404 passed、0 failed**；**裸快照的 audit 依赖问题仍未修复，不能写成干净快照全部通过。**

## 由总指挥执行的部署和回滚

以下是交接命令，**本轮零执行**。上线 e2e、真实演练和窗口由总指挥统一负责。三个候选不需要 schema 迁移；不升级 archive.sqlite3，不创建业务持久表。本轮工具只处理临时副本。

部署前：核实真实只收策略及暂停仍保持，完成总指挥统一 e2e 和补丁审查；选择上游允许的接收器维护窗口。暂停两个作业的 timer，确认已有 oneshot 自然结束，避免杀掉在途任务；不要为这些补丁启动日报或模型补处理。以下安装段由有权维护宿主机的总指挥在仓库根目录执行：

```sh
set -euo pipefail
systemctl stop waha-names-refresh.timer imgw-whatsapp-history-raw-index.timer
if systemctl is-active --quiet waha-names-refresh.service; then
  echo '名称作业仍在运行，请等本次结束后再安装'
  exit 1
fi
if systemctl is-active --quiet imgw-whatsapp-history-raw-index.service; then
  echo '历史索引仍在运行，请等本次结束后再安装'
  exit 1
fi
systemctl stop wechat-receiver.service
R7A_CANDIDATE="$(env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/r7a/verify_patch.py | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["候选目录"])')"
R7A_BACKUP="$(mktemp -d /var/backups/imgw-r7a.XXXXXX)"
chmod 0700 "$R7A_BACKUP"
for R7A_REL in wechat/api_groupbot.py console-waha-history-raw-v1/history_index_worker.py waha-names-refresh-v1/capture_names_worker.py; do
  cp --parents --preserve=all "/opt/imgw/$R7A_REL" "$R7A_BACKUP/"
  cp --preserve=all "/opt/imgw/$R7A_REL" "/opt/imgw/$R7A_REL.r7a-new"
  cat "$R7A_CANDIDATE/$R7A_REL" > "/opt/imgw/$R7A_REL.r7a-new"
  mv -T "/opt/imgw/$R7A_REL.r7a-new" "/opt/imgw/$R7A_REL"
done
printf '请记录代码备份目录：%s\n' "$R7A_BACKUP"
```

如任何基线或安装步骤失败，保持服务停止，先恢复本轮已覆盖的文件；不要继续到启动阶段。没有改 unit，无需 daemon-reload。经总指挥再次核对只收配置及保护闸门后，接收器恢复命令为 `systemctl start wechat-receiver.service`；若两个 timer 原本启用，恢复命令为 `systemctl start waha-names-refresh.timer imgw-whatsapp-history-raw-index.timer`。恢复索引 timer 会调用 BGE，刷新 timer 会做真实渠道 GET，均须留到总指挥批准的运维窗口，本报告不是启动授权。

名称刷新补跑的精确命令是 `systemctl start waha-names-refresh.service`；历史索引将来需要补跑时是 `systemctl start imgw-whatsapp-history-raw-index.service`，但当前不需要；`systemctl reset-failed UNIT` 只清 systemd 失败显示，不修业务原因，不能作为修复验证。未知发送或模型状态绝不通过 reset-failed 后循环 start 来重试。

代码回滚：继续保持两个 timer 停止，等正在运行的 oneshot 结束，停止接收器，然后使用安装时记录的 R7A_BACKUP，按同一三个相对路径恢复：

```sh
set -euo pipefail
systemctl stop wechat-receiver.service
for R7A_REL in wechat/api_groupbot.py console-waha-history-raw-v1/history_index_worker.py waha-names-refresh-v1/capture_names_worker.py; do
  cp --preserve=all "$R7A_BACKUP/opt/imgw/$R7A_REL" "/opt/imgw/$R7A_REL.r7a-rollback"
  mv -T "/opt/imgw/$R7A_REL.r7a-rollback" "/opt/imgw/$R7A_REL"
done
```

核对恢复后的三个原始 SHA，再按原策略恢复服务。**不回灌任何 SQLite、calls、usage 或原回调库，不解除 paused/hold，不把新 collected_silent 改回待发送。** 名称缓存如确需回滚，应先核对当前/previous 属于相同 binding，持有与 worker 相同的目录 flock，在私有目录另存当前缓存后原子替换为核对过的备份；不能在运行中裸 cp，也不能把另一账号缓存恢复进去。日报隔离变更的部署/回滚仍由原 R3-C/R5 方案负责，不在这三个文件的回滚范围内。

## 文件、共享合并、安全与未完成

新增 13 个文件，无原有文件修改：

- 本报告 `docs/wechat-stuck-and-jobs.md`。
- `deploy/r7a/{baseline.json,wechat-silent-admission.patch,job-errors.patch,verify_patch.py,diagnose_snapshot.py,journal_summary.py}`。
- `tests/r7a/{conftest.py,test_wechat.py,test_jobs.py,test_snapshot.py,test_patch.py,test_journal.py}`。

**需要总指挥合并的共享文件改动：**本轮没有直接修改共享文件。建议在 `web/src/data/requirements.json:532` 的 H8 说明中链接本报告，并写清“暂停下的 200 条旧回复队列、待审终态和未部署补丁；历史索引最近成功；名称刷新具体异常待证”，不要将全部 pending 当漏处理。H11 `:556` 补充日报受旧隔离逻辑阻断，归 R3-C/R5 联合处理；WG-13 的媒体快照依赖由总指挥统一补齐。README、master-plan、其他负责人的模块、web 路由/导航、rehearsal_server.py 均零改动。

安全自查：没有新增 HTTP 入口或权限，使用既有精确账号/群/scope/时间检查；没有网络调整、真实 GET、发送或模型调用。副本工具拒绝生产路径与软硬链接，SQL 动态表名仅来自内部固定列表、数据值参数化，不执行消息、附件或模型内容。错误输出用固定枚举，不输出异常正文、身份、配置值或正文；公开文件仅含源码补丁、虚构测试和获授权的脱敏计数。新入队状态仍在原 Store 的串行事务内，保留主键去重；没有新增重试器，错误或结果未知都不会主动再发。未新增持久业务库；未来旧状态修订审计如实施，须另有独立可配置库及单写者事务，不借此升级档案库。

未完成或不能证明：原 200 条未修改，当前动态授权和最初暂停原因未核实；名称刷新的历史具体 ValueError、历史索引旧失败的具体异常仍缺可读日志。日报的三条未知隔离恢复、历史失败周期的安全修订补跑未在 R7-A 实施，不能通过直接 restart 达成。没有真实模型质量、真实名称刷新/检索、全部媒体完整性、真实持续消息 SLA 或真实数据业务验收；裸快照仍缺 media 依赖。**404 项通过只证明列明的虚构数据及临时依赖组合，不证明真实数据可用。**

# 数据管道健康 v2（H8 / R4-A）

本轮为未部署候选。页面 `/app/system/pipeline`（前端路由 `/system/pipeline`），接口仍是 `GET /api/v1/pipeline/health`。
本轮只读核对生产源码和表结构，没有拿真实记录运行统计、验收或性能测试。所有自动测试、截图均使用虚构数据，不能据此认定真实数据完整、媒体链路接通或发送可用。

## 来源与时间口径

以下表和列已通过真实库的只读结构查询确认，实现全部沿用 `read_db`，不创建或迁移业务库。

| 来源 | 默认文件 | 读取内容与口径 |
| --- | --- | --- |
| 阿里入站 | `/var/lib/imgw/icbu/inbound.sqlite3` | `inbound.created_at` 为接收时间；仅提取 `payload.history`、`archive_reason` 判断实时/历史/未标明 |
| 阿里心跳 | `/var/lib/imgw/icbu/probe-status.sqlite3` | `probe_state.received_at`，最新 `snapshot_json` 的版本、状态、错误代码；每账号仅最新一条 |
| 微信消息 | `/var/lib/imgw/wechat/api_state.sqlite3` | `messages.created_at` 是发生时间，不能冒充接收时间；`status/updated_at` 用于积压检查 |
| 微信回调 | 同上 | `received_callback_archive.received_at` 是回调接收时间 |
| 微信视频号 | 同上 | `video_card_jobs.state`：indexed / 总数，blocked、download_unknown 分别列出 |
| 微信附件作业 | 同上 | `receive_collection_jobs.state`：complete / 总数；complete 只表示作业结束，不保证解析或索引成功 |
| 微信笔记附件 | 同上 | `company_note_assets.status`：ready / 总数；不能用 archive_status=ready 代替 |
| WhatsApp 消息 | `/var/lib/imgw/whatsapp/messages.sqlite3` | `messages.received` 为接收时间，状态事件不混入消息 |
| WhatsApp 桥接服务（辅助） | 同上 | `events.received` 统计服务事件，包含状态事件；time_basis=service_event、last_arrival_at=null，不表示消息到达，不参与渠道健康汇总 |
| WhatsApp 会话 | 同上 | 最近 session.status，仅返回 received、允许的 payload.status、状态来源和 me 是否非空，不返回原始 JSON、身份或其他字段 |
| WhatsApp 媒体作业 | 同上 | `media_jobs.status`，实际源码允许 archived/none/pending_media_url/deferred，其它值合并为“其他或未识别”，不输出原值 |

媒体作业分母是各自当前保留的全部登记记录，不是全部消息的附件覆盖率。WhatsApp media_jobs 来自既有知识处理作业，不等于 P2a 独立媒体库，更不能证明 P2a 入站采集已接通。零分母、缺来源、缺列、权限或超时不能算 100%。各库独立快照，不保证跨库一致性。

原来源卡保留 key/channel/label/ready/status/status_label/reason/next_step、last_seen_at/last_arrival_at/time_basis/count_1h/count_24h/hourly/notes。微信消息 time_basis=message_time、last_arrival_at=null；会话 time_basis=session_status、last_arrival_at=null。服务状态事件没有消息到达时间，不能让渠道变绿。

近 1 小时、24 小时为含起点和检查时间的滚动窗口；小时图为北京时间当前及前 47 个整点小时格，当前格仅算到检查时刻，不是精确滚动 48 小时。无效、非正数、未来时间不计；缺失统计为 null，不用零替代。心跳仅最新快照，count_1h/count_24h/hourly=null，不把账号行数当次数。

阿里分类不变：archive_reason=history_import 优先算历史；否则仅 JSON 布尔 history=true/false 分别为历史/实时，数值、字符串、无效 JSON、缺标记均未标明。占比分母含未标明，空窗口比例为 null。历史不刷新最后实时到达；实时标记也不证明来源、去重或草稿准入已验收。

## 会话、积压与健康规则

新增 whatsapp_session 输出当前状态及中文标签、has_me、last_working_at、failed_count_1h/failed_count_24h、restart_storm、连续非可用起点和采样范围。允许状态与已读过的渠道源码一致：WORKING、FAILED、STARTING、STOPPED、SCAN_QR_CODE、PASSKEY_REQUIRED、PASSKEY_CONFIRMATION_REQUIRED。me 只输出有/没有/未知，不输出其身份内容。`status_source` 为 `poll`（轮询）或 `event`（事件）；轮询不保存身份，has_me 为未知。

- 最新状态 WORKING 且记录不超过默认 6 小时才正常；过旧为“暂时读不到”。轮询只证明查询时的状态，事件只证明事件发生时的状态。
- 非 WORKING 立即提示变慢；连续非可用严格超过默认 5 分钟断流。连续段遇到 WORKING 或无法识别的状态即断开，不推测中间状态。
- 近 10 分钟事件型 FAILED≥20、无事件型 WORKING，且采样覆盖窗口、状态可识别，判“重启风暴”，立即断流。轮询记录不计入 FAILED 次数或风暴事件。提示“WhatsApp 会话没有登录成功，一直在失败重试”，下一步为渠道接入查看是否要重新扫码、请管理员查看 WAHA 容器。
- 仅消息空表、recipients 存在且为空、会话 WORKING 且记录未过期时，适用新号无联系人无消息正常等待。会话缺失、启动中、失败、过旧都不能套用；已有消息仍按消息空档检查。
- 默认最近 50000 条有效时间的状态事件，额外一条只用于识别截断。窗口未覆盖或状态无法识别时，精确失败次数为 null 并提供已见下限；不能证明完整 10 分钟时，风暴为未知。last_working_at=null 只表示采样中未找到，不表示从未登录成功。

桥接服务候选启动后台轮询，沿用原有配置和鉴权的 `GET /api/sessions/{session}`，默认每 300 秒查询一次；可用桥接配置 `session_poll_interval_seconds` 设置 1–1800 秒。首次、状态变化或距上次轮询落库满 1800 秒时，以 `payload.source=poll` 写入既有 events；不保存上游身份。失败只记录固定的 `session_poll_failed` 日志，不写状态，旧证据会照常过期。状态变化会立即落库；轮询记录不代表消息到达。真实 WAHA 接口契约尚待核实，参见 [WhatsApp 登录文档](whatsapp-login.md)。

微信只计 status=pending；最老消息年龄按 MIN(created_at)，最久未推进按 MIN(有效 updated_at，缺少时回退有效 created_at)，均不能冒充接收年龄。最久未推进严格超过默认 1 小时报“积压卡住”，新消息、回调再活跃也不能掩盖。缺状态列或有效时间为未知，消息统计仍保留；不推进队列或重试作业。

本轮有依据新增 pairing_reconnect，即使网关报告运行也按采集异常报警：“Win10 的采集网关在线，但浏览器扩展还没有与它重新配对”。下一步明确 Chrome 的“阿里聊天采集助手”已重新加载并连接，扩展程序页点一次，不要卸载。保留原有错误；现有源码未找到 pairing_required、extension_disconnected 的具体发出契约，没有猜测新增。未知错误仍按严格代码格式过滤，不输出原始错误正文。

状态：ok 正常、slow 变慢、interrupted 断流、never 从未有、not_connected 未接入、unavailable 暂时读不到。渠道汇总严重度：断流 > 暂时读不到 > 变慢 > 从未有 > 未接入 > 正常；WhatsApp 桥接辅助项排除。回调正常而消息旧、桥接事件正常而消息旧，会指出后续处理环节；这不是漏单证据。

| 来源阈值配置 | 变慢 | 断流 |
| --- | --- | --- |
| alibaba_inbound | 2 小时 | 6 小时 |
| alibaba_probe | 5 分钟 | 20 分钟 |
| wechat_messages | 2 小时 | 6 小时 |
| wechat_callbacks | 1 小时 | 3 小时 |
| whatsapp_events（辅助） | 24 小时 | 72 小时 |
| whatsapp_messages | 24 小时 | 72 小时 |
| whatsapp_session | 非可用立即提示 | 连续超过 5 分钟，或风暴立即断流 |

阿里日常健康仍按默认北京时间 08:00–24:00 白天、夜间按 1/4 计时，跨天积分，08:00 不突跳；明确异常不享折扣。记分卡按独立验收线，不把日常健康正常当验收通过。

## 验收记分卡与目标表

read() 与原 GET 新增 scorecard 数组。渠道项为 channel/label/result/result_label/metrics；指标为 key/name/target/actual/result/result_label/source/method/required。actual 只含计数、状态、时长等说明，无身份或正文；结果固定 pass/fail/unknown，中文“达标/不达标/未知”。

汇总只看 required=true：任一 fail → 不达标；无 fail 但有 unknown（或无必验项）→ 未知；全部必验项 pass → 达标。参考项明确标注，不参加汇总。零样本、缺来源或需要历史但只有最新快照，不能算 pass。“达标”只针对列出的当前可测口径，不保证未登记消息、媒体、真实发送、全部账号或完整业务验收。

阿里 A1–A8 仅摘目标值，不引用私有业务记录：

| 编号 | 项目 | 目标值 | 本轮范围 |
| --- | --- | --- | --- |
| A1 | 心跳连续性 | 24 小时按 120 秒发出，到达率≥99%，不得连续漏 3 次 | 未知，依赖另一任务新增逐次心跳流水 |
| A2 | 实时延迟 | 页面出现至持久确认，P95≤120 秒，最大≤10 分钟 | 未知，缺校时证据及页面出现/持久确认配对样本 |
| A3 | 对账 | 页面新消息数=服务器实时入库数，连续 3 天差异为 0，所有差异有原因 | 未知，缺页面侧每日计数、三天对账 |
| A4 | 去重 | 消息标识/指纹零重复，重放同批消息不增加行数 | 未知，缺受控重放前后计数及验收记录 |
| A5 | 实时到达 | 北京时间 08:00–22:00 持续到达，连续 2 小时无实时需说明 | 只检查当前白天空档，从当天 08:00 与最后实时到达较晚者计时；超 2 小时不达标。夜间、缺来源或白天尚无消息且不足 2 小时为未知；不是整段历史连续性证明 |
| A6 | 断点恢复 | 网关正常重启、扩展重载、网络短断分别在 10 分钟内恢复，无丢失、重复、游标回退且保留回执 | 未知，缺三类现场演练与读回 |
| A7 | 时钟 | 偏差<30 秒，≥300 秒暂停并报时钟异常 | 未知，服务器时间字段本身不能证明客户端校时及暂停行为 |
| A8 | 连续运行 | 满足 A1–A6 后，连续 72 小时无需人工干预，含完整白天业务时段 | 未知，缺前置验收和连续巡检 |

| 渠道 | 其它指标 | 目标和口径 |
| --- | --- | --- |
| 阿里 | 24h 实时数、最后实时到达距今 | 参考；无最低业务量，实际墙上时间空档不套作息折扣 |
| 阿里 | 心跳状态/原因、最近接收距今 | 可识别且未暂停、无已知阻断错误；接收≤5 分钟；未知状态不通过 |
| 微信 | 回调→消息 24h 比较 | 参考；回调数/按发生时间计的消息数，零分母无比值，差异不作为丢失证据 |
| 微信 | 消息最后到达 | 未知；表没有逐消息接收时间，仅补充最近发生时间 |
| 微信 | pending 积压 | 无停滞超过配置时限（默认 1 小时）的 pending；时间无效为未知 |
| 微信 | 三类媒体完成占比 | 登记记录 indexed/总数、complete/总数、ready/总数各为 100%，零分母未知；不是全部消息覆盖 |
| 微信 | blocked、download_unknown | 分别为 0；无作业为未知；下载未知不自动重试 |
| WhatsApp | 会话、最后 WORKING | WORKING 且记录未过期；最后一次为参考，采样外历史未知 |
| WhatsApp | 24h FAILED | 0；不完整为未知并显示下限，下限>0 已足以不达标 |
| WhatsApp | 消息最后到达 | ≤24 小时；符合会话可用条件的新号无联系人无消息可等待 |
| WhatsApp | 媒体作业分布 | 已登记媒体全部 archived，无等待/暂缓/未识别；none 不充媒体样本，零媒体未知 |

微信/WhatsApp 目标是本页可测运行检查，不替代阿里 A1–A8，也不保证所有附件、翻译均可用。

## 配置、覆盖率与后台作业

默认配置 `/var/lib/imgw/ops-app/pipeline-health.json`，环境变量 IMGW_PIPELINE_CONFIG 可覆盖。每请求重读，拒绝符号链接、非普通文件和超过 32 KiB；未知字段或范围错误整体回退默认并提示。paths 仍只接受 inbound/probe/wechat/whatsapp 四个服务端绝对路径，HTTP 不接受路径、SQL 或查询参数。省略项继承默认。

新增配置项（本轮没有创建生产配置）：

```json
{
  "thresholds": {"whatsapp_session": {"slow_seconds": 60, "interrupted_seconds": 300}},
  "whatsapp_session_event_limit": 50000,
  "whatsapp_session_max_age_seconds": 21600,
  "wechat_pending_stuck_seconds": 3600,
  "query_timeout_seconds": 1
}
```

采样整数 20–200000；会话证据时限、积压时限 1 秒–90 天。会话特殊规则立即提示，不等待 slow_seconds；该值仅保留统一阈值结构。其它阈值在 1 秒–90 天内、变慢小于断流；查询预算 0.01–3 秒。alibaba_day 白天起点须早于终点、夜间倍率 1–100，不支持跨午夜白天。记分卡 A5 固定 08:00–22:00，心跳接收 5 分钟、WhatsApp 消息 24 小时亦为独立验收线。

build(..., paths=..., config=..., clock=...) 保留；显式注入任一来源后，缺省来源、配置和运营计划均隔离到同一临时目录，不回落生产。

coverage_readers[(channel, metric)](now) 仍只接受整数 (分子,分母) 或 None；附件分母是消息、翻译分母是会话，不能拿作业行数代替。本轮未连接旧“附件与翻译覆盖率”卡：P1 公共服务没有按渠道、当前阅读语言、有效缓存汇总的只读计数接口。没有绕过公共服务读语言表或创建第二套语言数据。媒体作业完成率已在记分卡实际计算，不冒充旧覆盖率。未注入适配器时旧卡仍如实显示未接入，异常或非法计数独立降级。

failed_units_reader(now) 是可选服务端注入函数，返回最多 100 个 {unit, failed_at}，或 None 表示未接入。仅保留 service/timer 单元名与数字失败时间，时间没有就 null，不拿最后启动时间冒充失败时间。多余字段丢弃，非法名称、未来时间、异常统一暂时读不到，不透传错误。

现有源码无可复用 systemd 读取能力，因此默认不运行命令、页面明确未接入，没有新增特权路径。以后生产提供者只允许配置的作业范围、只读 systemctl show 指定状态/时间属性；不读 Environment、ExecStart、日志或凭据，不 sudo、启动、重启或 reset-failed。本轮没有生产接线，未以空清单假装已检查。

## 安全与局限

SQLite 独立只读连接、query_only、短事务、20 ms 忙等、截止时间，必定关闭；无 WAL 用 mode=ro&immutable=1 并核对前后文件，有 WAL 用 mode=ro 读取已提交记录。缺 SHM、有回滚日志或读取间快照变化即降级。不写库、不迁移、不加索引、不新增持久化、不升级 archive.sqlite3。

接口继续用 inbox.http.get_handler 的登录、旧会话、读后重新验权、线程池、中文错误和 no-store；仅 GET/HEAD，无 CSRF 例外或新增审计写入。标识符固定、SQL 值参数化、路径只由服务端配置，页面按文本渲染。无网络、WAHA、模型、发送或重试；不输出正文、身份、附件路径、原始 JSON、完整心跳、SQL 错误和库路径。

依然全库聚合，不能逐账号验收；轮询仅反映查询时刻，归档状态不是实时在线探测，清理/采样范围外无法补算。生产量级查询预算未验收，不能为本页改业务库。页面打开才每 60 秒刷新，失败保留旧结果并提示，没有常驻巡检和主动通知。

## 合并、部署与回滚（由总指挥执行）

工作区是无 Git 元数据的独立快照。本轮没有 commit、push、部署、生产写入、改网络、真实发送、固定端口服务或 e2e_smoke。没有改路由、导航、API builder、scripts/rehearsal_server.py、README、master-plan 或 requirements.json。

后端审查补丁：[pipeline-health-v2.patch](../deploy/pipeline-health-v2.patch)，仅 pipeline 内 3 个修改、2 个新增文件。生产只读基线：[pipeline-health-v2-baseline.sha256](../deploy/pipeline-health-v2-baseline.sha256)。本轮三个原后端文件与生产哈希相同，基线不同须先人工合并。生产 v3.py:59 已挂载 v3_pipeline_api，不要重复套用上一批 pipeline-health-v3.patch。

1. 审查并合并本轮模块、页面、测试和文档；补齐 WG-13 同版本 console.media 与演练测试隔离后跑完整相关回归。
2. **需要总指挥合并的共享文件改动：** web/src/data/requirements.json:532 的 H8 保持 partial；now 建议“v2 候选新增会话/风暴、积压、三态记分卡，虚构测试通过，未部署未实测”；next 建议“接心跳流水、延迟/对账证据、可选后台失败读取与附件/翻译公共覆盖率，补齐快照依赖后统一演练”。其他共享文件无需改；路由、导航、演练挂载零改动。
3. 候选执行 `cd web && npm run build`；真实数据只读演练、浏览器验收、scripts/e2e_smoke.sh 由总指挥统一跑，本轮未跑。
4. 部署前核对 baseline SHA-256，备份 `/opt/imgw/wechat/console/pipeline/`、`/var/lib/imgw/ops-app/dist/` 和健康配置；记录新增模块此前不存在，保留同次备份。
5. 审查后将补丁装入 `/opt/imgw/wechat` 对应 pipeline 文件（先在候选验证补丁）；将本轮 web/dist 发布至已核对的 v3_app.DIST_DIR=`/var/lib/imgw/ops-app/dist`。沿用已有路由，不改接收端、不迁移或创建统计库，旧配置省略新项可用默认值。
6. 按既有流程重启控制台；核对登录后 GET 的 7 个来源、3 个记分卡、失败作业未接入提示、读后撤权以及现场风暴/积压的只读观察，现场未核对不能标真实可用。
7. 回滚恢复同次备份的 pipeline、dist、配置并重启；移除本轮新加且此前不存在的 jobs.py、scorecard.py，避免覆盖同时合并的其它任务。没有业务库或接收状态需回滚，不重放、不补发。

## 本轮测试记录

sudo 尝试被环境拒绝，当前进程已是 imgw，按允许回退直接运行：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/pipeline" /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider
env OPS_MEDIA_KEY_PATH=/tmp/r4a-media-test.key PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/channels" /opt/imgw/venv/bin/python -m pytest tests/channels -q -p no:cacheprovider
env OPS_MEDIA_KEY_PATH=/tmp/r4a-media-test.key PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/inbox_api" /opt/imgw/venv/bin/python -m pytest tests/inbox_api -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/inbox_api" /opt/imgw/venv/bin/python -m pytest tests/inbox_api/test_model.py -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/web_app" /opt/imgw/venv/bin/python -m pytest tests/web_app -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/whatsapp" /opt/imgw/venv/bin/python -m pytest tests/whatsapp -q -p no:cacheprovider
cd web && npm run build
PLAYWRIGHT_BROWSERS_PATH=/tmp/pipeline-browsers node --test tests/pipeline/frontend.mjs
```

node 命令在仓库根目录运行；前端依赖复制至本工作区，浏览器复用 /tmp 已有 Chromium，不开 HTTP 端口，全部请求拦截成虚构响应。基线管道 77 passed；最终管道 110 passed，0 failed（10.28s）。相关回归：网页资源 6 passed、WhatsApp 桥接 5 passed、模型 18 passed / 37 subtests passed，均 0 failed。前端 npm run build 通过（tsc --noEmit 与 vite build）；浏览器 6 passed、0 failed，包含 1440/390/320 像素、小时图、刷新/失败恢复、未知记分卡、风暴和失败时间；虚构截图在 /tmp/pipeline-preview/，不是生产截图。

最终后端补丁在临时副本 dry-run、实际应用后 5 文件逐字核对、反向回滚均通过；生产只读基线 SHA-256 的 8 项校验全部 OK。没有执行生产补丁应用。

完整相关套件仍因 WG-13 的快照依赖缺失阻断，失败摘要原文：

```text
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/channels/test_adapters.py
ERROR tests/channels/test_api.py
ERROR tests/channels/test_whatsapp_login.py
ERROR tests/channels/test_whatsapp_login_api.py
!!!!!!!!!!!!!!!!!!! Interrupted: 4 errors during collection !!!!!!!!!!!!!!!!!!!!
4 errors in 0.80s

E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/inbox_api/test_api.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.42s
```

没有改其它任务模块、注入假媒体依赖或删断言使之通过。完整回归尚未通过；真实状态、生产性能、现场 SLA、后台失败生产接线和旧覆盖率适配器均未完成验收。

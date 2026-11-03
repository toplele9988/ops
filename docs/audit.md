# F3 数据体检后端

将微信、阿里国际站、WhatsApp 和业务档案中的数据缺口整理为规则问题清单。每项说明缺口意味着什么、应到哪里补；只提供建议，不自动接入群、补采、重试任务、合并档案或发送消息。岗位为 `audit`「数据体检员」，纯规则 L0，不调用模型。

这是开发候选，未部署。没有修改 `web/`（包括需求账本）、`/opt/imgw` 或生产配置，没有 commit/push。字段依据仓库源码、既有上下文、必要生产源码与 `sqlite3 -readonly … .schema` 核实；测试和性能数据全部虚构，不联网。

## 接口与输出

| 接口 | 行为 |
| --- | --- |
| `GET /api/v1/audit` | 当次读取全部来源并体检，按 high、medium、low、ok 排序，同级按 channel/key 稳定排序 |
| `GET /api/v1/audit/{channel}` | 仅计算 wechat/alibaba/whatsapp/archive 中指定渠道；其他值 404 |
| `POST /api/v1/audit/run` | 空 URL 编码业务表单，重新体检，将完整报告写入档案库 `role_runs`，响应额外带 `run` |
| `GET /api/v1/roles` | 登记「数据体检员」，显示 L0、纯规则、最近运行及 24 小时次数 |
| `GET /api/v1/roles/runs?role=audit` | 复用既有运行历史接口查看已保存报告 |

每个 `items` 元素：`key`、`channel`、中文 `title`、`level`、非负整数 `count`、`meaning`、`remedy`、最多 5 个 `samples`，以及本项的统计元数据。样例标识是 SHA-256 截短散列，名称、手机号、会话 key、聊天正文、价格、文件路径、媒体 URL 和配置凭据都不输出。前端路由采用代码中的固定允许列表，避免被通用路径脱敏误隐藏。

总览字段：`summary`、`attention_count`、`planned_count`、各级 `counts`、`total`、`failed_count`、`skipped`、`skipped_reasons`、UTC `generated_at`、`source="rules"`、`level="L0"` 和 `plan`。`attention_count` 只计 high/medium 真问题（包括读取失败）；low 项单列在 `planned_count`，summary 在有 low 项时显示“；计划中 N 项”。`counts` 仍保留全部级别，旧字段不删除。N 按**检查项数**计算，不把报价、消息、群等不同单位的数量相加。同一记录可同时触发多个检查。

渠道运营计划是只读角色配置，默认值写在 `console.channel_plan`：`whatsapp=not_started`、阿里主账号 `operating` / 其余账号 `planned`、微信 `operating`。配置文件默认路径为 `/etc/imgw/channel-operation-plan.json`，`Audit(plan_path=...)` 可注入路径；缺失、不可读、格式错误或结构不在白名单时回退代码默认值。配置只允许渠道状态和 `main/others` 角色，不接受账号名称、凭据或其它字段。对外 `plan` 只给状态和固定约定计数：阿里 6 个账号、主账号 1 个、其余 5 个。

单项读取失败返回 medium、`read_failed=true`、固定安全原因和「读取失败：…」。此时 `count=0` 表示数量未知，不能当成没有缺口。文件不存在、权限、格式/结构、超时、快照变化分别处理；原异常和配置内容不会透传。其他独立检查继续。可选 extract 缺表放在 skipped，既不当健康项，也不算失败。

鉴权、recheck、线程池、no-store/nosniff、URL 编码表单限制及 429/Retry-After 契约沿用 `v3_roles_api.py`。GET 返回前复核身份，POST 在读取表单和写入前复核。8 KiB/40 字段、单值、严格 UTF-8、未知字段和错误媒体类型限制复用 `form_fields`。CSRF 由既有宿主中间件验证，不能单独挂载绕过中间件；测试含宿主 CSRF 闸门。L0 不占用 AI 调用额度，与巡检纯规则路径一致，API 保留岗位额度错误处理。

## 微信口径

目录四项复用 `WechatDirectory.audit_snapshot()`，与原目录的并集、配置角色优先级、命名规则和采集状态一致。新增路径只读取身份/名称元数据和 SQL 群统计，不加载收件箱全部消息，不生成媒体令牌。角色 scope 以现有 app/message 边界取众数、平票按角色字典序；这只是来源角色元数据，不证明已生成有效收件箱会话。

| key（前缀 wechat.） | 判定和级别 | 这意味着什么、怎么补 |
| --- | --- | --- |
| not_enrolled | 通讯录并集 `in_directory` 且 `registered=false`，数量>0 medium | 已发现但未登记；历史可能仍有消息。系统后台 → 渠道接入 → 微信通讯录，核对准确群及用途，走现有授权接入流程 |
| silent_groups | 已登记且 enabled=true，目录状态 enrolled_silent/enrolled_no_messages；近 7 天含边界没有消息，medium | 可能群安静或采集中断；在微信通讯录对照原群和接收服务，不凭静默断言丢失 |
| role_conflicts | 完全复用目录 role_conflict，包括明确配置与命名方向冲突及 SUP/CUS 双标记，high | 可能影响分类/可见范围；微信通讯录核对业务用途后修正配置或名称 |
| unclassified_groups | 合并目录的 role=unclassified，low | 尚未明确用途；在微信通讯录确认客户/供应商/学习/内部等分类 |
| media_archive | `company_media` 全部登记项减去 archive_status=ready、64 位小写十六进制 archive_sha256、archive_bytes>0 的项，medium | 提供 total/archived/archive_rate；系统后台 → 微信 → 附件采集核对下载与归档。仅核元数据，不逐文件验 SHA；不包括从未登记的附件，归档不等于解析/索引 |
| jobs_raw_no_text | receive_collection_jobs.state=RAW_ARCHIVED_NO_TEXT，medium | 原文件已存并核验，但没有可用正文，可能等云解析配置/额度或格式不支持；在附件采集核对解析，不能称原文件丢失 |
| jobs_failed | state 为 interrupted/source_changed，或非 RAW 状态且 result_status 含 failed/为 source_revoked/source_changed，high | complete 也可能是 download_failed；重试中的失败仍统计。检查来源、下载和解析，不盲目重跑或清队列 |
| jobs_stuck | processing.claimed_at 超过 600 秒；retry_wait.not_before 逾期 3600 秒；RAW 的 waiting_cloud_configuration/budget/quota 到期超过 3600 秒；saved_original_needs_review.updated_at 超过 3600 秒，high | 任务占用、等待或人工复核超时；后台核对任务/额度及结果，再决定处理 |
| callback_drop | received_callback_archive：近 24h `[now−1d,now]` 数量低于前 7 天 `[now−8d,now−1d)` 日均的 30%，high；恰好 30% 不报 | 回调不等于消息。基线为 0 时明确无法比较；微信渠道后台核对回调接收链路与正常业务量 |
| discovery_stale | 发现库 snapshot_runs 中 state=observed_unverified、有效 finished_at 的最近时间超过 24h，或没有成功记录，medium | partial_source/未知状态不刷新成功时钟；后台核对联系人发现定时器和最后结果。observed_unverified 是成功接收来源列表，成员身份/全量完整性仍未验证 |

未来时间不用于证明健康；目录明确关闭/启用未知的群不混进“已启用但静默”。发现库、配置、消息等必要来源缺失时，不把未知误报成零。

## 阿里国际站口径

期望账号数 6 来自已有渠道业务约定，不是平台账号发现结果。消息字段及有效性沿用已核实 inbound.payload 契约；SQL 只在库内判断正文中的占位，Python 只接收计数、时间和脱敏标识。

| key（前缀 alibaba.） | 判定和级别 | 这意味着什么、怎么补 |
| --- | --- | --- |
| accounts | `max(0,6−有有效消息的账号数)`；主账号没有数据为 high；主账号有数据且缺失均为计划中的子账号时为 low | 仍显示“约定接入 6 个账号，目前 N 个有消息”。默认 `N=1` 时说明“另外 5 个子账号计划在自动发品和 AI 运营跑通后启用，目前没有数据是正常的”。不把账号名写进计划；现有业务约定下以唯一有消息的账号作为运行中主账号 |
| probe | 运行中的主账号心跳距今>6h、无记录或未来/非法时间为 high；计划中子账号没有心跳不报 | 对照阿里收件账号与探针账号的散列标识，只在本地匹配，不输出名称。输出最后上报 script_version/age_seconds；版本仅允许版本号格式，不说明当前安装版本。检查 Chrome、Tampermonkey、消息页、休眠和网络 |
| latest_messages | 每账号 MAX(payload.sent_at)，>7d/未来时间为 medium，无有效消息也提醒 | `accounts` 为每账号散列标识与最近时间，samples 最多 5。旧消息可能只是安静；结合心跳和原平台核对 |
| attachments | 有效消息 content 含 `[图片]`/`[文件]`/`[附件]`/`[视频]`/`[语音]` 占位，medium | 当前接收契约没有二进制文件归档字段，因此是可见缺口下限；0 不证明附件完整。`/inbox` 对照原会话，后台补附件采集或人工补原文件 |
| buyer_gap | role=buyer 且 history=false，按 created_at 入库时间；前 7 天（排除近 24h）distinct 到达时间至少 3 个正间隔。当前空档>max(6h,3×均间隔)，high | 排除历史补采和卖家消息；样本不足明确无法判断。后台/原平台对照最近买家消息核实是否漏入库，规则不能证明漏收 |
| duplicate_buyers | 同一账号内，每 conversation 最新 buyer_name 去首尾空格并 lower 后同名、会话数>1，low | count 是同名买家组数，extra_conversations 是多余会话条数；不跨账号，不把同名当同人。`/inbox` 核对平台身份，再到 `/customers` 整理关联 |

## WhatsApp 口径

默认完全离线：读取桥接 `messages.sqlite3`，不连接 WAHA、不读取其认证字段。会话状态来源是已归档事件；宿主可注入 `session_status()`，返回允许状态字符串或 `{status,received_at}`，测试只用假函数。

| key（前缀 whatsapp.） | 判定和级别 | 这意味着什么、怎么补 |
| --- | --- | --- |
| session | events 最新 session.status 的 payload.status 非 WORKING，或证据>6h/无记录/未来；operating 为 high，`not_started`/`planned` 为 medium | 未开始运营时写明“还没登录；准备开始运营时到 系统后台 → 渠道接入 扫码登录”；operating 下保持原告警。显示 observed_at/source，不能声称实时在线 |
| latest_message | messages 中 direction=inbound 的 MAX(received)，>7d 为 medium；兼容保留现有 `whatsapp.latest_message` key | `not_started`/`planned` 且从未有过入站消息时为 ok，说明“WhatsApp 尚未开始运营，暂无数据”；曾收到过消息后断流仍为 medium。operating 下保留原规则；`/inbox` 与原渠道对照 |
| media | has_media=1 且缺 media_jobs 行、或 status 不是 archived，medium；无任务表按尚未处理统计 | pending_media_url/deferred 等不算下载归档；none 仅无媒体，归档后未解析不计入。`/inbox` 核对附件，后台检查媒体任务并补原文件 |
| auto_send | 只允许配置的显式布尔；false 为 ok，true 为 high，缺失/字符串等读取失败 | 系统后台 → 渠道接入 → WhatsApp，由管理员将桥接配置 auto_send=false，再按后台流程重载。只读检查本身不改配置 |

## 档案与业务口径

日期采用 Asia/Shanghai 业务日期，可通过 today 注入。价格采用现有 `archive.validity` 的**当前 confirmed 记录**：供应商报价排除被 confirmed 更正取代的记录，再按 quoted_on/id 取最新；suggested/rejected 更正不替代原记录。运价按现有精确线路键取最新。过期/无有效期的最新价格不回退旧价，隐藏产品不参与价格与包装检查。

| key（前缀 archive.） | 判定和级别 | 这意味着什么、怎么补 |
| --- | --- | --- |
| offers_missing_expiry | 当前已确认供应商报价 valid_until 空，medium | 无法判断能否沿用；`/products` 向供应商确认有效期后补新报价 |
| expired_prices | 当前供应商报价与运价 valid_until<今天，high，到期当天仍有效 | 输出 offers/freight 分项；`/products`、`/freight` 获取新报价再使用 |
| missing_packing | 未隐藏产品缺 carton_l_cm/carton_w_cm/carton_h_cm/gross_kg 任一，medium | 运费估算缺依据；`/products` 核对并补外箱三边与毛重。此项不把装箱数或人工确认状态混入“字段缺失” |
| duplicate_suppliers | 未 archived 的 supplier 经现有 seed.short_name 去常见地区/公司/行业尾词，再 NFKC/casefold/去标点，归一后相同且至少两字，low | count 为每组 n−1 的总和；附重复组数和涉及档案数。`/suppliers` 核对来源/联系方式后人工整理，不做任意编辑距离匹配或自动合并 |
| deals_without_conversations | 已建 deals 无与 deal_participants 同 party 对应的 deal_conversations，medium | 包含已结束案子的结构缺口；`/deals` 补参与方并关联会话，只有单位档案不算完成 |
| pending_triage | triage_suggestions.status=pending，low | 仅已扫描队列，未扫描会话不在数量内；`/triage` 确认归属或忽略，必要时扫描新会话 |
| pending_prices | 未发现 extract/extract_* 表时跳过 | 本次源码和真实只读结构均无 extract 表，不能编造字段。未来出现未核实的结构时仅本项读取失败，待实际迁移/表契约接入；尚未实现该存在态的价格建议统计 |

## 存储、性能与只读边界

新 `migration_v6.py` 仅扩展 role_runs.role 的 CHECK 以允许 audit，不修改已上线 v4 或假设 v5 字段。保留已有列、行、外键引用、索引、触发器和视图；v2/v3/v4 旧路径仍保留各自目标版本。可接受已安装的 additive v5 版本标记并保留其结构，但本次没有实际 v5 文件可作生产兼容验收。首次显式 POST 才迁移写库，GET 不创建库、不迁移。演练脚本按现有动态列表自动挂载，运行结果只落演练临时档案副本。

`Audit` 可注入全部来源路径、clock/today、directory、session_status 和 `plan`/`plan_path`；显式测试来源会把未指定来源隔离到同目录 disabled 路径，包括计划文件。档案运行库不得与任何渠道库/配置同路径、符号链接别名或硬链接别名。

所有来源 SQLite 使用 mode=ro、query_only；大表在 SQL 内计数/聚合，样例 SQL LIMIT 5，不加载全量聊天到 Python。同次扫描共享目录元数据和阿里聚合，跨扫描重新读取，POST 不复用旧报告。总查询预算 2.65 秒，首次共享聚合分配较多时间，未用时间可向后结转；每项保留自己的份额。超时中断该项 SQL，继续后续检查。注入的外部 Python provider 须保持快速；底层 I/O 故障无法保证严格硬实时。

无活动 WAL 时用 immutable 只读快照避免创建侧文件，并拒绝非空 rollback journal、检查读取前后文件变化；有 WAL 时必须已有 SHM，避免在源目录新建文件。主库和 WAL 的数据哈希不变。**SQLite mode=ro 读活动 WAL 仍可能更新既有 SHM 的读者协调标记，因此不承诺 SHM 字节哈希不变**；不会写业务行或检查点。测试覆盖每次 WAL 新提交可见、拒读未提交 rollback 页和新建侧文件保护。

配置读取仅让目录允许字段、auto_send 布尔值或运营计划中的角色状态离开局部读取函数。真实配置未被打印/复制，异常不回显；测试配置专门放入假密钥、令牌和 Cookie 验证不泄漏。

## 验证与文件

测试入口：`tests/audit/`；逐检查触发/不触发、边界、失败隔离、脱敏、只读哈希、完整报告排序/总览、鉴权/CSRF/recheck/表单、运行历史、迁移与性能。渠道计划新增仅用虚构配置的 operating/planned/not_started、读取失败回退和汇总覆盖；channels 与 inbox_api 的旧文案断言同步更新。

2026-10-30 渠道运营计划校准后离线回归（全部通过，无跳过）：

| 套件 | 通过数 | 额外 subtests |
| --- | ---: | ---: |
| audit | 204 | 0 |
| channels | 193 | 14 |
| inbox_api | 37 | 99 |
| 本次合计 | **434** | **113** |

改前相关基线分别为 audit 191、channels 190（14 个 subtests）；audit/channels 合跑会因两个目录均有 `test_api.py` 而发生 pytest 模块名冲突，因此分开启动进程。此轮未重跑 roles、archive、directory。

虚构性能验收：阿里、微信各 20,000 条消息的完整冷扫描 `<3s`、26 项均无读取失败；另测阿里 100,000 条消息的共享聚合 `<3s`（单独测量约 1.67 秒，造库不计时）。这些规模测试未读取真实业务数据，也不构成任意数据规模或磁盘故障下的时间保证。

复现本次相关离线测试：`PYTHONPATH=src /opt/imgw/venv/bin/pytest -q tests/audit`、`... tests/channels`、`... tests/inbox_api`，每个套件单独运行。测试使用虚构数据库和配置，不发起网络请求。

新增：

- `src/console/channel_plan.py`：渠道运营计划白名单读取、默认值、角色计数和失败回退。
- `src/console/audit/__init__.py`、`common.py`、`service.py`、`wechat.py`、`alibaba.py`、`whatsapp.py`、`archive.py`。
- `src/console/v3_audit_api.py`、`src/console/archive/migration_v6.py`。
- `tests/audit/conftest.py`、`test_wechat.py`、`test_channels.py`、`test_archive.py`、`test_service.py`、`test_api.py`、`test_migration.py`、`test_review.py`、`test_integration.py`。
- `tests/audit/test_plan.py`：渠道计划默认、路径读取、失败回退和 API 注入。
- `docs/audit.md`。

修改：

- `src/console/archive/store.py`：新版本入口与旧调用兼容。
- `src/console/roles/common.py`、`service.py`：登记岗位及纯规则显示。
- `src/console/directory/service.py`、`sources.py`、`refresh.py`：复用目录的只读聚合路径和可注入数据库读取，不改变原目录默认接口。
- `scripts/rehearsal_server.py`：自动挂载，运行记录指向临时副本。
- `tests/roles/test_roles_migration.py`、`test_roles_api.py`、`test_roles_safety_cli.py`：新增岗位/路由的预期。
- `tests/directory/test_api.py`、`tests/channels/test_whatsapp_rehearsal.py`：新增模块的演练隔离。
- 本任务另修改 `src/console/audit/service.py`、`alibaba.py`、`whatsapp.py`、`src/console/v3_channels_api.py`、`tests/audit/test_channels.py`、`test_service.py`、`tests/channels/test_api.py`、`channel_fixtures.py`、`tests/inbox_api/test_api.py`，并更新本文及 `docs/channels.md`。

未做：前端、生产迁移/部署、真实业务全量体检、联网/扫码验收、自动修复、附件二进制核验、extract 已存在结构的接入。未运行需联网的上线浏览器 e2e；本次虚构测试不是生产验收。

# C 批：内部 AI 岗位后端

## R2-C：同一等待口径与事项催问（WG-04）

本节覆盖下文历史记录中“岗位额外处理方向和结束语”的旧差异。案子、秘书和巡检现在共用
`deals.waiting.observation/evaluate`：未知方向不覆盖已有有效方向和等待起点，结束语不制造待回复工作；
每条已关联会话分别保留方向和等待，不能用同单位另一会话的新消息盖住旧等待。参与方的 `state`
仍为 `us/them/none`，另用 `availability=unlinked/unreadable/unknown/readable` 区分尚未关联、已关联不可读、
最新方向未知和正常读取；不可读不是没有记录。连续的我方消息保留第一条未获对方回应的等待起点。
秘书逐会话说明对象和下一步，巡检以相同会话的 `since` 判断超时。

老板可以另外登记“请甲供应商核对包装”“请乙供应商确认交期”等具体事项，并明确等待起点。
具体事项的结束由人工确认，不会被其他话题的回复自动关闭。生成催问草稿不表示已经催问；
“人工记录已催问”只更新 `last_reminded_at`，始终保留原事项的 `since`。重复登记和重复记录催问幂等。
秘书与巡检也只读这些事项，因此一条事项在多会话、催问后或服务重建后仍有同一起点和对象。

独立库默认 `/var/lib/imgw/ops-app/deal-followups.sqlite3`，可通过 `IMGW_DEAL_FOLLOWUPS_DB` 或
`Followups(path=...)` 配置。非默认档案路径的隔离服务默认使用档案同目录文件。
`Roles(followups_path=...)` 可指定同一文件；GET 不创建库、不迁移档案库。所有事项写入经跨进程锁和
`BEGIN IMMEDIATE`，审计只保存字段名。拒绝档案库与事项库共用路径、父目录或 SQLite 旁路文件符号链接。

`Followups.read(deal_id)` 返回参与方/会话的可读状态和事项列表；
`write(operation,deal_id,fields,who,child_id=None)` 接受 `create/resolve/remind/draft`。
create 只允许本案参与方及其已关联会话，可用 `item_id` 指定本案产品项；remind 要求幂等 `request_id`；所有子项校验案子归属。
调用草稿时会重新核对关联，已移除的归属不能继续起草。

起草复用 `inbox.ai.make_followup_prompt/parse_output`，按客户、产品供应商、货代、配套供应商分别询问。
模型只接收具体事项、指定单项产品的名称/数量及其已接受需求候选中的目的地/规格/配套要求和人工补充。
未指定产品时不带入全案产品，避免不同产品串线；不读取任意产品备注或候选原文作为提示资料。
模型不接收秘书内部摘要、内部备注、
成本、利润或其他会话。新加数字、型号或链接的原稿被拒绝；每次只调用一次受控 runner，外层不自动重试。
每小时最多 20 次起草调用，失败也占一次。先通过 `Lang.read('contact',...)` 核对母语，
母语未知或自动识别把握不足时要求先确认，不调用起草模型；原稿经 `Lang.write('compose',...)`
生成母语稿、回译和保留片段检查。草稿与回译仍需人工核对，没有任何发送调用，也不自动登记已催问。
原稿的数字检查与回译不能证明语义正确，真实模型质量仍需另外验收。

验证新增 `tests/deals/test_followups.py` 和 `tests/roles/test_waiting_contract.py`，全部使用虚构会话、
临时 SQLite、fake runner 和临时媒体 key 路径；覆盖四类对象、同单位跨会话、催问不重置等待、
三种缺数据状态、母语未知、注入数字拒绝、归属变化、事务幂等与路径保护。未部署、未读真实业务库、
未连接真实模型或渠道，不能把虚构测试通过视为真实数据可用。

E1 后端已接入收款与履约巡检，详见 [收款确认与履约](fulfil.md)：成交金额优先 confirmed 收款净额，缺收款时仍用最新 accepted 销售额，每案标明 source=payment/quote；新增履约计数及可配置提醒。既有无收款 fixture 的金额断言保留，另增 E1 口径测试；演练路由断言从 42 调整为 59。下文 C 批记录保留其历史范围，E1 同样未部署。

本批提供案子秘书、分拣员、巡检员、每日成交简报及运行记录。产出供老板内部查看，包含人话结论和下一步。没有前端、定时任务、部署、真实模型调用或渠道发送。本次没有 commit/push，没有改 `web/`、`/opt/imgw` 或生产文件。

## 岗位与自主级别

| key | 名称 | 级别 | 产出与依据 |
| --- | --- | --- | --- |
| secretary | 案子秘书 | L1 | 案子、参与方、各关联会话最近 30 条消息、待办、最新报价轮和知识来源；生成现状、每方等待事项/时长、最多 3 个下一步、最多 3 个风险、建议阶段 |
| triage | 分拣员 | L0 | 为近期未入案会话建议关联案子、新建案子、确认往来单位或忽略；确认前只有建议 |
| patrol | 巡检员 | L0 | 纯规则检查到期、等待、付款及停滞，生成可忽略、可自动解决的提醒 |
| brief | 每日成交简报 | L0 | 确定性计算今天/本周/本月成交、金额、在谈、阶段、报价拉扯、丢单和最多 5 件重点；AI 只润色 narrative |

岗位自动运行只写本批新增表及字段名审计，不调用 deals 的更新/阶段接口，不调用 quotes 的任何写接口，不写收款状态、不创建待发送队列、不发送消息。`suggested_stage` 只是建议，没有执行入口。

唯一会改业务资料的操作是**人点击接受分拣**：复用 `Deals.write(create/conversation/identity)` 和必要的 `Archive.write(party_create)`。通过借用同一事务的 Store 协议，保留原服务校验、身份冲突、时间线及审计，连同决定记录一起提交或回滚；重复接受返回同一案子/单位。新案子从 inquiry 开始，接受表单不允许 stage、status、金额或付款字段。

## 存储与 v4 迁移

使用 `archive.sqlite3`，`Store(path, target_version=4).initialize()` 可显式升级。实现沿用 Store 的单个 `BEGIN IMMEDIATE` 迁移事务和外键检查；v4 **仅建表及新表索引**，不改旧业务表结构或业务行。已有审计和版本行保留，每个新迁移版本各增加一条版本记录和一条字段名审计。失败整体回滚，重复迁移无变化。

旧调用者默认仍为 v2，Quotes 仍请求 v3；两者均接受已升级的 v4，不降级。新岗位库版本为 `[2,3,4]`，v1 升级为 `[1,2,3,4]`。GET 不迁移或创建磁盘库，缺表返回空列表/404。

| 新表 | 内容与约束 |
| --- | --- |
| role_runs | id、role、subject_kind、subject_key、input_fingerprint、status(ok/fallback/failed)、source(ai/rules)、model、output_json、output_text、evidence_json、error、created_at、created_by；额外 `ai_success` 为 true/false/null（未调用） |
| role_alerts | kind、deal_id、ref_kind/ref_id、中文 message、severity(high/medium/low)、first_seen_at/last_seen_at、resolved_at、dismissed_at/dismissed_by；未解决时 `(kind,ref_kind,ref_id)` 部分唯一索引 |
| triage_suggestions | 建议的案子/单位/单位类型、reason、confidence、source、status、created_at、decided_at/decided_by；额外 action、run_id、summary_json、updated_at、decided_deal_id/decided_party_id；conversation_key 唯一，保留人工忽略决定 |
| role_ai_reservations | `at`（Unix 秒）和 `calls`（预留尝试数），用于跨岗位、跨 worker、重启后仍有效的滚动限额；清理超过一小时的预留 |

后两类额外字段用于把建议关联到完整运行依据、显示会话摘要、返回重复接受结果；第四张表避免把限额仅保存在进程内。审计 detail 沿用 `{fields:[字段名]}`，不记录提示词、聊天正文、金额或表单值。

**v4 已上线，不能再改 `migration_v4.py`。** 建议名称 `suggested_party_name` 继续保存在已有 `triage_suggestions.summary_json` 快照及 `role_runs.output_json` 中；C3 的同名主建议指针 `duplicate_of` 和渠道/完整名称的散列 `buyer_fingerprint` 也复用这两处 JSON，**不新增数据库列，不增加 v5**。列表将这些值作为顶层字段返回，不混入 conversation 摘要。旧 v4 快照没有名称时，仅在内存中清洗当时的 counterpart_name；没有同名元数据时返回 null。GET 不写库、不迁移，重新扫描才更新 pending 建议。`Store(target_version=2/3/4)` 重复初始化已升级的 v4，仍保持行、结构、版本和审计不变。

本补丁没有改变 Store 的版本白名单。今后如需新增数据库列，应新增 `migration_v5.py`、使 Store 支持 `target_version=5`，并保证 roles、quotes 等旧入口接受已升级的 v5；不能通过回改已上线的 v4 完成。本次未触及 archive/quotes 源码或生产库。

运行 API 返回完整 run 对象，`output_json`、`evidence_json` 已解码为对象/数组，`output_text` 可直接显示；生成时间为 UTC ISO 字符串，created_by 来自已认证 principal。消息时间/等待起点沿用 inbox 的 Unix 秒。运行时返回额外 `reused` 表示复用，GET 历史不带该临时字段。

所有规则输出也有时间和依据。AI 成功记录实际返回模型；失败区分调用失败、超时、空内容/格式不符、限额等固定中文原因，不回显异常、路径或模型原文。official_runner 失败时无法报告最终实际模型，因此 model 明确标为“尝试序列”，不冒充成功使用某个模型。正常规则运行 ai_success=null；AI 失败回退为 false。可用业务数据下 AI 失败总是保存非空规则结果；数据库本身不可读/不可写仍返回 503。

## 复用、保密、幂等与节流

- 会话通过共享 `UnifiedInbox.read/detail` 读取；案子投影复用 `Deals._view`、waiting；报价检查复用 `quotes.calc.compute`，不另算利润。岗位 lookup 额外使用 inbox 的 `last_known` 方向语义及 `is_closing`，unknown 不重置等待时钟，结束语不制造待回复工作，不改变原 inbox/deals 行为。
- 消息每会话最多 30 条（分拣提示词 12 条），每条最多 1000 字；复用 KB 的路径/控制字符显示脱敏，并隐藏邮箱、长电话号码、链接及常见凭据串。岗位自己的电话号码正则原先把日期的连字符也计入长度，本次改为先保护 `YYYY-MM-DD`、明确币种/小数金额、带单位的计数及带标签的订单号，再按至少 10 位数字识别长电话；`USD 1,234.50`、`3 单` 保留，`+86 138 0013 8000` 和邮箱仍隐藏。秘书/分拣提示词、AI 正文、简报 narrative/output_text 共用此逻辑；未修改 KB。不把账号、原始会话 key 或消息定位符放进模型提示词。引用另存于 evidence_json。
- 引用包括 deal、party、identity、task、event、conversation、message、quote、quote_line、quote_cost、offer、freight_quote、knowledge、alert、role_run 等 id。当前微信/WhatsApp 统一投影不提供原始消息 id，此时明确使用 `derived:<sha256>`，由会话 key、方向、时间、原文计算，并带 `derived:true`、conversation_key、sent_at；它是稳定定位指纹，不是假造的渠道 id。
- 客户相关知识检索直接调用 `kb.drafting.draft_context`，强制 customer_reply，保密/成本/其他客户资料过滤由原 KB policy 执行，精确 scope 证明规则不变。超时只用公司背景。知识引用保存真实 source_id。
- 秘书本批输出内部行动建议，不提供客户原话字段。AI 只能用编号选择规则生成的 next_steps，不能把任意客户话术、成本、利润或供应商名注入下一步。AI 可概括内部 headline 和补充内部风险；成本/利润仍只供内部评估。若今后增加可发送的客户草稿，应继续走 inbox 起草入口和 customer_reply，不复用内部摘要作客户回复。
- 模型直接复用 `inbox.ai.official_runner` 和其纯文本/信封解析。提示格式仍为“语言：中文”、正文、“=====待确认=====”；岗位字段使用中文文本行，不要求模型输出 JSON。外层没有重试：一次 invocation 内仍是 flash → flash → v4-pro，最多三次，gateway 单次 120 秒超时；非零退出沿用原逻辑立即失败。
- `runner(prompt)`、clock、inbox、hub、Config、today 均可注入。`runner=None` 表示只跑规则；测试全部注入 fake runner，验证官方重试时也替换 subprocess。岗位总等待上限默认 365 秒，可调；超时或两个模型工作槽均占用时回退规则，晚到结果不会写库。
- 输入指纹包括实际读取的业务依据、规则结果及影响结果的时间/日期。等待时间显示变化或有效期跨日属于输入变化；同一输入直接返回最新结果，包括已保存的失败回退，不追加运行或再调 AI。C3 仅将分拣规则版本由 2 升为 3，下一次手动扫描不会复用旧名称/忽略/同名处理结果；其他岗位仍为 2。GET 历史仍保留原记录，不在读路径改写历史。输入改变后可重新运行；没有强制绕过缓存的参数。
- 同一档案库的所有岗位入口用进程内 RLock 和 `/tmp/imgw-role-locks-<uid>/` 的文件锁串行化，多个本机 worker/CLI 也复用结果。锁只含路径散列。建议决定和提醒同步另有 SQLite 写事务保护。
- 默认每分钟 6、每小时 60 **模型尝试额度**。原 runner 不暴露逐次尝试计数，因此每次 invocation 保守预留 3 个额度，失败也消耗：默认最多 2 次 invocation/分钟、20 次/小时。额度保存在同一档案库，四个岗位共用，巡检不消耗。超限 HTTP 429，包含中文 error、retry_after、非空规则 result，并有 Retry-After 头。不同档案库互不占用额度。

## 分拣规则和确认

扫描最新有效对方消息在 30 天内（含边界）、尚无案内关联的会话；已有 identity 但未入案仍参与建议。排除最后有效消息是我方、普通结束语、learning/internal/ignored，以及本表已经接受/忽略的会话。符合下列忽略规则的结束语会话仍生成 ignore 建议，例如占位群名下的“好的”。来源目前没有另一套“忽略分组”接口，本批的人工忽略决定存于 triage_suggestions，后续新消息不会自动重开。

以下规则优先生成确定性的 `action=ignore`，不调用 AI；接受后沿用现有忽略决定，状态为 dismissed，不新建案子、单位或身份：

| 条件 | reason | confidence |
| --- | --- | --- |
| 群名以 `A-` 开头、含本公司全称、并以数字编号结尾 | 像是系统通知，不是客户或供应商 | 0.8 |
| 群名含“同行/资源共享/行业群/交流群/供需”任一词 | 像是同行交流群 | 0.7 |
| 无名称或只有 `微信群 ·room` 这类四字符占位尾码，且最后有效对方消息去首尾空白后 ≤6 字，或近期对方消息没有询价词 | 暂时看不出是哪笔生意 | 0.5 |

无名会话的询价依据取最近 12 条中的对方消息，并补看最后有效对方消息，不把我方或 unknown 的询价词算作需求。价格词包括 price/quote/how much/报价/询价/询盘/问价/多少钱/价格，数量词包括 MOQ/quantity/qty/数量/起订量。短消息条件与缺少询价词是“或”的关系；有明确名称的普通短消息不因此忽略。系统/同行规则只识别群聊，不把明确标记为私聊的联系人当作群。

非忽略会话先看 identities；再精确匹配单位名、简称、别名（规整首尾空白并忽略大小写）。已有单位身份优先，不因群名覆盖人工确认的身份。尚未匹配单位时，名称中的 `SUP`/`CUS` 加可选数字（忽略大小写，容忍空格、中英文连字符、波浪号、下划线、冒号、括号等分隔符及全角数字）优先于“供应/工厂”等旧线索。无编号标记须处于去掉群名前后缀后的边缘，不删除品牌内部的 SUP/CUS 字母：

- `SUP`：含快递/物流/货代/运费/空运/海运为 forwarder；否则含打样/包装/验货/报关/认证为 vendor；其余为 supplier。保留 freight/forwarder/配套等现有线索。若同时出现 SUP 和 CUS，保守按 SUP 处理。
- `CUS`：customer。编号分类 confidence 为 0.9。
- supplier/forwarder/vendor 会话只建议 identity；即使已有在谈案子或产品匹配，也不建议建案或关联。渠道已有供应方分类同样有效。AI 若把供应方建议成新建/关联，解析时收敛为确认身份；忽略建议仍可保留。接受时仍允许人明确选择 action。

客户会话只有一个在谈案子时建议关联；多个案子时继续核对。产品匹配复用 KB 分词，要求至少两个关键词重合、覆盖候选产品词至少 60%，且唯一领先或比第二名多至少两个词。没有编号时，继续使用群名的货代/运费/物流、供应/工厂/报价/发货、打样/包装/验货等线索。

规则版 create 的 reason 包含渠道、对方名、最近一条对方消息**先脱敏再截取的前 40 字**，以及价格、数量、产品关键词是否出现；线索来自最近 12 条消息中的对方消息，不拿我方的询问当作对方需求。若最近 12 条全是 unknown，仍用最后有效对方消息并保留其 evidence 引用，模型输入不扩容。仅有消息 confidence=0.3，出现 price/quote/MOQ/报价/多少钱/数量等明确询价词为 0.5，再叠加在谈案子的产品关键词匹配为 0.7。出现一般产品词但不能匹配现有产品，不提升至 0.7。编号分类及已有案子关联继续用各自的置信度；这些是线索强弱，不代表已核实交易。

`suggested_party_name` 反复清除 SUP/CUS 及可选编号、`乐初/Lechu/LC` 与“工作/沟通/对接/供应商/群”的任意组合前后缀和首尾分隔符。支持公司标记在前、工作词在后，例如虚构名称“乐初虚构霜叶沟通工作”；“厂家/工厂”等主体业务词保留，品牌内部字母如 FOCUS、SUPERNOVA 不删除。清洗后为空或仅剩一个字符，退回完整原名。已有单位使用其名称。接受 identity/create 时，显式 party_name 优先，否则使用建议快照中的名称；列表的当前群名变化不会偷偷改变已经建议的单位名称。

同一渠道、完整对方名相同（去首尾空白、忽略大小写）的未入案买家会话，以最新活动时间选一条主建议；时间相同按会话 key 稳定选择。不同账号可以成组，但不跨渠道、不用清洗后/脱敏截短后的名字合并；空名、占位名、供应方、ignore 以及已有身份明确冲突的会话不合并。副会话保留一行 `action=identity` 待确认建议，`duplicate_of` 指向主会话，reason 固定为“与另一条会话是同名买家，建议一起处理”，置信度最多 0.7，不再建议另建案子，也不调用 AI。

接受主建议建案后，同一事务将副建议改成 `link`，指向刚建案子和确认的客户单位，理由包含“加入刚建的案子”，并追加带主决定/案子/单位引用的规则运行记录；主建议若人工改为关联已有案子，副建议跟随该目标，理由为“加入已确认的案子”。副会话身份和业务关联仍要再次人工接受，原有身份/归属冲突检查保留。未处理主建议时接受副建议、或对副建议提交 create，返回 409，防止重复建案；ignore 和明确指定目标的 link 仍可使用。重复接受不会重复建案或追加运行，任何联动写入失败整体回滚。

关联信息持久化在 v4 快照中，服务重建、显式改客户名称或跨扫描批次仍能给出同一在谈案子的 link 建议。主建议被忽略后，下次扫描可把其余同名会话中最新一条提升为主建议。不会自动合并已确认的不同单位、改写历史运行或重新打开已忽略会话。

不确定时 AI 只看该会话最近 12 条脱敏消息与在谈案子的 id、标题和产品名，不传其他会话、客户档案、内部备注、报价或利润。模型只能选择给出的案子，非法 id/角色/格式回退规则。confidence 为启发式把握，不是统计概率。

每次扫描最多 100 个会话，扫描取样仍为最新优先；`remaining` 表示本次未扫描数。建议列表、扫描返回和 dry-run 预览统一排序：先 identity 且 confidence ≥0.8，再 create，再 link/低置信 identity，ignore 最后；各组按置信度降序、最近有效对方消息的等待时间从长到短，同分按会话 key 稳定排序，缺时间排在同置信度的最后。GET 尽量用当前会话时间排序，来源不可读时使用快照，读路径不写库。

为保证**整次手动扫描也不超过三次模型尝试**，只有首个需要新分析的不确定主会话调用一次 runner，其余先给规则建议及人工核对原因。没有后台继续调 AI。相同输入的规则回退也会缓存。

pending 建议随新输入更新原行并关联新 run；列表预览尽量用当前 inbox 摘要，`summary_current:false` 表示来源不可读、沿用生成时快照。接受默认使用建议 action，可由表单明确改目标/类型/名称。已有身份冲突或扫描后会话另有案子关联时返回 409，不能借接受操作静默转移归属。忽略只记决定，不更改渠道分类。

四个岗位的规则等待文案统一为：不足 1 小时“刚刚”，1 至不足 48 小时“已等 N 小时”，48 小时起“已等 N 天”，按完整小时/天向下取整，数字与单位间留一个空格。545 小时显示“已等 22 天”。秘书的 `waited_hours` 仍保留原始整小时供阈值判断，`elapsed` 为完整显示文案；等待判断与巡检阈值不变。分拣不再拼接“目前由我方确认归属”。

## 巡检阈值

`Config` 默认业务时区为 Asia/Shanghai，可注入。报价到期日当天仍有效，保持 quotes/archive 约定。

| kind | 默认触发 | 处理建议 |
| --- | --- | --- |
| quote_expiring / quote_expired | 客户报价距到期 0–3 天 / 已过期 | 核对客户报价有效期 |
| offer_expiring / offer_expired | 产品成本快照距到期 0–3 天 / 已过期 | 向供应商确认新价 |
| freight_expiring / freight_expired | 运费快照距到期 0–3 天 / 已过期 | 向货代确认新价 |
| cost_expiring / cost_expired | 配套等其他费用快照同上 | 核对费用来源 |
| no_reply | sent 轮发出后严格超过 48h，关联客户会话无后续对方消息 | 跟进意向；未关联客户会话时明确提示先核对回复 |
| waiting_too_long | 我方最后有效消息不是结束语，严格超过 48h 无对方回应 | 对相应客户/供应商/货代/配套催一下 |
| payment_overdue | awaiting_payment 阶段停留至少 3 天 | 核对到账及付款安排 |
| deal_stale | open 案子至少 7 天无事件或会话活动 | 确认继续跟进还是暂停 |

只巡检 open 案子；报价检查使用**最新一轮**且状态为 draft/sent/accepted，避免对旧草稿、已拒绝或被替代轮持续催办。即使源供应商价已更新，巡检仍检查报价中的冻结快照。缺有效期由秘书的报价 blocking 提醒，巡检不把缺日期说成已经过期。waiting_too_long 按案内会话检查，可能与 no_reply 同时出现，各自引用不同对象。

同一未解决提醒更新 last_seen_at，保留 first_seen_at 及人工忽略信息。条件消失自动 resolved_at；以后重新出现会建新提醒。GET 只列未解决提醒，默认隐藏已忽略项，按 high/medium/low、id 排序。巡检使用 inbox.detail 读取已有的有界会话历史，避免把 WhatsApp overview 中我方后续回复误判成客户从未回复。关联会话缺失/详情不可读时，不新增无依据的“未回复”判断，保守保留该案依赖聊天的原提醒；本次输出会列 incomplete_deals。

## 简报统计口径

- today/week/month 按配置业务时区，周一为周首，月初为月首；排除未来时间。成交按当前处于 paid 或后续阶段且有 won_at 的案子计算，首次付款计一次；接受报价不算成交。沿用 deals 回退阶段清 won_at 的语义，回退后不计入成交。
- 金额取该案**最新已接受轮**的销售单价×数量，以整数分相加、两位小数字符串展示；不取案子手填 value、不取成本或利润。没有接受轮、缺售价或数量未确认时记 amount_pending/“金额待补”。不同币种分别汇总，不擅自相加或折汇。
- 在谈指 status=open。by_stage 同时给所有状态 count 和进行中 open_count。付款后暂停/售后等仍保留成交事实，除非按现有规则回退。
- 本周发出按 quote_rounds.sent_at，包含之后被拒绝/替代的已发出轮。本周接受按 deals 在接受事务中写入的精确“报价第 N 轮已接受” system 事件；不能拿会被备注编辑刷新的 updated_at 当接受日。丢单按当前 lost 和 closed_at。
- priorities 综合未忽略的活跃提醒、最新秘书 headline 和 pending 分拣数量，最多五项，优先 high/medium/low。引用秘书结论附 headline_generated_at，避免把旧分析当成刚生成。简报不会自动启动巡检、秘书或分拣。
- 数字始终来自规则。AI 仅写 narrative；输出新数字会被拒绝并回退模板。比较时允许金额的千分位格式（`1,234.50` 与 `1234.50`），不会因长金额被误脱敏而绕过数字校验；但不能形式化证明润色文本没有交换已有数字的含义，核对以结构化 results 为准。同日输入改变追加新 run，GET today 取最新；没变复用。

## API

`console.v3_roles_api.build(app_module, archive_path=ARCHIVE_PATH, *, service=None, inbox=None, runner=DEFAULT_RUNNER, hub=None, clock=None, config=None, today=None, inbound_path=INBOUND_PATH, wechat_path=None, whatsapp_config_path=None, fetch=None, stat_fn=None, group_gid=None)` 返回 13 条 Route，兼容 `build(gate, archive_copy)`。演练脚本已在原动态模块列表中追加 v3_roles_api；未部署生产挂载。

与 v3_quotes_api 共用 authorised/forbidden/recheck/response 和 archive.form_fields：principal 与 legacy session 同时通过；POST 在读表单、调用 AI、写库前 recheck；GET 在返回前连同错误一起 recheck；I/O 在线程池；no-store/nosniff。**CSRF 由宿主现有中间件校验**，路由不新造一套 token。测试使用宿主 CSRF 闸门验证拒绝路径。

所有 POST 为 form-urlencoded，原始体及规范编码表单各 ≤8192 字节、≤40 字段，拒绝重复字段、未知字段、非法 UTF-8 和无法验证原始大小的缓存表单；csrf 校验后移除，不能伪造操作人。错误为中文 400/403/404/409/429/503，不泄漏堆栈。

下表前缀均为 `/api/v1/`；未列出的 POST 业务字段不接受，空操作可仅传 csrf。

| 路径 | 参数 / 返回 |
| --- | --- |
| GET roles | items：key/name/responsibility/level、last_run_at/last_status/last_source、runs_24h/failures_24h、model/last_model；failures 统计 fallback/failed，不计缓存命中 |
| GET deals/{id}/secretary | 最新完整 run；案子不存在 404，没有产出 404“还没有分析” |
| POST deals/{id}/secretary/run | 空表单，返回 run |
| GET triage | status=pending（默认）/accepted/dismissed/all；items 含顶层 suggested_party_name、duplicate_of（无则 null）、buyer_fingerprint，以及 conversation.channel/counterpart_name/last_at/preview（≤80 字）；按上述建议优先级排序 |
| POST triage/scan | 空表单；items、runs、scanned、remaining、source_ready、message |
| POST triage/{id}/accept | 可选 action=link/create/identity/ignore、deal_id、party_id、party_kind、party_name、title；返回 ok/id/deal_id/party_id/reused，ignore 只记忽略 |
| POST triage/{id}/dismiss | 空表单；返回 ok/id，记录 decided_by |
| GET patrol/alerts | 可选 deal_id、include_dismissed=0/1（默认 0，可为空）；items，不含已解决提醒 |
| POST patrol/run | 空表单，返回规则 run |
| POST patrol/alerts/{id}/dismiss | 空表单，返回 ok/id，记录 dismissed_by |
| GET brief/today | 当前业务日最新 run；未运行 404 |
| POST brief/run | 空表单，返回 run |
| GET roles/runs | 可选 role=secretary/triage/patrol/brief、limit=1–200（默认50）；items 按 created_at/id 倒序 |

## 手动运行与之后的定时器

CLI 默认 dry-run，输出 JSON（其中 output_text/message 可直接读），不写档案库、不调用 AI。下面路径只是示例，应指定自己的演练副本：

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python scripts/roles_run.py --role patrol --archive /tmp/fictional-archive.sqlite3
PYTHONPATH=src /opt/imgw/venv/bin/python scripts/roles_run.py --role triage --archive /tmp/fictional-archive.sqlite3 --inbound /tmp/fictional-inbound.sqlite3 --apply --who rehearsal-owner
PYTHONPATH=src /opt/imgw/venv/bin/python scripts/roles_run.py --role brief --archive /tmp/fictional-archive.sqlite3 --apply --who rehearsal-owner
```

省略 --archive 时沿用 ARCHIVE_PATH；默认库的本地入站/微信路径沿用 inbox 常量，自定义档案路径则默认同目录 inbound.sqlite3、禁用微信，可用 --wechat 指定。CLI 始终禁用 WhatsApp HTTP，且不安装任何定时器。`--apply` 才写库；即使 apply，也默认纯规则；显式 `--ai --apply` 才允许真实官方模型。`--timezone` 可改业务日边界。自定义档案的默认 Hub 使用同目录隔离知识路径，未配置时退回公司背景；应用可显式注入 hub。

后续调度器也可调用 `scripts.roles_run.run(service, role, apply=False, who=...)`，或注入 fake 依赖直接调用 Roles 的 run_secretary/scan_triage/run_patrol/run_brief。所有入口沿用同一指纹、锁和额度。

## 验证和限制

新增测试位于 `tests/roles/`，只建虚构临时库、虚构会话及知识片段，使用 fake clock/runner。覆盖迁移保留/回滚、结束语、规则与 AI、超时/空结果、指纹、跨进程幂等、分钟/小时额度、分拣接受去重和事务回滚、巡检触发/解决/忽略、简报金额与周期、鉴权/recheck/CSRF/表单限制、CLI 和演练挂载。通过业务表逐行快照与 outbox/BridgeClient/channel spy 验证岗位运行没有发消息、改金额或改阶段。

本次 C3 修补：改前原有 **171 项全部通过**（11.68 秒）；新增 **67 项**，改后 roles **238 项全部通过**（14.36 秒）。随后分别运行 quotes **28 项**（5.16 秒）、deals **32 项**（4.25 秒）、archive **38 项**（4.84 秒），全部通过；最终合计 **336 项通过、0 失败、0 跳过**，另有 **273 个 unittest subtest 子断言通过**，不计入主测试数量。

C3 新增覆盖无编号标记和两端名称清洗、单字回退、三类 ignore/接受后的持久忽略、占位名结束语和询价边界、同渠道跨账号主副建议、大小写/空白规整、三会话联动、AI 仅看主会话、事务回滚、身份冲突、主建议切换/忽略、人工改关联目标、显式改客户名、重建服务/重扫/跨扫描批次、历史关闭案子、排序/当前时间与快照回退、API 409/联动、已上线 v4 结构不变及旧快照只读兼容。所有原有测试继续保留。

上一次五项修补的历史记录：改前 90 项、新增 81 项，roles 171 项及 quotes/deals/archive 共 269 项通过；覆盖编号优先级及供应方 identity、名称快照、等待文案、消息摘录/脱敏/置信度，以及日期/金额/计数保留。初版更多套件的历史记录见下文，未计入此次 336 项。

所有输入均为虚构 fixture；未读取演练副本或真实业务数据、未调用真实 AI、未联网。最终回归进程额外使用 `/tmp` 临时 sitecustomize 限制网络、AI gateway、非临时 SQLite 访问及生产路径写入，关闭 bytecode 和 pytest 缓存写入。四套测试仍按目录独立运行；本次未重跑下文初版记录中的其他套件。未 commit/push、未部署，未修改 `web/`、`/opt/imgw`、生产文件或范围外源码。

取舍：名称和同名关系复用 v4 JSON 快照，不额外加列或增加 v5；Store 的版本白名单维持原样。副建议沿用现有 identity 动作表示待确认，接受主建议后才变为 link；同名只是建议依据，不自动绑定副会话或合并单位。未改前端、迁移、quotes/deals/archive 源码，没有自动更正旧单位或已接受/忽略的决定。旧 run 保留审计历史，需手动重新扫描才能得到新规则输出。规则只是内部建议，接受操作仍需人工确认。

此次四套测试分别使用以下命令；验证时还在 PYTHONPATH 首部加入了仅位于 `/tmp/c3-triage-check` 的离线审计闸门（不是仓库依赖）。先完成 roles，再运行后三套：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest tests/roles -q -p no:cacheprovider
for suite in quotes deals archive; do
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/$suite" /opt/imgw/venv/bin/python -m pytest "tests/$suite" -q -p no:cacheprovider || exit 1
done
```

仓库旧测试按各目录独立运行。直接全目录 pytest 收集的既有问题是 `test_api/test_service/test_migration/inbox_fixtures` 同名导入冲突；supplier_data 还需把自身目录加入 PYTHONPATH。未修改旧测试以改变这些约定。初版更多套件的历史复现命令（本次未全量执行）：

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest tests/roles -q
for suite in quotes deals archive kb inbox_api channels outbox media packing_api inspiration web_app supplier_data workbench whatsapp; do
  PYTHONPATH="src:tests/$suite" /opt/imgw/venv/bin/python -m pytest "tests/$suite" -q || exit 1
done
PYTHONPATH=src:tests/workbench /opt/imgw/venv/bin/python -m pytest tests/workbench/inbox_fixtures.py -q
CPQ_TEST_MODE=synthetic CPQ_TEST_PDF=real PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest tests/cpq -q
```

初版 C 批的验证记录（非本次重跑）：岗位测试 **90 项通过**。指定 12 个套件（quotes 到 supplier_data）分别通过 28、32、38、35、37、45、42、25、24、56、6、110 项，共 **478 项**；workbench 20、whatsapp 5，README 单独要求的 workbench/inbox_fixtures.py 另有 12 项通过。CPQ 使用真实 PDF 渲染器和虚构库，293 项通过、2 项按原合成模式约定跳过（依赖真实 vendor 前端资源）。live 三项也用 fixture_catalog.py 新建的 37 个虚构产品验证；因原 live 动态加载 conftest 的相对导入问题，按 CPQ 文档使用 `existing + CPQ_TEST_FIXTURE_SUPPLIER=1` 指向该**虚构**副本，三项通过，未读真实库。按套件隔离执行合计 **901 项通过、2 项已知跳过**，没有失败；此计数不包含 unittest 的 subtest 子断言。

验证进程额外通过临时 sitecustomize 审计闸门禁止 socket 连接、真实 AI gateway 和生产 SQLite 路径；没有运行联网浏览器演练或真实 AI。未执行上线 e2e_smoke，因为本批没有上线授权且要求测试不联网。

已知边界：列表/扫描在内存投影，未做大规模数据库分页优化；跨机器部署不能共享本机文件锁，需另配分布式锁/统一额度。基于最后消息、群名和关键词的规则不能证明业务事实；供应商资料标签完整性及人工审核仍重要。秘书行动列表保守，AI 只选择已有动作；分拣不做自动关联；简报依赖已有巡检/秘书结果。没有前端“AI 员工”页、客户可发送话术、独立核价员、分角色起草岗位、反馈归集、自动阶段推进、收款、发货或定时任务。v4 已上线；本次 C3 补丁未部署，也没有使用真实业务数据验收。

## 初版 C 批文件清单（历史）

修改：`src/console/archive/store.py`（接受 target_version=4 并保留旧路径）、`scripts/rehearsal_server.py`（原动态挂载列表追加 roles）。

新增：

- `src/console/archive/migration_v4.py`。
- `src/console/roles/__init__.py`、`common.py`、`inputs.py`、`deal_summary.py`、`triage.py`、`patrol.py`、`brief.py`、`ai.py`、`storage.py`、`service.py`、`validation.py`。秘书实现名为 deal_summary，避免仓库已有 `*secret*` 忽略规则把 secretary 文件静默漏掉；未修改原 .gitignore。
- `src/console/v3_roles_api.py`。
- `scripts/roles_run.py`。
- `tests/roles/__init__.py`、`conftest.py`、`test_roles_migration.py`、`test_roles_summary.py`、`test_roles_triage.py`、`test_roles_patrol.py`、`test_roles_brief.py`、`test_roles_api.py`、`test_roles_safety_cli.py`。
- `docs/roles.md`。

本批未修改 deals、quotes、inbox、kb、outbox、channels 的业务源码，也保留了开始任务时已有的其他未提交改动。

上一次五项修补改动了 roles 下的 common、triage、service、storage、ai、deal_summary、brief、patrol 及相应测试；没有修改 migration_v4/store。

## 本次 C3 修补文件清单

- `src/console/roles/common.py`、`triage.py`、`service.py`、`storage.py`。
- `tests/roles/test_roles_common.py`、`test_roles_api.py`、`test_roles_migration.py`；新增 `test_roles_triage_c3.py`。
- `docs/roles.md`。

其余已有未提交改动保持原样；未修改 `migration_v4.py`、archive Store、quotes/deals/archive 业务源码、`web/`、`/opt/imgw` 或生产文件，未 commit/push。

## 简报和巡检的更新口径（WG-11）

工作台显示简报生成时间、规则或 AI 来源及规则回退原因；提醒显示最近规则巡检时间。新消息或新收款不会自动重算，需点击“更新巡检和简报”：先运行内部规则巡检，再运行简报；若简报失败会提示巡检已完成。没有变化时复用已有结果和原生成时间；这不是实时新鲜度检测，来源不可读时也不能推断没有新情况。操作不发送消息、不安装定时器。

今天、本周、本月按新成交案子的首次成交时间分组，金额是这些案子**当前已确认收款净额**：已确认退款扣减，待核对或作废的收款不计；没有已确认收款时，以最新已接受报价的完整销售金额兜底，缺资料显示待补。币种分别显示，不折算相加。它既不是订单总额，也不是该期间的现金流。AI 仅润色文字，失败或额度不足时保留规则结果和固定原因；应以结构化金额为准。

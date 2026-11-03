# D1 / D1b 报价抽取员

报价抽取员把供应商、货代和配套供应商会话里的价格线索整理成老板待确认的内部建议。岗位 key 为 `extract`，级别 L0；扫描只保存建议和岗位运行记录。老板明确接受并补齐有效期后，才调用现有档案服务写入 `confirmed` 产品报价或货代运价。没有客户可见输出、渠道发送、前端、定时器或生产部署。

本次按明确任务范围不改 `web/`，包括其中的需求账本；需求、接口与验收方法记录在本文。

## 数据与迁移

开始开发时最高迁移是现有 `migration_v6.py`，它属于数据体检员；v5 留有另一批功能的兼容预留。D1 新增 **v7**，不改已上线 v4，也不覆盖 v6。`Store(path, target_version=7).initialize()` 在隔离副本中可显式升级；迁移在一个事务内新增建议表及索引、扩展 `role_runs.role` 允许 `extract`，保留既有业务行、运行记录、子表关联、索引、触发器和审计。重复初始化不重复记录迁移；旧 target_version=2/3/4/6 入口接受已升级的 v7，不降级。

D1b 开始时 **v8 已存在**，本次不修改 v4/v6/v7/v8，不新增版本迁移。抽取员仍以 target_version=7 打开隔离副本，也兼容已升级 v8 的库。独立额度表首次预留时在写事务内创建；不重建旧表，不改变旧迁移历史。

`extraction_suggestions` 的字段如下。金额沿用档案整数分，接口人工输入仍用最多两位小数的金额字符串。单位与产品不明时留空；规则币种仅在下文明确限定的同窗口 CNY 场景中推断，并带猜测标记。

| 字段 | 含义 |
| --- | --- |
| id、kind | 建议编号；`offer` 产品报价或 `freight` 货代运价 |
| conversation_key、source_message_refs | 会话及消息引用；引用复用岗位 `message_ref`，没有渠道消息 ID 时明确记录 `derived:<sha256>`、derived=true、方向/时间形成的稳定指纹 |
| party_id、party_hint | 已确认 identities 的往来单位 ID；名称仅线索时不填 ID |
| product_text、matched_product_id | 产品原话及唯一、保守匹配到的档案产品 ID |
| price_fen、currency、unit | 整数分、三字母币种、计价单位 |
| moq_units、lead_time_text、tax_text | 起订量、交期原话、税费原话 |
| route、mode、origin、destination_country、destination_detail | 线路原话、运输方式、始发地、目的国、目的地详情 |
| min_charge_fen | 最低收费整数分 |
| validity_text、suggested_valid_until、quoted_on | 有效期原话、可确定换算的到期日、来源消息业务日期 |
| excerpt、has_attachment | 最多200字脱敏摘录；附件标记，提示人工查看 |
| confidence、source | 启发式置信度；`rules` 或 `ai`，不是准确率承诺 |
| status、decided_at、decided_by | `pending` / `accepted` / `dismissed`，决定时间及认证操作人 |
| created_at、input_fingerprint、run_id、accepted_record_id | 生成时间、唯一去重指纹、岗位运行引用、接受后的档案记录 ID |

建议查询另外返回 `is_cost:true`、`currency_guess`、`is_bundle`。两个新标记保存在首个 `source_message_refs` 的 `extraction_metadata` 中，接口展开为顶层布尔字段，与 `excerpt` 一起返回；旧行没有标记时按 false 读取。复述引用附 `restated:true`，消息 ID 保持原样。这样无需修改 v7 表结构。

建议状态与档案 `suggested/confirmed/rejected` 是两套状态：待确认建议不会提前插入档案价格表，扫描从不创建 `confirmed`。接受后的档案价格继续受原有有效期和当前记录规则约束；过期、缺有效期或非当前记录不能用于算价。新建产品候选仍是 `candidate`，不会被确认报价顺带确认。

GET 不迁移或创建磁盘库；缺表时返回空列表/零计数。审计沿用 `{fields:[字段名]}`，不写消息正文、价格、提示词或表单值。长来源引用超过档案 `source_ref` 的500字限制时，保留建议 ID 和来源指纹，完整会话 key 与消息引用仍保存在建议表供追溯。

## 扫描与规则

默认回看30天、每次最多20个会话。身份优先使用 deals identities；没有身份时复用分拣员已有的渠道分类、群名、SUP编号和货代关键词判断，不另造会话分类规则。已确认为客户的会话不能因群名包含物流词而混入供应方抽取。会话详情通过 `UnifiedInbox.read/detail` 读取，不补采渠道历史。

只把对方消息以及我方明确复述供应方价格的消息作为线索。我方回复按分句核对归属，混在旁边的我方还价不参与抽取。`unknown` 方向不能提供价格事实。先检查数字与价格/币种/计价单位等线索，再为命中消息组装前后各两条的上下文窗口；无线索的普通聊天不调用模型。规则最多查看详情最近300条，每条最多1000字；模型最多接收最新12条窗口消息、合计10000字正文。消息进入提示词前复用 `roles.common.safe`：邮箱、长电话、链接和凭据隐藏，日期与金额保留。模型提示词使用窗口消息编号，不传原始会话 key、账号或来源定位符。

图片、PDF 等附件不下载、不 OCR、不让模型猜内容；只标记“有附件，需人工看”。附件旁明确出现的文字价格可保留，纯附件不调用 AI。显式禁用 AI 时可运行纯规则模式；AI 已启用时，失败、漏回的条目和超过调用上限的会话均不会用规则补足。单位不认识时留空并保留原文。

噪音筛选按价格所属分句执行：

- 多运算符或等号的总价算式不作为单品报价。选择“仅 AI 判断为整单报价时保留”的方案：AI 必须输出 `类型：整单报价参考`，结果 `kind=offer`、`is_bundle=true`、`product_text` 为空、置信度≤0.2，默认隐藏。纯规则模式不保存算式。`单价=130元` 这种价格标签不算整单算式；程序不计算算式总额。
- 增加、加价、多加、优惠、便宜、少、让、降、涨、差等增减金额，没有明确产品及基准报价时过滤。明确基准价可保留，旁边的优惠金额不作为第二条报价。
- 问号、吗、能否、多少、是不是等疑问表达不提供报价事实；独立的确定报价分句仍可保留。
- 产品原话须命中 `rules.PRODUCT_CLUE` 的名词、瓦数或型号线索，或由 AI 从对应价格短语中明确提取。无意义片段留空，规则结果降到≤0.2。
- 规格附近的“块”等数量歧义，须有元、块钱、RMB、货币符号或单价、报价、价格、含税等明确价格线索。米、尺寸、功率、重量、数量不当价格。

纯规则置信度封顶0.5；完整 AI 产品、价格、币种、单位为0.7–0.9（运价以运输方式作为对象线索）；缺字段≤0.5；噪音降级≤0.2。默认列表排除置信度≤0.2的 pending；`include_low=1` 才纳入。`pending_quality.trusted` 表示>0.2的可见待确认建议，包含仍需人工核对的规则结果；不是准确率保证。`pending_quality.low` 表示≤0.2。

规则币种缺失时，仅当该线索前后两条消息的供应方明确价格币种集合为 CNY 时推断 CNY，减置信度0.1并标 `currency_guess=true`；混用外币或距离更远不推断。单位始终不猜。AI 的币种、单位不继承规则字段：模型明确给出时先规整同义写法，再要求引用消息中也出现同币种/单位的原话形式；没有证据或无法规整时清空字段并降低置信度。

AI 输出沿用收件箱中文纯文本行、`语言：中文` 与 `=====待确认=====` 包装，不要求 JSON。解析容忍首尾空白、中英文冒号、行首序号、语言行、代码围栏、粗体标记及块分隔符周围空行。多块最多12块；重复字段取首次值并记一次 `bad_field`。每块分别校验消息引用、类型、置信度及原文：价格数字须在引用消息中出现，千分位和尾随零小数按数值原样规整；匹配不到原文的价格只丢弃该块。币种支持 CNY（元、块、人民币、RMB、¥ 等）、USD（美元、美金、$ 等）及其它已有币种别名；单位支持“一台/元/台/台”规整为 `台`、“公斤/kg”规整为 `kg` 等。币种或单位证据不足时字段留空、置信度降级，不影响同窗口的其它块。

`价格：无` 的块（包括整单报价参考）直接跳过；单独 `无报价` 是成功空结果。产品可以逐字出现在引用消息或前后两条上下文消息；没有产品、产品不在上下文，或产品与同消息另一价格短语不对应时，产品留空并降低置信度。规则候选不再是 AI 报价成立的前提，但仍用于保护 D1b 的产品与金额对应关系、问题句、议价差价、规格数字及算式筛选：同一金额若已由规则判为噪音，不会因 AI 标签而放行；算式仍需标为整单参考，且不会计算原文没有的总价。有效块保留，坏块按原因码逐块丢弃；全块均不可用且没有 `无报价` / `价格：无` 声明时，下次再扫。完全不符合约定格式与“没有可用块”使用不同失败文案。模型只返回部分价格时仍不补规则结果。

有效期使用消息发送时间在业务时区（默认 `Asia/Shanghai`）的日期，不用扫描当日倒推。明确报价语境中的“本周有效”换算到该周周日，“10天内有效”换算为发送日加10天，“月底前有效”换算到当月最后一天。相对时间、是否包含起止日等表达仍应由老板核对；模糊、不完整、矛盾或无法确定的期限只留原话，`suggested_valid_until` 为空。AI 不能擅自补日期。

产品匹配采用唯一精确名称/型号规则，并考虑已确认供应商；同名多产品、仅功率数字、部分名称或身份不明的歧义不会自动绑定。老板仍可选择已有档案产品，或明确新建产品候选。

同种建议按（会话、币种、价格、规整产品文本）去重，产品规整统一大小写、全半角、标点及明确复述前缀；产品成本和运价不跨类型合并。保留最早来源、摘录和已明确的事实，缺字段由复述补齐，后续来源引用追加并标复述。合并规则与 AI 结果时，产品、币种、单位以 AI 的明确输出为准，不能把其空字段用规则补齐。同一消息同价不同产品仍可分别保留。已接受/忽略建议仅追加来源，不改变决定和报价事实；兼容 D1 旧指纹的已决定行。

重复扫描依次优先处理新/变化会话、上次失败会话、已完成缓存。每次返回 `scanned`、`added`、`remaining`、`ai_calls`、`ai_windows`、`ai_failed`、`dropped_total`、`deferred`、`items`、`runs`、`source_ready`、`dry_run`。`ai_windows` 是本批实际调用 AI 的窗口数；`ai_failed` 是调用后未能解析并待重扫的窗口数，不含额度未预留成功的窗口；`dropped_total` 汇总解析原因计数（含被丢弃块、被清空字段及重复字段）。`remaining` 包括未处理的新/变化会话、失败/超时、受上限阻挡及详情暂不可读的会话，已完成未变化的缓存不计入。`deferred` 给出本批被推迟的会话及“下次再扫”原因；未进入会话批次的会话计入 remaining。`added` 为新增建议总数（含隐藏低可信），不是 AI 调用数。

## 额度、锁和运行记录

复用岗位 RLock、跨进程文件锁及 SQLite 原子预留。原 `role_ai_reservations` 没有岗位维度，因此采用独立的 `extract_ai_reservations`，不修改原表；旧记录无法可靠归属，留在原共享桶。秘书/分拣/简报继续按原共享额度运行，双方互不挤占，旧版本的读写也不会误统计抽取额度。

默认每小时24次 runner 调用，即72次尝试；每次 runner 调用预留3次尝试，失败也计入。分钟桶默认同为72次尝试，不额外阻挡正常8会话批次。每个有价格线索、未缓存的会话窗口调用一次 AI，单次扫描最多8次，超出的留到下次。官方 runner 自己负责最多三次尝试，无外层重试。默认单会话等待30秒、单次扫描总等待预算180秒，使用单调时钟，每次调用取两种剩余上限的较小值。超时后的迟到结果不入库；已有 runner 后台调用由既有执行器自行收尾，不承诺强制终止。

规则、成功 AI 和失败记录都写 `role_runs`；每个 AI 会话的 `output_json.ai_parse` 仅保存 `blocks_total`、`accepted`、`dropped_by_reason` 计数，不保存聊天正文或模型原始回答。运行 JSON 保留 `item_count`、输入指纹和延期标记，不复制建议项摘录；摘录仍只在待确认建议记录中按既有 D1b 设计保存。失败为 `status=failed`、`source=ai`、`item_count=0`、`output_json.deferred=true`，不会被当作成功缓存。每次 apply 另记含扫描计数的新汇总运行。额度用尽仍返回429、`Retry-After`，`result` 保留本次成功项及真实剩余数。规则模式与 AI 模式使用不同输入指纹，过去规则缓存不会阻止后来启用 AI。

`Extract` 可注入 store/path、inbox、runner、clock、config、timer（单调时钟）。`console.extract.Config` 支持 `scan_ai_limit`、`scan_timeout`、`ai_timeout`、`per_hour`、`per_minute`；后两者按尝试次数配置，例如 `Config(scan_ai_limit=5, scan_timeout=120, per_hour=72)`。兼容旧 `roles.Config` 入口的时区/会话数量及显式超时，但不继承共享额度或365秒旧默认超时。runner=None 明确禁用模型；dry-run 始终不调用 AI、不写建议、不预留额度、不迁移磁盘库。调用数量等配置由服务构造参数注入，本次不扩展受范围限制的 CLI 文件。

## 接口

`console.v3_extract_api.build(app_module, archive_path=ARCHIVE_PATH, *, service=None, inbox=None, runner=DEFAULT_RUNNER, clock=None, config=None, inbound_path=INBOUND_PATH, wechat_path=None, whatsapp_config_path=None, fetch=None, stat_fn=None, group_gid=None)` 返回五条路由。演练服务按已有动态模块列表自动挂载，用演练档案副本写入；本批未运行使用真实数据的演练服务。

鉴权沿用 `v3_roles_api`：principal 与 legacy session 同时有效；POST 读表单/调用AI/写库前 recheck，GET 返回前（含错误）recheck，I/O 在线程池。CSRF 仍由宿主中间件检查。响应带 no-store/nosniff。只接收 URL 编码表单，原始正文和规范编码表单各≤8192字节、≤40字段，拒绝重复/未知字段、非法UTF-8和无法核验原始大小的缓存表单。操作人只来自认证 principal，不能由表单伪造。

| 路径 | 参数与返回 |
| --- | --- |
| GET `/api/v1/extract/suggestions` | status=pending（默认）/accepted/dismissed/all；kind=offer/freight；conversation_key；include_low=0（默认）/1；page（默认1）、page_size（默认30，最多100）。低可信过滤在分页之前；已决定记录不受 pending 过滤影响。返回 items/total/page/page_size |
| POST `/api/v1/extract/scan` | 可选 conversation_key、days=1–365（默认30）、limit=1–100（默认20） |
| POST `/api/v1/extract/suggestions/{id}/accept` | 人工确认并补充字段，见下；返回 ok/id/kind/record_id/product_id/offer_id或freight_quote_id/reused |
| POST `/api/v1/extract/suggestions/{id}/dismiss` | 空业务表单，记录人工忽略；返回 ok/id |
| GET `/api/v1/extract/summary` | 返回 pending:{offer,freight}、pending_quality:{trusted,low}、total、last_scan_at；total 包含低可信 pending |

接受表单必须提供 `valid_until=YYYY-MM-DD`，或者 `confirm_valid_until=1` 明确确认建议中已有的到期日。未明确确认时不会直接使用建议日期。`quoted_on` 默认来源消息日期，可人工修改；有效期不能早于报价日。

产品报价必须有 `product_id` 或建议的 matched_product_id；也可 `create_product=1` 并提供 `product_name`、可选 `product_model`。产品名未填写时使用建议产品原话。已有产品与明确供应商冲突时拒绝；隐藏产品不可接受。配套供应商的新产品候选保留空 supplier_id，因为档案该字段只允许 supplier，不能把 vendor 身份改成 supplier 绕过校验。可人工覆盖 `party_id`、`price`、`currency`、`unit`、`moq_units`、`lead_days_min`、`lead_days_max`、`tax_basis`、`price_basis`、`includes_flightcase`。自由交期原话保留在价格依据中，不能自动变成数值承诺。

运价必须有已存在的 `forwarder_id`（或 party_id/已确认身份）、mode、unit、price、currency、目的国。档案原有服务还要求 `origin` 和 `service`，缺失时需老板补齐，不能用“未知”绕过。可人工覆盖 `destination_country`、`destination_detail`、`min_charge`、`transit_days_min`、`transit_days_max`、`notes`。mode 仅 air/sea/express/rail/truck；运价 unit 仅 kg/cbm/ticket/container。

产品与运价字段不能混填。接受在同一事务中核对当前会话身份、调用 `Archive.write(product_create/offer_create/freight_create)`、写入“由抽取员建议，老板确认”来源并更新决定状态，失败全部回滚、返回中文原因。扫描后身份发生变化时返回409，要求老板核对并明确选择当前单位及对应产品；当前身份为客户时拒绝写成本。重复接受返回同一个档案记录；已忽略建议不能接受，已接受建议不能忽略。未知 ID 返回404，业务校验400，状态冲突409，无法读写503，错误不回显原始异常。

## 手动运行

以下路径全部为虚构示例，应用前使用自己的演练副本：

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python scripts/extract_run.py --archive /tmp/fictional-archive.sqlite3
PYTHONPATH=src /opt/imgw/venv/bin/python scripts/extract_run.py --archive /tmp/fictional-archive.sqlite3 --inbound /tmp/fictional-inbound.sqlite3 --apply --who fictional-owner
PYTHONPATH=src /opt/imgw/venv/bin/python scripts/extract_run.py --archive /tmp/fictional-archive.sqlite3 --days 10 --limit 5 --conversation-key '<会话key>'
```

默认 dry-run，`--apply` 才保存，apply 默认仍用纯规则；必须同时显式 `--ai --apply` 才启用官方模型。`--timezone` 配置业务日期。CLI 始终禁止 WhatsApp HTTP：自定义档案使用同目录隔离入站路径，微信默认禁用，可用 `--wechat` 指定本地只读库。没有安装 cron、systemd timer 或后台任务。

## 验证与限制

测试在 `tests/extract/`，所有数据库、消息、价格、时间和AI回答均为虚构 fixture；runner和clock可注入，测试统一禁止联网及真实AI。D1c 新增币种/单位别名、Markdown 格式变体、同消息多价格、逐块丢弃、噪音防绕过、运行摘要脱敏和24窗口模拟覆盖。原 extract 测试中“币种/单位不匹配、产品短语无效、重复字段必须整窗失败”的断言已按 D1c 改为字段清空/置信度降级、首值保留或块级失败；这些输出不再使同窗其它有效报价丢失。测试仍检查价格无原文依据、引用无效、非法把握值及完全非格式输出会失败或延期。验收继续覆盖迁移保留/幂等、预筛及失败延期、有效期换算、匹配保守性、accept字段/事务/幂等、dismiss、扫描额度、鉴权/CSRF/recheck/表单限制、CLI及无渠道发送/自动确认。旧套件按既有目录隔离命令运行：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest tests/extract -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest tests/roles -q -p no:cacheprovider
for suite in quotes deals archive audit; do
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/$suite" /opt/imgw/venv/bin/python -m pytest "tests/$suite" -q -p no:cacheprovider || exit 1
done
```

边界：仅扫描渠道当前可提供的有界历史，不补采；不解析附件；不保证识别所有散文价格、币种和国际地名；不自动创建往来单位，不用地名猜目的国，不用含糊单位猜计费方式。规则与AI都可能漏报或误报，人工确认是业务事实进入档案的关口。产品候选仍需后续完善规格/包装并确认，confirmed报价不代表完整到手成本可用。未进行真实AI质量评测、真实会话验收、浏览器/前端验收、生产迁移或部署，也没有 commit/push。

## D1 原始文件清单（历史记录）

新增实现：

- `src/console/archive/migration_v7.py`。
- `src/console/extract/__init__.py`、`service.py`、`rules.py`、`ai.py`、`storage.py`、`validation.py`。
- `src/console/v3_extract_api.py`、`scripts/extract_run.py`、`docs/extract.md`。

修改实现：

- `src/console/archive/store.py`：v7迁移与旧入口兼容，注册建议表。
- `src/console/roles/common.py`：登记 L0 报价抽取员，岗位列表和运行查询自动接受 extract。

## I2：运价来源渠道

抽取员接受 `freight` 建议时可提交 `channel=platform_alibaba|forwarder|other`，不传时按 `forwarder` 入档；产品报价拒绝运价专属字段。该字段落在 `freight_quotes`，v9 迁移为历史行补 `forwarder` 默认值。抽取写服务升级到 v9 后，接受建议和档案行在同一事务中提交。
- `scripts/rehearsal_server.py`：原动态挂载列表增加 extract。

新增测试：`tests/extract/conftest.py`、`test_extract_rules.py`、`test_extract_service.py`、`test_extract_storage.py`、`test_extract_api.py`、`test_extract_cli.py`、`test_extract_review.py`。

更新旧测试：`tests/roles/test_roles_api.py`、`test_roles_migration.py`、`test_roles_safety_cli.py` 和 `tests/channels/test_whatsapp_rehearsal.py`。只更新新增岗位/路由的兼容断言或隔离 mock；原鉴权、数据保留和禁止渠道写入断言保留。

工作区原有未提交文件保留；没有修改 `migration_v4.py`、`migration_v6.py`、`web/`、`/opt/imgw` 或生产文件，没有 commit/push。

## D1 原始隔离验收（历史记录）

各目录独立运行，避免旧测试同名模块的收集冲突。extract 使用 fake runner/fake clock，并由统一 fixture 禁止 socket 连接和真实 AI 子进程；回归额外采用只在 `/tmp/d1-extract-check` 中的临时审计闸门，禁止网络、真实 AI gateway、非临时 SQLite 和生产目录写入。roles 自身已有同类离线 fixture。没有运行需要真实数据/浏览器联网的上线演练。

| 套件 | 通过主测试数 | 额外通过 subtest |
| --- | ---: | ---: |
| extract | 230 | 0 |
| roles | 238 | 0 |
| quotes | 28 | 189 |
| deals | 32 | 38 |
| archive | 38 | 46 |
| inbox_api | 37 | 99 |
| audit | 191 | 0 |
| channels | 190 | 14 |
| outbox | 42 | 53 |
| 合计 | **1026** | **439** |

最终各套件均无失败、无跳过。抽取套件含跨线程/跨进程扫描幂等与额度、无自动业务写入的逐表快照、outbox/bridge/channel发送 spy、真实服务 HTTP 接受闭环，以及审查发现的反例：自己的还价、跨产品挪价、不确定日期、价格精度截断、多价格字段串线和扫描后身份变化。

## D1b 文件与验收

本次修改仅限：

- `src/console/extract/`：`__init__.py`、新增 `config.py`、`service.py`、`rules.py`、`ai.py`、`storage.py`、`validation.py`。
- `src/console/roles/storage.py`：额度预留增加可选桶与抽取员限额提示，其他岗位默认行为不变。
- `tests/extract/`：更新 `test_extract_rules.py`、`test_extract_service.py`、`test_extract_storage.py`；新增 `test_extract_quality.py`、`test_extract_quota.py`、`test_extract_d1b_scan.py`、`test_extract_d1b_storage.py`。
- `docs/extract.md`。

改前先运行原 extract：**230 passed**。D1b 增加110项虚构用例，覆盖五类噪音每类至少3例、两条正确报价的 fake runner 闭环、原句复述去重、字段缺失与币种推断、隐藏/查询/summary、24次调用额度与旧库兼容、线程/进程原子性、17会话按8/8/1推进、可调N、真实线程短超时及迟到结果丢弃、fake timer 的180秒总预算、失败重扫和不回填规则。

完整 extract 通过后，再运行指定回归。测试使用虚构临时 SQLite、fake runner，并加 `/tmp/d1b-check/sitecustomize.py` 临时离线闸门，阻止网络、真实 AI 命令、非临时 SQLite 和生产目录写入；闸门不进入项目文件。

| 套件 | 主测试通过 | 额外 subtest 通过 |
| --- | ---: | ---: |
| extract | 340 | 0 |
| roles | 238 | 0 |
| archive | 38 | 46 |
| audit | 191 | 0 |
| quotes | 28 | 189 |
| deals | 32 | 38 |
| 合计 | **867** | **273** |

audit 最初与其他套件并行运行时，10万行聚合测试触发其原有2.8秒限时；隔离重跑完整 audit 通过，未修改测试门限或 audit 实现。最终验收按单独运行 audit 执行。

取舍：整单参考只由 AI 明确分类后保存为隐藏低可信，不新增 `dismissed_auto` 状态；低可信仍是可人工查看的 pending。额度用独立表保留旧写入兼容性，标记用已有引用 JSON 保存，冻结所有既有迁移。失败会话下次重新分析，无后台重试。配置通过服务注入，本次不新增 CLI 参数。

未做：真实数据扫描或真实 AI 质量测评、历史建议批量清理、附件识别、前端改动、部署及生产写入。`web/`、`/opt/imgw` 与 v4/v6/v7/v8 均未修改，迁移文件 SHA-256 前后相同；没有 commit/push。

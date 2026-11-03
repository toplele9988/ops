# 报价轮与核价（R2-B）

本模块沿用档案库保存报价轮、产品/费用快照和协商记录。本轮补齐数量与单位、运费替换与追加、配套计价、30%/70% 条款、中文 PI 预览和客户母语稿。`send` 仍只记录状态，不发送、不证明送达；母语生成仅调用语言服务的公共接口。未部署、未访问真实业务库或真实渠道，未 commit/push。

数量与单位在报价产品行直接修改；新增产品必须先填数量。24→30 后重算产品成本、销售小计、自动运费及关联数量行的配套费用。人工总费用和独立数量费用标记须复核，未复核不能标记发出。测试只使用虚构场景；交付基线、隔离依赖、测试与回滚见 [R2-B 交付记录](r2b-delivery.md)。

## 数据与迁移

实现位于 `console.quotes`、`console.v3_quotes_api` 和 `archive/migration_v3.py`。默认档案路径沿用 `archive.store.ARCHIVE_PATH`。`Quotes(path, store=None, today=None, fx_path=..., clock=None, state_path=None, lang_service=None)` 可注入临时数据库、业务日期（date 或返回 date 的函数）、缓存路径和 Unix 秒时钟。默认日期为进程本地日期；部署者应注入业务时区日期。

本轮不增加档案库迁移，不修改已发布 v9。条款、母语生成结果、审阅正文与版本历史保存在独立 `quote-state.sqlite3`：`state_path` 参数或 `OPS_QUOTES_STATE_PATH` 可配置，默认 `/var/lib/imgw/ops-app/quote-state.sqlite3`；显式临时档案默认使用同目录的 `<档案名>.quote-state.sqlite3`，内存档案默认内存状态库。禁止状态库与档案库同一路径，拒绝符号链接及父路径穿越。状态库使用进程锁、`BEGIN IMMEDIATE`、0600 权限和仅字段名审计，不在请求中接受库路径。

条款和审阅是独立库事务，不宣称跨库崩溃原子性。正文版本绑定本轮商业快照哈希，生成期间报价发生变化时拒绝保存，旧版本只能作为历史；副库不可读时返回固定错误，不能据缺失状态发出。复制新轮保留条款草稿，必须重新确认；配套数量引用同步改为新轮产品行。新增状态不在档案库创建任何新表。

以下 v3–v9 结构为已有基线说明，本轮未再次发布迁移。

迁移在一个 `BEGIN IMMEDIATE` 事务中执行，使用逐条 SQL，不使用会隐式提交的 executescript；升级失败全量回滚，提交前做 `foreign_key_check`，恢复连接原外键设置。v3 增加以下表，不重写任何 v1/v2 业务行：

| 表 | 保存内容 |
| --- | --- |
| quote_rounds | deal_id、round_no、status、currency、fx_x10000、fx_source、incoterm、destination_country、target_margin_bp、valid_until、notes、sent_at、created_at、updated_at |
| quote_lines | round_id、deal_item_id、product_id、name、qty、unit、offer_id、cost_unit_fen、cost_currency、cost_valid_until、cost_validity、units_per_carton、carton_l_cm/carton_w_cm/carton_h_cm、gross_kg、sell_unit_minor、note |
| quote_costs | round_id、kind、party_id、freight_quote_id、description、amount_fen、manual、valid_until、validity、source_note、calc_json |
| quote_negotiations | round_id、side、party_id、note、price_minor、currency、at、who |

为保存实际业务语义，额外增加 `quote_rounds.copied_from_round_id`（复制来源轮）、`quote_lines.lead_days_min/lead_days_max`（档案真实交期字段的快照）、`quote_lines.qty_confirmed`。案子原产品数量可空，复制时使用 qty=1 作为编辑占位，同时 qty_confirmed=0，阻断发出和自动运费；人工填写 qty 后才确认为本轮数量。案子 target_price 是客户目标，不自动作为销售价。

`UNIQUE(deal_id,round_no)` 和写事务串行化保证并发轮号唯一。报价子表随轮次、轮次随案子级联删除；删除案子产品项只清空对应 deal_item_id，保留报价快照。产品、报价及往来单位引用受外键保护。

旧档案调用者保留各自的迁移基线；报价 Store 设置 `target_version=9`，依次升级报价及后续档案字段。v9 给 `freight_quotes` 增加 `channel`，历史记录默认 `forwarder`。GET 不升级磁盘库；已有旧结构读出的渠道按 `forwarder` 兼容展示；缺库 GET 不创建文件。

迁移新增一条版本记录和一条字段名审计；所以 **业务表行数不变，audit 和 schema_migrations 各增加 1**（v1 直接升级还需各增加一条 v2 记录）。原有审计/版本行完整保留，重复迁移不再增加。新文件和 WAL/SHM 权限、锁、超时沿用现有 Store。

所有业务插入、修改、删除、状态、时间线和阶段待办都在同一业务事务记录 audit；detail 仅 `{fields:[字段名]}`，不记名称、备注、来源说明、谈判正文、金额或表单值。who 来自认证 principal，谈判 at 来自注入时钟，不允许表单冒充操作人或时间。

## 成本来源与历史快照

产品只接受 archive.offers 的**当前 confirmed**、quoted_on 不在未来、validity 为 valid/expiring、币种 CNY 的报价。沿用 `current_offer`：排除被 confirmed 更正取代的记录，再取报价日期/id 最新叶记录。最新记录过期或没写有效期时，不回退旧价。显式引用不可用 offer 返回 400；只给 product_id 时无可用报价则保存空成本并产生阻断。产品报价与产品必须匹配，隐藏/已合并产品不能新引用。

运价按档案线路键（含来源渠道）分组，用 `current_quote` 选择当前报价，同样须 confirmed、非未来、有效且为 CNY。显式不可用运价返回 400；不自动确认 suggested、不引用 rejected、不把外币成本当人民币。运价快照保存在 calc_json.quote，只存计算输入、有效期、目的国、来源渠道和引用标识，不复制原始备注或单位名称。

产品成本、包装、有效期及交期在引用时快照；随后修改源产品、报价或运价不会改变历史轮次。修改售价/数量不会刷新源成本。明确重新提供 product_id/offer_id 才重新选取产品报价和包装。新一轮复制上一轮产品/费用快照，并以 copied_from_round_id 标明来源；不复制谈判、sent_at 或客户有效期。原轮 sent 则自动 superseded，accepted/rejected 保留原状态。

有效期复用档案规则：到期当天仍可用；距到期 0–6 天 expiring（警告）；≥7 天 valid；昨天及以前 expired；空值 no_expiry。每次计算都以传入 today 重新检查快照日期，send 在写事务内重新计算后才能落状态。源记录后续变化不重写快照；要采用新资料必须新轮或草稿显式更新。

人工费用保留 `amount`（总价）或 `unit_price`（人民币单价）两种方式，须填 `valid_until`、`source_note`，零费用允许；不能同时填总价和单价。`vendor`、`domestic` 必须明确选择 `party_id`，不会把空单位配给第一家。运输费用对象须是货代或配套运输单位，配套费用须是 vendor。`description` 对内说明费用内容。

按单价计费用 `quantity_line_id` 关联本轮已确认数量行，冻结计价单位后随数量重算；或填人工确认的 `quantity`。关联单位不一致时要求先确认换算；改单位、删关联行或改独立数量的基础条件时不猜换算，标记 `needs_review`，通过 `costs/{cid}/review`、`confirm=1` 人工复核。改价或费用内容用移除后重新填写，原人工金额不被静默改写。

`source_offer_id` 可引用具体配套方已确认的询价。只读 `cost_sources` 从当前报价和抽取接受记录解析实际单位，不将 vendor 冒充 supplier；保留单位、产品、单价/单位、币种、有效期、来源引用和会话标识快照，可回看询价会话。随后修改档案不改写快照。配套产品已作为报价产品行时拒绝再次作为配套费；反向新增产品也拒绝。同一人工项目但未结构化关联产品的记录仍需人核对描述和来源，不能凭名称猜测是否同一项。

自动运费禁止混入人工单价、数量、来源、有效期等字段。所有自动录入/选择/重算核对目的国和运价可用性；人工运费也须明确目的国和运输方式。

## 运费、汇率与利润

每一条自动运费都按整轮产品清单计算。所有单位（含 ticket/container）都要求每行数量已确认且外箱三边、毛重、每箱数量齐全，否则金额为 null，阻断码 `freight_packing_missing`。不使用净重、本体尺寸或默认每箱一件来替代缺失资料。

- 每行箱数 = ceil(qty / units_per_carton)，不同产品独立进箱，不拼箱。
- 实重 kg = Σ箱数 × gross_kg。
- 体积 cbm = Σ箱数 × 长 × 宽 × 高（cm）/ 1,000,000。
- 体积重 kg = Σ箱数 × 长 × 宽 × 高 / 5000。除数直接引用 supplier_data.volumetric.DEFAULT_VOLUMETRIC_DIVISOR；因现有浮点工具不适合金额路径，运算使用 Decimal 重写同一公式。
- air/express 计费重量 = max(整票实重, 整票体积重)；sea/rail/truck 的 kg 使用实重。
- kg 按计费重，cbm 按体积，ticket 按一票，container 按一柜；各自乘单价后与 min_charge 取较大者，不拆柜、不按体积折算柜价。

calc_json 保存箱数、分行包装汇总、实重、体积、体积重、计费重、单价、最低收费、计费基数及运价快照。增删产品或改数量后自动按**冻结运价和本轮包装**重算，避免旧总价漏算新增数量。`costs/{cid}/recalc` 才刷新当前产品包装和同线路当前可用运价；本轮其他自动运费也按刷新后的包装及各自冻结运价重算。产品成本不随运费重算变化。重算失败整体回滚。

汇率只读 `/var/lib/imgw/wechat/fx_usdcny_cache.json`，路径可注入；实测结构为 `rate`、`source`、`source_updated`、`fetched_at`。读取 rate（可为十进制字符串）和 source，fetched_at 为来源抓取 Unix 秒，输出 as_of 取整数秒；不猜 source_updated 的时区。缓存不会被更新，也不发网络请求。新 USD 轮次读最新缓存，缺失、无效、不可读时 fx=null 并阻断，必须手填。非 USD 必须人工给汇率；后续同币种轮次沿用原手填值和来源。切换币种会清空销售单价，未同时提供新汇率则清空汇率，避免旧数字被重新解释。

保存方向固定为 `1 单位报价币种 = fx_x10000 / 10000 人民币`，默认 USD。缓存多余精度 HALF_UP 至四位；表单 fx_rate 最多四位，不接收科学计数法。`fx_source` 可自填，未填时记“人工填写”；缓存来源包含抓取时间。缓存年龄只展示，不自动判断交易汇率或保值成本。

金额计算以整数人民币分/外币分为基础，维度 REAL 从 `Decimal(str(value))` 转入；**没有二进制浮点金额运算**。

```text
产品成本（分） = Σ cost_unit_fen × qty
总成本（分） = 产品成本 + Σ amount_fen
销售额（外币分） = Σ sell_unit_minor × qty
销售额（人民币分） = HALF_UP(销售额外币分 × fx_x10000 / 10000)
利润（分） = 销售额人民币分 - 总成本分
利润率 = 利润 / 销售额人民币分
inverted = 利润 < 0
below_target = 利润 × 10000 < target_margin_bp × 销售额人民币分
```

默认目标利润率 15%，可设 0–90%，不按显示后百分比比较。金额除法、运费金额和汇率缩放使用 Decimal、ROUND_HALF_UP；利润率显示两位百分比。销售额先合计外币分，再折人民币并四舍五入一次，不逐行折汇累加。

费用按**数量占比**分摊。按产品行 id 顺序，对累计数量计算 `HALF_UP(费用总分 × 累计数量 / 总数量)`，本行分摊为相邻累计值之差。最后一行累计必然等于总费用，不产生负尾差。例：数量 1、3，费用 101 分，分摊为 25、76 分；数量各 1 的四行分摊 2 分为 1、0、1、0 分。`landed_cost_cny` 是该行总到手成本（产品总成本加分摊费用），不是每件成本。

理论每件目标价 = 行到手成本 / qty / 汇率 / (1−目标利润率)。`min_price_for_target_minor` 返回满足实际 HALF_UP 人民币收入规则的**最低整数外币分单价**，不能因普通四舍五入少一分而达不到目标。实现先求最小所需人民币销售分 `ceil(行成本分/(1−目标))`，再反解 HALF_UP 下界（所需分−0.5），向上取整外币分；这是求可达下界的取整，不改变金额 HALF_UP 规则。零成本也要求正销售额。每行目标单独检查，全轮销售额折汇可能有分级尾差，最终以整轮 compute 为准。

缺少成本金额、运费未能计算或数量未确认时 `cost_complete=false`，cost_cny 仅是**已知成本小计**，profit_cny、margin、最低售价均为 null，不能将缺失值当作零成本利润。缺汇率或有售价未填时 revenue_cny/profit_cny/margin 为 null；revenue_minor 是已填售价的小计。所有这些情形均阻断发出。

## 检查与状态

`calc.compute(round, lines, costs, today)` 无数据库、文件或时钟 I/O，不修改输入。输出 totals、per_line、blocking、warnings、inverted、below_target。问题仅含稳定 code、中文 message 和可选 line_no/cost_no，不在错误中泄露名称或金额。

| 类别 | code |
| --- | --- |
| 阻断：产品成本 | offer_unusable、offer_no_expiry、offer_expired、cost_currency_invalid |
| 阻断：费用 | freight_unusable、freight_packing_missing、cost_no_expiry、cost_expired、manual_source_missing、cost_amount_missing、manual_cost_review、duplicate_accessory、freight_destination_mismatch、freight_currency_invalid |
| 阻断：轮次/售价 | lines_missing、qty_unconfirmed、fx_missing、sell_price_missing、revenue_zero、ddp_no_freight、inverted、quote_no_expiry、quote_expired |
| 阻断：承诺期限/条款 | offer_ends_before_quote、cost_ends_before_quote、terms_unconfirmed（服务层） |
| 警告（不阻止发出） | offer_expiring、cost_expiring、below_target、fob_no_domestic、ddp_scope_unverified、freight_scope_unverified |

客户有效期晚于任一产品或费用快照有效期时阻断，本轮不提供人工锁价旁路。DDP 必须有至少一条运费行，并始终提示税费、清关及附加费仍需人工核对；成本小计齐全不代表全部到门费用已确认。FOB 无 domestic 行提示可能漏国内到港费用。目标利润率未达标只是警告，倒挂才阻断。零销售额、空产品轮以及客户报价已过期也不能发出。

状态只允许 draft → sent → accepted/rejected/superseded。send 必须 confirm=1，无阻断，且客户有效期已填未过期；写“报价第 N 轮已发出”。accept 仅从 sent，写接受事件；当前案子阶段在 awaiting_payment 之前时，调用案子阶段规则推进并补模板待办；后续阶段不倒退，不在 accept 设置 won。reject 仅从 sent，不擅自将案子设 lost。new round 将最近的 sent 轮置 superseded。已发出或结束的轮次只允许记录谈判及更改 notes，其他编辑返回 409“这一轮已发出，请新建下一轮”。

谈判 side=customer/supplier/forwarder/vendor/us，保存可选 party_id、note、price/currency、at/who。price 非空未指定币种时采用轮次币种；任何状态都可追加谈判，同时写一条“报价第 N 轮记录还价与回应”案子事件。正文留在谈判表，不复制进事件摘要或 audit。

## HTTP 接口

`build(app_module, archive_path=ARCHIVE_PATH, *, service=None, today=None, fx_path=FX_PATH, clock=None)` 返回 22 条路由，兼容已有演练自动挂载 `build(gate, archive_path)`，因此没有另建演练挂载路径。生产挂载仍需另行部署。

认证、响应头和表单完全复用既有实现：principal 与 legacy session 闸门；POST 在读取表单及任何写入前 recheck，GET 在返回前（含错误）recheck；数据库/文件操作在线程池执行；no-store、nosniff。宿主必须保留 CSRF 中间件。字段只接收单个文本值，优先 request.state.form，回退 parse_qs，原始体及规范编码表单均 ≤8 KiB，≤40 字段，重复、未知或非法 UTF-8 参数拒绝。预解析表单没有 Content-Length 也没有缓存原始体时拒绝，避免无法核验大小。业务错误为中文 400/404/409，未知读写异常为固定中文 503，不回显路径。

以下前缀均为 `/api/v1/`。POST 返回 `{ok:true,id}`；子行操作 id 为子行 id。未列出的 GET 参数不接受。

| 路径 | 字段与返回 |
| --- | --- |
| GET deals/{id}/quotes | `{items:[{id,round_no,status,currency,incoterm,totals,blocking,warnings,sent_at}]}`，blocking/warnings 为数量，按轮号升序 |
| POST deals/{id}/quotes | 空表单；下一轮，首轮复制案子产品，USD/DDP、目标15%、最新本地汇率；后续轮复制行/费用和贸易条件 |
| GET quotes/{id} | 轮次字段、lines、costs（含 calc）、negotiations、全部 compute 结果、fx 当前缓存 |
| POST quotes/{id}/update | incoterm、currency、fx_rate、fx_source、destination_country、target_margin、valid_until、notes |
| POST quotes/{id}/lines | product_id 或 name 必有其一，qty 必填；unit、sell_unit_price、offer_id、note 可选 |
| POST quotes/{id}/lines/{lid}/update | 同产品行字段，部分更新；显式给 product_id/offer_id 刷新引用 |
| POST quotes/{id}/lines/{lid}/remove | 空表单，必须是该轮的产品行 |
| POST quotes/{id}/costs | kind；party_id/description；自动 freight 给 freight_quote_id；人工给 amount 或 unit_price/source_offer_id、quantity_line_id 或 quantity、unit、valid_until/source_note；人工 freight 另给 destination_country/mode |
| POST quotes/{id}/costs/{cid}/remove | 空表单，必须是该轮费用 |
| POST quotes/{id}/costs/{cid}/recalc | 空表单，仅草稿的自动 freight |
| GET quotes/{id}/freight-options?mode=air\|sea\|express\|rail\|truck | 当前目的国/运输方式的有效运价；推荐按目的国、目的地细分、方式、起运地和服务覆盖范围分组；未填目的国只可查看，不能选择 |
| POST quotes/{id}/freight-options/choose | confirm=1、freight_quote_id；selection_mode=replace/append；替换给 replace_cost_id，追加给 segment；request_key 保证同请求幂等；reason_type/reason 由人核对，页面要求具体原因。详见物流比价文档 |
| GET freight/compare?destination_country=&mode= | 不依赖报价轮，列出当前有效运价；可选 page/page_size，含价格、最低收费、时效、有效期和渠道 |
| POST quotes/{id}/negotiations | side、note 必填；price、currency、party_id 可选 |
| POST quotes/{id}/status | action=send/accept/reject；send 需 confirm=1 |
| GET quotes/{id}/text?lang=en\|zh\|native | 默认 en；返回 text、ready、blocking、warnings、source_hash、lang；native 仅读取仍有效的已生成母语稿 |
| POST quotes/{id}/terms | confirm=1、payment=30_70、delivery_scope；可选 seller_name/seller_address；确认本轮条款 |
| POST quotes/{id}/native | conversation_key，生成母语报价与中文回译，返回语言来源和人工锁定状态；不会发送 |
| POST quotes/{id}/review | confirm=1、source_hash、lang、text；必须逐字匹配当前生成版本，保存审阅人、时间和版本历史 |
| GET quotes/{id}/pi | 中文非正式 PI 预览，列出缺少的条款与公司开票资料；不会发送 |
| POST quotes/{id}/costs/{cid}/review | confirm=1；人工确认数量改变后的总费用仍可用 |
| GET fx | `{usd_cny:"7.1234"\|null,source:文本\|null,as_of:整数Unix秒\|null}` |

输入金额为元字符串，最多两位，范围 0–9999999999.99；拒绝浮点类型、负值、指数和表达式。`0.1` 与 `0.2` 精确存为 10、20 分，合计输出 `0.30`；字面 `0.1+0.2` 返回 400。qty 1–1000000；ID 为正 SQLite 整数。name≤200、unit≤40、destination_country≤100、source_note/fx_source/description≤500、notes/note≤2000。日期为 ISO 年-月-日。币种为三位大写字母。

API 金额用两位小数字符串：line.cost_unit / sell_unit_price、cost.amount、negotiation.price、calc.unit_price/min_charge/amount 和内部运价 quote.price/min_charge。不输出 *_fen。汇率 fx_rate 四位；target_margin、totals.margin 两位百分比（如 `15.00%`），写入 target_margin 为无 `%` 的百分数（如 `15.00`）。明确带 `_minor` 的计算契约字段 revenue_minor、min_price_for_target_minor 保留整数便于精确计算，详情同时提供 totals.revenue、per_line.min_price_for_target 两位价格字符串。

客户文本只使用白名单：轮号、产品名/数量/单位/销售单价/小计、贸易术语、目的地、总销售额、有效期、交期，以及人工确认的 30% 定金、70% 发货前付清和交付范围。缺交期或条款保留明确【待确认】。中英文模板确定性生成；PI 在同一快照上计算定金与余款，按外币分四舍五入且两者之和等于销售额，缺开票公司名称/地址时列明缺项，始终标非正式预览。文本不读取成本、利润、供应商/货代名称、运价明细、内部备注、谈判记录或案子标题；独特虚构价格数字和保密标记有泄露断言。操作者必须确保自行填写的客户产品名/目的地本身适合对外。

## 母语与审阅

只通过 `Lang.read('contact', ...)` / `Lang.read('settings')` 和 `Lang.write('compose', ...)` 访问语言模块，不创建语言表。会话必须已挂本案，并与本案客户及 identities 一致；不自动取第一条会话。母语未知、仅国家提示、低置信自动识别均拒绝生成；人工锁定优先。中文回译使用 P1 公共 compose 的阅读语言，因此阅读语言不是 `zh-CN` 时明确要求先设置为简体中文，不把其他语言标成中文。

传给语言服务的只有中文客户白名单文本，不传成本、利润、供应方名称、内部备注或协商。产品名称、单位、完整有效期用 P1 能保护的型号式标记逐字保护，再校验标记数量还原；P1 同时校验型号、DMX、金额等保留片段。必须母语稿和回译都通过校验才保存，不用英文静默兜底。独立额度用完返回中文 429 与 Retry-After；翻译失败仍是草稿，不触发发送或重试。

翻译缓存由 P1 按原文哈希、目标语言和术语版本维护。报价商业快照变化、目标母语/来源/锁定改变时旧稿不可用；模型调用期间再核对本轮版本，避免把旧金额译文覆盖到新数量。审阅需提交当前 `source_hash` 和逐字相同的生成文本；改文案先改产品/条款表单再生成，不接受弹窗临时改价。已审阅正文按版本去重保留历史，当前哈希或母语不符时 `review=null`，历史仍可回看。

## 取舍与局限

- 本轮有前端数量/费用/条款、母语生成与中文 PI 预览；没有正式 PI 文件导出、公司资质核验、渠道交接或真实客户报价验收。WG-09B 的实际发出版本/渠道回执绑定由后续任务负责。
- 成本全部须为 CNY，所有报价币种按两位小数计价；未实现零位/三位小数币种、交叉汇率或自动换币售价。缓存来源时间不代表供应商锁汇承诺。
- 物流比价遵循现有报价轮人民币运费流程；外币运价可在比价中展示原币计算信息，但不生成人民币金额，不能用新接口直接写入运费成本行。
- 运费不支持混装、体积/重量阶梯、承运商重量进位、超长费、电池附加费、柜容量、多柜、关税/税费估算。每个已选择运输段按整票计算；追加段会累加，页面须人填写运输段与原因，不能推断分段/税费。国内/配套支持总价或按已确认数量计价。
- 已校验运费目的国；起运地/服务覆盖、DDP 税费完整性仍须人工核对，不自动推断贸易术语覆盖范围、单位转换、MOQ 或包装真实性。
- 复制旧轮的成本仍是旧快照，可能马上阻断；系统不悄悄换成新成本。已发出轮不能“撤回编辑”，须新建下一轮。
- 客户有效期超过任一成本有效期会阻断；本轮没有人工锁价确认功能，不自动延长供应方承诺。
- 列表无分页，适用于单案较少轮次；没有大规模性能或真实浏览器验收。
- 缺成本的已知成本小计不能用于判断利润。真实库演练确认了此分支；真实可发出整单尚缺有效产品成本资料，不能据此声称已经验证真实成交价格。

## 历史验证与真实库副本演练（本轮未执行）

自动测试只使用虚构产品、单位、价格、文本和临时文件；API 缺 Starlette 时按既有约定 skip，没有安装依赖。覆盖纯函数表驱动、汇率/金额精度、费用尾差、最低售价下界、历史快照、有效期、状态、并发轮号、事务回滚、事件/待办、文本保密、v1/v2 数据保留、认证/recheck/表单限制和所有路由。

演练对真实 archive.sqlite3 只读 backup 到临时副本，WAL 当时为空，源连接使用 `mode=ro&immutable=1` 及 query_only；只在副本执行迁移和演练写入。源数据库、WAL、SHM 在演练前后大小和 SHA-256 均一致，临时副本已删除。逐行比对原有内容全部相同，foreign_key_check 无错误，再次迁移幂等。

| 原有表 | 迁移前 | 迁移后 |
| --- | ---: | ---: |
| parties | 17 | 17 |
| products | 138 | 138 |
| product_sources | 460 | 460 |
| offers | 39 | 39 |
| freight_quotes | 0 | 0 |
| terms | 0 | 0 |
| product_merges | 0 | 0 |
| identities | 0 | 0 |
| deals | 0 | 0 |
| deal_participants | 0 | 0 |
| deal_conversations | 0 | 0 |
| deal_items | 0 | 0 |
| deal_tasks | 0 | 0 |
| deal_events | 0 | 0 |
| audit | 785 | 786 |
| schema_migrations | 2 | 3 |

新增四张 quote 表在迁移后各为 0 行。原库包装齐全产品 1 个，其中有可用成本报价的产品 0 个。演练取该产品包装，新增虚构有效空运报价（2.00 元/kg，最低50.00元）、虚构案子及售价，只输出以下数字和问题，不输出真实名称或价格明细：

- 数量 3；箱数 2；实重 5.6 kg；体积 0.18228 cbm；体积重/计费重 36.456 kg；虚构运费 72.91 元。符合 max(5.6,36.456)×2，HALF_UP 后高于最低收费50元。
- 虚构销售额 USD 300.00，按当时缓存折人民币 2004.15 元；已知成本小计 72.91 元，cost_complete=false；利润/利润率/最低售价均 null。
- ready=false；阻断 offer_unusable（没有可用供应商报价）、offer_no_expiry（缺成本有效期）；warnings=[]。没有将建议或缺期限记录改成已确认成本，没有对外发送。

以下为最终双环境各套件命令及末尾原文；系统 Python 的依赖跳过如实保留。

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/quotes -p 'test_*.py'
----------------------------------------------------------------------
Ran 28 tests in 4.622s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/deals -p 'test_*.py'
----------------------------------------------------------------------
Ran 32 tests in 4.444s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/archive -p 'test_*.py'
----------------------------------------------------------------------
Ran 38 tests in 4.208s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/kb -p 'test_*.py'
----------------------------------------------------------------------
Ran 35 tests in 7.942s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inbox_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 37 tests in 2.350s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/channels -p 'test_*.py'
----------------------------------------------------------------------
Ran 45 tests in 2.934s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/outbox -p 'test_*.py'
----------------------------------------------------------------------
Ran 42 tests in 4.605s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/media -p 'test_*.py'
----------------------------------------------------------------------
Ran 25 tests in 0.254s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/packing_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 24 tests in 0.487s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inspiration -p 'test_*.py'
----------------------------------------------------------------------
Ran 56 tests in 2.156s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/web_app -p 'test_*.py'
----------------------------------------------------------------------
Ran 6 tests in 0.036s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/supplier_data -p 'test_*.py'
----------------------------------------------------------------------
Ran 110 tests in 4.616s

OK
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/quotes -p 'test_*.py'
----------------------------------------------------------------------
Ran 28 tests in 4.172s

OK (skipped=4)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/deals -p 'test_*.py'
----------------------------------------------------------------------
Ran 32 tests in 9.560s

OK (skipped=7)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/archive -p 'test_*.py'
----------------------------------------------------------------------
Ran 38 tests in 2.320s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/kb -p 'test_*.py'
----------------------------------------------------------------------
Ran 35 tests in 7.037s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/inbox_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 37 tests in 0.210s

OK (skipped=19)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/channels -p 'test_*.py'
----------------------------------------------------------------------
Ran 45 tests in 1.313s

OK (skipped=16)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/outbox -p 'test_*.py'
----------------------------------------------------------------------
Ran 42 tests in 4.241s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/media -p 'test_*.py'
----------------------------------------------------------------------
Ran 25 tests in 0.094s

OK (skipped=9)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/packing_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 24 tests in 0.000s

OK (skipped=24)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/inspiration -p 'test_*.py'
----------------------------------------------------------------------
Ran 56 tests in 1.989s

OK (skipped=11)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/web_app -p 'test_*.py'
----------------------------------------------------------------------
Ran 6 tests in 0.000s

OK (skipped=6)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/supplier_data -p 'test_*.py'
----------------------------------------------------------------------
Ran 110 tests in 4.956s

OK (skipped=6)
```

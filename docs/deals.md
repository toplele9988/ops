# 案子（Deal）地基

供内部运营人员把同一笔生意的客户、产品供应商、货代、配套供应商和跨渠道会话放到一起，查看下一步、阶段待办与“卡在谁手上”。初版交付后端模型、迁移、纯函数规则、接口与虚构测试；R2-A 在既有结构上修正付款成交规则、页面和读取恢复，未新增迁移、修改应用挂载或生产程序，未部署。宿主挂载 `console.v3_deals_api.build(app_module, ...)` 返回的 17 条路由，并保留现有认证及 CSRF 中间件。

## R2-C：需求确认、多供应商方案与逐项等待

本节为 WG-05、WG-04、WG-06B 的开发候选，覆盖下文早期“只有后端、没有界面”的交付范围。
未部署，未读真实业务数据，未连接真实渠道或模型。验收仅用虚构询盘、供应商、价格、临时 SQLite 和假 runner；
测试通过不能证明真实数据完整、真实询盘覆盖率或翻译质量。

案子页新增三个独立组件，原产品清单仍可手工填写：

1. 「询盘里的产品需求」从本案已确认客户身份的文字消息提取候选。当前用保守中/西语规则，
   展示产品原话、数量、目的地、规格、配套和来源会话/时间；范围、或者、多产品归属不清时留空。
   先核对补齐，再「加入产品清单」，也可忽略。扫描不会创建产品项、成本报价或往来单位。
   重复扫描/确认不会重复产品；原文或客户身份变化要求重新核对。目的地/规格/配套写入已有产品项备注，
   结构化确认值和来源留在独立候选库。只处理当前渠道可提供的最近 100 条、每条至多 4000 字文字；
   超长消息跳过，不解析附件，不从地名推国家，不猜价格、包装承诺或未说出的规格。
2. 「同一需求的供应商方案」按本案参与供应商，投影各产品当前已确认 offer；并排展示型号、规格、
   单价/币种/单位、MOQ、交期、包装、有效期和来源。不自动换汇，不按最低数字排序，不把同型号当成同规格。
   跨档案产品必须人工确认能用于该需求，并写选择理由；失效/未生效/缺有效期报价不能新选用。
   可以先仅记录 offer 引用，也可选本案现有草稿报价轮，复用报价服务在该需求行更新所选 offer，
   保留选择、谈判和经过记录。已发轮不允许新选择；重选不会改写其他轮的历史快照。
3. 「现在在等谁、等哪件事」分别展示每条会话方向及尚未关联、已关联不可读、方向未知。
   具体事项由老板登记对象、会话、等待起点和可选产品项；不会因另一话题的新消息自动结束。
   原事项等待起点、上次人工催问时间分别保留。催问只生成母语草稿和回译，生成草稿不登记已催问，
   人工登记催问也不重置等待。详见 [岗位的统一等待口径](roles.md)。

客户 CUS 与供应商 SUP 数字编号从单位名、别名、identity label 和原会话名派生，附单位资料与会话入口。
缺编号不猜；一个单位多号、多个单位同号或编号类别不符均提示人工核对，绝不据群名/编号合并身份。
没有新增编号映射表。客户详情可在本组件读已有单位 API，供应商可返回已有供应商页。

### API 与独立存储

新增 `console.v3_deal_workflow_api.build(app_module, archive_path, ...)` 返回 11 条路由。
宿主须保留既有 principal、旧会话、recheck、CSRF 中间件；POST 先 recheck 再读表单/写库/调用模型，
GET 返回前 recheck。仅接受 URL 编码表单、每字段一次、至多 8KB；未知字段与查询参数拒绝，
操作人来自认证状态，客户端不能选数据库路径。响应 no-store/nosniff；错误中文、400/404/409/429/503，
不返回异常路径、堆栈或模型原始输出。HTTP 模拟另验证宿主 CSRF 在候选写入前拒绝。

以下路径统一以 `/api/v1/deals/{id}/` 开头：

| 方法与路径 | 表单 / 行为 |
| --- | --- |
| GET `offers` | 需求、当前报价、编号、选择历史、本案草稿轮 |
| POST `items/{item_id}/offer` | `offer_id,reason,confirm_specs=0或1,round_id`（可选） |
| GET `demands` | 需求候选及已确认客户来源会话 |
| POST `demands/scan` | 可选 `conversation_key`，空则本案来源询盘 |
| POST `demands/{candidate_id}/accept` | 可覆盖 `name,qty,destination,specifications,accessories`；空数量保持未知 |
| POST `demands/{candidate_id}/dismiss` | 空业务表单，人工忽略 |
| GET `waiting` | 各方逐会话状态、具体事项、可选本案产品 |
| POST `waiting` | `party_id,conversation_key,title,waiting_on=them或us`；可选 `since,item_id,request_id` |
| POST `waiting/{item_id}/resolve` | 空业务表单，人工结束事项 |
| POST `waiting/{item_id}/remind` | `request_id` 必填；可选 `at`，只登记实际已催问时间 |
| POST `waiting/{item_id}/draft` | 可选 `supplement`，返回草稿/母语/回译/校验，不发送 |

独立库默认放 `/var/lib/imgw/ops-app/`，不增加档案库版本、列或表：

| 默认文件 | 环境变量 | 构造参数 |
| --- | --- | --- |
| `deal-offers.sqlite3` | `IMGW_DEAL_OFFERS_DB` | `Offers(path=...)` / builder `offers_path` |
| `deal-demands.sqlite3` | `IMGW_DEAL_DEMANDS_DB` | `Demands(path=...)` / builder `demands_path` |
| `deal-followups.sqlite3` | `IMGW_DEAL_FOLLOWUPS_DB` | `Followups(path=...)` / builder `followups_path` |

builder 对非默认档案路径自动把未指定的独立库及语言库放到档案同目录，避免演练回落到生产。
直接构造各服务时，测试须显式传临时 `path`。语言只复用 `Lang` 公共 read/compose，不建语言表。
各库写入串行并使用事务；新选择和确认用 ATTACH 同连接事务调用既有领域写逻辑。
既有档案可能用 WAL，SQLite 不能保证断电时跨文件同时提交；需求用产品项确定性来源标记、
报价选择用既有谈判记录中的稳定指纹，在人工重读/重提交时核对恢复，避免重复产品/报价行/谈判。
不要删除这些恢复标记。UI 的 POST 均关闭自动重试，结果不明确先重读，尤其不要重放任何发送。

完整文件、验证、基线和总指挥合并/部署/回滚说明见 [R2-C 交付记录](r2c-delivery.md)。

供内部运营人员把同一笔生意的客户、产品供应商、货代、配套供应商和跨渠道会话放到一起，查看下一步、阶段待办与“卡在谁手上”。本次交付后端模型、迁移、纯函数规则、接口与虚构测试；没有修改 `web/`、应用挂载或生产程序，没有部署。后续宿主挂载 `console.v3_deals_api.build(app_module, ...)` 返回的 17 条路由，并保留现有认证及 CSRF 中间件。

## 模型与事务

复用 `/var/lib/imgw/ops-app/archive.sqlite3`、`archive.Store`、`schema_migrations` 和字段名审计。生产库本次未迁移。`Store(path)`、`Deals(path, store=..., inbox=..., lookup=..., clock=...)` 和 API builder 都可注入隔离依赖。

| 表 | 主要字段和约束 |
| --- | --- |
| parties | 保留全部原字段和 id；kind 扩为 supplier/forwarder/customer/vendor；新增可空 country TEXT；同 kind、name 唯一 |
| identities | id、party_id、conversation_key UNIQUE、channel、label、created_at、created_by；一个会话只有一个全局归属，单位可有多个身份 |
| deals | id、title、customer_party_id、stage、status、value_fen、currency、next_action、next_action_due、lost_reason、notes、source_conversation_key、created_at、updated_at、stage_changed_at、won_at、closed_at |
| deal_participants | id、deal_id、party_id、role、note、created_at；同案子同单位唯一；role 可与单位类型不同，缺省取单位类型 |
| deal_conversations | id、deal_id、conversation_key、party_id、note、created_at；同案子同会话唯一；同会话可关联多个案子，party_id 可空 |
| deal_items | id、deal_id、product_id、name、qty、target_price_fen、currency、note；可引用档案产品，也可手工填写名称 |
| deal_tasks | id、deal_id、stage、title、due、status、auto、template_key、created_at、done_at；非空 template_key 在案子内部分唯一索引 |
| deal_events | id、deal_id、at、kind、summary、who；summary 1–500 字，kind 为 created/stage/status/note/link/participant/item/task/system |

案子子表外键 `ON DELETE CASCADE`；本轮没有删除案子的接口。档案单位、产品及身份继续受外键保护。索引覆盖案子状态/阶段/客户、参与方、会话 key、待办与事件时间线。

v1→v2 在一个 `BEGIN IMMEDIATE` 事务中完成：事务前关闭当前连接的外键执行，建立新 parties 表、逐列原样复制、删除旧表、换名，再恢复显式索引和触发器；不先重命名旧父表，避免 SQLite 改写子表引用。提交前 `foreign_key_check`，失败整体回滚；finally 恢复该连接原外键设置。旧库保留版本 1 并追加版本 2；新库直接记录版本 2 基线，重复迁移不再记录审计。未知迁移版本拒绝写入。新文件和 WAL/SHM 的权限、busy timeout、WAL 与写事务机制沿用档案库。

GET 不迁移文件：缺库只在内存返回空结果，v1 的案子域返回空列表/空汇总，身份查询返回 null，详情 404；首次写入通过同一 Store 迁移。每个业务插入、更新、移除以及时间线事件均在同一业务事务记录 audit。audit.detail 只有字段名，不复制标题、正文、备注、会话 key、原因或金额；who 只取已认证 principal.username。时间线允许用户记录内部笔记，不能与仅记录字段名的 audit 混为一谈。

`/api/v1/archive/parties` 的创建、列表 kind 过滤现已接受四种类型；创建/更新可写 country（≤100 字）。customer/vendor 名称与短名只规整首尾空白、全角空格，不删除城市、公司后缀或外文拼写。客户/配套供应商创建时未给短名则使用规整后的名称。供应商种子的原有简称规则保持原实现。

## 阶段与待办

顺序、中文标签、模板集中在 `deals/stages.py`，状态转换在 `deals/rules.py`。阶段进度条 index 从 0 开始，done 表示位于当前阶段之前，不表示历史上逐一实际经过或任务均已完成。

| 阶段 | 中文 | 模板（从 1 开始编号） |
| --- | --- | --- |
| inquiry | 询盘 | 弄清要什么产品、数量、目的国、价格预期 |
| sourcing | 询价核实 | 向供应商确认型号、尺寸、重量、价格、交期；向货代要运价；有特殊要求时联系配套供应商 |
| quoting | 报价 | 核对成本报价有效期；算到手价并检查倒挂；发出报价 |
| negotiating | 谈判 | 记录客户还价；回供应商和货代协商；更新报价 |
| awaiting_payment | 待付款 | 发 PI；确认付款条件；确认收款 |
| paid | 已成交·备货 | 下单给供应商；确认交期；通知客户生产进度 |
| inspection | 验货 | 安排验货；确认外箱、唛头、数量 |
| shipped | 已发货 | 订舱；发提单和发票给客户；确认预计到达日 |
| delivered | 已签收 | 确认签收；跟进使用反馈 |
| aftersales | 售后 | 记录问题；转给供应商或货代；跟进解决 |
| reorder | 返单 | 询问复购；对照上次价格和反馈 |

创建或实际切换阶段时，按 `<stage>:<n>` 稳定 key 生成缺少的模板待办，auto=1。重复进入同一阶段不会重复生成，也不会重开 done/skipped。手工待办 auto=0，绑定创建时的当前阶段；状态可设 open/done/skipped，done 写 done_at，重新打开或跳过清 done_at，真实状态变化记中文 task 事件。

**成交定义是人工已确认的客户付款。** 普通 create 只能创建付款前阶段；付款前案子不能用普通 update 进入 paid 或任何后续阶段，也不能单独设置 won。接受报价、待核对收款、退款都不算成交。人工确认首次定金/全款，或确认其他非退款收款时明确选择“这笔达到成交条件”，才在同一事务写入确认人、确认时间、收款事件并进入 paid、标记 won 和 won_at；任一步失败整体回滚。首次定金/全款不能用 qualifies=0 取消成交，页面应明确说明；尾款默认不成交，保留显式达标选择。重复 confirm 保留原确认人/时间，不重复付款、事件或成交时间。

已成交案子可由人继续推进 inspection、shipped、delivered、aftersales，保留 won 和原 won_at，不要求重新打开为 open。验货节点完成只提供建议，不自动发货。已有缺付款记录的历史 won 案不迁移、不批量回退，也不因编辑资料或推进阶段补造付款/成交时间；收款区标记待核对，由人核查。人明确回退到付款前阶段仍沿用原操作：恢复 open、清 won_at 和 closed_at，记 system/status/stage 事件；再次成交须通过新的收款确认，普通 update 不能跳回 paid。付款后不能单独设 open。一个请求中同时提交冲突阶段和状态返回 400，整个业务事务回滚。退款/作废不自动回退阶段。

“首次”只检查本案仍有效的已确认达标款（deposit/full 或 qualifies=1）。仍有此类旧款时，人工回退后的普通新入账不会推翻回退；若唯一达标旧款已作废，新确认的定金/全款重新视为首笔，qualifies=0 也会成交。

每次显式提交 status=lost，必须同时提交非空 lost_reason（≤200 字）；首次变为 lost 写 closed_at。离开 lost 清 closed_at。paused 可手工设置，暂停不会自动清除既有付款时间。阶段变化示例：“阶段：询价核实 → 报价”；状态变化也使用中文标签。

按本次“won/lost 不再生成新的自动待办”规则，状态转换先执行，模板生成后执行：**进入 paid 时已是 won，因此 paid 及后续模板保留定义但不会自动落库**；可手工添加履约待办。已有未完成待办不自动关闭，lost 案子保留历史任务。暂停状态可在切换阶段时生成模板；单独把 lost 恢复 open 不补发当前阶段模板，下一次实际进入阶段时按缺失 key 生成。

## R2-A 页面与读取恢复

案子页在待付款阶段提供“核对并确认收款”，不再用普通阶段更新进入成交；已成交案子提供逐步确认验货、发货、签收、售后的按钮，不显示必然失败的 won→open“重新打开”。履约节点独立记录实际进度，建议显示后端 `reason` 并由人确认改阶段，不生成另一套自动履约待办。收款可核对关联报价轮、确认人和时间，并补充币种差异说明；首笔定金/全款固定说明成交规则，尾款保留显式达标选择。历史 won 缺收款只提示核对，不补造收款。

案子详情、报价、可选参与方及收款/履约/问题/履约看板/返单线索，均把读取失败与成功空列表分开。失败显示哪项未读到和“重新读取”，不显示无记录引导或允许用过期付款列表再次登记。POST 明确 4xx 拒绝提示原因；408、5xx、断线或响应解析失败视作结果未知，先只读回核对，保持提交锁定，读取成功后还需人工核对才可继续，绝不自动重放。手工录入已知案子 id 时保留案子入口并说明补完；建案响应丢失时先查候选案，不盲目重建。

完整文件清单、基线 SHA-256、测试命令、已知环境限制和部署/回滚见 [R2-A 交付记录](r2a-delivery.md)。

## 会话归属与“卡在谁手上”

所有新身份、来源会话和案内会话 key 均使用现有 `inbox.model.decode_key(key, 'conversation')` 验证，不接受 customer key 或旧档案 `{channel,scope}` 引用。验证只保证格式正确，不要求此刻能读到该会话。

给会话关联 party_id 时，无身份则创建，有其他归属则 409；省略 party_id 时沿用已知身份。已知单位自动成为该案参与方。重复关联返回原关联 id，不增加事件。身份可独立创建；更换已有归属必须 confirm=1，随后同步该会话在所有案子的关联归属、添加新参与方并记事件。原参与方记录及备注保留，可能变成“未关联会话”；不会把显式 customer_party_id 一并改成另一个客户。

移除参与方时，只移除该案内此参与方的会话关联；全局身份和其他案子的关联保留。移除会话关联也不删除身份。source_conversation_key 是创建来源，解除关联后仍保留来源字段。

`waiting.evaluate(participants, conversations, tasks, lookup, now, overdue_hours=48)` 是注入时钟和 lookup 的纯投影。lookup 返回 `{last_role,last_at,channel_label,title}` 或 None；不会返回聊天正文。案子默认 lookup 使用共享收件箱的请求内快照，直接读取已有最后消息摘要，不逐会话请求历史。缺失/失败视为无可用观察。

- 对每个参与方，仅从**该案子内**归属于它的关联会话取 last_at 最新的一条；buyer → us，seller → them，没有可用时间/角色 → none。相同时间按关联 id 顺序稳定取第一条。
- waiting_on_us：任一参与方 us。waiting_on：them 的参与方角色，按参与方顺序去重。
- overdue：them 且等待**严格超过** 48 小时的角色去重；正好 48 小时不超时，阈值可注入。
- due_tasks：status=open 且 due≤now 的待办数。needs_attention：waiting_on_us、非空 overdue、due_tasks>0 任一成立。
- unlinked_participants 统计完全没有案内会话关联的参与方；有链接但暂时读取失败仍为 state=none，不计为“未关联”。无归属的案内会话影响最近活动时间，不推断参与方或等待角色。
- last_activity_at 为全部事件时间与全部案内会话最新消息时间的最大值，Unix 秒或 null；不依赖被截取的最近 100 条时间线。

这套判断只用消息方向，与收件箱本身的结束语、30 天休眠待回复规则不同；不分析语义，不推断付款、真实业务角色、履约完成或责任。模板也仅是提醒，不会发送报价、PI 或消息。

## API 契约

builder 支持 `archive_path/service/inbox/lookup/clock/inbound_path/wechat_path/whatsapp_config_path/fetch/stat_fn/group_gid/overdue_hours`；clock 是业务 Unix 秒时钟。鉴权、no-store/nosniff 响应头沿用档案接口；POST 在读表单前和解析后紧邻业务写入前各 recheck，GET 返回前（包括错误）recheck。I/O 在线程池。403 由宿主生成，400 参数、404 记录不存在、409 归属/约束冲突、503 固定中文服务错误，不回显路径或内部异常。

POST 复用档案表单读取：优先 request.state.form，回退 parse_qs；≤8 KiB、≤40 字段，校验原始体长度和编码后大小，拒绝重复/未知字段。csrf 仍由宿主校验，不进入业务或审计。以下新增路径均在 `/api/v1/` 下。

| 路径 | 方法与字段/返回 |
| --- | --- |
| deals/summary | GET；open/won/lost/paused 案子数，by_stage:[{stage,label,count}] 只数 open；waiting_on_us/overdue 是案子数，due_tasks 是待办数，三者统计全部状态；stale 只数 open 且至少 7 天没有事件或会话消息 |
| deals | GET stage/status/q/waiting/party/page/page_size；status 缺省 open，可显式 all；q 匹配标题/客户名的字面不区分大小写子串；waiting=us 筛 needs_attention，them 筛非空 waiting_on；party 是参与方单位 id |
| deals/{id} | GET 完整详情，字段见下 |
| deals | POST title/customer_party_id/conversation_key/party_kind/party_name/stage；stage 缺省 inquiry，只允许付款前阶段；返回 {ok:true,id} |
| deals/{id}/update | POST title/stage/status/value/currency/next_action/next_action_due/notes/lost_reason；只更新所给字段；付款前不能跳到 paid 及之后或设 won；可选文本和金额传空串可清空，title 不能清空 |
| deals/{id}/participants | POST party_id 必填，role/note 可选；重复单位更新参与方信息，返回参与方行 id |
| deals/{id}/participants/{pid}/remove | POST 空表单；pid 是参与方行 id，必须属于路径中的案子 |
| deals/{id}/conversations | POST conversation_key 必填，party_id 可空；返回关联行 id |
| deals/{id}/conversations/{cid}/remove | POST 空表单；cid 是案内会话关联行 id |
| deals/{id}/items | POST name 必填，product_id/qty/target_price/currency/note 可选；返回产品项行 id |
| deals/{id}/items/{iid}/remove | POST 空表单；iid 是产品项行 id |
| deals/{id}/tasks | POST title 必填、due 可空；返回待办行 id |
| deal-tasks/{id}/status | POST status=open/done/skipped |
| deals/{id}/events | POST note 必填、≤500 字；新增 note 时间线 |
| deals/by-conversation | GET key 必填；{deals:[{id,title,stage,stage_label,status}],party:{id,name,kind}\|null}，包含各状态的全部关联案子 |
| identities/lookup | GET key 必填；已有身份返回 {party:{id,name,kind}}，不存在返回 JSON null |
| identities | POST conversation_key/party_id 必填，confirm=1 才可更换归属；返回身份行 id |

创建时 customer_party_id 必须指向 customer。会话未归属且给了 party_kind+party_name，按同 kind、规整后同名复用或新建单位并建立身份；两个字段必须一起提供且必须搭配 conversation_key。有既有身份则沿用其归属。无身份且给了 customer_party_id 时，以该客户建立身份。无显式客户且会话单位是 customer 时自动使用该客户。空标题使用客户名+“的案子”，长客户名裁到总长 500 字；无客户则“未命名案子”。客户加入参与方；给定会话关联入案子，已知归属单位同样加入参与方。

列表返回 `{items,total,page,page_size}`；page 默认 1（1–9999999），page_size 默认 30（1–100），先筛选再分页，按最近活动、id 倒序，越界页 items=[]。每项包含：

```text
id, title, customer:{id,name}|null, stage, stage_label, status,
value:"12.34"|null, currency, next_action, next_action_due,
last_activity_at, participants:[{party_id,name,role,state,last_at}],
waiting_on_us, waiting_on, overdue, needs_attention, open_tasks, due_tasks
```

详情保留案子所有非金额内部字段，包括创建/更新/成交/关闭时间；不输出 value_fen。participants 增加 id/deal_id/note/created_at/conversations；会话包含 `{id,key,party_id,note,channel_label,title,last_role,last_at}`。顶层 conversations 为全部案内关联。items 含 target_price 两位小数字符串或 null，不输出 target_price_fen。tasks 是按 11 个 stage key 分组的字典（空阶段为空数组），每项包含 due/status/auto/template_key/done_at 等字段。events 最近 100 条按 at、id 倒序。另含 unlinked_participants、stages:[{key,label,index,current,done}]。

字段范围：标题/下一步≤500 字，产品项名≤200，notes/参与方和产品项 note≤2000，lost_reason≤200，单位名/短名≤500；due 和 next_action_due 接受 YYYY-MM-DD 或带 Z/±HH:MM 时区的 ISO 时间，时间规范到 UTC。纯日期按 UTC 当天 00:00 到期。qty 为 1–1000000 整数；id 为正 SQLite 64 位整数；currency 为三位大写字母，金额默认 USD。value/target_price 输入沿用档案 Decimal 校验，0–9999999999.99，最多两位小数；数据库存整数分，输出固定两位小数字符串，不做汇率或单位换算。

## 进程内共享收件箱

`channels.shared_inbox(...)` 由 RLock 保护缓存查找和构造，key 包含实际来源路径、WhatsApp 配置路径、群角色集合、group_gid 和 fetch/clock/stat_fn 的对象 id。缓存条目持有注入依赖，避免对象销毁后 id 重用误命中。来源路径规整为绝对路径；WhatsApp 配置只做绝对路径规整，保留符号链接供适配器既有安全校验拒绝。

`build_inbox` 现为 `shared_inbox` 的兼容别名，因此四个旧 API builder 无需改调用和业务逻辑即共用缓存；新案子 builder 也直接使用它。相同配置只构造一次；不同路径或依赖不共用；显式 inbox= 假对象仍优先。构造不触发读取，默认来源的实际读取仍由请求发生。缓存只在当前进程内，不跨 worker；不同配置的条目保留至进程结束，没有自动淘汰，适合少量稳定的应用配置。

## 局限与验收边界

- 没有前端、应用主体挂载、部署或浏览器验收。本次严格未改 `web/`（包括需求账本）、`/opt/imgw`、生产文件，以及 kb/media/outbox 业务逻辑。
- 此文的案子地基不承担报价计算、银行支付对账或渠道发送；收款确认与履约记录见 [履约说明](fulfil.md)。人工确认只是内部登记，仍需操作者核对实际到账；自动测试通过不代表真实款项已到账。
- 列表和汇总从数据库快照在 Python 内投影筛选；大规模案子/时间线尚需 SQL 聚合与分页优化。读取会话失败会降低判断覆盖，state=none 不等于没有真实待办。
- 身份转移会同步已有案内会话链接，旧参与方与显式客户字段保留；运营人员仍需核对真实业务归属。不会根据聊天名称自动推断单位身份或关系。
- 付款后的模板不自动生成、未完成历史待办不自动关闭，属于本轮明确规则取舍。
- 所有自动测试只使用虚构数据。下面的真实数据演练只在临时副本上写入，演练临时目录退出后删除；未引用聊天正文、单位名、会话 key 或真实价格。

## 真实库副本只读演练

将真实 archive、阿里入站库、微信库及非空 WAL 仅通过文件读取复制到 0700 临时目录；复制前后检查源文件组指纹一致才采用快照。原文件不以可写 SQLite 连接打开，迁移和演练案子只写副本。对真实收件箱内容调用本仓库 `build_inbox` 和相同 lookup；WhatsApp HTTP 及 socket 连接禁用，未读取 WhatsApp 数据。参与方类型是临时案子的演练标记，只核验消息方向判断。

迁移版本 `[1] → [1,2]`，耗时 **0.009407 秒**。逐表对比旧字段及 id 全部相同；仅迁移记录、审计各新增一条。第二次迁移无变化，foreign_key_check **0** 项。

| 表 | 迁移前 | 迁移后 | 旧行字段/id 保留 |
| --- | ---: | ---: | ---: |
| parties | 17 | 17 | 1 |
| products | 138 | 138 | 1 |
| product_sources | 460 | 460 | 1 |
| offers | 39 | 39 | 1 |
| freight_quotes | 0 | 0 | 1 |
| terms | 0 | 0 | 1 |
| product_merges | 0 | 0 | 1 |
| audit | 784 | 785 | 1 |
| schema_migrations | 1 | 2 | 1 |

新增 7 表在迁移后均为 0 行。真实副本的 terms/freight_quotes 为空，非空引用完整性由含供应商、货代、产品、报价、条款、运价的虚构 v1 夹具覆盖。

真实会话快照：alibaba **476**，wechat **22**，读取 **1.408481 秒**。副本演练案子 **1**、参与方 **2**、关联会话 **2**：state=`[us,them]`；waiting_on_us=`1`；waiting_on=`[supplier]`；overdue=`[]`；needs_attention=`1`；unlinked_participants=`0`；open_tasks=`1`；与最后消息角色一致=`1`；WhatsApp 网络请求=`0`。

整轮演练前后原档案库和阿里源文件组指纹一致；微信源文件组元数据期间发生变化。使用的是复制时通过前后指纹一致检查的快照，不声称微信实时源在整轮演练期间未变化。演练代码对原文件只执行读取/复制，没有执行 SQLite 写入或迁移。

## 双环境测试末尾原文

以下是本轮实际运行结果，系统 Python 缺失依赖的跳过原样保留。最终案子测试包括迁移逐行保留/回滚、共享缓存并发和四个 builder 复用、阶段付款规则与模板、等待阈值、事务、身份/跨案关联、金额、审计、API 认证/recheck/表单/分页。旧套件未改测试断言。

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/deals -p 'test_*.py'
----------------------------------------------------------------------
Ran 32 tests in 3.283s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/archive -p 'test_*.py'
----------------------------------------------------------------------
Ran 38 tests in 4.314s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/kb -p 'test_*.py'
----------------------------------------------------------------------
Ran 35 tests in 10.041s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inbox_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 37 tests in 3.089s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/channels -p 'test_*.py'
----------------------------------------------------------------------
Ran 45 tests in 3.215s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/outbox -p 'test_*.py'
----------------------------------------------------------------------
Ran 42 tests in 5.598s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/media -p 'test_*.py'
----------------------------------------------------------------------
Ran 25 tests in 0.361s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/packing_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 24 tests in 0.462s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inspiration -p 'test_*.py'
----------------------------------------------------------------------
Ran 56 tests in 3.006s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/web_app -p 'test_*.py'
----------------------------------------------------------------------
Ran 6 tests in 0.062s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/supplier_data -p 'test_*.py'
----------------------------------------------------------------------
Ran 110 tests in 5.525s

OK
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/deals -p 'test_*.py'
----------------------------------------------------------------------
Ran 32 tests in 2.652s

OK (skipped=7)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/archive -p 'test_*.py'
----------------------------------------------------------------------
Ran 38 tests in 1.717s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/kb -p 'test_*.py'
----------------------------------------------------------------------
Ran 35 tests in 6.784s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/inbox_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 37 tests in 0.231s

OK (skipped=19)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/channels -p 'test_*.py'
----------------------------------------------------------------------
Ran 45 tests in 1.717s

OK (skipped=16)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/outbox -p 'test_*.py'
----------------------------------------------------------------------
Ran 42 tests in 4.182s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/media -p 'test_*.py'
----------------------------------------------------------------------
Ran 25 tests in 0.097s

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
Ran 56 tests in 2.103s

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
Ran 110 tests in 5.110s

OK (skipped=6)
```

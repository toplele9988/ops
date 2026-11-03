# E1 收款确认与履约后端

开发候选，未部署。**成交是已人工确认的客户付款。** 接受报价、登记待核对款项和退款都不算成交。只记录、提醒、汇总或返回可复制文本，没有渠道发送、物流查询、自动确认收款、自动建返单或定时推进阶段的路径。收款确认和对外发货信息仍由人操作。测试全部使用虚构临时数据库、假时钟和注入的假 AI，不代表真实数据可用；没有修改生产文件，没有 commit/push。

## 迁移与事务

`archive/migration_v8.py` 是既有基线，添加四张表和索引；本轮 R2-A 没有新增表或升级档案库版本。既有 `Store(target_version=8)` 在原有单次 `BEGIN IMMEDIATE` 事务内补齐历史迁移、检查外键、记录 v8；任一步失败整体回滚。旧 target_version=2/3/4/6/7 入口接受 v8，不降级、不丢失旧数据；保留可选 v5 标记，不创造 v5 结构。

`Fulfil(path, store=..., deals=..., clock=..., today=...)` 使用共享档案库，同事务复用 `Deals._write` 和 `Deals._event`。内部 Deals 使用空 lookup，不构造渠道或 AI 客户端。GET 不迁移文件：旧库新表缺失时返回空记录/虚拟待办节点；未知案子 404。首次业务写入才安装 v8。所有业务插入、更新、处理记录、时间线复用字段名审计，含操作人、时间、动作；audit.detail 不存备注、凭证、客户信息或金额值。

## 收款

`deal_payments` 字段：`id/deal_id/kind/amount_fen/currency/received_on/method/quote_round_id/note/evidence/currency_note/status/qualifies/created_at/created_by/updated_at/confirmed_at/confirmed_by/voided_at/voided_by/void_note`。

| 字段 | 约定 |
| --- | --- |
| kind | deposit 定金、balance 尾款、full 全款、refund 退款 |
| amount_fen | 正整数分，1–999999999999；表单必须是十进制整数文本，不接受小数、指数、正负号、空格、浮点或布尔 |
| currency | 三位大写字母；不换汇、不把不同币种相加 |
| received_on | YYYY-MM-DD，人工填写的收到/退款日期 |
| method | wire 电汇、paypal PayPal、lc 信用证、other 其他 |
| quote_round_id | 省略默认本案最新 accepted 轮；显式空串表示不关联；不得关联其他案子的轮 |
| note / evidence | 内部备注 ≤2000 字；凭证说明 ≤1000 字，仅文本，不存文件 |
| currency_note | ≤1000 字；关联报价轮币种不一致时必须在确认前说明，可在 confirm 时补充 |

创建固定 `pending`。只有 `POST /payments/{pid}/confirm` 且 `confirm=1` 才变为 `confirmed`；再次核验金额/关联币种，写确认人、确认时间及“收到客户付款”时间线（退款为“确认退款”）。重复确认幂等，不改确认人/时间、不重复事件。

普通 create/update 不能让新案或付款前案子进入 paid 及以后阶段。首次 confirmed deposit/full，或确认时人工选择 `qualifies=1`（“这笔达到成交条件”），才在同一收款确认事务内进入 paid，写入 won/won_at 和原状态事件。首次定金/全款即使传 `qualifies=0` 也算成交，页面说明此规则且不提供无效的取消勾选；balance 默认不触发成交，可由人显式选择达标；refund 不允许 qualifies。已在 paid 及以后不重复推进或刷新 won_at；历史已成交但缺收款记录的案子保留原数据，需人工核对，不批量回退。老板曾人工回退阶段且仍有有效的已确认达标旧款时，普通后续入账不推翻回退，新的确认请求显式 qualifies=1 才重新认定成交；若唯一旧达标款已作废，新定金/全款重新视作首笔，qualifies=0 也会成交。已确认款项不能通过再次 confirm 补改 qualifies，也不能靠普通修改阶段补认成交，应先核对现有收款记录。

`pending/confirmed → void` 可人工作废，重复作废幂等，保留确认历史和独立作废人/时间/说明；void 不能再确认。refund 的 amount_fen 存正数，读接口给负的 `signed_amount_fen`，仅 confirmed 退款参与负向汇总。退款/作废均不回退阶段。

GET 返回 items 与分币种 summary：`received_fen`=confirmed 收款减 confirmed 退款；`receivable_fen`=最新 accepted 一轮销售单价×数量之和；`unreceived_fen`=应收−已收，可负数（超收）。不叠加多个报价轮；报价售价/数量未确认、无 accepted 轮或该币种不匹配最新 accepted 轮时，应收/未收为 null。void/pending 不计已收，不推测跨币种抵扣。

## 履约

`fulfilment_nodes` 每案每种节点唯一。公共字段：`id/deal_id/node/status/planned_on/completed_on/responsible/party_id/note/created_at/updated_at`。按需落库；读接口始终投影八节点，未记录者 `id=null,status=todo`，GET 不落库。

| node | 中文 | 专用字段 |
| --- | --- | --- |
| po | 下单给供应商 | 无 |
| supplier_confirmation | 供应商确认交期 | promised_on |
| production | 生产中 | 无 |
| inspection | 验货 | inspection_result=pass/fail/needs_fix；inspector 验货人/公司 |
| booking | 出货订舱 | carrier 船司/航班；tracking_number 提单/运单；etd、eta、departed_on |
| transit | 物流在途 | 无 |
| customs | 到港/清关 | 无 |
| signoff | 客户签收 | signed_on |

状态为 todo/doing/done/blocked，可人工调整。responsible=us/supplier/forwarder/vendor，party_id 可空，填写时匹配负责方类型。验货计划/完成用公共日期。专用字段只允许写对应节点。done 未填完成日期时采用当日；signoff done 未填 signed_on 同样采用当日。完成验货须有结果，供应商确认 done 须有承诺交期；实际完成/离港/签收日期不能晚于当日。重新打开 done 默认清完成日期，签收节点同时清签收日期。日期均 YYYY-MM-DD，默认业务日 Asia/Shanghai，可注入 today。

仅当前 paid 或后续阶段可写节点。真实变化写中文时间线，同值重复更新不重复事件。建议：生产 done→inspection；验货 done 且 pass→shipped；在途 doing/done→shipped；签收 done→delivered。建议字段固定为 `{stage,reason}`，页面展示原因并由人确认修改阶段。验货通过本身不代表已发货，必须核对实际交货；保存节点不自动联动节点或改阶段。确认定金后可人工依次进入 inspection/shipped/delivered/aftersales，保留 won 和首次 won_at。真实阶段键为 delivered，没有 signed 阶段。

案子进度返回 items/done/total/suggestions/next_due/overdue；看板列出全部 paid 及后续案子，先延期，再最早日期，再无日期，并按案子 id 稳定排序。供应商确认 done 后，其承诺日期在未发货前仍参与；订舱 done 后，ETA 在未签收前仍参与。物流号只记录，不查询、不发送。

## 问题与反馈

`deal_issues`：`id/deal_id/kind/quote_line_id/product_id/responsible/party_id/severity/status/description/resolution/impact_kind/impact_fen/currency/created_at/created_by/updated_at/resolved_at/closed_at`。

- kind：quality 质量、shortage 少发错发、damage 破损、delay 延误、documents 资料不符、feedback 客户意见建议、other 其他。
- severity：low/medium/high/critical；responsible：supplier/forwarder/vendor/us/customer/unknown。party_id 可空，填写时匹配责任类型。
- quote_line_id/product_id 可空；行必须属于本案，产品须已在本案产品清单或报价中；给行时采用该行 product_id，禁止跨案关联。
- 创建固定 open；人工可转 waiting_party/resolved。resolved/closed 须有 resolution，关闭必须先 resolved；可重新打开并清当前解决/关闭时间，历史仍保留。
- impact_kind=compensation/discount/refund、正整数分 impact_fen、currency 须一起提供或清空；只记录金额影响，不自动生成收款/退款账目。
- description/note/resolution ≤2000 字。`deal_issue_notes(id,issue_id,note,at,who)` 追加式保存处理记录；独立 note API 与状态/结论变化都追加，不覆盖历史，并写时间线和审计。

草稿 GET/POST 只返回 `{text,target,source:'rules',copy_only:true,needs_manual_details:true}`；target=supplier/forwarder，不保存、不发送、不调用 AI。为保证不包含成本利润与其他客户信息，只插入受控类型和严重程度标签，产品型号、现象、数量和日期留人工补充位置。任意内部描述/产品名称可能夹带敏感信息，关键词过滤无法证明安全，因此不直接插入原文。这是本批明确取舍；未提供自由文本自动润色。

`issues/summary?start=YYYY-MM-DD&end=YYYY-MM-DD` 按创建的 Asia/Shanghai 业务日期闭区间筛选。返回 total/unresolved、products、parties；unresolved=open+waiting_party。products 按 product_id 分组（null 为未关联），parties 只汇总 supplier/forwarder，保留 responsible 与 party_id。不输出客户、成本或问题正文。

## 返单

`reorder/leads?days=30,60,90` 支持 1–12 个不重复的 1–3650 正整数，默认 30/60/90。签收依据为 signoff done 的 signed_on/completed_on；旧案使用 delivered 事件日期（Asia/Shanghai）；直接创建于 delivered 的案子可用 stage_changed_at。aftersales/reorder 没有签收节点或历史签收事件时不猜日期。

同一 customer_party_id 没有更晚案子才列线索，created_at 相同用 id 判断先后；后续任何状态新案子都抑制旧线索。达到阈值后持续列出，reached_days 给跨过的阈值。返回上次 accepted 轮产品/数量/销售价 sell_unit_minor/币种和人话建议；没有 accepted 轮仅给产品清单，售价 null，不把目标价当成交价。基础预览增加 `feedback`（仅本案问题类型、状态、描述、结论、产品/责任单位 id，不带处理备注或金额影响）和 `suppliers`（id/name/source_label）。供应商依据本案供应商参与方及产品档案当前关联，`source_label` 明确提示核对实际供货方，不能当作历史供货快照；不返回报价内部成本。

人工 POST reorder 同事务复用 deals.create，实现客户、产品名称/product_id/qty 复制，标题加“返单”，固定 inquiry/open。优先 deal_items，没有时采用 accepted 轮产品。不会复制 target_price、报价轮、成本、金额、备注、收款、履约节点、会话或供应方身份；客户参与方和询盘模板沿用 deals。每次显式 POST 创建一份草稿，之后旧返单线索消失；没有自动建案。

## 巡检与每日简报

复用 role_alerts 唯一键、忽略、自动解决、运行存储。只写内部提醒，不改业务阶段/节点/付款。旧库缺 v8 表时按空记录兼容。新履约规则独立于原 open-only 循环，确保 won 案参与。

| 提醒 | 触发 | 解除 |
| --- | --- | --- |
| delivery_due / delivery_overdue | 已成交未发货，承诺交期在 delivery_due_days=3 天内（含当日）/已过 | 交期调整或人工记录发货/离港/在途 |
| inspection_failed | inspection_result=fail/needs_fix | 人工复验改 pass 或清除错误记录 |
| eta_overdue | ETA 早于当日且未签收 | 修正 ETA 或人工记录签收/签收阶段 |
| issue_open_overdue | 已签收，open 问题创建后严格超过 issue_open_days=7 天 | 转 waiting_party/resolved/closed 或不再满足阈值 |
| payment_pending | pending 创建后严格超过 pending_payment_days=2 天 | 人工确认/作废 |

三个新阈值通过 `roles.common.Config` 配置，沿用原阈值校验；ETA 到达当日不算逾期。签收判定同时兼容已记录 signoff 与现有 delivered 及后续阶段。

简报继续按 won_at 选择今日/本周/本月新成交案子；每案有任一 confirmed 款时取当前 confirmed 净额（退款扣减），source=payment；否则最新 accepted 销售额，source=quote。**这是期间新成交案子的当前已收金额，不是期间现金流或订单总额。** void 不计，多币种 amounts 分开；单币种保留原 currency/amount，多币种这两个单值为 null。period.amount_sources 标明两种来源案子数，缺金额仍记 amount_pending。

新增 `fulfilment={in_transit,arriving_this_week,open_issues,pending_payments}`。在途为未签收的发货/离港/在途案子数；本周到货为本周 ETA 且未签收案子数；open_issues 仅 open（不含 waiting_party）；pending_payments 为待核对款项数，文案同步展示。

原无 confirmed 付款的 fixture 继续走 quote 兜底，旧金额断言没有修改；新测试覆盖 payment/quote、多币种、退款、void。原演练路由断言 42→59；渠道隔离测试仅补新模块 mock。

## HTTP 契约

`v3_fulfil_api.build(app_module, archive_path=ARCHIVE_PATH, *, service=None, today=None, clock=None)` 与报价 builder 风格一致；演练服务按原动态列表挂载，未修改生产应用挂载。路径均以 `/api/v1/` 开头。

| 路径 | 方法与参数 |
| --- | --- |
| deals/{id}/payments | GET：items/summary；POST：kind/amount_fen/currency/received_on/method 必填，quote_round_id/note/evidence/currency_note 可选 |
| payments/{pid}/confirm | POST：confirm=1 必填；qualifies=0/1、currency_note 可选 |
| payments/{pid}/void | POST：note 可选 |
| deals/{id}/fulfilment | GET：节点与建议 |
| deals/{id}/fulfilment/{node}/update | POST：node 为稳定键；只接收公共字段与该节点专用字段 |
| fulfilment/{node}/update | POST：node 为已落库的数字 id；同上，兼容无案子前缀入口 |
| fulfilment/board | GET：全部履约案子；不接收查询参数 |
| deals/{id}/issues | GET：items 含 notes；POST：kind/description 必填，关联/责任/严重程度/金额影响可选，不能指定初始状态 |
| issues/{iid}/update | POST：问题字段白名单，可另给 status/resolution，合并完整记录校验 |
| issues/{iid}/note | POST：note 必填 |
| issues/{iid}/draft | GET/POST：target 可选；均纯读，不写库 |
| issues/summary | GET：start/end 可选 |
| reorder/leads | GET：days 逗号列表可选 |
| deals/{id}/reorder | POST：空业务表单，返回新草稿 id/source_deal_id |

鉴权/CSRF 沿用宿主中间件，不可绕过宿主挂载。POST 读表单前 recheck、表单解析后紧邻业务调用再次 recheck；GET（含错误）及 POST 纯读草稿返回前复核。URL 编码表单 ≤8 KiB/40 字段，严格 UTF-8，拒绝重复/未知字段和错误媒体类型；csrf 经宿主校验后排除。线程池 I/O，no-store/nosniff；400 输入错误、404 未知 id/节点、409 状态/关联冲突、503 固定中文错误，不透传路径/异常。

## R2-A 页面与读取恢复

履约看板的返单线索可展开预览上次接受售价、反馈和供应商来源，再人工点击“创建返单草稿”。不带入旧价、成本、收款、节点、会话和供应方身份。复制结果未知时先读取线索与同客户案子，两项独立核对；读取失败不视作没有新草稿，不自动再次创建。

收款、履约、售后问题、说明草稿、看板、返单线索均显式显示读取失败及重读入口；付款读取失败不显示再次登记引导。未知 POST 先读回，保留表单或已建案入口，由人核对后继续。首次确认定金/全款的成交提示、尾款达标选项及币种差异说明已接入页面。节点可从本案参与方选具体负责单位，保存节点不自动改阶段；建议原因和阶段确认按钮可见。多供应商拆分采购与 WG-12 扩展售后责任/提醒不属本次基础入口。

R2-A 全部仅在虚构数据、临时 SQLite、SSR 与假 fetch 上验证，未做真实浏览器交互和生产验收。最新结果及基线/部署/回滚见 [R2-A 交付记录](r2a-delivery.md)。

## E1 初版验证（历史记录）

以 imgw 身份独立运行各目录，全部虚构数据，无联网/真实 AI。测试额外经 `/tmp/e1-payments-check/sitecustomize.py` 审计闸门禁止 socket/域名查询、真实 AI gateway、非临时 SQLite 和生产写入。fulfil fixture 也自带断网/真实 AI 禁用；outbox、BridgeClient、local_post 等 spy 验证零发送。关闭 bytecode 和 pytest 缓存。

| 套件 | 主测试通过 | 额外 subtests |
| --- | ---: | ---: |
| fulfil | 167 | 0 |
| deals | 32 | 38 |
| quotes | 28 | 189 |
| roles | 238 | 0 |
| archive | 38 | 46 |
| audit | 191 | 0 |
| extract | 230 | 0 |
| inbox_api | 37 | 99 |
| channels | 190 | 14 |
| 合计 | **1151** | **386** |

全部无失败、无跳过。覆盖旧数据迁移/幂等/整体回滚、付款确认与 won/won_at、退款/void/多币种/严格金额、节点事件与不改阶段、问题全流程与无敏感草稿、日期汇总、返单价格隔离、巡检自动解决、简报来源、真实 HTTP 闭环、鉴权/CSRF/双重 recheck/表单限制/404。新增跨业务日、实际日期、复制失败整体回滚等反例。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest tests/fulfil -q -p no:cacheprovider
for suite in deals quotes roles archive audit extract inbox_api channels; do
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/$suite" /opt/imgw/venv/bin/python -m pytest "tests/$suite" -q -p no:cacheprovider || exit 1
done
```

## E1 初版文件与边界（历史记录）

新增：`src/console/archive/migration_v8.py`；`src/console/fulfil/{__init__,common,payments,nodes,issues,reorder,service}.py`；`src/console/v3_fulfil_api.py`；`tests/fulfil/{conftest,test_migration,test_payments,test_service,test_roles,test_api}.py`；本文件。

修改：`src/console/archive/store.py`；`src/console/roles/{common,inputs,patrol,brief,service}.py`；`scripts/rehearsal_server.py`；`tests/roles/test_roles_safety_cli.py`；`tests/channels/test_whatsapp_rehearsal.py`；`docs/roles.md`。任务开始前已有大量未提交文件；这里只列本次触及文件。

E1 初版未做前端，R2-A 已补本任务页面入口与读取恢复。仍未做：生产迁移/部署、真实业务验收、联网浏览器 e2e、收款凭证上传、PI/装箱单、第三方物流、对外发送、自动阶段推进、定时任务、自动返单、AI 润色。看板/汇总使用当前本地库投影，未作大规模分页性能承诺。收款更正采用作废后重建，不覆盖已确认原记录。完整自由文本仍仅供内部查看，草稿需人工补充对外事实。

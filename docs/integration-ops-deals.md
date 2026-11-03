# deals：W3 / W6 / W7 集成记录（开发候选，未部署）

本次需求与状态：案子看板操作、三种建案入口、参与方与会话、跨渠道经过、待办和备注、有效成本与运价核价、报价/PI 演练草稿、还价与回应、人工确认收款成交及备货待办、履约节点及超期。以上主流程已完成本地虚构数据的桌面/手机点击验收；不代表真实业务接口或生产已验收。任务入口已先记录于本文件；共享需求账本未越界修改。

工作区是本次 session 的隔离源码快照；没有可用 Git 元数据，无法读取当前分支/提交。开工源码 SHA256 保存在 `src/console/deals/patches/baseline.sha256`，不是生产哈希。没有 commit、push、部署、生产库访问、真实渠道调用。没有修改网络分流、发品或共享文件，没有运行 `tsc -b`。

## 文件与实现

修改：

- `web/src/pages/Deals.tsx`：按阶段列展示；每卡客户、金额或金额待定、等待方、下一步；卡片按钮推进售前阶段；列头新建案子。付款阶段只能去案子人工确认收款。
- `web/src/pages/DealPage.tsx`、`web/src/deals.ts`：案内关联各方会话、跨渠道经过、待办勾选/新增、备注、报价入口、演练草稿箱、丢单原因与后果确认。换用本模块的收款/履约操作组件。
- `web/src/pages/QuotePage.tsx`：核价检查、产品与数量、成本及运价选择沿用真实契约；新增报价与 PI 草稿保存、成本问题的处理入口；保存期间保护单价输入，旧回执不会清掉较新的编辑；仍保留原报价文本、审阅、人工记录已发出功能。
- `web/src/pages/FulfilBoard.tsx`：每个成交案子直接展开节点按钮和日期，超期标红；可在看板上编辑并看到完成状态。
- `src/console/deals/{service,validation,rules}.py`：一次事务建立客户/案子/产品/目的国/会话/默认待办；从已有客户会话建案复用已确认的身份；修复成交后不生成备货待办的旧规则；本案供应方的询价/采购草稿；持久化幂等回执。
- `src/console/quotes/service.py`：新轮带入案子目的国；报价/PI 草稿生成前重新核验成本有效期、倒挂和条款；报价条款与审阅也记入案子经过；最新一轮有效草稿把销售总额回填进行中案子的看板金额；写入幂等回执。
- `src/console/fulfil/service.py`：收款登记/确认、节点保存等写入与回执同一事务。
- `src/console/v3_{deals,quotes,fulfil}_api.py`：新增草稿路由、接收幂等键；报价在读取表单后再次复核权限。保留宿主鉴权与 CSRF。

新增：

- `web/src/pages/deals/{NewDeal,Conversations,Fulfilment}.tsx`、`actions.ts`、`deals.css`。
- `src/console/deals/operations.py`；`src/console/deals/patches/{README.md,baseline.sha256,production-baseline.sha256,candidate.sha256,production-source.patch}`。
- `tests/ops_deals/test_operations.py`；`web/e2e/ops-deals.mjs`、`web/e2e/fixtures.deals.mjs`。
- 本文件与 `artifacts/deals/` 下的测试日志、结果 JSON、八张截图。

## 接口契约

以下路径统一以 `/api/v1` 开头。POST 使用 `application/x-www-form-urlencoded` 和现有 `X-CSRF-Token`；不能绕过宿主认证。失败为 `{error: 中文原因}`，400 参数不合法、404 未找到、409 状态/内容冲突、503 暂不可用；无权限为 403。

| 方法、路径 | 入参 | 成功出参/副作用 |
| --- | --- | --- |
| POST `/deals`（扩展） | `customer_name` 或 `customer_party_id`；新增 `product_name`,`qty`,`destination_country` 必须一起给；可带已有 `title`,`conversation_key`,`stage`。新建仍不能跳过收款直达已成交 | `{ok:true,id}`；客户、案子、产品、会话和阶段待办同事务。已归属客户的会话复用其客户身份 |
| POST `/deals/{id}/drafts`（新增） | `kind=supplier_order/price_request`，本案供应方 `party_id`，可带属于这位参与方的已关联 `conversation_key`；采购草稿要求已成交 | `{ok:true,id,state:"draft",mode:"dry-run",text}`；保存草稿及时间线，不外发 |
| POST `/quotes/{id}/drafts`（新增） | `kind=quote/pi`，`lang=zh/en`（默认 zh，PI 生成中文预览） | 同上；过期/缺有效期成本、倒挂、未确认条款会拒绝，PI 另要求开票名称/地址 |
| GET `/deals/{id}`（扩展） | 原编号 | 原详情增加 `destination_country`、`drafts[]`；每份草稿含 `id,deal_id,quote_id,kind,text,conversation_key,state,mode,created_by,created_at` |

本次页面使用的原有写入口保留：`POST /deals/{id}/update`（`stage` 或备注/下一步、或 `status=lost&lost_reason=...`）、`/participants`（`party_id,role`）、`/conversations`（`conversation_key,party_id`）、`/tasks`（`title`）、`/deal-tasks/{id}/status`（`status=done`）、`/events`（`note`）；`POST /deals/{id}/quotes` 新建报价；`POST /quotes/{id}/lines/{lid}/update`、`/update`、`/terms`、`/freight-options/choose`、`/negotiations` 沿用既有源码字段；`POST /deals/{id}/payments`（`kind,amount_fen,currency,received_on,method,note,currency_note`）、`POST /payments/{pid}/confirm`（`confirm=1,qualifies,currency_note`）、`POST /deals/{id}/fulfilment/{node}/update`（状态、计划/完成日、负责方及各节点专属字段）。普通写返回 `{ok:true,id}`，运价选择保留原有附加返回字段。

上述案子/报价/收款/履约写入口支持可选 `operation_key`（8–100 位字母、数字、连字符）。同一操作者、动作、记录、编号及同样内容返回原结果；同编号不同内容返回 409；失败回滚时不保存回执。前端本次操作均带键，未知结果后相同内容重试保留该键。原货代选择的 `request_key` 仍保留，两个字段用途独立。既有母语模型生成沿用原逻辑，不在本次幂等及浏览器验收范围内。

新表 `deal_operation_context`、`deal_operation_receipts`、`deal_draft_outbox` 在授权写事务中按需建立，GET 不建表。不改既有 archive 迁移版本。草稿箱在案子详情展示，数据库强制 `state='draft'`、`mode='dry-run'`。现有渠道 outbox 的状态没有草稿枚举，所以本次没有往其 `sending` 队列塞记录；也没有调用它的发送器。对接统一草稿箱及发送审核是后续集成事项。

## 生产补丁、挂载与回滚

交付位置：`src/console/deals/patches/`。`production-source.patch` 含后端业务和 API builder 内的新增路由注册；沿用三个已存在的 builder 挂载，不新增父路由模块或端口。补丁已经在临时基线副本完成正向预检/应用、反向预检/回滚，并逐文件核对回到原基线；日志 `artifacts/deals/patch-check.log`。

按 `README.md` 操作，先核对候选基线 SHA；不匹配则重新对齐补丁，不能直接覆盖。实际生产父路由源码 `console/v3.py` 不在本快照，本轮未读取/修改它；总指挥须核对三个 builder 已挂载，路由数量应为案子 18、报价 23、履约 17，并验证新增两个 POST 路径。没有完成真实生产挂载验收。

回滚前停止候选写入；恢复前端备份构建，后端反向应用补丁或恢复代码备份。保留扩展表和新增业务记录；旧代码忽略扩展表，不删除收款、待办、时间线或草稿。不要用旧库覆盖新到账记录。完整说明见补丁目录 README。未执行任何生产部署脚本；没有为了冒烟调用会写生产目录的 `scripts/e2e_smoke.sh`。

## 已执行的验收

所有浏览器请求在浏览器内拦截；静态文件由原 `local-server.mjs` 启动的 loopback 服务提供，严格 CSP，无后端代理，未登记 API 报错；不接受外部 BASE。POST 夹具确实改内存中的案子、待办、报价、收款、节点、草稿及回执。SQLite 单元测试只用临时虚构库并禁止 socket 连接。

| 命令（根目录，注明 web/ 者除外） | 结果 | 日志 |
| --- | --- | --- |
| `cd web && npm run build` | 通过 | `artifacts/deals/build.log` |
| `cd web && npx tsc --noEmit` | 通过，正常无输出 | `artifacts/deals/typescript.log` |
| `cd web && PLAYWRIGHT_BROWSERS_PATH=/tmp/ops-deals-browsers node e2e/ops-deals.mjs` | 1440×1000、400×860 各一轮通过 | `artifacts/deals/playwright.log`、`playwright-results.json` |
| `PYTHONPATH=src /tmp/ops-deals-venv/bin/python -m pytest -q tests/ops_deals` | 8 通过 | `artifacts/deals/python-ops.log` |
| 同一 Python：`-m unittest discover -s tests/quotes -p 'test_service.py'` | 17 通过 | `python-quotes-service.log` |
| 同一 Python：`-m unittest discover -s tests/quotes -p 'test_calc.py'` | 8 通过 | `python-quotes-calc.log` |
| 同一 Python：`-m pytest -q tests/deals/test_service.py tests/deals/test_rules.py tests/deals/test_migration.py -k 'not templates_reentry_does_not_reopen_and_terminal_stops_new_auto and not stage_order_templates_and_idempotence'` | 22 通过，45 子场景通过，2 个旧要求测试排除（见下） | `python-deals-core.log` |
| 同一 Python：`-m pytest -q tests/fulfil/test_migration.py tests/fulfil/test_payments.py tests/fulfil/test_service.py tests/fulfil/test_api.py -k 'not rehearsal_automounts and not real_http_payment_node_issue_and_reorder_loop and not payment_paths_never_enqueue_or_send_channels and not whole_flow_never_sends_and_audits_only_field_names'` | 143 通过，4 个依赖缺失场景排除，1 个 Starlette 弃用提醒 | `python-fulfil-core.log` |

表中省略目录的日志也都在 `artifacts/deals/`。Python 测试依赖装在 `/tmp/ops-deals-venv`；浏览器在 `/tmp/ops-deals-browsers`；npm 缓存在 `/tmp/ops-deals-npm-cache`。依赖安装日志也已保存。

Playwright 真点的流程：手动建案 → 看板推进 → 添加供应商 → 分别关联阿里客户与微信供应商会话、汇入经过 → 加待办并完成 → 记备注 → 建报价 → 过期/缺有效期产品拦截 → 有效成本 → 延迟价格保存时保护输入、回执后倒挂拦截并改价 → 确认条款、FOB 核算 → DDP 选有效运价并核对成本 440 元 → 客户还价和我方回应 → 报价/PI 演练草稿 → 记收款但状态保持进行中 → 打开二次确认、取消，再人工确认到账 → 成交及三条备货待办 → 采购演练草稿 → 履约看板安排逾期日并看到红色提醒 → 八节点逐一保存完成及日期 → 从客户/会话建案 → 丢单原因必填与确认 → 同键重放不多建案子。

每种宽度实际记录 38 次写请求、3 份 dry-run 草稿、8 个完成节点，所有测试写请求均有幂等键；没有外部请求，没有发送接口调用。截图 `board-1440/400.png`、`deal-1440/400.png`、`quote-1440/400.png`、`fulfil-1440/400.png`；结果总表 `verification.json`。新后端测试另核对并发创建只落一笔、失败不留回执、已有客户身份不被昵称覆盖，以及撤权后不能写入。

## 全量旧测试的真实结果与未验证项

不能声称全量 Python 回归通过：

- `unittest discover -s tests/deals -p 'test_*.py'`：57 个测试，8 处断言失败、6 个加载/依赖错误。8 处是旧路由数 17 与新增 18 不同，以及旧测试断言已成交永远不产生默认待办（6 个阶段子场景和一个服务测试），与 W3/W6 明确要求冲突。新测试已按本轮要求验证生成三条备货待办。6 个错误追溯到本公开快照缺少 `console.media`。日志 `python-deals.log`。
- `unittest discover -s tests/quotes -p 'test_*.py'`：运行 31 个条目，2 个导入错误，追溯到缺少 `console.media`；相关纯服务/核价测试已单独通过。`v3_quotes_api` 的旧测试还硬编码 22 条路由，集成新增路由后应改为 23，不能把未加载的 API 测试称为已通过。日志 `python-quotes.log`。
- `pytest -q tests/fulfil` 在加载 `test_roles.py` 时因 `console.media` 缺失中止。排除该文件后 143 通过、4 失败；4 项依赖缺失的演练/真实 HTTP 装配、outbox 模块导入/打桩场景没有验证。日志 `python-fulfil.log`、`python-fulfil-isolated.log`。随后只运行不依赖缺失模块的 143 项，结果通过，见上表。

未验证/未做：生产部署、真实渠道会话与媒体/附件、真实报价来源及金额、真实银行到账、统一渠道 outbox 草稿展示/发送、报价母语生成与模型、原有报价“标记已发出/客户接受/拒绝”、返单与售后操作。本轮履约节点完成有实点，节点建议的后续阶段确认没有在 Playwright 点击，原相关隔离单测有覆盖。没有实现拖动，使用已验收的按钮推进。

## 总指挥需整合的共享改动（本轮未直接修改）

1. `web/src/data/requirements.json` 的 `groups`：新增 deals 的 W3/W6/W7 条目，使用该文件实际字段 `id,ask,status,now,accept,next`。建议记 `status:"partial"`，`now` 写明“开发候选与隔离实点通过”，`next` 保留真实接口/父路由挂载/依赖补全和发布验收，避免宣称生产完成。需求内容见本文件首段与验收流程。
2. `web/src/pages/Inbox.tsx` 的 `DealLink`（原“新建案子”按钮）：可改为 `Link` 到 `/deals?new=1&conversation=${encodeURIComponent(convKey)}&customer_name=${encodeURIComponent(name)}`。主看板的 `NewDeal` 支持 `new=1` 只打开一个建案窗，并预填会话/客户；原加入已有案子的动作保留。该外部入口联动尚未实点，本次实点的是案子页内“从会话建案”。
3. `web/src/pages/Customers.tsx` 的 `Profile` 客户资料卡：新增“新建案子”链接到 `/deals?new=1&customer_name=${encodeURIComponent(c.buyer_name)}&destination_country=${encodeURIComponent(c.country ?? "")}`；若已具有经核对的 archive 客户数字 ID，则传 `customer=<数字ID>` 使用已有客户选择模式。当前该页 `Customer.key` 是聚合客户字符串，不能当作 `customer_party_id`。该外部入口联动尚未实点；案子页内“从客户建案”已实点。
4. `web/e2e/smoke.mjs` / CI：将独立 `node e2e/ops-deals.mjs` 加入构建后的验收命令。`fixtures.deals.mjs` 自己安装基础夹具，再覆盖本模块 API，无需把可变状态混入共享 `fixtures.mjs`。`web/src/nav.ts`、`App.tsx`、`api.ts` 均无需改动。
5. 旧测试集成：`tests/deals/test_api.py` 路由数 17→18；`tests/quotes/test_api.py` 两处 22→23；`tests/deals/test_rules.py` 的已成交断言应按阶段区分（售前不生成、paid 及以后生成）；`tests/deals/test_service.py` 的成交/验货阶段默认待办断言更新为模板数量，保留重复进入不重建、不重开完成项的断言。新增覆盖在 `tests/ops_deals/`，本轮未越界改旧测试文件。
6. 若总览的“今天要做的事”尚未读取原 `/fulfilment/board` 的 `overdue,next_due`，请由总览负责人接入；本模块已持久化日期并在履约看板标红，原后端巡检/角色链因缺依赖未做本轮跨模块验收，不能宣称总览队列已联通。

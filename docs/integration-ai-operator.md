# AI 运营员交接（当前 r11 源码）

本工作区交付候选，未部署、未挂入共享路由/导航。上一轮目录只读使用：迁入 19 个源码、测试和页面文件；原有 31 项 pytest 在本工作区重新通过后再续做。当前工作区没有可用 Git 元数据，未提交或推送。

依据 `docs/north-star.md` 第 5 节红线 1 实现分级自动，优先于旧操作文档的全人工发送表述。开发和测试未真实发送消息，未调用真实模型、未确认真实收款、未读取生产业务库、未改网络分流、未新增或复制凭据，未运行 `tsc -b`。只读 `channel-status` / `ai-task status` 在本容器退出非零，不据此判断生产可用性。

## 交付与 r11 对齐

- `src/console/operator/`：持久事件/决策/动作/发送尝试；资料快照；核价与单据草稿；分级策略、时区、分段与限流；国内模型入口；领域操作和显式注入的渠道适配器。
- `src/console/v3_operator_api.py`：六条 authenticated API，不自行构造生产服务。
- `web/src/pages/Operator.tsx`、`web/src/pages/operator/`：总开关、演练、授权矩阵、确认/修改/拒绝、联系人语言/时区/上限、动作与决策记录。
- `tests/operator/`、`web/e2e/{ops-operator,fixtures.operator}.mjs`：虚构数据隔离验收。
- `docs/ai-operator.md`：业务使用规则与当前边界。

已比较上一轮与当前依赖：outbox/roles/kb/quotes/fulfil 相同；inbox 的 model/workflow、deals/service、archive/store、customers/operations 已有 r11 改动。运营员读取当前 `projected_parties()` 的合并投影与 `parties_ops_profiles.timezone`，不移动历史 FK；合并/撤销导致身份变化时，旧草稿发送前被拦截。按原 conversation key 保留渠道账号和联系人限额，不因客户合并重置计数。

## API

所有写请求为 `application/x-www-form-urlencoded`，保留宿主登录、密码复核和 CSRF。每个操作必须传 `operation_key`（8–100 位字母/数字/连字符，建议 UUID）。相同 key 内容相同复用回执，内容变化返回 409。GET 不创建数据库，不迁移；响应 `Cache-Control: no-store`。禁止额外 query 和重复表单字段；固定错误信息不包含上游异常。

| 方法与路径 | 字段/结果 |
|---|---|
| GET `/api/v1/operator` | `settings,profiles,types,actions,decisions,unresolved_events,transport`；最近 200 动作加最早 200 未完成动作，最近 100 决策；完整历史保存在私有库 |
| POST `/api/v1/operator/settings` | `scope,key,enabled`；scope=`master/levels/types/contacts`；enabled=`0/1` |
| POST `/api/v1/operator/profiles` | `conversation_key,type,timezone,language,tone,hourly_limit,daily_limit,interval_seconds` |
| POST `/api/v1/operator/actions/{id}/approve` | 仅 operation_key；确认后再次取当前来源与策略，最多执行一段，返回 `action` |
| POST `/api/v1/operator/actions/{id}/edit` | `text`；空行分段；可删除事实段，不能添加无来源声明；已经开始分段发送的动作不可改正文 |
| POST `/api/v1/operator/actions/{id}/reject` | `reason`（必填，最多 500 字）；保留原稿和拒绝审计 |

授权字段示例：`levels['1']`、`types['1:supplier_product']`、`contacts['1:'+conversation_key]`。联系人细分允许客户工程/批发/终端/其他、供应商产品/物流/配套；不能通过运营员资料把客户伪装为供应商。收款确认没有可执行 API。

状态：`dry_run/pending/ready/scheduled/sending/sent/done/blocked/unknown/failed/duplicate/rejected`。开启总开关/退出演练不会释放历史演练草稿；人工待确认队列逐条确认，新事件按新策略判定。全停清除未发动作的旧人工授权。已进入渠道的一次请求无法撤回，结果必须核对。

## 私有库表与主动迁移

专用 SQLite 路径由集成者显式传入，例如运行私有目录中的 `operator.sqlite3`，不能放到公开源码目录。文件 0600，WAL + FULL，准入事务 `BEGIN IMMEDIATE`；不修改现有 archive 版本号。

| 表 | 用途 |
|---|---|
| `operator_settings` | 总开关/演练/三级矩阵，默认 enabled=false、dry_run=true、全部权限关闭 |
| `operator_profiles` | 按 conversation key 的细分、语言、时区、语气和限流；未设置时可继承 r11 时区和已核实语言服务结果 |
| `operator_events` | 稳定事件 ID、内容摘要、原始触发、evaluating/complete；模型调用前持久占位 |
| `operator_actions` | 动作、对象、等级、状态、预约时间、人工批准者、演练标记及完整草稿 |
| `operator_decisions` | 触发、上下文来源、模型元数据/输出、提案、策略快照、人工操作和每段发送结果 |
| `operator_attempts` | action+part 唯一、幂等键、内容摘要、联系人和额度时间；调用发送桥前先提交 |
| `operator_receipts` | 人工操作幂等回执 |

安装后显式调用 `operator.store.initialize()`。读接口不负责安装。archive 要先完成当前 r11 所需迁移；本模块仅只读这些表。经授权执行内部动作时，复用 Deals/Fulfil 的写入和幂等语义；资料更新向 `deal_events` 与 `parties_ops_events` 追加带来源的待核对备注，不覆盖已确认字段。履约只建缺失的 todo，不确认收款、不倒退已存在节点。

完整决策含内部成本和消息，只可在现有内部认证权限下查看；不可把此 JSON 当作客户回复。输出正文另经 `facts.guard()` 限定。

## 组装接口（无导入副作用）

```python
from console.operator.runtime import build_operator, QuotePolicyFromRound
from console.operator.model import AiTask
from console.operator.bridge import FakeBridge
from console.v3_operator_api import build

# inbox、hub、quotes、lang 均为宿主已配置实例；显式使用既有私有路径。
operator, events = build_operator(
    operator_path=operator_path, archive_path=archive_path,
    inbox=inbox, hub=hub, quote_policy=QuotePolicyFromRound(quotes),
    language=lambda key: lang.read('contact', {'conversation_key': key})['contact'],
    model=AiTask(), bridge=FakeBridge(),
)
operator.store.initialize()  # 安装/迁移阶段主动执行
routes.extend(build(app_module, service=operator))
```

`hub` 使用 `console.kb.hub.Hub`，即 `scripts/kb_search.py` 的同一检索引擎和标签策略。客户用途传 `customer_reply` 与精确会话 scope；模型上下文不包含 offers/freight/其他单位的历史。再次过滤保密和成本标签；KB 结果未暴露 source_scope 的买家片段被保守排除，当前会话直接从 inbox 取得。KB 命中只供理解，不自动升级成已核准数字事实。规格来自产品的 confirmed/manual 字段、source、at；交期来自当前已确认、带来源且未过期的 offer。

`QuotePolicyFromRound` 读取本案最新报价轮的 FX、目标毛利、币种、有效期及人工确认条款。没有核准规则时问老板。也可注入老板核准的按客户类型/联系人定价规则函数 `quote_policy(deal)`，返回字段见 `operator/quote.py`，不能塞默认汇率或默认利润。当前自动算价覆盖 EXW 和单一明确线路的 DDP；有额外配套/国内费用、模糊线路或其他贸易术语时交现有报价页处理，不静默漏算。所有算术复用 `quotes.calc.compute` 和 `quotes.freight.calculate`。

## inbox / outbox / deals / roles 接口点

1. **新入站**：统一消息持久化成功并确认归属后，调用 `events.inbound(conversation_key, durable_message_id, deal_id=...)`。不要在 HTTP 请求未提交时调用。未绑定对象/案子有歧义进入老板队列；没有案子时提出建案动作，不猜供应商。
2. **供应商/货代回复**：原始入站也可触发 inbound；提取/核对流程把来源、报价日期、有效期写入已有 `offers/freight_quotes` 后，再调用 `events.supplier_reply(...)` / `events.forwarder_reply(...)`，source_id 使用消息+档案版本的稳定标识。规则重新检查依赖，齐备自动算价并生成客户报价草稿。不把刚收到的自由文本自动视为已核准价格。
3. **定时事件**：宿主后台作业每 30–60 秒调用 `operator.tick()`。它扫描 `deal_tasks` 到期、已关联会话最后一条 seller 超过 48 小时、恢复到时段落；不 sleep，不启动私有 daemon。任务/截止时间、会话/最后发送时间构成稳定 ID，不反复问模型。来源服务的补偿重放可重复 emit 同一稳定 ID；不能每次生成随机 ID，不能用“最新一条”代替持久增量游标。
4. **报价/订单**：新报价草稿含完整成本和利润计算快照，对客户的 parts 仅含销售价；发前再次核价并比对摘要。客户认可时复用现有 quote 文本/PI 计算，生成 PI 与订单草稿。缺卖方抬头、条款或来源则阻断并留待办。未自动把运营员报价写成既有报价轮的 sent/accepted 状态，避免分段尚未结束就假记已发送；整合时可以据最终 sent 记录追加案子时间线。
5. **付款与履约**：只在既有 Fulfil 人工收款提交成功后调用 `events.payment_confirmed(...)`。快照仍核对 `deal_payments.confirmed_by/confirmed_at/qualifies` 与 won/paid 阶段，伪造唤醒无效。仅安排验货、订舱、在途待办；物理发货/签收不由模型确认。
6. **岗位服务**：继续复用 roles 的归属/提取建议及档案核准结果；本模块不另起一个修改正式成本的模型通道。档案变更原文只作为待核对备注保存。

事件入口是进程内方法，未增加可公开伪造消息/收款的 HTTP 接口。`handle()` 生成持久提案，`Events.emit()` 会推进 ready 动作的首段，`tick()` 续剩余段。一个联系人失败不阻塞其他联系人的后续 tick；evaluating/发送结果未知必须核查，不能通过换 ID 重跑模型或重发。

### 渠道桥（本次只用假实现验收）

`operator.gateways.ChannelBridge(senders=..., domain=DomainActions(deals, fulfil))` 是显式生产适配边界。

- 微信：`WeChatSender(client, resolve=..., allowed=...)` 接收已配置的 WTAPI 客户端，调用实际 `send_text(app_id,to_wxid,content)`；resolver 从原会话对应账号绑定取 app_id，不能把 inbox 的显示账号“微信”当 app_id。to_wxid 必须等于 key 中原身份。须另有部署渠道 allowlist。供应商确认回执的 toWxid/newMsgId 才记 sent；异常 unknown。WTAPI 没有幂等键，外层 durable reservation 禁止重发。
- WhatsApp：`WhatsAppSender(existing_bridge_client, resolve)` 复用 outbox 的配置/GET/POST/结果解释；自动请求为 `actor=model_auto, auto_send=true`，人工确认为 `approved_operator,false`。绑定 session/account 必须与解析原会话所得目标一致；底层 bridge 仍检查 `auto_send` 与具体联系人白名单。不借用旧 `BridgeClient.deliver()` 的 owner_explicit 来冒充自动许可，不自动绑定新收件人。
- 阿里：没有后台发送分支；人工批准也只返回 channel_unavailable，保留原账号的待发正文。
- 不修改原 outbox 表结构，不复制凭据，不变更网络。运营员自己的动作/发送账本是此流程的 outbox；原工作台手动发送 outbox 保持原接口。假桥 transport 明确为 fake，页面提示模拟，不作为生产交付成功证明。

## 前端挂载（由总指挥整合）

当前没有修改 `web/src/App.tsx`、`web/src/nav.ts`：

```tsx
import { Operator } from './pages/Operator';
// 现有 /app basename 的布局子路由，靠近 roles：
<Route path="operator" element={<Operator />} />
```

在 `nav.ts` 的 PAGES 增加 `{key:'operator',label:'AI 运营员',path:'/operator',kind:'live',area:'front',group:'日常工作',icon:Bot}`，并把 operator 加入前台 key 列表；保留 r11 原入口。使用现有 `get/postForm`，保留宿主 CSRF 和错误处理。

本次限定文件范围不含需求账本和 project-status。整合时在 `web/src/data/requirements.json` 追加本任务条目（建议 ID `AI-operator`），验收文字为下表覆盖项、状态为“隔离验收通过，待宿主挂载/生产演练”；同时更新 `docs/project-status.md`，不要直接标记真实自动运营已上线。

## 验收命令与结果

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /tmp/inbox-venv/bin/python -m pytest tests/operator -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /tmp/inbox-venv/bin/python -m pytest tests/quotes/test_calc.py -q
OPERATOR_NODE_MODULES=/tmp/r7f-web-deps/node_modules \
CHROME=/tmp/inbox-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell \
OPERATOR_ARTIFACTS=/tmp/operator-current-e2e node web/e2e/ops-operator.mjs
```

浏览器脚本在临时副本加路由与导航，先运行 `tsc --noEmit` + Vite build，再打开真实 React 组件；API 全拦截，未知 API/外部网络请求失败。没有修改共享路由或生成工作区 dist。

| 验收 | 结果 |
|---|---|
| operator 隔离 pytest | 80 项通过；socket/subprocess 禁外部调用，SQLite 限制在 /tmp；模型/发送桥全部虚构 |
| 报价内核回归 | 8 项、151 subtests 通过 |
| 从虚构询盘到报价 | 供应商与货代首段各自推进；已核准回复后生成 USD 销售价和内部利润；客户文本无成本 |
| 安全策略 | 三级各 8 种开关组合、默认演练、全停/重开、夜间、间隔/小时/天上限、未知/失败/崩溃不重发、重复事件并发 |
| 业务来源 | 无来源数字/承诺、其他买家/成本、报价过期/变更、规格变更、r11 合并/撤销、PI/订单缺条款、人工收款证据、资料备注幂等 |
| Playwright | 1440×1000 与 390×844：矩阵、改稿、确认发送（假桥）、拒绝、联系人设置、总停、决策、读取失败禁操作；各 10 次写请求、1 次模拟发送，无页面错误/横向溢出 |

浏览器报告/虚构截图：`/tmp/operator-current-e2e/results.json`、`operator-1440.png`、`operator-390.png`。Starlette TestClient 的 httpx 弃用提示仍存在。上述结果不是生产渠道或模型可用性验收。

## 上线步骤与回滚

1. 总指挥在暂存副本挂载 API/页面、事件增量来源与 tick；使用当前运行源码重新核对身份 resolver、archive/quotes/lang 路径，不套旧 r11 补丁。运行上述验收及项目要求的 `scripts/e2e_smoke.sh`（本次未上线，未执行生产数据全页演练）。
2. 备份待覆盖源码、前端、数据库；操作库与所有业务库都留在私有目录。先装候选，核对路由无重复，显式初始化 operator 库；保留原鉴权/CSRF。
3. 默认 fake + enabled=false + dry_run=true。生产来源接入后只演练，核查绑定、消息归属、报价来源和决策。确认宿主有持久来源重放/补偿与作业异常记录，不能靠一次回调保证不漏事件。
4. 重启相关业务服务并检查静态页面/API/作业健康；不改任何代理、路由、DNS、防火墙。确认运营员关闭时仍正常收消息、正常生成演练记录。
5. 老板审阅下面清单后，由总指挥显式注入生产桥、配置既有渠道许可，再只开选定的第 1 级联系人。第 2/3 级保持关闭。不要用真实客户做发送测试。
6. 回滚先全停并停止 tick/入站调度，记录所有 sending/unknown；撤下新挂载、恢复源码/前端。保留 operator 库及发送尝试，不回滚或删除账本导致已发送消息被当成未发送。已进入渠道的请求须人工核查。

## 开启第 1 级前老板需要看的东西

- 演练样本：该客户是谁、是哪笔案子、给哪家供应商/货代发、为什么发、具体正文和来源；未绑定或疑似串单项已处理。
- 所选联系人/原渠道账号、产品/物流/配套分类、当地时区、语言、每小时/每天段数与间隔；未知时区/语言不能自动发。默认 08:00–20:00，6 段/小时、20 段/天、30 秒间隔。
- 第 1 级只问资料、价格、有效期、交期、运费，不下单、不谈付款；第 2 级连续两周基本无需改稿后另开；第 3 级报价/PI/订单/发货逐项确认或单联系人授权。
- 成本/运价过期、无有效期、无来源的实例如何被挡住；客户正文看不到内部成本/利润和其他买家资料。
- 深夜排队、上限、总停、未知回执的演练记录；unknown 没有重发按钮，先到原渠道核对。总停不会撤回已进入渠道的一次请求。
- 国内模型真实准入须由部署环境核验 `ai-task status`；免费池不静默转付费，未知调用不重试。人工收款确认始终留在案子页。

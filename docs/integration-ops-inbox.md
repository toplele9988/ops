# inbox：W2 商机沟通交付

本次只在任务 workspace 修改候选源码、构建和演练，未部署。按 W2 补齐会话处理闭环，发送给客户或供应商都必须人工点确认，并且只写 `dry-run` 草稿；没有调用真实渠道接口，没有写入生产目录，没有改网络分流或发品，没有运行 `tsc -b`。

任务工作副本没有可用 Git 元数据，未创建提交或分支；未重置或覆盖他人工作。开始时依次读取了 `/srv/imgw-dev/ops` 指定文档、夹具及源码，随后在受限 workspace 实施。

## 改动文件

- `web/src/pages/Inbox.tsx`：渠道、要我回复／等对方／已处理／全部、搜索、等待时长与客户归属；新消息提示；完整展示适配器提供的消息；翻译、附件及对话操作入口。
- `web/src/pages/inbox/Operations.tsx`：客户卡、案子卡、处理／恢复、时间提醒、归属客户、新建／关联案子、供应商草稿预览与人工确认、可编辑客户回复与失败锁定。
- `web/src/pages/inbox/Attachment.tsx`：点击后读取已归档文件；纯文本预览、下载、读取失败提示，沿用图片放大组件；不执行文件内容。
- `web/src/pages/inbox/inbox.css`：局部布局与手机资料展开，避免资料区和回复框重叠。
- `web/src/ops/inbox.ts`：本页面独立的类型及请求函数，复用原 `api.ts` 的 CSRF 表单请求。
- `src/console/v3_inbox_api.py`：新增 W2 路由；列表状态投影、客户名与消息搜索；摘要修订指纹；取消 API 的额外 300 条／4000 字截断；原 AI draft 接口增加可选幂等标识。
- `src/console/inbox/workflow.py`：SQLite 跟进状态、提醒、客户身份、案子关联与操作回执；复用 customers 投影和 Deals 的事务、事件与身份归属实现。
- `src/console/inbox/drafts.py`：AI 草稿请求幂等、增量迁移；保留原接口调用兼容性。
- `src/console/inbox/migrate.py`：显式增量建表命令，三个数据库路径必填，不含生产默认路径。
- `src/console/outbox/dry_run.py`：outbox 库内独立的演练草稿表；绝不调用桥接发送器，不修改现有发送开关和发送流程。
- `src/console/inbox/patches/*`：候选统一补丁、修改前／修改后 SHA256、挂载与回滚说明。
- `tests/inbox/test_workflow.py`、`tests/inbox/conftest.py`、`tests/inbox/test_patch.py`：隔离存储／HTTP／幂等／迁移／补丁验证。
- `web/e2e/ops-inbox.mjs`、`web/e2e/fixtures.inbox.mjs`：严格 CSP 下真实点击；POST 会修改内存，断言页面变化和副作用。
- `tests/inbox/*.log`、`tests/inbox/artifacts/*`：验收日志、虚构请求与结果、截图。

没有直接修改共享的 `nav.ts`、`App.tsx`、`api.ts`、`fixtures.mjs`、`smoke.mjs`。

## 接口

沿用宿主的登录、密码复核、CSRF 中间件。全部写操作为 URL 编码表单；`request_id` 为 8～100 位字母、数字、下划线或短横线，由浏览器为一次操作生成并保持不变。网络失败不自动重试；相同标识与同一内容返回原结果，标识冲突拒绝。

| 方法、路径 | 入参 | 出参和效果 |
| --- | --- | --- |
| GET `/api/v1/inbox/conversations/{key}/ops/context` | 会话 key | `workflow_state`、`remind_at`、`reminder_due`、`customer_key/name`、来自 customers 的 `customer`、来自 deals 的 `deals/party`、可选供应商 `suppliers`、该会话相关的演练 `outbox`、`dry_run:true` |
| POST `/api/v1/inbox/conversations/{key}/ops/action` | 公共字段 `action, request_id`，具体见下 | `{ok?,id?,notice,message?}`，结果保存后立即回显 |
| POST `/api/v1/inbox/conversations/{key}/ops/supplier-draft` | `request_id,supplier_key,product?` | `{message,recipient,notice}`；从当前客户最后一个问题与关联案子产品名整理询问，附加手填产品；没有产品则要求补充；保存到供应商会话的演练草稿，尚未入队 |

`/ops/action` 的动作：

| action | 其他入参 | 持久副作用 |
| --- | --- | --- |
| `send` | `text,confirm=1,dry_run=1` | 保存可编辑后的客户回复到演练表，`state=queued,dry_run=1`，回显“已进入发送队列（演练）”；不会把客户会话假记为已回复 |
| `supplier_send` | `supplier_key,draft_id,text,confirm=1,dry_run=1` | 核对草稿确属本客户和所选供应商，再保存演练队列记录；确认前只有草稿 |
| `done` | 无 | 标记已处理，记住来源消息版本；新消息到达重新进入跟进 |
| `reopen` | 无 | 清除已处理和提醒，按当前消息方向恢复跟进 |
| `remind` | `remind_at`，带时区 ISO 时间 | 保存未来一年内的提醒；到时读取即回到“要我回复”，新消息会提前唤起 |
| `assign` | `customer_key,confirm=1` | 选 customers 列表中的客户，保存映射并调用 Deals 身份归属逻辑；同步案子内的会话归属。更换已有归属需确认；不自动改案子本身的商业客户 |
| `create_deal` | `title?` | 复用 Deals 创建案子、参与方、会话关联、阶段待办与时间线；并发和刷新重试只产生一个同来源的进行中案子 |
| `link_deal` | `deal_id` | 复用 Deals 的会话关联，重复关联不增加第二条记录 |

兼容修改：

- GET `/inbox/conversations` 原参数继续可用，新增 `state=done`；`waiting/replied` 读取持久跟进状态。每条增加 `workflow_state,remind_at,reminder_due,customer_key,customer_name`。搜索覆盖当前可读消息与归属客户名，仍分页。
- GET `/inbox/summary` 增加 `revision`（仅哈希）和 `conversation_count`；等待计数使用跟进状态。页面每 10 秒刷新摘要，列表和对话每 15 秒更新。
- POST `/inbox/conversations/{key}/draft` 沿用原 AI、知识来源、分类限制和限流，新增可选 `request_id`；相同请求返回原草稿，不重复调用模型。
- 翻译继续调用现有 `/api/v1/lang/translate`，读取 `/lang/settings`，未新增翻译模型或外发路径。
- 400 为参数问题，403 为授权／复核失败，404 为不存在，409 为冲突，429 为原起草限流，503 为无法确认保存。服务端异常不返回底层路径或原始错误。客户和供应商入队失败后按钮锁定，要求刷新记录并人工核对，不自动重发。

## 存储与生产候选

档案库新增 `inbox_workflow` 与 `inbox_action_receipts`，状态和客户／案子修改同事务提交。outbox 库新增 `inbox_dry_run_drafts`，`dry_run` 的 CHECK 固定为 1，只有 `draft/queued` 状态；真实 `outbox` 表保持零演练发送。AI 草稿表增添可空 `request_id` 和唯一索引。读操作不建表；迁移命令可重复执行，保留既有数据。

候选包：`src/console/inbox/patches/production-mount.patch`；基线：`SOURCE_BASELINE.sha256`；结果：`CANDIDATE.sha256`。详细步骤及回滚见同目录 `README.md`。基线来自开发源码副本，未声称核验过生产版本。

既有宿主调用 `v3_inbox_api.build` 时新增路由自动注册，不改共享挂载文件。总指挥必须校验实际源码基线，应用补丁到候选副本，备份源码／静态资源／三个 SQLite 库，主动运行显式迁移，再依既有流程发布和复查。反向补丁已在临时目录验证；默认保留新增数据，若恢复数据库备份会撤销备份后业务写入，不能自动做。

## 共享文件整合清单（逐条，尚未改）

1. `web/src/nav.ts`、`web/src/App.tsx`、`web/src/api.ts`：本任务不需要改动，现有 `/inbox` 页面挂载和 CSRF 请求工具足够。
2. `web/e2e/smoke.mjs` 顶部导入处：增加 `createInboxState, installInboxFixtures, conversationKey, supplierKey`，来源 `./fixtures.inbox.mjs`。
3. `web/e2e/smoke.mjs` 的 PAGES 中，“会话-客户”改为 `/inbox?c=${conversationKey}`，“会话-微信”改为 `/inbox?channel=wechat&role=all&c=${supplierKey}`；不能继续用 `demo-alibaba/demo-wechat` 作为本模块验收会话。
4. `web/e2e/smoke.mjs` 每个页面安装夹具的位置：当 `path.startsWith('/inbox')` 时使用 `await installInboxFixtures(page, createInboxState(), problems)`；其他页面继续 `installFixtures`。新安装函数内部已调用共享夹具，且为本模块的写操作提供内存副作用。
5. `web/e2e/smoke.mjs` 手机资料展开断言处：按钮名称更新为“客户资料与 AI 建议 · 展开操作”和“收起客户资料和操作”；`#conversation-profile` ID 保留。展开后资料与动作占满手机内容区，收起回到对话。
6. `web/e2e/fixtures.mjs`：不需要直接改。以上按页面选用新增夹具即可；若总指挥统一合并夹具，需要在原会话详情前加入 `/ops/context` 分支，并采用新增夹具中的状态、幂等回执、POST 持久内存和演练队列逻辑，不能只回固定成功 JSON。
7. `web/src/data/requirements.json` 的沟通组 `id:A` 中追加一条 `id:A-W2-inbox`（实际 schema 已读取）：`ask` 写 W2 会话筛选、AI 编辑确认演练、客户与案子归属、问供应商及提醒；`status:partial`；`now` 写本候选及隔离验收结果；`accept` 写桌面／手机真实点击及数据库幂等验收；`next` 写共享烟测整合、渠道历史边界及授权发布。此文件超出我的范围，留给总指挥合入。
8. `tests/inbox_api/test_api.py` 的旧路由清单断言：原 8 条（含客户／渠道）需加本模块 3 条路由，总数变 11；新增两个 POST 名称 `inbox_ops_action,inbox_ops_supplier_draft`，GET 名称 `inbox_ops_context`。现有测试里的临时 request 替身需提供 `url.path` 以区分 supplier-draft。未直接修改该旧测试。
9. 客户负责人后续整合：`v3_customers_api.py` 当前仍按渠道账号与买家名称聚合。W2 归属已落入 `identities` 并在本页立即正确显示；跨渠道归属需要客户页时间线也读取 identities／本次映射，才能在客户页显示转入的会话。该文件不在我的范围内。

## 验收命令与结果

最终结果以本节和对应日志为准；所有数据虚构，所有服务仅绑定 loopback，浏览器未知 API 失败且阻止外站请求。

```sh
# workspace/web
npm run build
npx tsc --noEmit
PLAYWRIGHT_BROWSERS_PATH=/tmp/inbox-browsers node e2e/ops-inbox.mjs

# workspace 根目录；隔离 venv 安装 starlette/httpx/pytest/python-multipart
PYTHONPATH=src /tmp/inbox-venv/bin/python -m pytest \
  tests/inbox tests/inbox_api/test_model.py tests/lang -q
```

- 构建及独立类型检查：`npm run build` 和 `npx tsc --noEmit` 均通过；日志 `tests/inbox/build.log`、`typecheck.log`（成功无输出）。
- Playwright：4/4 组通过（桌面主流程、桌面失败流程、手机主流程、手机失败流程），共实际入队两条演练回复／每种宽度、关联两个案子并核对内存副作用；`tests/inbox/playwright.log` 与 `artifacts/results.json` 的 `errors` 为空。
- Python：本模块 14 项通过（`tests/inbox/python.log`）；连同现有 model/lang 相关测试共 105 项通过、37 个子测试通过（`python-related.log`），无跳过；Starlette 的 httpx 测试客户端有一条弃用提示，不影响断言。补丁正反向、基线与候选 SHA 一致性包含在通过项中。
- 文件范围：五个指定共享文件与源工作区逐字一致，见 `tests/inbox/scope.log`。源工作区的 `requirements.json` 在任务期间被另行追加 OP7，本地仍保留初始副本，未回写或覆盖。

脚本覆盖桌面 1440×900 和手机 400×860：图片放大、文件打开／下载、外语翻译、AI 起草、编辑、人工确认前后队列变化、创建案子、关联已有案子、归属客户、供应商草稿与再次人工确认、选时间提醒、已处理及刷新保留、筛选／搜索／空态、新会话提示；另测加载、AI 失败恢复、资料和列表读取失败、客户／供应商入队失败不重发。截图在 `tests/inbox/artifacts/`，`results.json` 记录实际 POST、演练队列与关联副作用。

Python 验证临时 SQLite 持久化、请求冲突、并发建案和入队去重、客户归属一致、提醒到期、新消息唤起、起草幂等和完整可读消息、授权复核／错误隐藏、增量迁移及补丁正反向。`tests/inbox/conftest.py` 明确处理公开快照缺少 `console.media` 的情况：只在渠道组合边界注入虚构 Inbox，不伪造 W2 存储、HTTP、Deals 或 outbox；真实采集及媒体服务未验证。相关现有 model/lang 测试同时运行。

## 未做或未验证

- 未部署、未运行生产迁移、未连接真实业务接口、未发送任何真实消息；未确认收款。
- 真实渠道历史完整性未验证。页面与 API 不再额外截断已读消息，但上游 WhatsApp 适配器现为 `messages?limit=100`，需渠道负责人扩展历史读取；不宣称远端完整历史已接通。媒体归档仍依赖缺失于公开快照的 `console.media`，本次浏览器使用虚构归档文件。
- 真实 AI／知识库／翻译服务调用未验证；本次注入假模型，沿用既有实现并跑隔离语言测试。
- 提醒通过读取时计算到期，并在本页定时刷新出现；未接入 W1 总览队列或额外推送。
- 客户页跨渠道时间线整合与全站共享 smoke 仍待总指挥按上面清单处理；本次没有宣称全站回归通过。
- 手机系统键盘、真实附件格式及真实渠道发送回执未验证；本次验证 Chromium 的两种宽度和虚构纯文本／图片。

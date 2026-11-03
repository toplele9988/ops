# parties：W4 / W5 交接（候选，未部署）

仅修改本任务负责范围。未访问生产目录、真实业务库或真实接口，没有外发消息。当前目录没有 Git 元数据（`.git/` 为空），无法核对分支与提交；没有提交或重置任何文件。

## 需求账本（请总指挥整合）

本任务授权只读共享文件。请在 `web/src/data/requirements.json` 对应 W4/W5 条目记录本任务需求和验证结果：客户/供应商可筛选列表、每条下一步按钮、多渠道身份和完整往来、跟进日期、客户编辑/合并撤销/转案子、供应商询价/催资料/包装核对、四类往来单位建档并关联已有会话。状态须标「本地隔离验收通过，生产未挂载」，不可标已上线。

## 变更文件

- `web/src/pages/Customers.tsx`、`Suppliers.tsx`：接入共用联系人工作页。
- 新增 `web/src/ui/PartiesWorkbench.tsx`、`parties.css`：列表、详情、表单、反馈、二次确认、手机布局。
- 新增 `src/console/customers/operations.py`：往来单位的真实 SQLite 写入、完整往来投影、演练草稿、可撤销合并、原子请求去重。
- `src/console/v3_customers_api.py`：新增独立导出 `build_parties`；旧 `build` 与两个只读客户接口保持兼容，工作区默认没有自动挂载新路由。
- 新增 `src/console/customers/patches/`：生产挂载候选补丁、公开源码基线与 SHA256、候选 SHA256。放在本任务允许范围内，沿用 `patches/overview` 的补丁方式。
- 新增 `tests/parties/{conftest.py,test_operations.py,test_mount_patch.py}`。
- 新增 `web/e2e/{fixtures.parties.mjs,ops-parties.mjs}`：浏览器内可变夹具，POST 真正改变内存状态；桌面、手机分别真点主流程并验证请求副作用。
- 日志与虚构截图在 `artifacts/parties/`（被现有 .gitignore 忽略）。

## 新接口契约

前缀 `/api/v1/parties-ops`，GET 返回 JSON；所有 POST 用 URL 编码表单、同源 CSRF 头，并且必须传 `request_id`（1–100 字符）。同一编号与相同操作者、操作、路径、内容重复提交返回原结果；内容不一致返回 409。业务写入、操作记录与请求去重在同一个 `BEGIN IMMEDIATE` 事务，失败全部回滚。

| 方法 | 路径后缀 | 入参 | 出参 |
| --- | --- | --- | --- |
| GET | 空 | `kind` customer/supplier/forwarder/vendor；`q`、`country`、`channel`；`contact` 空/7/30/stale/never；`open` 空/yes/no；`sort` recent/oldest/name/tasks/followup；`page`、`page_size`≤100 | `{items,total,page,page_size,countries,channels}` |
| GET | `/conversations` | 无 | `{items:[{key,channel,name,party_id}]}`，用于选择已有会话 |
| GET | `/{id}` | 数字往来单位 ID | 列表字段及 `identities,timeline,deals,products,pending_questions,drafts,merges,merged_profiles` |
| POST | 空 | `name,kind,country?,conversation_key?` | `{ok,id,message}`；原子建档并关联会话 |
| POST | `/{id}/update` | `name?,country?,notes?,tags?,next_followup?`（空日期清除） | `{ok,id,message}` |
| POST | `/{id}/link` | `conversation_key` | `{ok,id,message}`；拒绝抢占其他单位会话 |
| POST | `/{id}/merge` | `source_id,confirm=1`，路径 ID 为保留客户 | `{ok,id,merge_id,message}` |
| POST | `/{id}/undo` | `merge_id,confirm=1` | `{ok,id,message}` |
| POST | `/{id}/draft` | `purpose` followup/inquiry/materials，`conversation_key` 必须已归属本单位 | `{ok,id,draft_id,text,state:"draft",dry_run:true,message}` |
| POST | `/{id}/review` | `draft_id,text,confirm=1` | `{ok,id,draft_id,state:"draft",dry_run:true,message}`，只记录人工核对，绝不发送 |
| POST | `/{id}/verify` | `product_id,confirm=1` | `{ok,id,message}`；必须本供应商产品、外箱三边/毛重/每箱数量齐全，更新真实 `products.field_state_json` 为 confirmed，保留来源并记核对人/时间 |
| POST | `/{id}/term` | `field` 为现有 TERM_FIELDS 之一、`value` | `{ok,id,term_id,message}`；记录人工确认合作条款 |
| POST | `/{id}/term-status` | `term_id,action` confirm/reject | `{ok,id,message}`；只处理本单位待核对条款 |
| POST | `/{id}/deal` | `title` | `{ok,id,deal_id,message}`；调用现有 Deals 事务方法，建立真实客户案子、参与方、会话关联、询盘默认待办与案子时间线 |

列表字段：`id,kind,name,country,notes,tags,next_followup,last_contact,channels,todo_count,open_deals`。最后联系是现有会话最后消息时间，不把内部保存或草稿当作已联系。待办为未结案子的未完成待办加待回复会话数。暂停案子计入未结。完整往来由 `inbox.detail(key)` 获取全部现有已归档消息，并与内部记录按时间合并；不声称渠道未归档的历史已经采到。

HTTP 层复用现有登录、旧会话与密码复核；POST 复核后才读表单、落库前再次复核。必须保留宿主 CSRF 中间件，不能单独暴露无认证服务。错误返回固定中文；400 无效输入，404 不存在，409 重复/归属冲突，503 读取或保存失败。

## 存储、合并与演练边界

- 复用真实 `parties`（四种 kind 与 country）、`identities`、`deals`、`deal_participants`、`deal_conversations`、`deal_tasks`、`deal_events`、`products`、`offers` 表。主 archive 迁移目标 v9，不修改共享迁移代码。
- 本任务附加表：`parties_ops_profiles`（标签/跟进）、`parties_ops_events`（时间线）、`parties_ops_merges`（保留原记录的合并映射与撤销记录）、`parties_ops_requests`（原子去重）、`parties_outbox`（演练草稿）。`Parties.initialize()` 可显式触发迁移和附加建表。GET 不创建文件、不迁移现有数据库。
- 合并只对两位有效客户开放。身份与原案子 FK 不挪动、不删除，详情统一展示，撤销恢复独立展示；拒绝链式合并。合并期间新建内容留在保留客户名下，确认框明确说明。
- `parties_outbox` 是本任务专用的**持久演练发件箱**，与 archive 同库以确保写入和去重原子一致。数据库强制 `state='draft' AND dry_run=1`，人工核对只更新正文和 `reviewed_at`。现有全局 `/outbox` 使用单独库且只支持 sending/sent/failed/unknown，没有 draft 状态；本任务没有伪造已发送记录，也没有修改其协议。
- 所有草稿在联系人页可编辑、人工二次确认与回看，实际发往会话键留存；未调用发送桥、AI 外部接口或真实业务接口。

## 共享文件与跨模块待整合事项（逐项）

1. **后端宿主路由**：二选一，不能同时挂载。
   - 采用下述 production-mount.patch：在原 `v3_customers_api.build()` 的返回列表后追加 `build_parties(app_module, inbox=cache)`，复用宿主同一会话源。
   - 总指挥直接整合当前候选源码时：在宿主现有 `v3_customers_api.build(...)` 后添加 `routes += v3_customers_api.build_parties(app_module, archive_path=archive_path, inbox=shared_inbox_instance)`。测试/演练一定传临时 archive 与虚构 Inbox，不能隐式回落真实来源。
2. **`web/e2e/fixtures.mjs`**：客户/供应商页面现改读 `/parties-ops`。需要在共享 `installFixtures` 安装后调用 `installPartiesFixtures(page, problems)`，但本文件当前反向调用了共享 installFixtures；整合时请把共享基础安装和 parties 安装拆开避免递归。可直接让总冒烟脚本在 parties 页面调用 `installPartiesFixtures` 替代基础安装。本任务没有直接改共享文件。
3. **`web/e2e/smoke.mjs`**：客户资料 URL `?c=demo-customer` 改成虚构往来单位数字 ID `?c=1`；供应商资料使用 `?party=3`（本任务夹具供应商 ID）。联系人时间线标题现为「全部往来时间线」。请把 `node e2e/ops-parties.mjs` 纳入总验收；仅旧截图检查不能证明写操作。
4. **旧链接与既有客户资料迁移**：客户列表现在管理真实 `parties`，不再按“同名 + 账号”自动合并。未建档会话通过“新建往来单位→关联现有会话”或详情“关联到本单位”入档。调用方若仍传旧聚合 customer key，应先确认/创建 party 并改链接为 `/customers?c=<party_id>`。旧聚合接口保留只读；不进行猜测式自动身份迁移。
5. **合并跨页显示**：案子、收件箱等其他页面仍读取原身份/案子 FK。需要这些页面在展示保留客户时读取 `parties_ops_merges WHERE undone_at IS NULL`，将 source 映射到 target；本任务只完成客户页统一展示与撤销，没有修改他人负责的业务源代码。合并后新案子从保留客户创建，带入合并展示中的各渠道会话，同时保留每条会话原身份归属，不挪动历史外键。
6. **全局发件箱**：如需在全局发件箱统一浏览演练草稿，请其负责人增加只读读取本任务 `parties_outbox` 的适配，明确 dry-run 草稿与人工核对状态；不要把本表接到现有发送队列。任何未来真实外发流程都必须另行保持人工点击与二次确认。
7. `web/src/nav.ts`、`App.tsx`、`api.ts` 不需要调整，本任务未修改。货代/配套供应商通过供应商页“单位类型”选择进入，不新增导航。

## 生产挂载补丁与回滚（未执行上线）

补丁：`src/console/customers/patches/production-mount.patch`。基线：同目录 `baseline.sha256`（来自授权开发仓库公开源码，**不是已核对生产版本**），候选：`candidate.sha256`。新增模块包含在补丁中，不直接覆盖现网文件。

本地测试将 `baseline/v3_customers_api.py` 放到临时暂存根的 `console/` 下，执行 SHA 校验、`patch --batch --fuzz=0 -p1 --dry-run`、正向应用、候选 SHA 校验，构建路由检查 16 个独立路径/方法，再反向 dry-run 与应用，验证还原到基线、删除新增文件。

未来发布须由总指挥在授权发布流程中：备份源码与 archive 数据库；核对实际源码基线 SHA，任何不匹配停止并重新生成补丁；先在发布暂存副本严格 dry-run；保留宿主认证和 CSRF；通过 `Parties(..., inbox=明确配置的只读会话源).initialize()` 主动升级 archive 并建立附加表；再装入候选、核对路由、重启与演练复查。本任务没有运行历史 `scripts/e2e_smoke.sh`（会写生产静态目录）。

回滚：先暂停新写入，恢复发布前前端；在暂存副本 `patch --batch --fuzz=0 -R -p1 --dry-run -i <patch>` 并反向应用，校验基线 SHA；再通过发布流程恢复代码。附加表可以保留，不影响旧代码。真实 parties/deals/products 已写入的正常业务记录不能盲删；若需要数据库整体回滚，必须停写并使用发布前数据库备份，单独评估发布后新增业务记录。合并仍活跃时回滚页面会恢复展示原资料，因为本补丁没有破坏原外键。

## 测试命令与结果

详见 `artifacts/parties/{build.log,tsc.log,python.log,python-related.log,browser.log,browser-results.json}`。全部是本地虚构数据；没有生产验收或部署。浏览器覆盖编辑资料/标签/日期、关联已有会话、草稿编辑与人工核对、转案子、取消合并/合并/撤销、国家/渠道/近期/未联系/未结案筛选、待办与名称排序、新建四类单位、包装核对和缺项禁止核对、记录合作条款、询价/催资料、失败重试与请求重放、刷新后保留、空态出口。GET 夹具筛选结果与 POST 后内存状态均断言。Python 另验证实际临时 SQLite 持久化、并发与回滚；浏览器使用内存夹具，不声称已对真实后端做浏览器联调。

- `cd web && npm run build`：通过（脚本为 `tsc --noEmit && vite build`）。
- `cd web && npx tsc --noEmit`：通过，退出码 0；tsc.log 为空表示无错误。
- `PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 /tmp/inbox-venv/bin/python -m pytest -q tests/parties`：30 通过，1 条测试库弃用提示；包括补丁正反向应用与路由构建。
- `PYTHONPATH=src:tests/supplier_data PYTHONDONTWRITEBYTECODE=1 /tmp/inbox-venv/bin/python -m pytest -q tests/parties tests/archive/test_core.py tests/archive/test_freight_channel_migration.py tests/supplier_data`：155 通过、147 subtests 通过，1 条测试库弃用提示。
- `CHROME=/tmp/inbox-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell node web/e2e/ops-parties.mjs`：通过。桌面 1440×900、手机 400×860 各完成18次成功/幂等重放写请求、另1次预期保存失败、6次二次确认；另测手机空态。零脚本/CSP/未登记接口问题、零整页横向溢出。

公开快照缺少 `console.media`，真实渠道包无法直接导入。`tests/parties/conftest.py` 只替换渠道构建边界并禁止调用，测试均显式注入虚构 Inbox；实际 Parties、Archive、Deals、路由与 SQLite 代码运行。不能把这理解为真实渠道适配器已验证。没有运行 tsc -b。

## 尚未完成 / 未验证

- 未部署、未连真实业务接口、未外发、未确认任何真实收款。
- 全站共享夹具、跨模块合并投影、旧聚合 customer key 链接、全局发件箱适配由总指挥整合后再验收。
- 重复客户候选支持搜索，每次最多100位，超过时提示缩小搜索范围；没有做自动判定或自动合并。
- 保留供应商合作条款记录、采用或否决操作，并纳入本任务幂等事务；条款新增已在浏览器真点，采用/否决的后端归属与状态约束已测，浏览器尚未点采用/否决。产品资料详情仍通过原产品页补充，该跨页补资料流程未验证。
- 没有真正点击到的其他页面与真实渠道消息/附件覆盖均未验证。

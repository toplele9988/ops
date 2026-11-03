# queue：W1 与 W8 页面引导交接

本候选仅在当前 workspace 开发、构建与虚构验收。未部署、未挂载生产、未连真实业务接口、未发送消息、未确认真实收款。未改网络分流或发品。workspace 没有可用 Git 元数据，无法提供分支/提交 SHA，未重置任何已有内容。

## 文件与已完成行为

- `web/src/pages/Overview.tsx`：顶部说明条、新手提示、今天要做的事；保留原渠道流水线、预算、历史覆盖和今日运营。
- `web/src/pages/Workbench.tsx`：保留页复用引导和队列；嵌入总览时不重复；旧买家入口追加 conversation，保留 c。
- `web/src/pages/Guide.tsx`：按 W1–W8 重写为八步图文（图标、箭头、做法、预期结果、去做按钮），W8 有知识检索、AI 员工两个入口。
- `web/src/pages/overview/{TodayQueue,PageIntro,Welcome}.tsx`、`queue.css`：独立队列、共享说明条、新手提示、响应式样式。
- `web/src/ops/queue.ts`：接口类型、本机反馈和提示存储；只保存条目哈希与版本，不存客户正文。
- `src/console/ops_queue/{__init__,service,inbox}.py`：只读聚合。字段已核对 inbox 源码、inbound/微信消息结构和 archive v2/v3/v4/v8；复用 overview、pipeline、fulfil 的日期规则。无业务写入、迁移、AI、outbox 或渠道客户端。
- `src/console/v3_ops_queue_api.py`：GET/HEAD、鉴权、读后复核、no-store/nosniff。
- `src/console/ops_queue/mount/`：挂载补丁、基线 SHA、回滚说明。未越界改共享 patches 或宿主。
- `tests/ops_queue/test_queue.py`：27 项隔离测试，禁网络、SQLite 仅允许临时虚构库，含补丁套用与反向回滚。
- `web/e2e/fixtures.queue.mjs`：浏览器内存夹具，POST 真改内存草稿或履约节点，不转发请求。
- `web/e2e/ops-queue.mjs`：桌面 1440×900、手机 400×860 真点击主流程，并复用原总览 22 个边界断言。

紧急排序：中断/失败任务 → 物流超期 → 待核对收款、超过一天未回复的买家 → 已到期报价 → 一般回复/催供应商 → 临近到期报价/归属核对 → 未知或偏慢的渠道。报价取最新一轮，到期窗口 3 天；供应商为我方最后发言后至少 48 小时；收款只取实际 pending 记录，不把“待付款”当作到账。

“已处理／稍后”仅是本浏览器可撤销的提醒反馈，不修改业务状态、不代表发送或到账。已处理暂存至下一个北京时间零点，稍后暂存 2 小时；来源版本变化重新提醒，可恢复全部本机暂存。回到页面、窗口聚焦、每 60 秒和手动更新均重读。读取失败保留旧数据并禁用暂存反馈；来源不完整不显示成完整的“今天没有待办”；全部核对且办完才显示该空态及下一步入口。

新手提示首次打开总览或工作台显示，关闭后存 `ops.queue.welcome.v1`；反馈存 `ops.queue.feedback.v1`。目前没有可用登录人字段，因此按浏览器记住，未按账号隔离、未跨设备同步。存储失败有明确反馈。

## 新增接口

| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| GET / HEAD | `/api/v1/ops/queue` | 无；任何 query 均 400 | `{generated_at,items,total,complete,unavailable,counts}` |

`items[]` 为 `{id,kind,label,title,detail,to,action,priority,due_on,revision}`。id/revision 是稳定哈希，kind 为 buyer_reply / supplier_chase / payment / quote_expiry / logistics / ownership / pipeline。generated_at 是 Unix 秒，due_on 是日期或 null。to 为不含 /app 的应用内路由。counts 按七类统计；complete=false 时 unavailable 列出未齐来源。

登录门沿用宿主：未授权 403，参数错误 400，异常脱敏 503，其余方法 405；没有新增 POST，不自动发送或确认收款。

## 请总指挥逐项整合（本人未改这些文件）

1. `web/src/pages/Inbox.tsx` 的 `const selected = params.get("c") ?? ""` 改为优先 `params.get("conversation") ?? params.get("c") ?? ""`。统一 `/inbox?conversation=KEY`，兼容旧 c。queue 当前同时带 `conversation=KEY&c=KEY&role=all&channel=CHANNEL`，两种宽度均已真点打开指定会话。供应商入口仍须 role=all 或 supplier。
2. `web/src/App.tsx` 的客户、供应商列表路由附近：由对应任务加入 `/customers/:id`、`/suppliers/:id` 并由各页面读取参数。共同约定 `/deals/ID`、`/customers/ID`、`/suppliers/ID`，ID 来自真实对象标识，Router 自动加 /app。现案子路由可用已真点；客户/供应商只有列表路由，queue 未生成不存在的详情链接。
3. `web/src/pages/Triage.tsx`：读取 conversation 与 suggestion，定位、高亮对应待确认条目。queue 已带 `/triage?conversation=KEY&suggestion=ID`，原页能显示列表，但自动定位未实现、未验证。
4. `web/src/pages/Pipeline.tsx`：可读取 channel 并定位渠道。queue 带 `/system/pipeline?channel=KEY`；页面可打开，参数定位未验证。
5. 其他运营台页面现有 page-head 后（收件箱主体前）加说明条。Overview、Workbench、Guide 已接，其余由总指挥/页面负责人整合：

   ```tsx
   import { PageIntro } from "./overview/PageIntro";
   <PageIntro page="inbox" />
   // page 可用：inbox/deals/customers/suppliers/products/fulfilment/search/roles
   // 案子详情可用更具体业务说明：
   <PageIntro text="查看这笔生意的往来和待办，核对后推进下一步。" />
   ```

6. `web/e2e/fixtures.mjs` 的 GET exact 区域新增 `/ops/queue` 的完整空队列（结构同自有 fixtures.queue.mjs 的 queueResponse([])），未知请求继续失败关闭。操作测试用 installQueueFixtures，其 POST 会修改内存。未改共享 fixtures。
7. `web/e2e/smoke.mjs`：整合夹具后继续原冒烟，另加 `node e2e/ops-queue.mjs` 操作验收。自有脚本给原 checkOverview 仅补登记了新 GET，22 个断言保持原样。未整合的新端点会让原 smoke 单独运行报“未登记 /ops/queue”，不能声称全站 smoke 已通过。
8. `web/src/data/requirements.json`：请登记 W1 七类待办与本机反馈、W8 八步引导与说明条，状态“候选已验证，待整合/未部署”。CLAUDE 要求维护账本，本任务禁止越界，因此将要求记录在此。
9. 如希望任何保留页登录后均有新手提示，可在 AppShell 插入 Welcome。目前总览、工作台已生效。无须修改 api.ts、nav.ts。

## 挂载、基线与回滚

详见 `src/console/ops_queue/mount/README.md`。production-mount.patch 在现有 pipeline 挂载后加两行；rehearsal-mount.patch 只读演练 archive 与临时虚构 inbound。显式 paths 会隔离全部省略来源，不回落真实库。未直接覆盖宿主。

baselines.sha256 中生产 SHA `0c5ad44de0a5dbe0887819300d448f4bdcd0b970a83ed125365069dbd4666c21` 仅复制自仓库 `docs/r7f/production-code-baselines.sha256` 的历史值。本轮未碰生产目录，未重新核验当前生产版本。生产补丁只在虚构匹配上下文完成 dry-run、套用、AST、反向回滚，不是当前生产适配证明；维护者须在部署候选副本核对真实基线后使用。演练 SHA 来自当前 workspace，并在临时副本验证套用、AST、回滚及字节恢复。

回滚：相应补丁 `patch --batch --fuzz=0 -R -p1`，恢复旧前端构建，再按既有发布流程核对路由。无业务库升级或业务数据回滚。本机提示/反馈可仅清除上述两个键。未运行会写生产目录的历史 scripts/e2e_smoke.sh。

## 验证与日志

日志、截图和 result.json 在 `artifacts/queue/`。Python 用现有临时 venv；Node 用 `npm ci --cache /tmp/queue-npm-cache`，未修改 package-lock。未运行 tsc -b。

```sh
cd web
npm run build
npx tsc --noEmit
CHROME=/tmp/pipeline-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell node e2e/ops-queue.mjs
cd ..
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /tmp/wa-session-test-venv/bin/python -m pytest tests/ops_queue -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /tmp/wa-session-test-venv/bin/python -m pytest tests/overview -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /tmp/wa-session-test-venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider
```

- build、typecheck 通过：build.log、typecheck.log。
- queue 27 passed：python-queue.log。
- 原 overview 85 passed，原 pipeline 125 passed：python-overview.log、python-pipeline.log。须分进程；两套既有测试都直接导入 conftest，合跑曾产生同名导入冲突（保留 python-regression.log 记录），最终分别通过。
- Playwright 桌面、手机均真点击买家入口→指定会话→起草，页面出现草稿，内存 outbox 增加一条 status=draft、dry_run=true；未点击外发。物流入口→案子→履约节点改为已完成→保存，断言 POST 字段、内存状态、页面变化、返回后队列减少。
- 另验证已处理/稍后、刷新记忆、恢复、2 小时到期、新进展再提醒、空态、失败保留/禁用反馈、不完整来源、工作台说明条、新手关闭、八步所有入口与 W8 两个入口。
- 原总览 checkOverview 22 个边界场景通过，含渠道、预算精度、加载/404/500/断网/刷新失败、空值和缺环节；未改其断言。无脚本异常、CSP 错误、整页溢出。
- API 全在页面内拦截，未知 API 返回 501 并使测试失败；local-server 只服务当前 dist，不导入/代理后端。脚本拒绝出站到静态服务以外的源。

初次浏览器验收修正了夹具表单定位；补丁测试修正了演练上下文。最终通过记录见日志与 result.json。

## 尚未做／未验证

- 生产部署、当前生产 SHA、真实业务/渠道/外发/到账均未验证。
- 默认只汇总本地阿里与微信会话。现有 WhatsApp inbox 适配器会请求桥接 HTTP，本任务禁止连接，故未主动拉取；页面明确提示 WhatsApp 未汇入。请来源负责人提供只读本地或内存快照（可注入 inbox=），禁止直接注入会触发网络的 UnifiedInbox。
- 本机反馈不是业务完成证明；未按账号隔离、未跨设备同步。
- 其他页说明条、客户/供应商详情路由、归属与渠道自动定位等待上述整合。
- W8 知识引用、保存备注、采用/修改/拒绝由对应页面任务负责。本任务只完成引导与入口，真点验证到达页面，未代验其他任务的写操作。
- 未做全站真实业务演练、未执行生产写入脚本。

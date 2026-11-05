# flow 交付说明（未部署）

## 老板过目：新的导航

```text
运营台
├─ 今天
│  ├─ 首页（流程总览） /
│  └─ 待我处理 /workbench
├─ 沟通
│  ├─ 消息 /inbox
│  ├─ 待确认归属 /triage
│  └─ 草稿未发送 /outbox
├─ 客户与生意
│  ├─ 客户 /customers
│  ├─ 案子 /deals
│  └─ 备货与发货 /fulfilment
├─ 供应与产品
│  ├─ 供应商 /supplier-dossiers
│  ├─ 产品 /product-dossiers
│  └─ 缺资料 /dossier-gaps
└─ AI 助手
   ├─ AI 运营员 /operator
   └─ 知识检索 /search

后台设置
├─ 渠道与数据：渠道接入、数据管道健康、阿里订单接入、知识中枢
├─ 设置：语言设置、账号与权限（原后台）
├─ 整理工具：待分类、微信通讯录、数据体检、货代运价、价格建议、供应商包装核对、需求与验收
├─ 资料维护：供应商旧列表 /suppliers、产品旧列表 /products
└─ 使用与改进：AI 员工、灵感库、怎么用
```

采用任务书分组顺序，让“今天看状态、先沟通认人、再推进生意、找资料、让 AI 帮忙”成为主线。前台共 13 个入口。档案是供应商和产品的主入口，旧列表转到后台，不重定向详情，也不丢筛选或旧书签。`/overview`、`/workbench`、`/guide`、`/roles`、`/inspiration`、旧供应商和产品列表及全部原路由继续保留；未删页面代码。

术语建议：保留**案子**。一笔生意就是一个案子，页面说明先解释清楚，已有报价、付款、发货按钮可继续沿用这个叫法；“生意”用于说明业务主线。导航、首页、说明统一使用“消息、待确认归属、待我处理、供应商、产品”。

## 老板过目：每页的一句话

所有前台入口及详情均由布局插入同一组件，加载和失败时也保留。已有内嵌说明由上下文去重，客户和消息源码零改动。

| 页面 | 这页做什么 + 下一步 |
| --- | --- |
| 首页 / 流程总览 | 从左到右看消息、客户和案子走到哪一步；点数字处理，拿不准就看“怎么用”。 |
| 待我处理 | 今天等你回复、核对收款和催交期的事都在这里；先处理最紧急的，办完回来更新待办。 |
| 消息 | 三个渠道的客户消息都在这里；先处理“要我回复”的，拿不准就打开会话点“AI 起草回复”。 |
| 待确认归属 | 还没认清是谁、属于哪笔生意的会话在这里；核对建议后确认归属，再去消息里回复。 |
| 草稿未发送 | 这里的草稿还没发送；先核对内容，再回到对应会话继续处理。 |
| 客户及详情 | 客户资料和往来都在这里；先认准同一个客户，再打开会话跟进需求。 |
| 案子列表 | 一笔生意就是一个案子，从询盘跟到收款和售后；先看卡在谁手上，再处理下一步。 |
| 案子详情 | 这笔生意的往来、报价和待办都在这里；先核对进展，再推进阶段，到账后亲自确认收款。 |
| 备货与发货 | 已收款后的备货、验货、发货和签收都在这里；先处理延期的，再记录每一步的进展。 |
| 供应商及档案详情 | 供应商发来的资料和报价在这里；先看出处和日期，缺什么就去消息里问。 |
| 产品及档案详情 | 产品规格、图片和报价出处在这里；先核对资料，再用来回复客户或报价。 |
| 缺资料 | 供应商和产品缺什么资料都列在这里；先找到缺项和出处，再向供应商补问。 |
| AI 运营员 | AI 运营员的开关和处理记录在这里；先看演练结果，再决定哪些事交给它做。 |
| 知识检索 | 有出处的资料在这里查；先核对来源和日期，再用到回复或案子备注里。 |
| 报价详情 | 这轮报价的产品、成本和运费在这里；先核对利润和有效期，再生成报价或形式发票草稿。 |
| 怎么用（后台） | 照着这八步，从消息一路做到收款和发货；看完一步就点按钮去处理。 |
| 供应商旧列表（后台） | 这里保留原来的供应商价格列表；先核对有效期，完整资料请看前台的“供应商”。 |
| 产品旧列表（后台） | 这里保留原来的产品成本列表；先核对可用成本，完整资料请看前台的“产品”。 |
| AI 员工（后台） | AI 员工最近做了什么在这里查；先核对记录，再决定是否采用建议。 |

## 首页数字：真实口径与去向

```text
新消息 → 待认人 → 潜在客户 → 已报价 → 待收款 → 待发货 → 售后
                   询盘/核实                      备货/验货
```

| 环节 | 已读源码、取数字段 | 点击的页面与筛选 |
| --- | --- | --- |
| 新消息 | `src/console/v3_inbox_api.py::summary`，`GET /api/v1/inbox/summary` 的 `ready`、`waiting_total`；只计待回复客户会话，排除供应商 | `/inbox?state=waiting&role=customer` |
| 待认人 | `src/console/roles/service.py::read`，`GET /api/v1/triage?status=pending` 的完整 `items.length`，是待确认归属建议数，含身份和案子归属 | `/triage?status=pending` |
| 潜在客户 | `src/console/deals/service.py::read`，`GET /api/v1/deals/summary` 的 `by_stage[].stage/count`，分列 `inquiry`、`sourcing`；仅进行中 | `/deals?status=open&stage=inquiry`、`/deals?status=open&stage=sourcing` |
| 已报价 | 当前没有“已发送报价”的全局数字；报价阶段含待发草稿，谈判阶段也不能证明已发，因此显示 **—** 和原因 | `/deals?status=open&stage=quoting`；另有谈判阶段入口 |
| 待收款 | 同案子汇总 `awaiting_payment` 阶段，仅进行中 | `/deals?status=open&stage=awaiting_payment` |
| 待发货 | `src/console/fulfil/nodes.py::board`，`GET /api/v1/fulfilment/board` 的 `items[].stage`，要求 `items.length === total`，分别统计 `paid`、`inspection`；不混入已发货、签收或售后。按阶段数，不虚称按实物出库数 | `/deals?status=all&stage=paid`、`/deals?status=all&stage=inspection`；另有“跟进出货节点”打开 `/fulfilment` |
| 售后 | 案子列表 `GET /api/v1/deals?status=all&stage=aftersales&page=1&page_size=1` 的 `total`，包括已成交案子，不拿第一页长度代替总数 | `/deals?status=all&stage=aftersales` |

已核对 `src/console/v3_quotes_api.py`：现有报价列表按单个案子读取，没有全局“已发报价”汇总或列表筛选。

未知、缺字段、缺阶段、读取失败或未接入显示「—」及原因；完整空记录显示 0。刷新失败隐藏本轮不可靠的旧数字。每个数字和「—」均可打开列表核对。各来源独立失败，不用渠道采集量、待办条数冒充销售阶段数。首页 60 秒更新；刷新按钮同时更新七步与渠道状态。

渠道仍使用 `GET /api/v1/overview` 的 `channels`、状态、`stages[].last_arrival_at`，正常状态也写最近消息时间；异常排前。原四环节检查、处理办法、模型预算和历史覆盖全部保留在可展开区域。具体待办、成交简报、提醒仍在 `/workbench`，首页提供明确链接。

## 改动文件

- `web/src/nav.ts`：五组前台顺序，单一档案主入口，旧列表及学习入口转后台，保留路径元数据。
- `web/src/layout/AppShell.tsx`、`web/src/layout/flow.css`：统一说明、旧说明去重、未知待回复提示、手机导航键盘操作、说明纳入消息页剩余高度。
- `web/src/ui/PageIntro.tsx`、`web/src/ui/page-intro.css`：统一说明组件与全部短句。
- `web/src/pages/Overview.tsx`、`web/src/pages/overview/BusinessFlow.tsx`、`web/src/pages/overview/flow.css`、`web/src/pages/overview/PageIntro.tsx`：首页业务线、来源独立统计、紧凑渠道状态、旧说明兼容导出。
- `web/src/pages/Guide.tsx`：与新入口衔接的八步说明、原供应商入口改指档案、移除说明里的演练代号。
- `web/e2e/smoke.mjs`、`web/e2e/overview.mjs`、`web/e2e/fixtures.flow.mjs`、`web/e2e/fixtures.deals.mjs`、`web/e2e/fixtures.inbox.mjs`：导航、全页面、流程各数字与实际筛选、未知和故障边界的虚构验收。原案子夹具的 `by_stage` 为空、收件箱夹具忽略阶段筛选，已补齐为真实接口行为，避免 URL 对了却结果不对。
- `docs/ui-development.md`：新增“流程与用词”；本文按任务交付要求新增在 `docs/`。

未改 App 路由表、客户页、消息页或其他业务页面内部代码；未改依赖清单、后端或数据库。**新表：无。新接口：无。** 除任务明确要求的本文和截图外，未改允许列表外文件。

## 验收命令与结果

`npm run build` 与 `npx tsc --noEmit` 均通过。完整 smoke 按设备拆跑：**桌面 72 / 72、手机 72 / 72，共 144 / 144 通过**（100 个普通页面/筛选/旧路径视图、22 个原总览边界视图、22 个新业务流程边界视图）。另一次首页专项 44 / 44 通过，与全量结果重叠，不额外累计。

数字和未知占位全部用键盘逐个打开，校验目标 URL、请求参数及列表中其他阶段没有混入；验证手机流程横向键盘滚动、菜单 Tab 循环和 Escape 焦点返回、每页说明只出现一次、桌面首屏能同时看到流程与三渠道状态、严格 CSP、无脚本异常和整页横向溢出。

```sh
cd web
npm ci --no-audit --no-fund --cache /tmp/flow-npm-cache
npm run build
npx tsc --noEmit
XDG_CONFIG_HOME=/tmp/flow-chrome-config XDG_CACHE_HOME=/tmp/flow-chrome-cache \
  CHROME=/tmp/ops-deals-browsers/chromium-1248/chrome-linux64/chrome \
  VIEWPORT=desktop OUT="$PWD/../artifacts/flow/desktop" node e2e/smoke.mjs
XDG_CONFIG_HOME=/tmp/flow-chrome-config-mobile XDG_CACHE_HOME=/tmp/flow-chrome-cache-mobile \
  CHROME=/tmp/ops-deals-browsers/chromium-1248/chrome-linux64/chrome \
  VIEWPORT=mobile OUT="$PWD/../artifacts/flow/mobile" node e2e/smoke.mjs
# 不设 VIEWPORT 时默认一次跑桌面与手机；本环境拆两次以缩短单进程时长。
# 只跑首页（含原总览边界与新增业务流程）时在命令前加 ONLY=overview。
```

浏览器使用 workspace 构建、loopback 静态服务、严格 CSP 和虚构夹具，未调用真实后端。所有未登记 API 和外部请求都会拦截失败。所有新增流程验收仅允许 GET，不调用模型或发送接口。截图及结果位于 `artifacts/flow/`，根目录 `result.json` 汇总两次完整 smoke；各设备目录保留原始结果和截图。`screenshots.md` 是合并截图索引，`home-desktop.png`、`home-mobile.png`、`nav-mobile.png` 可供老板先看。`flow.patch` 与 `files.sha256` 提供本任务 17 个文件的改动补丁和校验值。

按任务要求，后端目录分别尝试：

```sh
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/operator
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/roles
```

两项均因当前快照缺少 `console.media`，在收集阶段失败：`src/console/channels/alibaba.py` 导入 `from console.media import tokens` 报 `ModuleNotFoundError`。未改后端、未注入假模块掩盖问题；需要完整快照后原命令重跑，不能记为后端通过。

## 总指挥接线

1. “已报价”需要后端提供同一口径的已发报价待跟进总数和可筛选列表；本次没有可靠字段，因此按任务要求明示缺口。原有 `quoting`、`negotiating` 不能冒充已发总数。
2. 若需要按实物出货节点统计全部“待发货”，应由后端统一提供出货筛选口径及列表。本次按当前阶段分列，点击可精确对应，实际已出货事实在原节点页核对。
3. 各页面内部只准加说明的限制优先于跨页重写；本次通过布局统一实现说明，避免对并行任务冲突。保留页标题及正文中旧称“工作台”“商机沟通”“待整理的会话”，由对应页面任务/总指挥统一替换为“待我处理”“消息”“待确认归属”；不对业务对象“案子”另改名。首页不再嵌入旧工作台，总指挥若要回接今日队列，可在 `/workbench` 保持现有内容。
4. `docs/project-status.md` 与需求账本不在允许修改范围，留总指挥整合结果，勿据本次前端验收标记后端或真实数据已接通。
5. `/home/cz/.claude/skills/frontend-design/SKILL.md` 当前读取返回 Permission denied，未发现可读副本；已依照 `docs/ui-development.md` 和任务书列明的设计原则实现和验收。

未部署，未改 `/opt/imgw`、网络、代理、防火墙或服务；未调用真实模型，未发任何渠道消息，未写入真实客户、供应商、聊天或价格资料。

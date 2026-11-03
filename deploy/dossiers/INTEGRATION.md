# M2-A：供应商与产品档案运营台候选

2026-11-03。**本包未安装、未重启生产、未发送消息、未修改网络。** 交付为可审查源码、哈希补丁、安装/回滚脚本与隔离验收。生产数据、来源原文、真实截图及运行配置均不包含在本包。

## 交付与兼容

- `production.patch`：在已审核微信私聊包的候选基线上追加档案路由和今日待办投影，新增 `console/dossiers/` 与 `console/v3_dossiers_api.py`。
- `original.patch`：仅追加本任务功能，接受私聊包安装前的原始基线。两套基线/候选 SHA256 分别在 `production.*.sha256`、`original.*.sha256`。安装脚本自动识别；未知哈希拒绝安装。精确重装接受本包候选哈希。
- `web.patch`、`web.baseline.sha256`、`web.candidate.sha256`：本任务工作区前端改动。前端基线是任务快照，**不是生产静态资源的源码基线**。合并时只加自己的 import、Route、导航项，保留并行 AI 运营员的行；然后重新构建、验收并生成 `dist.sha256`。
- `importer/` 与 `importer.sha256`：逐字复制 `tools/dossier/import_candidate.py`、`prepare.py`、`schema.sql`，保持规范导入器的 `sd_*` 契约。`build_bundle.py` 可以重新生成后端包；它验证私聊包的整份原始/候选清单，不凭记忆重建字段或私聊逻辑。
- `install.sh` / `release.py`：备份代码、侧库及可选静态资源，导入、主动迁移、核对路由与哈希；不调用 systemctl、发送器或网络。
- `rollback.sh`：核验当前文件仍为本次候选/原基线后恢复代码和可选前端，保留私有档案与草稿。

开工时按要求实际执行过 `python3 deploy/wechat-private/prepare.py --destination <tmp>`，以其候选队列服务为开发基线。交付前只读复核发现私聊包已由独立流程安装，整份 `deploy/wechat-private/candidate.sha256` 均匹配。当前生产 `console/v3.py` 与队列服务匹配本包 `production.baseline.sha256`；本任务没有安装该私聊包。原基线与已装私聊包的安装/回滚都在临时目录验证。

推荐总指挥先完成私聊包验收，再安装本包。若选择原基线分支，之后的私聊包安装会遇到队列服务哈希变化，须先合并重建包；不可绕过哈希检查。`console/ops_queue/inbox.py` 在本包中不修改。`console/v3.py` 只增加两行挂载。并行 AI 运营员若已修改该文件，重新审核/生成补丁，不可直接覆盖或放宽哈希检查。

## 页面入口与使用

| 新入口（挂载在 `/app`） | 使用方式 |
| --- | --- |
| `/supplier-dossiers`、`/:id` | 搜名称/品类、按产品/物流/配套筛选；卡片显示产品族、有价/缺价配置、最新报价日期、缺口；详情看配置、每条报价的来源、可靠性备注和缺口 |
| `/product-dossiers`、`/:id` | 按品类分组、搜索型号/配置，图片来源明确的产品族优先；每个标准配置一行，各供应商一列。手机转为按配置分块展示，仍保留供应商名称 |
| `/dossier-gaps` | 按供应商、优先级、类型筛选；逐项展示问什么、为什么；选一家生成并存档问话草稿 |

旧 `/suppliers` 和 `/products` 保留往来单位、联系关系、旧产品候选/核准功能，各加到新档案的入口。新页也可回到旧工具。没有将旧业务 ID 与档案 ID 当作相同身份；旧导航与 URL 继续可用。新导航三项属于本任务明确新增，先前九项导航限制由本任务的新要求扩展。

今日待办的响应追加 `supplier_dossiers={ready,total,items}`，只列最多五项最优先缺口；原 `items/total/counts` 统计不变。前端独立卡片显示入口与缺口；侧库失效时标明不可读，不伪造为零。测试显式传入 `paths` 时，不会偷偷打开默认生产档案库，可显式注入测试档案服务。

金额直接显示 `amount_decimal` 字符串，不转 float/Number、不四舍五入。未取得明确金额显示“未报价”，保留 `amount_raw`；币种、税制、有效期和其他未知字段显示“未知”。有价/缺价按配置是否有明确金额统计，包含费用记录，不能当作现行可下单成本。单价、套装总额、运费、配置加价独立标识，多条报价保留日期与条件。未关联供货关系的格子显示“未确认供货”。对比只遵循 `sd_supplier_product` 与标准配置身份，不按同名或相似外观强并。

来源标签打开精确定位与字段片段，优先展示该位置的证据，其余可展开；消息显示归档原文、会话、北京时间与会话入口。当前收件箱的会话链接没有承诺滚到历史单条消息，精确时间与消息身份留在来源响应。NAS 显示原文件名及原定位，PDF 链接带 `#page=` 打开对应原页；Office 文件下载后按单元格/段落核对。图片走复用 `console/media/tokens.py` 的签名链接。原件缺失、哈希不符或当前供应商范围不可验证时，显示不可用并保留历史证据；不恢复附件、不联网下载、不签发任意路径。图片仅关联已登记的产品/报价证据，不把同文件夹其他图片自动当作该产品。

## API、认证与只读约束

前缀 `/api/v1/dossiers`：

| 方法 | 路径 | 参数/作用 |
| --- | --- | --- |
| GET | `/suppliers` | `q,type`（type 为产品/物流/配套） |
| GET | `/suppliers/{id}` | 配置、该供应商的报价、其他费用、可靠性和缺口 |
| GET | `/families` | `q,category`；返回产品族卡片与品类列表 |
| GET | `/families/{id}` | id 为 product_family 的 URL-safe Base64；含独立配置、供货关系和各家报价 |
| GET | `/gaps` | `supplier,priority,type,page,page_size`；默认每页 30，最多 100 |
| GET | `/sources/{id}` | 注册来源及逐字段证据，不返回 private_ref、任意文件路径或下载凭据 |
| GET | `/media/{token}` | 认证后的签名原件；重算哈希、核对范围；PDF inline，其余非图片按附件返回 |
| GET | `/suppliers/{id}/drafts` | 最近 30 份该供应商的已存档草稿 |
| POST | `/suppliers/{id}/drafts` | 表单 `priority,type`；合并该供应商在此筛选下的全部分页缺口 |

沿用 `inbox.http.authorised/recheck/forbidden/response`：密码 principal、旧 session、密码复核，`Cache-Control:no-store`、`nosniff`。POST 保留宿主同源/CSRF 中间件，并再次校验 session/header CSRF 与密码。未知/重复参数拒绝，错误不返回异常正文。GET 从不创建数据库或迁移，不构造 Archive Store、发送桥或模型 runner。侧库/消息库均 `mode=ro`、`query_only`，无活动 WAL 时使用 immutable 并检查快照是否变化；活动 WAL 必须已有 SHM，热 journal 拒绝读。

运行路径默认 `/var/lib/imgw/ops-app/dossiers/`；目录 `0700 imgw`，其中 `dossiers.sqlite3`、`drafts.sqlite3`、`runtime.json` 为 `0600 imgw`。仅隔离演练可通过 `OPS_DOSSIER_ROOT` 改路径；若正式使用非默认 `--state`，须同时配置该环境变量。运行配置在私有 `runtime.json` 注入 NAS 根、消息库、归档 blob 根及现有媒体 key 路径。GET 不创建媒体 key。

## 草稿表与后续 AI 第 1 级接续

`drafts.sqlite3` 独立于只读档案侧库，主动执行 `console.dossiers.drafts.migrate(path)`，`PRAGMA user_version=1`。DDL 定义在 `src/console/dossiers/drafts.py`。不修改原 `parties/products/offers`，也不写任何现有 outbox。

| `sd_question_draft` 字段 | 含义 |
| --- | --- |
| `draft_id TEXT PRIMARY KEY` | `sdq-` 加快照 SHA256 |
| `supplier_id TEXT NOT NULL` | 档案供应商身份；不能直接当原 party/contact ID |
| `basis_sha256 TEXT NOT NULL` | 缺口和来源快照哈希；与 supplier_id 构成 UNIQUE |
| `gap_ids_json TEXT NOT NULL` | 本次覆盖的全部缺口 ID，跨全部分页 |
| `source_refs_json TEXT NOT NULL` | 来源 ID、内容/文件 SHA256 |
| `gap_snapshot_json TEXT NOT NULL` | 缺口、问话、影响、优先级和来源的生成时快照 |
| `text TEXT NOT NULL` | 生成的完整问话草稿，合并重复问句 |
| `state TEXT NOT NULL CHECK(state='draft')` | 本模块唯一状态，不具有发送能力 |
| `created_at TEXT NOT NULL` | UTC ISO 时间 |
| `created_by TEXT NOT NULL` | 认证操作员用户名 |

相同供应商、筛选结果、来源版本重复生成返回同一行；条件或证据变化生成新快照。GET 列表不会迁移。未来 AI 运营员先读取此表，重新验证缺口/来源/当前联系人绑定及第 1 级各级开关，再走其独立审核和发送契约；本表既不是发送授权，也没有“已发送”状态。联系人映射、自动消费、去重回执和发送不在本任务中实现。

## 上线步骤（由总指挥另行执行）

1. 合并并行任务，核对最新现场哈希。将本包与候选库放在 `imgw` 可读的私有/源码目录。读私有批次交接，确定 NAS 根和来源可读性；不要将配置或候选库复制到 Git。
2. 检查两个 console 服务的实际源码/静态资源挂载及当前路由。必要时在审核后的新基线上运行 `build_bundle.py`；不能因为哈希不同就删校验。前端在最终合并工作区执行 `npm run build --prefix web`、隔离浏览器验收，再运行 `python3 deploy/dossiers/build_web_patch.py`，此命令更新待安装静态包的哈希。
3. 发布前执行项目规定的全站 smoke，确认没有发送；当前 `scripts/e2e_smoke.sh` 会写生产 dist 并管理演练进程，**本任务因不部署红线未执行它**。本次新页面的安全演练见下节；不能把它说成生产全站 smoke 已通过。
4. 隔离准备并核对路由，不写生产：

   ```sh
   python3 deploy/dossiers/prepare.py --destination /tmp/dossiers-review
   PYTHONPATH=/tmp/dossiers-review PYTHONDONTWRITEBYTECODE=1 \
     /opt/imgw/venv/bin/python deploy/dossiers/check_routes.py /tmp/dossiers-review
   ```

5. 审核与 smoke 通过后，在维护窗口执行安装。以下变量均须由总指挥设置为已审核的实际路径，备份目录必须不存在。默认 source/state/dist 路径与真实生产源码已核对；安装脚本不重启服务：

   ```sh
   sh deploy/dossiers/install.sh \
     --candidate "$DOSSIER_CANDIDATE" --backup "$DOSSIER_BACKUP" \
     --nas-root "$DOSSIER_NAS_ROOT" \
     --web-dist "$DOSSIER_REVIEWED_WEB_DIST"
   ```

   默认 `--root /opt/imgw/wechat --state /var/lib/imgw/ops-app/dossiers --owner imgw`；现有消息、blob、media key 默认路径见 `release.py --help`。`--web-dist` 必须精确匹配 `dist.sha256`，发布到 `/var/lib/imgw/ops-app/dist`（可显式指定 `--web-target`）。省略它只装后端，不会暗中发布网页。安装重复执行使用新备份目录，导入相同行为零新增；不兼容结构、主键内容冲突整事务回滚，不静默纠正旧批次。

6. 安装顺序：只读预检与完整路由构建 → 私有备份（SQLite backup 捕获一致快照）→ 代码 → 侧库规范导入 → **主动迁移草稿表并核对 user_version/列结构** → 路由/哈希/权限核对 → 可选静态包发布。备份 `release.json`、`routes.json` 与旧静态资源均在私有备份目录。发生中途失败，按该目录执行回滚；脚本不会重启或自动继续服务操作。
7. 总指挥核对安装后的 `production.candidate.sha256`（原基线分支改用 original）、真实路径与路由，按实际服务管理流程重启 console，完成登录、双端页面、图片/PDF、草稿、今日待办验收；不向任何人发送测试消息。

## 回滚

```sh
sh deploy/dossiers/rollback.sh --backup "$DOSSIER_BACKUP"
```

先核对所有待恢复文件和静态包，遇到后续版本/人工改动拒绝覆盖。恢复受影响代码及安装前静态资源；不动私聊包的其他文件，不回滚原业务库。保留新侧库、所有新增草稿和 runtime 配置（仍为私有权限），避免丢失生产后形成的追溯记录；备份中的侧库是审计恢复材料，不自动覆盖新的草稿。之后由总指挥按实际流程重启并复查旧入口。若安装后其他任务继续改动共享文件，先合并回滚，不可强行覆盖。

## 已验收与复现

- `tests/dossiers`：20 项隔离 pytest 通过。含双基线的安装、幂等重装、代码/静态回滚，旧表标记保留、冲突事务回滚、草稿迁移拒绝未知结构、全分页草稿快照、精确小数、鉴权/复核/CSRF、消息/附件/笔记/NAS、篡改/路径/范围拒绝、私聊账号绑定与待办独立失败。仅临时虚构 SQLite，禁止网络。
- 原 `tools/dossier`：5 项合成单元测试通过；`tests/ops_queue`：27 项原隔离回归通过。
- `npm run build --prefix web`（含 tsc --noEmit）通过。
- `web/e2e/dossiers.mjs`：真实组件、虚构 API、严格 CSP，1440×1000 与 390×844，卡片/详情/来源原图/精确小数/跨家对比/未报价/未知条件/过滤/翻页/全分页草稿/存档展开/今日待办入口及 404、503、断网、空态通过；无页面脚本错误、外部请求或整页横向溢出。截图/结果在 `private_out/dossiers-acceptance/synthetic/`。
- 真实私有库只读导入演练与双端浏览器完成；截图只在 `private_out/dossiers-acceptance/real/screenshots/`，其余报告同目录。真实输入未修改，重放零新增；不把真实来源内容写进此交接。
- `check_routes.py` 构造生产聚合路由时禁用 SQLite 与出站连接：无重复 path/method，9 个档案接口与原待办接口均存在。输出报告在私有验收目录。

复现合成检查：

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q tests/dossiers
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools/dossier \
  /opt/imgw/venv/bin/python -m unittest discover -s tools/dossier -p 'test_*.py'
npm run build --prefix web
cd web
# CHROME 指向已有 Chromium；受限环境可给 XDG_CONFIG_HOME/XDG_CACHE_HOME 设临时可写目录。
CHROME="$DOSSIER_CHROME" node e2e/dossiers.mjs
```

复现真实只读页面：`tools/dossier/rehearse_ui.py prepare --candidate <私有库> --output <private_out子目录>`，随后同目录 `serve --port <空闲回环端口>`；`web/e2e/dossiers-private.mjs` 通过 `DOSSIER_PRIVATE_STATE`、`DOSSIER_PRIVATE_BASE` 连接。演练只监听 127.0.0.1，随机本地 token，拒绝所有非 GET，不导入 `console.app`，关闭访问日志，无生产静态资源发布。所有输出目录必须含 `private_out`，不保存真实浏览器 trace/正文日志。退出演练进程即可，不影响生产服务。

## 尚未执行/边界

本包尚未上线，也未执行生产重启、安装后登录、全站发布 smoke 或与并行 AI 运营员的最终合并验收。M2 的日常增量 worker、联系人自动消费/发送、原件缺失恢复、产品同款的新增事实核验及商业条件补齐不在本模块中完成。现有读到的历史档案不等于已核准的当前采购承诺；来源签名校验也不等于来源内容真实。发布后请分别确认这些边界，不将页面上线写成 M2 全量完成。

当前工作区 `.git` 为只读占位目录，无法执行真实 git status/diff/commit；本任务未提交或推送。通过补丁清单、构建、隔离测试及私有脱敏扫描交接。

# R2-B 交付记录

本轮在独立快照 `/var/lib/imgw/sessions/codex/dev-jobs/20261101T075301Z-77f0d2b223c345e7/workspace` 施工。快照没有 `.git` 元数据；没有 commit、push、部署、网络配置修改、真实业务库查询或真实渠道/模型调用。下述 SHA-256 固定的是开工时仓库源码基线，**不是已核验的生产文件哈希**。

## 1. 文件清单与基线

修改：

| 文件 | 修改前 SHA-256 |
| --- | --- |
| `src/console/quotes/validation.py` | `54bf7c3dc4fa2f93424a314de46e7be6307d07ecb2baf9f4bdcadd143e9747b5` |
| `src/console/quotes/service.py` | `8e161a26eb198fb4e6d7bcf61d507f6567538c2c98ec1da3d182b9c62f421784` |
| `src/console/quotes/text.py` | `d50ae521bdb19d47fd31a535dadc9c398a0939261eb84f702f337bb33d0da477` |
| `src/console/quotes/calc.py` | `aee349c33edc4886c5a9dd919a4928b9f7ff29e21189ae2c9217ca27f92e099e` |
| `src/console/v3_quotes_api.py` | `218ddf94d12ad1288b0ea6de730df582c90deeefd2bd69bc6325c0012a8b7e13` |
| `web/src/pages/QuotePage.tsx` | `27f3e046d1a05198c9123340373dfb6ed97d80c635ecea4478af55be35691b78` |
| `web/src/pages/Prices.tsx` | `0d2bca4aa9ce167ad96fba580cb12168c5ab2d4525ef23f300e3c4aa35a62800` |
| `web/src/quotes.ts` | `74e1b2c0056bc4a8287d5514e8bfa9aed6d8a0a89bbb735cae49b5b3f6003678` |
| `docs/quotes.md` | `b8cb86522949bebcb8a67b0e8fdd333f2b83b73262ee100131f5c178e0b1cea8` |
| `docs/freight-compare.md` | `467868661c02869968417ac6b9c2fb44e9a4f2cf9f2a3319a1cab2662554f42a` |
| `tests/quotes/fixtures.py` | `4f172d53de5e143fc615b72e36d00d3492ca7ef718686b15f8262ff82d0cba08` |
| `tests/quotes/test_service.py` | `ebb9935d3ae48772cf39382573776fc5e7303fcfba31ae6e4a89e614c0b85c00` |
| `tests/quotes/test_api.py` | `66d80c226ab6a095a545c3017453fa9d79b266132c2a75f406717d678c87e26a` |

新增：

- `src/console/quotes/documents.py`
- `src/console/quotes/storage.py`
- `tests/quotes/test_workflow_r2b.py`
- `docs/r2b-delivery.md`

实现按数量→运费→配套→条款/PI→母语顺序在同一负责人下集成。新增测试文件由独立代理编写，另有只读安全复核。

- 数量/单位：复用既有更新接口；24→30 后重算产品、销售小计、冻结运价及关联数量的配套；未知数量的 1 仍是占位，不能发出。人工总费用或单位变更须复核。
- 运费：替换与追加明确区分；替换、费用、协商、事件、审计在同一档案事务完成；同请求幂等，失败保留旧选择。两个目的国/货代、外币原币显示、跨方式、期限均有虚构断言。
- 配套：两家 vendor 明确选择，不补成 supplier；询价单位/产品/单价/单位/期限/会话来源保留快照，正反两方向阻断产品与配套重复计价。复制轮数量引用改为新行，旧轮不变。
- 条款：本轮确认 30% 定金、70% 发货前付清和交付范围进入文本；中文 PI 为非正式预览，缺公司开票信息逐项提示；审阅保存版本哈希、正文、人和时间，保留历史。
- 母语：只用 Lang 公共 read/write，核对本案客户身份；人工锁定和未知母语有明确行为。母语与中文回译保留型号、DMX、单位、金额、完整有效期；报价/目标母语变化旧稿失效，不回退英文，不发送。

## 2. 测试环境、命令与结果

当前身份为 imgw，尝试 `sudo -n -u imgw true` 失败，按任务约定直接以当前身份运行。原文：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

干净快照最初运行要求的 quotes 命令，收集阶段失败：

```text
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/quotes/test_api.py
ERROR tests/quotes/test_migration.py
ERROR tests/quotes/test_service.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!!
3 errors in 0.73s
```

仅从开发副本 `/srv/imgw-dev/ops/src/console/media/` 只读复制四个源码到 `/tmp/r2b-runtime/console/media/`，未复制媒体、配置、凭据或业务库，未写回共享源码。文件 SHA-256 如下（不能证明与缺件快照同一提交）：

| 临时依赖 | SHA-256 |
| --- | --- |
| `__init__.py` | `ced5f3a0f932bbaaa6140ae8ceb081161800f896b998260729deb97fef5880f8` |
| `tokens.py` | `25440e4ce6ad69f2e952aaa2c1d55e2d0070807f9731f898faba828ee58ed184` |
| `files.py` | `7c6a84e3819dc6a50e3e11c8e9f92ba6e5f941c656ba6eda9b6e808b5a21917b` |
| `alibaba.py` | `282823a19e141425fa2f352eccc9dff2ab019721f67d06a48cc13e2811f76c93` |

`/tmp/r2b-runtime/sitecustomize.py` 仅补包路径，并禁止真实网络及非临时 SQLite。最初临时隔离白名单漏掉 `file:///tmp/` URI，已修正，未放宽到生产路径。当时末行原文：

```text
25 failed, 9 passed, 151 subtests passed in 2.29s
AssertionError: R2-B 测试仅允许临时 SQLite
```

迭代中旧测试未填新增必填条款/目的国/具体单位、仍使用旧自动累加入口，曾失败；均改成显式虚构输入，保留原算价、状态、保密和审计断言。反向重复配套在写入前拒绝后，相应新增测试最初仍期望只在计算时阻断，修正断言为 BadRequest 且全事务不变。该次末行原文：

```text
1 failed, 54 passed, 189 subtests passed in 22.09s
console.archive.validation.BadRequest: 该产品已计入配套费用，不能再计入产品成本；请保留一种计价方式
```

最终实际命令（各套件独立进程，无固定端口）：

```sh
env OPS_MEDIA_KEY_PATH=/tmp/r2b-runtime/media-test.key PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/tmp/r2b-runtime:src:tests/quotes" /opt/imgw/venv/bin/python -m pytest tests/quotes -q -p no:cacheprovider --tb=short
env OPS_MEDIA_KEY_PATH=/tmp/r2b-runtime/media-test.key PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/tmp/r2b-runtime:src:tests/extract" /opt/imgw/venv/bin/python -m pytest tests/extract -q -p no:cacheprovider
env OPS_MEDIA_KEY_PATH=/tmp/r2b-runtime/media-test.key PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/tmp/r2b-runtime:src:tests/lang" /opt/imgw/venv/bin/python -m pytest tests/lang -q -p no:cacheprovider
env OPS_MEDIA_KEY_PATH=/tmp/r2b-runtime/media-test.key PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/tmp/r2b-runtime:src:tests/archive" /opt/imgw/venv/bin/python -m pytest tests/archive -q -p no:cacheprovider
env OPS_MEDIA_KEY_PATH=/tmp/r2b-runtime/media-test.key PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/tmp/r2b-runtime:src:tests/deals" /opt/imgw/venv/bin/python -m pytest tests/deals -q -p no:cacheprovider
```

| 套件 | 最终通过 | 失败 | 额外通过 subtests |
| --- | ---: | ---: | ---: |
| quotes（含新增 24 个虚构工作流测试及新增 API 测试） | 59 | 0 | 189 |
| extract | 358 | 0 | 0 |
| lang | 73 | 0 | 0 |
| archive | 40 | 0 | 46 |
| deals | 32 | 0 | 38 |
| 合计 | 562 | 0 | 273 |

这些全绿结果属于补齐上述临时源码后的组合，不能称干净快照已全绿。所有数据均虚构；Lang 使用真实公共服务、临时库和假 runner，只证明契约与保护，不证明真实模型翻译质量、真实价格或实际成交可用。原有 archive 迁移断言仍通过，没有修改 migration 文件。

前端在 `web/` 运行：

```sh
npm run build
```

最初本地无 node_modules，原文 `sh: 1: tsc: not found`。将开发副本 node_modules 只读复制到 `/tmp/r2b-web-deps/node_modules`，由工作区忽略的 node_modules 临时链接引用，未安装包或改 lockfile。最初临时根目录命名不是 node_modules，导致嵌套依赖解析失败，原文含 `error TS2724: '"@tanstack/react-query"' has no exported member named 'QueryClient'. Did you mean 'useQueryClient'?`；修正临时目录布局后同一完整构建通过，未改共享页面或放宽类型检查。最终：`tsc --noEmit && vite build` 成功，`2134 modules transformed`、`built in 442ms`。未运行 `tsc -b`、e2e_smoke、监听服务或浏览器演练。

## 3. 需要总指挥合并的共享文件改动

- `web/src/data/requirements.json`：将 WG-06A、WG-07、WG-08、WG-09A、WG-03 记为“本地虚构数据测试通过、待界面及真实业务验收”，列明独立状态库和临时媒体依赖；本轮未修改账本。
- 快照打包流程：补齐本版本 console.media 源码（WG-13）。本文给出临时借用代码的精确哈希，总指挥须核对版本，不能把临时补依赖当作正式合并。
- 后续全量联调的测试夹具应同步本轮必填契约：例如 `tests/roles/conftest.py::Env.good_round` 目前人工 freight 未带目的国/方式、没有本轮条款；需要显式设置虚构目的国和 mode，再确认 terms。该共享夹具本轮未修改，也未声称 roles/fulfil 全套通过。
- 没改 README、docs/master-plan.md、lang/extract/其他任务业务模块。没有 web 路由表、导航表或 rehearsal_server.py 改动；新增 API 已由原 v3_quotes_api builder 挂载，无需演练脚本追加逻辑。

## 4. 部署步骤与回滚（仅方案，未执行）

1. 总指挥先核对上表仓库基线与待部署实际源码；不一致时逐项合并，不能直接覆盖。核对 console.media 与 P1 语言服务版本，确认保留宿主认证与 CSRF。
2. 在获准维护窗口备份当前报价后端、相关前端静态资源、archive.sqlite3（含一致性 WAL 处理）及已存在的独立 quote-state.sqlite3；记录实际生产文件 SHA-256。本文没有读取生产文件来替代这一步。
3. 审查后安装本轮 7 个报价 Python 源码（含新 documents/storage）及构建资源。API 仍由既有 builder 挂载。配置 `OPS_QUOTES_STATE_PATH=/var/lib/imgw/ops-app/quote-state.sqlite3` 或调用参数；不得指向 archive.sqlite3。以应用用户权限设置父目录 0700、文件 0600；状态表在第一次明确条款/审阅操作的独立事务中惰性创建。
4. 不执行新档案迁移，不改变 archive 版本。总指挥统一重跑隔离测试、正式快照依赖检查、真实业务只读演练及全页面冒烟，再按既有程序重启/核对 22 条报价路由。
5. 人工验收虚构流程：24→30、两货代替换与重复请求、不同目的国、配套来源/复核、30%/70%、PI 缺项、西语母语/中文回译。真实模型与渠道验收由总指挥另外安排，不能向客户发送测试消息。

回滚：停止新增报价操作，恢复已备份的后端与前端资源并按原程序重启；保留独立状态库以便追溯，不自动删除或重建，也不回滚/覆盖已经发生的新业务数据。旧代码不消费新状态库，但会失去本轮核价保护，所以回滚后暂停对外报价并人工复核，不能把旧版恢复视作业务验收通过。若需恢复数据库，必须由总指挥依据业务写入窗口核对后单独决定，不能用上线前旧库直接覆盖新增成交记录。

## 5. 安全自查

- 越权：沿用 principal/session、POST 前及 GET 返回前 recheck；宿主 CSRF 不改。子费用/产品必须属于本轮；母语会话须关联本案客户与 identities，不能取供应方或其他客户会话；已发轮不接受价格、数量或条款修改。
- 路径穿越：HTTP 不接受数据库路径；新状态库拒绝同 archive 路径、符号链接及父路径穿越，新文件 0600。来源引用只展示/链接既有收件箱，不抓 URL、不读附件或任意路径。
- 注入：新增 SQL 值均绑定参数，表名仅现有白名单；金额和数量用原严格解析，模型只收白名单文字，不执行模型输出，保留片段须双向校验；系统请求标记禁止人工正文注入。
- 泄露：客户文本/PI/翻译输入不读取内部成本、利润、供应方名称或备注；审计仅字段名；未知异常固定中文错误，不回显路径、异常、模型输入或响应。测试用虚构敏感标记断言未混入客户文本。
- 重复发送：本模块没有发送器或自动发送，不自动重试；请求未知结果先读回核对。货代替换/追加的重复请求不重复成本、协商、事件或审计，事件失败测试断言整体回滚。

## 6. 未完成与边界

未部署、未运行真实数据演练/全页面冒烟/浏览器交互，未验证真实翻译模型、真实价格、公司开票资质或渠道交付。PI 仅中文非正式预览，没有文件导出；WG-09B 的实际发送版本、收件人和渠道回执绑定不在本任务。客户期限跨成本期限采用直接阻断，没有人工锁价旁路。自动税费核算、包装真实性、数量单位自动换算和未结构化人工项目的重复识别仍需人工核对。中文回译要求 P1 阅读语言为 zh-CN；不满足时明确提示先调整设置。

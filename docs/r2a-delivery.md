# R2-A 成交口径与履约交付

状态：开发候选，未部署、未 commit/push。当前工作位置是用户提供的独立快照，快照无 `.git` 元数据，不能报告提交号。没有读真实业务库或发送消息；所有业务验证仅使用虚构数据、临时 SQLite、SSR 和假服务。

## 已完成的行为

- WG-02：新案及付款前普通 create/update 无法跳到 paid 及以后或设 won。收款确认在原单写者事务内记确认人、时间、事件和成交，失败整体回滚；重复 confirm 幂等。接受报价、pending、退款不成交。首笔有效定金/全款固定成交，尾款需显式达标；页面不再提供无效的首笔否决。关联报价轮与币种差异说明可核对。
- WG-14：案子详情、报价、参与方、付款、履约、问题、说明草稿、履约看板和返单线索区分读取失败与成功空列表，均有说明和重读入口。408、5xx、断线、坏响应按结果未知处理，先 GET 核对且锁住再次提交；读回成功仍需人工核对解锁，零自动重放。手工录入保留已建案入口，响应丢失先查候选案。
- WG-10A：确认付款后可人工依次推进 inspection/shipped/delivered/aftersales，保留 won/won_at；won 不显示“重新打开”。建议消费真实 `{stage,reason}`，按钮由人确认；验货通过不自动发货。节点可从本案参与方选择负责单位并显示期限，不另生成履约自动待办。
- WG-12 基础：返单线索可展开上次接受售价、反馈及供应商来源，人工调用已有复制接口创建 inquiry/open 草稿。新案不复制内部成本、旧价、收款、节点、会话或供应方参与身份。供应商当前档案关联明确标“需核对”，不冒充历史供货快照。

兼容边界：不新增表、字段或档案库版本，不批量迁移/回退旧 won，不补造历史付款和成交时间。保留原接口的显式人工回退行为；这与退款/作废自动回退不同，页面没有新增回退入口。“首笔”只认仍有效的 confirmed 达标款；唯一旧达标款作废后，新定金/全款重新按首笔规则成交。

## 文件清单与基线

修改 25 个文件：

- `docs/deals.md`
- `docs/fulfil.md`
- `src/console/deals/rules.py`
- `src/console/deals/service.py`
- `src/console/fulfil/nodes.py`
- `src/console/fulfil/payments.py`
- `src/console/fulfil/reorder.py`
- `src/console/v3_deals_api.py`
- `tests/deals/test_api.py`
- `tests/deals/test_rules.py`
- `tests/deals/test_service.py`
- `tests/fulfil/conftest.py`
- `tests/fulfil/test_payments.py`
- `tests/fulfil/test_roles.py`
- `tests/fulfil/test_service.py`
- `tests/quotes/test_service.py`
- `tests/roles/conftest.py`
- `tests/roles/test_roles_brief.py`
- `tests/roles/test_roles_patrol.py`
- `tests/roles/test_roles_safety_cli.py`
- `web/src/fulfil.ts`
- `web/src/pages/DealPage.tsx`
- `web/src/pages/FulfilBoard.tsx`
- `web/src/ui/DealFulfil.tsx`
- `web/src/ui/ManualDeal.tsx`

新增 5 个文件：

- `web/src/readback.ts`
- `web/src/ui/ReadState.tsx`
- `web/tests/r2a-flow.test.tsx`
- `docs/r2a-delivery.md`
- `docs/r2a-file-baselines.json`

每个修改文件的开发基线与候选 SHA-256 见 [文件基线清单](r2a-file-baselines.json)。该清单不是现场生产哈希；部署前需核对现场基线，不能直接覆盖不同版本。`npm ci` 只安装既有锁文件依赖；`npm run build` 在快照生成 `web/dist/`，它是构建产物，不是额外业务源码。没有修改 package/lock、路由表、导航表、演练脚本、需求账本、README、master-plan 或其他任务的业务模块。

## 测试命令与最终结果

规定的 sudo 方式受容器 no-new-privileges 限制，改用当前身份。原始快照缺 `console.media`，标准 `PYTHONPATH="src:tests/<套件>"` 命令会收集失败；没有把缺失模块私自合并进仓库。为完成隔离验证，只将已审阅的开发目录 `/srv/imgw-dev/ops/src/console/media/{__init__,tokens,files,alibaba}.py` 复制到 `/tmp/r2a-test-deps/console/media/`；临时 `console/__init__.py` 用 pkgutil.extend_path 继续加载本快照源码，`sitecustomize.py` 仅 `import console`，避免 CLI 在 sys.path 首位插入 src 时丢失该覆盖。没有复制密钥、配置或业务数据。这是测试依赖覆盖，不能认定原快照自包含。

在仓库根目录执行，各套件分别设置自己的路径：

```sh
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r2a-test-media.key PYTHONPATH="/tmp/r2a-test-deps:src:tests/deals" /opt/imgw/venv/bin/python -m pytest tests/deals -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r2a-test-media.key PYTHONPATH="/tmp/r2a-test-deps:src:tests/fulfil" /opt/imgw/venv/bin/python -m pytest tests/fulfil -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r2a-test-media.key PYTHONPATH="/tmp/r2a-test-deps:src:tests/quotes" /opt/imgw/venv/bin/python -m pytest tests/quotes -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r2a-test-media.key PYTHONPATH="/tmp/r2a-test-deps:src:tests/roles" /opt/imgw/venv/bin/python -m pytest tests/roles -q -p no:cacheprovider
cd web
./node_modules/.bin/vitest run tests/r2a-flow.test.tsx --no-file-parallelism
npm run build
```

| 套件/检查 | 最终结果 |
| --- | --- |
| deals | 36 passed，另 62 subtests passed |
| fulfil | 174 passed |
| quotes | 34 passed，另 189 subtests passed |
| roles | 238 passed |
| 前端 SSR 与假 fetch | 53 passed，0 failed，0 skipped |
| npm run build | 通过（tsc --noEmit && vite build） |

付款单项最近一次另跑 `tests/fulfil/test_payments.py`：37 passed，已包含在 fulfil 的 174 项内，不重复计数。没有运行 e2e_smoke.sh，没有启动服务、固定端口或连接真实渠道；没有使用 tsc -b。既有 quotes/roles 受新规则影响的夹具改成明确历史数据注入，保留无收款历史成交的报价兜底断言；roles 的无关 lang/pipeline builder 只在测试里隔离，演练脚本不改。

## 需要总指挥合并的共享文件改动

- `web/src/data/requirements.json`：由总指挥登记 WG-02、WG-14、WG-10A、WG-12 基础已完成开发候选；真实浏览器/真实业务/部署仍待验收。本任务没有修改该文件。
- 补齐干净快照的既有同版本 `console.media` 开发依赖（WG-13），移除对本机 `/tmp` 覆盖的依赖后再跑标准命令。不能将临时测试覆盖当成生产补丁。
- `DealPage.tsx` 仅在原查询、按钮、挂载参数及参与方表单附近集中修改；R2-C 的少量挂载请在这些局部改动之上合并，避免整文件覆盖。
- 路由表、导航表、`scripts/rehearsal_server.py` 新增/修改位置：无，本任务全部复用既有路由与挂载。README/master-plan 无所需业务改动。

## 部署步骤与回滚办法（仅供总指挥执行）

1. 审查本候选，按基线清单只读核对现场源码。现场与快照基线不符先合并差异，不强行覆盖。确认宿主现有 deals/fulfil builder、认证及 CSRF 均已挂载，并补齐本快照缺失的媒体开发依赖。
2. 总指挥统一完成真实数据只读演练、真实浏览器桌面/手机走查与既定上线冒烟。此次没有执行这些检查，不能据单元测试直接上线。
3. 在维护窗口备份对应源码、现有前端产物和业务库（SQLite 使用一致性备份方式）；保存现场备份哈希。构建本候选前端，审核差异后安装六个后端文件：deals/rules.py、deals/service.py、fulfil/payments.py、fulfil/nodes.py、fulfil/reorder.py、v3_deals_api.py。运行位置以总指挥现场核对为准，项目文档中的 console 基线位于 /opt/imgw/wechat/console。
4. 按 docs/web-app.md 既有约定把 web/dist 作为一套产物装入 /var/lib/imgw/ops-app/dist，由现有 /app/ 宿主提供。前后端一起切换，保留宿主认证与 CSRF，不新增监听端口、渠道配置或数据库迁移。由总指挥重启现场确认的控制台服务，并检查路由、读取失败提示及旧 won 案可读。
5. 回滚时恢复同一批备份源码和完整旧前端产物，重启同一宿主并复查。没有本任务库结构升级可回滚；不要覆盖部署后人工登记的款项、事件或案子数据库，未知操作结果必须保留并核对。回滚旧代码会恢复旧付款旁路，回滚期间应停用相关写入口，直至修正候选重上。

## 安全自查与未完成项

- 越权：复用宿主鉴权/CSRF；deals 新增表单解析后紧邻写入的 recheck；操作人从认证 principal 取，不接受前端伪造。API/事务回滚测试覆盖。
- 路径穿越/注入：未新增文件路径输入或上传；新增返单投影按本案参数绑定 SQL，不拼接用户值。页面使用 React 文本输出，无 HTML 注入；查询文字通过现有 qs/URL 编码。
- 泄露：未读取、打印或复制任何真实密钥/令牌/Cookie/授权头值；未读取真实业务库，未把聊天、价格或客户数据写入仓库/日志。返单投影不返回成本/内部处理备注/其他案子的反馈，草稿不复制旧成本。
- 重复发送/写入：没有新增发送路径，真实渠道零调用；确认付款幂等，自动 mutation retry 关闭。未知结果只读回，不自动重放；已有复制接口每次显式 POST 仍会建一案，没有新增跨刷新/多窗口幂等令牌，人仍需核对已有草稿。
- 未完成：真实浏览器弹窗/复选框/导航点击、真实数据走查、生产部署均未执行。本机无可执行 Chromium；SSR 与假 fetch 不等于浏览器验收。多供应商拆分履约（WG-10B）、WG-12 扩展售后责任/等待提醒和自动返单均不属本次基础任务。

## 过程中失败原文摘录

以下保留本轮实际失败，后续已按说明修正环境或测试夹具。不是把失败改称通过。

初次 npm ci 使用默认缓存写入受限，改用 `npm ci --ignore-scripts --cache /tmp/r2a-npm-cache` 后成功：

```text
npm error code EROFS
npm error syscall open
npm error path /var/lib/imgw/sessions/codex/.npm/_cacache/tmp/d5ad0912
npm error errno -30
npm error rofs EROFS: read-only file system, open '/var/lib/imgw/sessions/codex/.npm/_cacache/tmp/d5ad0912'
```

sudo 规定命令被环境拒绝，原文：
```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

当前身份原命令首跑因快照缺 `console.media` 收集失败（3 errors），随后使用根代理准备的、经审核的 /tmp 只读媒体源码测试覆盖路径；没有改仓库缺失媒体模块。

roles 首跑失败末尾原文（之后已按根代理同意修测试环境/基线隔离）：
```text
FAILED tests/roles/test_roles_safety_cli.py::test_cli_subprocess_defaults_to_preview_and_requires_apply
FAILED tests/roles/test_roles_safety_cli.py::test_rehearsal_auto_mounts_quotes_and_roles_on_copied_archive
2 failed, 236 passed in 29.57s
```

两处原始原因：子进程覆写 PYTHONPATH 丢失 console.media 覆盖，且 scripts/roles_run.py 会将 src 插入 sys.path 首位；演练既有新增语言/管道 builder 令实际路由 82 与测试旧预期 70 不符。根代理同意后另在 /tmp/r2a-test-deps/sitecustomize.py 仅写入 `import console`，使子进程先加载已 extend_path 的临时 console 包；没有修改 CLI 源码。这说明快照依赖并非自包含。


roles 第二跑仍失败的末尾原文（随后加临时 sitecustomize）：
```text
FAILED tests/roles/test_roles_safety_cli.py::test_cli_subprocess_defaults_to_preview_and_requires_apply
1 failed, 237 passed in 26.51s
```

首跑两处错误原文要点：
```text
E       AssertionError: assert (1 == 0)
E        +  where 1 = CompletedProcess(args=['/opt/imgw/venv/bin/python', '/var/lib/imgw/sessions/codex/dev-jobs/20261101T075301Z-baec85c9d0..., line 6, in <module>\n    from console.media import tokens\nModuleNotFoundError: No module named \'console.media\'\n').returncode

tests/roles/test_roles_safety_cli.py:123: AssertionError
E       AssertionError: assert 82 == 70

tests/roles/test_roles_safety_cli.py:146: AssertionError
```

## 首轮完整测试：18 失败，32 通过

命令（工作目录为快照的 `web/`）：

```sh
./node_modules/.bin/vitest run tests/r2a-flow.test.tsx --no-file-parallelism
```


原始失败汇总：

```text
 ❯ tests/r2a-flow.test.tsx (50 tests | 18 failed) 611ms
   ❯ 读取失败与真实空列表 (23)
     × 收款权限拒绝不会变成没有记录或允许再次登记 62ms
     × 收款服务不可用不会变成没有记录或允许再次登记 7ms
     × 收款读取超时不会变成没有记录或允许再次登记 5ms
     × 履约权限拒绝会明确显示错误和重读入口 9ms
     × 履约服务不可用会明确显示错误和重读入口 14ms
     × 履约读取超时会明确显示错误和重读入口 18ms
     × 问题权限拒绝不会当成没有问题 10ms
     × 问题服务不可用不会当成没有问题 7ms
     × 问题读取超时不会当成没有问题 10ms
     × 履约看板权限拒绝不会当成没有成交 7ms
     × 履约看板服务不可用不会当成没有成交 5ms
     × 履约看板读取超时不会当成没有成交 5ms
     × 返单线索权限拒绝不会静默隐藏 11ms
     × 返单线索服务不可用不会静默隐藏 30ms
     × 返单线索读取超时不会静默隐藏 9ms
     × 案子详情权限拒绝显示恢复读取入口 14ms
     × 案子详情服务不可用显示恢复读取入口 10ms
     × 案子详情读取超时显示恢复读取入口 7ms
```

原始关键断言：

```text
 FAIL  tests/r2a-flow.test.tsx > 读取失败与真实空列表 > 收款权限拒绝不会变成没有记录或允许再次登记
 FAIL  tests/r2a-flow.test.tsx > 读取失败与真实空列表 > 收款服务不可用不会变成没有记录或允许再次登记
 FAIL  tests/r2a-flow.test.tsx > 读取失败与真实空列表 > 收款读取超时不会变成没有记录或允许再次登记
AssertionError: expected '收款' to contain '没能读到'

Expected: "没能读到"
Received: "收款"

 ❯ tests/r2a-flow.test.tsx:54:25
     52|     failed(client, ["payments", 41], error);
     53|     const html = render(client, <PaymentsPanel dealId={41} />);
     54|     expect(plain(html)).toContain("没能读到");
       |                         ^
     55|     expect(plain(html)).toContain("重新读取");
     56|     expect(plain(html)).not.toContain("还没有收款记录");
```

原始末尾：

```text
 Test Files  1 failed (1)
      Tests  18 failed | 32 passed (50)
   Start at  01:01:34
   Duration  2.35s (import 37%, transform 31%, tests 31%, worker 1%)
```

原因与修正：测试预填的错误缓存首次被 SSR 挂载使用；React Query 的默认 `retryOnMount` 将无数据的错误状态重新投影为等待状态。仅在测试使用的 QueryClient 增加 `retryOnMount: false`，让 SSR 检验已经读取失败的页面状态，随后同一组 50 项全部通过。没有修改业务 UI 来消除这些断言失败。


# r11 运营整合候选

本交付为工作区候选，**未部署生产**。按总纲第 5 节和本次任务执行；未外发客户/供应商消息，未确认真实收款，未改网络分流，未运行 `tsc -b`，自测未访问生产 8791。工作区 `.git` 没有可用元数据，无法给出分支、提交或 Git diff；未提交推送。`deploy/kb`、`deploy/icbu` 未改。

## 改动文件与行为

| 文件 | 整合结果 |
| --- | --- |
| `web/src/App.tsx`、`nav.ts`、`api.ts` | 数字单位详情路由、只读 `/outbox` 路由与元数据、草稿查询类型；保留原九个运营台导航入口，商机沟通提供草稿箱入口 |
| `web/src/pages/Inbox.tsx`、`pages/inbox/Operations.tsx`、`ops/inbox.ts` | 优先 conversation，兼容 c；客户归属使用真实 party ID；完整建案深链接与原快速建案同时保留；北京时间提醒、当地时间、PageIntro |
| `web/src/ui/PartiesWorkbench.tsx` | `/customers/:id`、`/suppliers/:id` 与旧 query 参数兼容；时区编辑、当地时间、真实客户 ID 的完整建案入口、统一会话链接 |
| `web/src/pages/Outbox.tsx` | 只读展示 inbox/parties 演练草稿，明确“草稿未发送”；没有发送动作 |
| `web/src/ui/CounterpartTime.tsx`、`format.ts`、`archive.ts` | IANA 当地时间与夏令时；单一民用时区国家可推导，多时区国家须明确时区；缺失/无效不显示；普通时间与业务日期使用北京时间 |
| `web/src/deals.ts`、`pages/DealPage.tsx`、`pages/deals/{NewDeal,Conversations}.tsx` | 规范的会话深链接、案子客户的国家/时区、完整建案使用合并后的单位列表 |
| `web/src/pages/{Deals,QuotePage,Products,FulfilBoard,Search,Roles,Inspiration,Soon}.tsx` | 补说明条；Overview/Workbench/Guide 原说明条保留 |
| `web/src/pages/{Triage,Pipeline}.tsx`、`pages/overview/queue.css` | 读取队列参数、定位、聚焦和高亮；归属条目已消失时提示核对 |
| `web/src/ui/{ManualDeal,DealFulfil,WaitingPanel,DemandCandidates}.tsx` | 北京业务日期、北京时间手工催问、规范会话链接 |
| `web/src/data/requirements.json` | A-W2-inbox 与 R11 的 W1/W8、W4/W5、W3/W6/W7、时区需求及候选状态 |
| `src/console/customers/{identity,operations}.py` | 共享可撤销合并投影；旧资料 URL 投影到保留客户；附加 profile 时区列及校验 |
| `src/console/inbox/{workflow,model}.py`、`v3_inbox_api.py` | 收件箱尊重真实身份与合并映射；显式指派支持 party ID；保留原 FK；消息版本纯函数供队列复用；补参数类型校验 |
| `src/console/deals/service.py` | 案子客户与参与方名称尊重合并；新建案子归到保留客户，历史归属保留，撤销恢复 |
| `src/console/ops_queue/{service,migrate}.py` | 队列尊重合并、已处理及未到期提醒；新消息恢复提醒；共享显式迁移命令 |
| `src/console/v3_ops_{drafts,r11}_api.py` | 草稿箱只读 API 与独立宿主挂载，避免重复挂载原 builder |
| `src/console/archive/store.py` | 串行化同进程 WAL 设置、迁移与写事务，修复并发首次建案出现的 database locked |
| `scripts/rehearsal_server.py` | 真实本地数据只读快照；副本位于 private_out；所有新增写接口显式临时库；禁真实发送/登录操作；脚本直接服务工作区 dist |
| `web/e2e/fixtures{,.base,.inbox,.parties,.queue,.deals}.mjs` | 基础夹具只安装一次，四块可变夹具组合；队列、建案、草稿共享状态；未知接口仍 501 并使测试失败 |
| `web/e2e/{smoke,ops-inbox,ops-queue}.mjs`、`ops-day.mjs`、`rehearsal.mjs` | 原视图回归、操作脚本适配、新工作日流程和真实只读演练模式 |
| `tests/inbox/{test_patch,test_r11,test_snapshot}.py`、`tests/ops_queue/test_queue.py` | 新补丁、跨模块合并/撤销/草稿/队列、只读鉴权、稳定 WAL 快照与演练补丁测试 |
| `tests/deals/{test_api,test_rules,test_service}.py`、`tests/{quotes,fulfil,inbox_api}/test_api.py` | 按真实新增契约更新旧断言，详见下面逐项说明 |
| `tests/r11_{guard,regressions}.py` | 隔离库与禁网络；每套旧测试独立进程；公开快照缺失的可选模块由只读安装代码补足，含 CLI 子进程 |
| `patches/ops-r11/*` | 对准真实生产的合并补丁、SHA、父路由候选、演练补丁、安装/回滚/校验/安全演练脚本 |
| `docs/integration-r11.md` | 四份交接清单的逐项落点、验收边界 |

生产补丁还包含四个已拷入模块相对生产的改动，完整文件范围见 `patches/ops-r11/candidate.sha256`，共 27 文件，其中 14 个已有生产文件、13 个新增文件。四个原任务的补丁不再用于安装，不能叠加。

## 接口与联动

沿用四份原交接文档的 `/inbox/.../ops/*`、`/parties-ops/*`、案子/报价/履约接口；认证、密码复核、CSRF 保留。新增/扩展：

| 契约 | 行为 |
| --- | --- |
| GET/HEAD `/api/v1/ops/drafts` | `{items,dry_run:true}`；只读 `inbox_dry_run_drafts` 与 `parties_outbox`，GET 不迁移；POST 405；查询参数 400；未授权 403；错误脱敏 |
| 草稿 item | `id,source,conversation_key,text,state,dry_run,created_at,reviewed_at`；source 为 inbox/parties；每来源最多最近 500 条 |
| POST `/api/v1/parties-ops/{id}/update` 的 timezone | IANA 时区或空值；保存在 `parties_ops_profiles.timezone`；非法值 400，旧数据无需猜测补齐 |
| POST inbox ops/action 的 assign | 支持 `customer_party_id`，保留旧 customer_key 契约；合并显示不挪动历史 identities/deals FK |
| GET inbox context、deals detail | party/customer 带规范客户 ID、国家、可选 timezone；已合并客户使用保留客户资料 |
| GET `/api/v1/ops/queue` | 同一消息版本的 done/未来提醒不重复出现；新消息重新提醒；仅 dry-run 入队不会把会话假记为已回复 |
| URL | `/customers/:id`、`/suppliers/:id`；`/inbox?conversation=KEY` 优先，保留 c；triage 的 conversation/suggestion 与 pipeline 的 channel 可定位 |

时间戳用 UTC/Unix 秒保存；普通显示与日期输入按北京时间。当地时间单独标出时区，不受设备时区影响。等待时长按秒差除以 3600，不扣夜间、周末或节假日。

## 测试命令与真实结果

日志、真实数据截图与中间产物均以 `private_out/r11/` 为交付位置。该目录在现有 `.gitignore` 中忽略；真实数据不写进本文件和仓库源码。演练快照最终在 `private_out/rehearsal/`，finally 尝试清理，本次退出后的残留副本也已手动清理。早期演练曾使用系统临时目录，随后迁到 private_out；原进程已中断清理。隔离测试的默认 build_routes 禁用真实来源复制。

```sh
cd web
npm run build
npx tsc --noEmit
cd ..
/tmp/inbox-venv/bin/python tests/r11_regressions.py
export CHROME=/tmp/inbox-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell
OUT="$PWD/private_out/r11/smoke-final" node web/e2e/smoke.mjs
node web/e2e/ops-inbox.mjs
node web/e2e/ops-parties.mjs
node web/e2e/ops-queue.mjs
node web/e2e/ops-deals.mjs
node web/e2e/ops-day.mjs
patches/ops-r11/rehearse.sh
python3 patches/ops-r11/verify.py
PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python patches/ops-r11/check_routes.py \
  private_out/r11/production-stage --output private_out/r11/route-table.json
```

`tests/r11_regressions.py` 的可选模块补全使用 `/opt/imgw/wechat/console/` 源码，不是渠道或真实数据验收；网络和生产 SQLite 被测试守卫禁止。pytest 目录必须分进程，避免旧测试的裸 `conftest`/`test_service` 导入重名。

| 验证 | 已核实结果 / 日志 |
| --- | --- |
| 构建、独立类型检查 | 通过；`logs/build-final.log`、`typecheck-final.log` |
| pytest | 1104 passed，另 461 subtests；每套最终日志 `logs/pytest-*.log`；汇总 `test-results.json` |
| 必选五套 | inbox 19、parties 30、ops_queue 27、ops_deals 8、overview 85 |
| 相关旧套件 | deals 128、quotes 59、fulfil 174、inbox_api 37、archive 40、pipeline 125、lang 73、outbox 50、web_app 6、roles 243 |
| 原 smoke | 110 个桌面/手机视图通过（原视图保留，新增草稿箱两视图），含总览 22 个边界场景；`logs/smoke-final.log` |
| 四个 ops 与 ops-day | 全部通过；inbox 4 组、parties/queue/deals 各桌面手机；ops-day 两宽度均队列 7→6，并验证夏令时/缺失时区、深链接定位、完整建案预填；`logs/ops-*-final.log` |
| 真实数据只读演练 | 56 个视图、0 页面检查失败；两处 WhatsApp 远程登录 503 的明确失败态另记 unavailable；`logs/e2e-real-final.log`、`real-smoke/results.json`，不宣称 WhatsApp 登录正常 |
| 补丁 | 生产和演练补丁正向 dry-run/应用、反向 dry-run/还原、字节/SHA 与语法校验通过；`logs/patch-final.log` |
| 安装/迁移/回滚 | 在暂存源码和临时库实际执行 install.sh/rollback.sh，通过且保留数据；`logs/install-rollback-final.log`；未执行 root 分支或生产安装 |
| 路由 | 实际候选 v3 构建 218 路由，324 个路径/方法组合，无冲突；`logs/routes-final.log` |

原失败逐项说明：

- inbox 原补丁 SHA 断言：本次整合修改源码后发生；测试改验 r11 合并包的真实基线正反向完整性。
- queue 原 rehearsal-mount.patch：本次整合后不能再重复挂载；测试改验 r11 演练补丁的反向/正向恢复。
- deals 17→18、quotes 22→23、inbox 8→11 路由断言，以及 inbox 固定数组下标：四块候选已改变契约；改为新数量和按路由名查找，保留鉴权检查。
- inbox 300 条/4000 字截断断言：W2 已移除 API 额外截断；改验 310 条/4200 字完整顺序，列表摘要 140 字约束仍保留。
- paid/inspection 默认待办和 won 模板：W3/W6/W7 已补成交后模板；更新确切数量，保留重复进入不重建、不重开完成项及丢单停止模板断言。
- quotes 写入前复核次数、inbox summary 读缓存次数：原候选增加表单后复核/状态投影；按真实执行序列更新。inbox 全只读遍历改按 HTTP 方法排除新增 POST。
- inbox 非文本查询 q=1 被 QueryParams 字符串化绕过：W2 新转换路径的实际缺陷，本次补转换前类型校验；相应旧测试通过。
- 并发首次建案 database locked：整合回归暴露的原 Store WAL 初始化竞争，本次加写锁并通过并发/幂等测试。
- fulfil 演练“不能创建任何临时文件”断言：本次新增主动迁移已改变行为，改验三个临时库、扩展表和源文件未创建；测试默认禁复制真实入站/微信库。
- roles CLI 子进程缺 console.media：公开快照本来不完整；测试启动补齐只读可选代码路径，父/子进程一致，最终 243 项通过，未改生产模块补齐依赖。
- 旧浏览器脚本的资料按钮、草稿自动填入后手机收起、节点按钮/表单名称：四块 UI 已改变，断言改为真实可见行为及对应内存副作用。初次 inbox 失败场景曾超时，保留日志；最终结果以 final 日志为准。
- 真实演练初次 inbox/parties 503：只读 WAL 缺 shm，改为稳定文件快照；classify 的默认真实 inbox 改为副本注入。WhatsApp 登录配置/远程状态 503 仍显式保留为未验证来源，不改成正常状态。

Starlette/httpx 弃用提示仍存在，不是失败。当前没有以跳过断言方式处理业务失败。

## 生产安装步骤（尚未执行）

1. 在发布暂存副本重做上述检查，确认 SHA 与 `absent.list`；不匹配即停止并重新对齐真实基线。审查唯一 `production.patch`，不要套用四个旧补丁。
2. 暂停业务写入，备份当前前端目录。实际已只读核对单元 `imgw-console.service`、`imgw-console-lan.service`，均使用 `imgw:imgw`，工作目录 `/opt/imgw/wechat`；只重启这些业务服务，不动网络服务。
3. 由有生产源码写权限的安装者执行，备份目录必须新建且位于私有区：

   ```sh
   R11_BUNDLE=/绝对路径/ops/patches/ops-r11
   R11_BACKUP=/私有备份路径/ops-r11-发布批次
   cp -a /var/lib/imgw/ops-app/dist "$R11_BACKUP-frontend"
   systemctl stop imgw-console.service imgw-console-lan.service
   "$R11_BUNDLE/install.sh" /opt/imgw/wechat "$R11_BACKUP" \
     /var/lib/imgw/ops-app/archive.sqlite3 \
     /var/lib/imgw/ops-app/outbox.sqlite3 \
     /var/lib/imgw/ops-app/drafts.sqlite3 /opt/imgw/venv/bin/python
   ```

   脚本先备份源码与三个 SQLite，再装入并显式迁移。root 调用时迁移使用业务账号 imgw；要确认发布路径允许该账号读取候选源码。迁移新增 inbox workflow/receipts、dry-run 表、parties 附加表与 timezone，以及 deal 扩展表；archive 升级至所需 v9，保留更高已有版本。
4. 核对候选路由表（上面的 check_routes 命令可对 `/opt/imgw/wechat` 运行，输出到私有目录），检查新接口和权限；安装已验证的 `web/dist`，保留原静态目录备份，设置 `imgw:imgw` 与原资源权限。
5. `systemctl start imgw-console.service imgw-console-lan.service`，核对服务健康、路由、登录/CSRF与只读新接口。用正常授权会话确认界面；消息测试仍只保存 dry-run 草稿。不得把本地演练的假登录服务用于生产。

## 回滚步骤（尚未执行生产回滚）

暂停候选写入，停止上述两个业务服务；执行 `patches/ops-r11/rollback.sh /opt/imgw/wechat`，校验基线 SHA 和新增文件消失；恢复前端备份，启动服务并复核旧路由/登录。脚本不会删除附加表或发布后新增业务记录。

原 FK 未迁移，旧页面会恢复原身份展示。若决定恢复数据库备份，须先评估发布后新增业务记录并安排停写；不可自动用旧库覆盖新到账或新案子。可使用源码 tar 备份应急恢复，仍须核对 SHA 与权限。

## 未完成 / 未验证

- 未安装生产、未生产迁移、未重启生产服务，未提交 Git。
- 真正外发、真实 AI/翻译、真实报价成本与银行到账、渠道完整历史和附件覆盖不在本次验收结果中。
- 默认今日队列只有阿里/微信本地记录，WhatsApp 待办仍明确“未汇入”；远程登录在真实只读演练返回 503，页面失败态已验，来源未验好。
- 真实副本当前没有可走完工作日链路的客户/案子记录；完整链路由虚构浏览器状态与临时 SQLite 跨模块测试验证，不冒充真实成交验收。
- 国家多时区且无明确 IANA 时区时不显示当地时间；可在单位编辑中补齐。未做自动城市/地址推断。
- 手机为 Chromium 400px 视口，未验证实体手机键盘。
- 本机已处理/稍后反馈继续按浏览器存储，未跨账号/设备同步；时间提醒没有新增推送服务。

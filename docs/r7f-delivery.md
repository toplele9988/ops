# R7-F：新群与联系人分类、忽略标记交付

2026-11-01，开发候选，未部署、未 commit、未 push。仅在独立快照修改源码；未改生产文件或数据库、网络或采集登记，未发送任何消息。

## 文件清单

新增：

- `src/console/classify/{__init__,rules,store,service,policy}.py`：规则、独立库、人工确认、只读资格接口、收件箱及语言服务包装器。
- `src/console/v3_classify_api.py`：五个分类接口。
- `web/src/pages/Classify.tsx`：当天建议、所有日期、改正、撤销、每批最多 20 条确认/忽略。
- `tests/classify/{conftest,test_classify}.py`：全虚构、禁网络的 40 项测试。
- `patches/r7f/{production-mount,rehearsal-mount,lang-policy}.patch`：生产宿主加两行挂载、演练脚本只新增两行、语言入口的最小策略接入补丁。都未应用到原目标。
- 本文；`docs/r7f/{baselines,production-code-baselines,test-dependencies,candidate}.sha256`。

修改：

- `src/console/v3_inbox_api.py`：默认使用分类覆盖层；忽略后的列表/统计不再待回复；起草前拒绝，起草期间被忽略则不保存结果；详情返回资格标志。
- `src/console/inbox/model.py`：列表投影附加分类和资格标志。
- `web/src/App.tsx:19` 加一行 import，`:52` 加 `/classify` 路由。
- `web/src/nav.ts:36` 加一行「待分类」，key=`classification-review`，路径 `/classify`，运营台/日常工作。

`scripts/rehearsal_server.py`、`v3_lang_api.py`、已有语言/渠道/名录/档案/KB 实现、README、master-plan、requirements 均未修改。`web/dist` 是本地构建产物，不是部署记录；临时 node_modules 入口不属于交付。

## 已核查的真实边界

已读 CLAUDE、master-plan、data-pipelines（含阿里 v2 契约）、workflow-gaps 全文及 WG-04/05/13、directory、inbox-api、channels、ui-development；另读项目入口要求的 README、roadmap、status。

核查仓库 directory/inbox/customers/channels/archive/lang 真实代码及路由风格。只读核查 `/opt/imgw/wechat/video_knowledge/channel_classifier.py`：SUP/CUS 是线索，生产授权、memory_bindings、channel_role_intake 另有严格边界，不能复用生产分类函数去写登记。通过只读 PRAGMA 核对实际发现库 identities、微信 messages/message_scopes/memory_bindings/channel_role_intake、档案 parties/schema_migrations 的结构；没有读取它们的业务行或任何凭据值。

workflow-gaps 没有独立 A8/A9 工作包；本任务以任务书为直接验收依据，沿用 WG-04 的等待边界、WG-05 的名录关联边界和 WG-13 的依赖隔离要求。

## 行为与接口

默认库 `/var/lib/imgw/ops-app/classify.sqlite3`，可传 `path` / `classify_path` 或 `OPS_CLASSIFY_PATH`。新建文件 0600；GET 不建文件。独立表为 suggestions、classification_events，不改 archive.sqlite3 版本。写入经进程锁及 SQLite `BEGIN IMMEDIATE`；多进程竞争由 SQLite 串行。拒绝符号链接、硬链接、非普通文件及误指向已有其他业务表的数据库。

扫描只读已有会话和本地微信通讯录，不刷新远程通讯录、不启用采集、不补历史消息。页面打开时 POST 扫描一次，按钮可再次扫描；没有定时任务。若要在页面未打开时每天处理，宿主调用同一 POST 即可，本交付没有创建任务。来源坏了返回可用来源并展示缺失说明；读不到不称为「没有新联系人」。

以标准 conversation key 区分渠道、账号和准确身份，不按同名合并。扫描全体已发现记录，列表默认展示北京时间当天首次出现者。`day=all` 可找回旧记录，配合 ignored 状态恢复忽略。来源没有可靠首次时间时使用首次扫描日期，并明确提示；后续来源补足时间会更新待确认建议的日期。WhatsApp 来源仍受现有 overview 范围限制，不能据此确认完整通讯录或真实首次建聊时间。

规则覆盖 SUP/CUS、公司名、生产/物流/配套/内部/测试/垃圾关键词、前 20 条摘要中的对方采购/报价/运单类别、首批发言者名称方向、来源渠道。完整群成员列表不可得，只用不同发言者名称作弱线索并明确说明，不能证明完整成员构成。公司后缀和外语本身不决定买卖方向。语言只通过 P1 `Lang.read('contact', ...)` 读取已有结果，不调用语言识别模型、不另建语言表；默认把本次会话快照提供给 P1，避免为读语言逐会话拉取远程历史。没有 AI 调用路径，返回 `ai_enabled=false`，本版为规则引擎。

SUP 与采购表达、SUP 与 CUS、测试与内部等竞争方向一律标冲突，置信度最多 0.40；无方向线索为待核对。置信度是规则分数，不是校准的成功概率。理由来自固定中文模板，不复制正文。后续消息到达时可更新未确认建议并递增 revision；扫描不覆盖人工确认。

- `GET /api/v1/classify?day=YYYY-MM-DD&status=pending`：日期缺省今天；日期可 all；状态 pending/confirmed/ignored/all。一次返回当日符合条件的全部条目，按置信度降序、key 稳定排序。包含固定理由、冲突、版本、人工结果和资格标志。
- `GET /api/v1/classify/summary?day=YYYY-MM-DD`：新增总量、待确认/已确认/忽略或内部数、冲突数、按建议类别计数、首次时间未知数、时区。不创建调度任务。
- `POST /api/v1/classify/scan`：URL 表单 `confirm=1`；返回 added、updated、notes 和中文说明。
- `POST /api/v1/classify/decide`：URL 表单 action=confirm/ignore/undo、items=JSON 数组。每项 key/revision，确认另传 role，可传修正后的 name。类别 supplier/customer/forwarder/vendor/internal/test/spam。批量 1–20，整体事务，仍受既有 8KB 表单上限约束；极长标识可缩小批量。客户端不重试写操作。
- `GET /api/v1/classify/flags?key=...`：classification、ignored、reply_eligible、draft_eligible、kb_eligible。

所有路由沿用 principal + legacy session + recheck + 线程池 + no-store/nosniff。POST 必须放在现有 CSRF 中间件之后；confirm 不是 CSRF 校验。字段重复、额外字段、非表单、无效日期/版本/分类会拒绝。503 只用固定中文，不输出异常正文、路径内容或来源响应。

业务分类确认才调用现有公开 `Archive.write('party_create', ...)`；用借用事务的 store 接口，不调用档案迁移。确认时把 archive 作为附加库，在同一 writer 事务里写名录及本任务状态，并沿用名录审计。已存在同名但未关联本会话的单位会拒绝，要求人核对后输入区分名称，不按群名自动合并。重放同一确认不新增档案，乐观版本防止用旧建议覆盖新线索。

内部、测试、垃圾、ignore 只在本任务库记标志，均排除待回复与回复起草，KB 资格为 false。撤销恢复待确认及资格；不删除已建名录单位。改正业务类别会建立/复用新类别名录，原单位保留，需人在名录核对；不暗中修改可能被案子引用的旧单位。批量中一项冲突会回滚整批的名录与分类写入。SQLite 附加库若混用 WAL，不能把普通事务回滚测试当作跨库掉电原子性证明；未知结果先核对分类和名录，按会话及唯一名称去重，不能盲目自动重试。

## Inbox / KB / P1 接入范围

现有 inbox builder 已实际使用 PolicyInbox。每次请求重读决定，不污染渠道缓存；详情重新计算等待状态后也会重新覆盖标志。汇总只读一次来源，客户待回复、供应商待回复、逐账号与逐渠道统计一致。策略库损坏时 503，不放行起草。已发起的模型调用不能强行撤回，但返回时再核对资格，被忽略的结果不入草稿库。没有发送操作。

其他消费者可用公共接口：

```python
from console.classify import Policy, PolicyInbox, PolicyLang

policy = Policy(configured_classify_path)
if not policy.flags(conversation_key)['kb_eligible']:
    # 在取正文、生成候选或调用模型之前跳过
    continue

# 已有公共服务可以用包装器注入，不读取本任务内部表：
filtered_inbox = PolicyInbox(shared_source_inbox, policy)
guarded_lang = PolicyLang(existing_lang_service, policy)
```

KB 按任务约束只交付接口/标志，没有修改生产 KB worker、抽取候选或已入库知识。标记不会自动清除既有知识或草稿。

P1 的 `/api/v1/lang/compose` 是另一条起草入口。`lang-policy.patch` 将其 service 包装为 PolicyLang，使用 P1 公开 read/write，不修改 P1 内部表；在补丁被总指挥合入前，该独立母语起草入口还不会读取本任务标志。直接持有 Lang 的其他业务服务也须注入 PolicyLang 后才受同一限制。不要把 inbox 已阻止起草说成全系统所有调用方已完成接入。

## 测试命令与结果

规定 sudo 首次被容器拒绝，原文：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

随后以当前身份运行同样 Python/pytest 参数。原快照标准命令
`env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/classify" /opt/imgw/venv/bin/python -m pytest tests/classify -q -p no:cacheprovider`
在收集阶段失败，未运行测试，原文：

```text
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/classify/test_classify.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.22s
```

这是 WG-13 已知的快照缺件。只将开发副本 `/srv/imgw-dev/ops/src/console/media/{__init__,tokens,files,alibaba}.py` 源码复制到 `/tmp/r7f-test-deps/console/media/`，没有复制配置、媒体、密钥或业务数据，没有把媒体模块写入本仓库。四个源码哈希见 `docs/r7f/test-dependencies.sha256`，不承诺它们与缺件快照属于同一提交。临时 console/__init__.py 使用 pkgutil.extend_path，sitecustomize.py 只 import console；测试始终设置临时媒体 key 路径。

完成依赖覆盖后的命令（S 依次为 classify、inbox_api、directory、channels、archive）：

```sh
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r7f-regression-media.key \
  PYTHONPATH="/tmp/r7f-test-deps:src:tests/$S" \
  /opt/imgw/venv/bin/python -m pytest "tests/$S" -q -p no:cacheprovider
```

| 套件 | 最终结果 |
| --- | --- |
| classify | 40 passed，0 failed |
| inbox_api | 37 passed，99 subtests passed，0 failed |
| directory | 189 passed，22 subtests passed，0 failed |
| channels | 193 passed，14 subtests passed，0 failed |
| archive | 40 passed，46 subtests passed，0 failed |
| lang（临时应用语言挂载补丁） | 73 passed，0 failed |

合计 572 passed，181 subtests passed，最终零失败。语言补丁用 `patch -p1` 只应用到 `/tmp/r7f-patch-check/`，再把修改后的 v3_lang_api.py 放入 `/tmp/r7f-lang-overlay/console/`；未改仓库语言模块或生产。命令：

```sh
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r7f-lang-media.key \
  OPS_CLASSIFY_PATH=/tmp/r7f-lang-disabled-classify.sqlite3 \
  PYTHONPATH="/tmp/r7f-lang-overlay:/tmp/r7f-test-deps:src:tests/lang" \
  /opt/imgw/venv/bin/python -m pytest tests/lang -q -p no:cacheprovider
```

中途 inbox 回归发现本次包装器重复读取来源，修复后复跑通过。失败原文：

```text
FAILED tests/inbox_api/test_api.py::ApiTests::test_get_recheck_after_read_error_missing_and_invalid_parameter
E       AssertionError: Lists differ: ['read', 'read', 'recheck'] != ['read', 'recheck']
1 failed, 36 passed, 99 subtests passed in 2.18s
```

新增测试覆盖：七类规则、SUP/询价冲突、公司/语言不硬判、待确认线索更新及旧版本拒绝、每日口径与所有日期、确认/改正/撤销/重复确认/并发确认、批量跨库回滚、档案版本不变、四种忽略对实际 inbox 计数/详情/起草的影响、起草中被忽略不保存、策略损坏不放行、P1 包装器、宿主 CSRF、越权、表单重复、SQL 注入文本、符号/硬链接、固定错误及正文不入建议/审计/日志。均用临时 SQLite、虚构消息、假模型；不连接真实渠道。

前端在 web 下执行 `npm run build`（tsc --noEmit && vite build），最终通过，2144 modules transformed；没有执行 tsc -b。原来没有 node_modules，第一次环境准备失败 `sh: 1: tsc: not found`；借用只读目录又遇到 `Error: EROFS: read-only file system, open '.../node_modules/.vite-temp/...'`。最终把开发副本依赖复制到 `/tmp/r7f-web-deps/node_modules` 后构建通过，未修改外部依赖、package.json 或锁文件。临时复制目录必须保留 node_modules 这个目录名，否则 TypeScript 无法解析跨包导出；早先命名错误曾引发 `error TS2724: '"@tanstack/react-query"' has no exported member named 'QueryClient'. Did you mean 'useQueryClient'?`，纠正测试依赖位置后消失。未启动固定端口服务、未跑 e2e_smoke、没有手机或桌面真实浏览器验收；手机布局只做源码及构建检查。

## 需要总指挥合并的共享文件改动

1. 审查 App.tsx:19、:52 和 nav.ts:36 的最小新增行；没有修改其他页面。nav.ts:70 原有 `/system/classify`「规划中」项仍保留，建议总指挥删除旧规划项或重定向到新页，避免同时显示新旧入口；本任务没有越过只准最小新增的约束去重写原导航块。
2. requirements.json A8（约 :82）建议 partial：规则建议、人工确认、日报 API 与页面在虚构数据验证，未做真实来源验收。A9（约 :90）建议 partial：inbox 已接忽略/内部资格；语言入口需合补丁，KB 提供读取接口，生产消费者尚待接入。不直接标 done。
3. `lang-policy.patch`：在 v3_lang_api.py 接入 PolicyLang，生产/演练都须与 inbox 使用同一个 classify 路径；其他直接调用语言服务的任务负责人采用同一包装器。KB 负责人按 kb_eligible 过滤候选，未改其模块。
4. `rehearsal-mount.patch`：只在 scripts/rehearsal_server.py 既有 :57 动态列表前新增两行，独立库在演练 tmp 内；原挂载逻辑不变。inbox 的自定义 drafts_path 已自动推导同目录 classify.sqlite3。不要由本任务启动演练。
5. 按 WG-13 补齐同版本 console.media；不把本机临时依赖当正式修复。README、master-plan 未改，无需本任务合并。

## 部署与回滚（仅由总指挥执行）

1. 先核对 `docs/r7f/baselines.sha256` 的快照改前基线、`production-code-baselines.sha256` 的只读生产源码基线、candidate.sha256 的交付源码。生产当前 v3_inbox_api/model 与快照基线相同；任何现场 SHA 变动先合并，不能覆盖其他任务成果。
2. 总指挥合并上述共享项和语言补丁，补齐媒体依赖，跑隔离套件、统一真实只读演练及自己负责的 e2e。业务表、正文和真实数据不进本仓库。没有真实验收前保持 A8/A9 partial。
3. 停止旧版本写入窗口，备份待替换源码、前端 dist、既有 archive.sqlite3 及 classify.sqlite3（如存在）。数据库用 SQLite backup 得到一致副本，不能在线只复制主库而遗漏 WAL；备份只留私有目录，不进仓库。
4. 安装新 classify 包和 v3_classify_api.py；合并候选 v3_inbox_api.py、inbox/model.py，以及审核后的 v3_lang_api.py。向实际 `/opt/imgw/wechat/console/v3.py` 应用 production-mount.patch，在目录接口后新增 import/build 两行；保留原认证和 CSRF 中间件。源码基线见 production-code-baselines.sha256，未直接修改该文件。
5. 部署前端构建到宿主既有 dist 位置，配置 `OPS_CLASSIFY_PATH` 或各 builder 显式同路径，确保运行用户有私有目录写权限。分类库在第一次 scan 事务创建，无 archive 迁移、无需升级档案库版本。先在可丢弃副本上扫描/确认，再由老板核对真实来源覆盖；不因页面存在就认为采集已接通。
6. 按现有服务流程重启并核对新增五个路由、页面入口、认证、CSRF、每天日期、忽略前后待回复统计。上线操作本任务未执行。

回滚：停止新代码处理请求，撤销新增路由与语言挂载，恢复备份源码和前端 dist，再按既有流程重启；保留分类库供核对。无需档案降级。扫描/忽略只改独立库；若上线后已确认业务类别，名录已有新增单位及审计，不可为回滚代码就覆盖整个 archive 或删除已被案子引用的单位。按会话和审计人工核对，撤销本任务标记，保留/停用多余档案；只有确认无人写入且没有后续业务时，才由总指挥决定是否恢复一致备份。代码回滚后旧收件箱不再读忽略标志，待回复数会恢复旧口径。

## 安全自查与未完成范围

- 越权：沿用现有登录、旧 session、recheck；写操作先复核，读失败也复核；CSRF 由宿主守住，测试证明缺少校验会被拒绝。没有增加采集、发送或生产微信群用途授权。
- 路径穿越：HTTP 不接收路径；配置路径拒绝链接、特殊文件及已有来源表，库缺失只读不创建。目录须由可信管理员配置；没有把本地文件检查称为抵抗恶意本地并发替换的沙箱。
- 注入：SQL 值参数化、固定表与操作白名单，类别/版本/JSON/表单大小受限，React 文本自动转义。公司同名拒绝自动合并。
- 泄露：不读取凭据值，不复制客户数据；建议只存固定理由、元数据和人工决定，审计不写正文或原始错误；仅通过 P1 公共服务读取已有语言。生产结构检查只输出列结构，未输出业务行。
- 重复发送：没有发送调用、发送重试或发送队列；扫描/确认幂等，前端 mutation retry=false。未知结果提示先刷新核对。既有已发起模型调用不能取消，只能丢弃新标记后的结果；未验证掉电故障恢复。
- 未完成：未部署、未接真实采集链路、未验证完整微信成员/个人消息/WhatsApp 通讯录、未接生产 KB worker、未把语言补丁合进其他任务模块、未完成全系统直接 Lang 调用方接入、未做真实数据/浏览器手机/e2e 验收。没有自动 AI 分类调用、没有新调度任务、没有清理既有知识/草稿。确认改正/撤销保留旧名录，不能宣称等同于删除或修改生产微信角色。

所有通过结果只说明这些候选在虚构数据与临时库上符合测试；不代表真实数据已可用。

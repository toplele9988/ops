# M3 前置：运营员接真实事件的演练候选

本包未安装到生产，未重启服务，未真实发送，未修改网络。以 `docs/north-star.md` 第 5 节和 `docs/integration-ai-operator.md` 为准。只有总指挥按下述步骤安装后，生产运营台才会出现入口。

## 组装与边界

- `console/v3.py` 在 roles API 后追加 operator API；`console/app.py` 在创建 Starlette 后、密码认证包装前追加 lifespan 挂载。保留现有密码复核、CSRF、认证与宿主生命周期。六条 API 的路径/方法无重复。
- `/app/operator` 路由靠近 roles；nav 的前台顺序在 roles 后追加“AI 运营员”。不覆盖供应商档案任务添加的业务入口；共享文件补丁只加自己的行。
- `console/operator/host.py` 显式组装 `build_operator`、现有 Hub、QuotePolicyFromRound、Lang、AiTask 和 **FakeBridge**。无真实桥注入选项；不加载 WTAPI/WAHA 发送客户端。全部内部动作也只走假桥。
- 操作库固定为 `/var/lib/imgw/ops-app/operator/operator.sqlite3`，目录 0700，文件 0600，所有者 imgw。库安装阶段主动初始化；默认 `enabled=false, dry_run=true, levels={}, types={}, contacts={}`。GET 和模块导入不建库、不迁移、不启动线程。安装和回滚都会关掉旧许可，保留事件和尝试账本。
- 现有手工 outbox 不改。假桥的完成结果不写成真实报价已发送/已接受，不写正式订单、成本、收款或履约节点。

## 事件来源与作业

全部源库用 SQLite `mode=ro`、`query_only=ON` 读取。无 WAL 的静态源使用 `immutable=1`，有 WAL 时要求既有 SHM，避免读操作创建旁文件。解析只读取消息正文和元数据，不创建媒体密钥、不拉附件、不访问渠道网络。

| 来源 | 实际路径与读取方式 |
|---|---|
| 阿里 | `/var/lib/imgw/icbu/inbound.sqlite3`，`inbound.id/payload`，复用 `inbox.model.parse_message` 及其稳定 ID/摘要规则 |
| 微信群 | `/var/lib/imgw/wechat/api_state.sqlite3`，`messages.rowid/app_id/payload`；会话 key 与 `channels/wechat.py`、`ops_queue/inbox.py` 相同，稳定 ID 含 app_id 和原 message_id |
| 微信私聊 | 同一消息库，按 `wechat_private_messages.rowid` 与 `messages(app_id,message_id)` 联接；账号 key 与待装包 `account_key(app,own)` 算法相同；表尚未安装时跳过，不推进游标 |
| WhatsApp | `/var/lib/imgw/whatsapp/messages.sqlite3`、`/var/lib/imgw/whatsapp-history-private/history.sqlite3` 的持久 messages；检查单一 binding、现有配置中的 session/owner、逐行 scope，隔离冲突记录；不使用页面 overview 或有限条聊天历史 |
| 供应商缺口 | `/var/lib/imgw/ops-app/dossiers/dossiers.sqlite3`，优先 `sd_supplier/sd_gap`，亦支持同结构未加前缀的候选表；库不存在安静跳过 |

`operator_cursors` 保存每个输入的文件代际和物理行位置；`operator_ingested` 按 **渠道、会话、消息稳定 ID** 去重。先提交引擎事件，再原子保存接入回执和游标；在两者之间崩溃会重放同一事件，不重调模型。卖方消息也推进物理游标但不冒充入站。坏行记录 `source_skip` 和位点后继续，原始记录保留在源库。源库替换/截断从头扫描，既有稳定 ID 不重复入队。来源必须保留待消费行；不支持生产侧删除未读记录后保证补回。

初次从零补读全部历史，每个来源每轮最多 100 个物理行；然后持续读取新增。安装前先核对身份和案子归属，避免大量历史入站变成待核对事项。事件处理使用触发消息及更早的历史，不拿同批较晚消息代替触发内容。

运营台 **imgw 进程内每 45 秒**调用接入和 `operator.tick()`。ASGI lifespan 启停接入和 tick 两个受控线程，关机唤醒等待；慢模型处理不会阻塞独立的 tick 线程。两个运营台进程分别用私有 `worker.lock`、`tick.lock` 文件锁避免同时执行同类工作，崩溃由系统释放锁。选择它是为了不新增 root 权限、timer、服务或网络设置；代价是运营台停止时作业也停止，重启后靠游标续跑；单次 tick 本身超时或来源不可读会留健康记录。停止时若已入场的工作未完成，会记录日志；运维应等进程退出后再覆盖源码。

一个来源/联系人/到期事件失败不影响其余项；异常类型、组件、时间及累计次数留在 `operator_health`，日志不含异常原文、聊天或凭据。`evaluating/unknown` 不自动再调模型或重发。页面显示失败来源，不能将部分失败解释为全部健康。

`ArchiveEvents` 在原 roles/档案流程提交核准的 offers/freight_quotes 后，按记录内容版本发 `supplier_reply/forwarder_reply`。人工确认的非退款、qualifies 收款才发 `payment_confirmed`；上下文仍核对真实人工凭据与案子阶段。收件会话必须是唯一的已绑定案子参与方；有歧义留健康错误，等待人工处理。未核准自由文本不提升为成本。未新增可伪造这些事件的 HTTP 接口。

缺口仅取 `state=open, priority IN (1,2)`，每家合并一条，来源和影响一起保留。采用固定要资料模板，不执行侧库 ask_text 中的任意指令，不下单、不谈付款。联系人未经过原档案确认，不从 contacts_json 猜收件人或时区。此类草稿永久 `rehearsal_only`：开关打开或调用批准接口仍不能发；缺口更新替换旧草稿，解决后撤下旧草稿并留历史。

## 补丁与并行包

`baseline.sha256`/`candidate.sha256` 针对采样时真实源码，覆盖 7 个已有文件；`payload.sha256` 列出新增文件。`production.patch` 的共享路由/导航改动为追加行，现有 Operator 页面增加可读卡片。构建源在 `build_release.py`，安装程序不会自动刷新哈希。

本包**没有修改** `deploy/wechat-private/candidate.sha256` 所列的任何文件，私聊读取通过新模块完成。已实际执行该包 `prepare.py --destination /tmp/operator-wechat-private-candidate` 生成候选并验证兼容。安装主动接受该包的完整 **baseline 或 candidate** 两种前置哈希，拒绝混装、其他版本；两份清单保存在本包。两种前置状态均在隔离副本完成组装和路由核对，安装/回滚分别演练。

并行供应商档案包若先修改 `console/v3.py`、`web/src/App.tsx` 或 `web/src/nav.ts`，这里的精确哈希会拒绝覆盖。由总指挥在隔离整合副本保留双方追加行，重新审阅/生成合并清单与补丁，再跑验收。不能强行忽略哈希、复用整份旧文件或用 `build_release.py` 静默吸收未知改动。

## 验收结果

- `tests/operator`：98 项通过，含原有 80 项及游标去重、重启续跑、事件提交后崩溃、源替换、坏行隔离、私聊账号、WhatsApp 全行读取/归属复核、缺口合并/撤下/不可批准、作业互斥、tick 异常隔离、宿主生命周期、慢入站不阻塞 tick、核准资料版本唤醒、人工收款唤醒。
- 报价内核：8 项及 151 subtests 通过。模型/网络/真实库写入被隔离测试禁止。
- Playwright：1440×1000、390×844；真实组件、真实共享路由/导航，API 使用虚构 fixture。演练卡片、预计时间、缺口卡片、缺口禁止批准、授权矩阵、编辑/拒绝/假桥确认、总停、错误禁用均通过；无页面错误和横向溢出。每个视口 10 次写请求、1 次假桥调用。
- 两种前置哈希：真实 v3 聚合得到 224 条路由、331 个唯一方法/路径，含全部 6 条 operator API；核对期间禁止网络和 SQLite。
- 完整 install → 主动初始化 → rollback 在 `/tmp` 隔离副本通过；原文件哈希恢复，私有账本保留且关闭，0600。后续其他补丁改动会阻止回滚覆盖。
- 真实库只读演练已执行；**数字、脱敏样例和异常计数仅在忽略目录** `private_out/operator-dryrun/summary.json`，不写进受跟踪文档。真实正文、身份、价格未落盘到演练产物；回放账本为内存，持久性另由磁盘测试验证。演练禁网络和模型执行，因此不等于真实模型可用性验收。现场缺少身份/案子关联及侧库时，结果进入归属核对而非猜测第 1 级对象。
- 沙箱将宿主 root UID 映射为另一 UID。仅只读演练的配置检查按 `/` 的映射 UID 核对；生产继续使用既有严格 root 校验器。未复制密钥或变更生产校验。

测试报告/虚构截图：`/tmp/operator-m3-pytest.txt`、`/tmp/operator-m3-e2e/results.json`、`operator-1440.png`、`operator-390.png`。这些是候选验收，不是已上线证明。

## 上线步骤（交总指挥执行，本任务未执行）

1. 核对 `docs/north-star.md`、当前运行源码与两个前置包的哈希；先完成并行共享文件整合。确保 archive 已主动完成 r11 所需迁移；operator 不升级业务库。确认所需 Python/现有 web node_modules 可用，不下载新依赖。
2. 在临时副本组装并核路由：
   ```sh
   python3 deploy/operator/prepare.py --destination /tmp/operator-reviewed
   PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/operator/check_routes.py /tmp/operator-reviewed
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /tmp/inbox-venv/bin/python -m pytest tests/operator tests/quotes/test_calc.py -q
   OPERATOR_NODE_MODULES=/tmp/r7f-web-deps/node_modules CHROME=/tmp/inbox-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell OPERATOR_ARTIFACTS=/tmp/operator-m3-e2e node web/e2e/ops-operator.mjs
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /tmp/inbox-venv/bin/python deploy/operator/dryrun.py
   ```
   私有报告检查未处理来源/坏行；缺口不存在不能当成已补齐。首次模型调用、免费池准入另核验，unknown 不自动重试。
3. 正式上线前须按 CLAUDE.md 运行 `scripts/e2e_smoke.sh` 的全页验收。**本任务没有运行原脚本**：它会 `sudo cp` 到生产 dist 并 `pkill` 演练进程，与本任务“不部署、不重启”边界不符。总指挥应先使用隔离路径版本完成全页验收，再进入正式安装；本包不宣称这个发布门禁已通过。
4. 由总指挥停下两个运营台服务的 worker/进程并确认退出（`imgw-console.service`、`imgw-console-lan.service`）；备份业务库与当前前端。只操作业务服务，不改网络。将本包置于 imgw 可读的位置。
5. 以 imgw 或已获授权的安装身份执行（备份路径须全新且私有）：
   ```sh
   deploy/operator/install.sh /opt/imgw/wechat /srv/imgw-dev/ops/web \
     /var/lib/imgw/ops-app/operator-backup-RELEASE \
     /var/lib/imgw/ops-app/operator/operator.sqlite3 \
     /var/lib/imgw/ops-app/dist /opt/imgw/venv/bin/python
   ```
   安装器先隔离组装、类型检查、构建、核路由，再重核前置哈希、备份源码/前端/既有操作库，安装源码并重建已安装路由表，**以 imgw 主动 initialize**，关掉全部许可，然后装前端。它不启动/停止/重启任何服务。失败时保留私有备份，先处理错误或回滚再启动。
6. 再次核对安装清单、库权限与六条路由（可运行 `PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/operator/check_routes.py /opt/imgw/wechat --web-root /srv/imgw-dev/ops/web`）；由总指挥重启两个业务服务，检查认证的 `/app/operator`、GET `/api/v1/operator`。确认 transport=fake、全部关闭且演练开启；未经认证不可读，写请求保持密码复核与 CSRF。原 inbox/outbox/deals/roles 页面照常使用。
7. 等待至少两个 45 秒轮次，检查 `operator_health`、游标推进、同一稳定消息重放不重复、坏行位点、档案缺口草稿来源。页面必须展示对象/类型、原因、正文、来源和假设开启后的北京时间；未知绑定/时区明确提示待核对，不虚报时间。

## 回滚

先全停，记下 sending/unknown，停下两个运营台进程并确认 worker 退出。保留备份目录和本包：

```sh
deploy/operator/rollback.sh /var/lib/imgw/ops-app/operator-backup-RELEASE /opt/imgw/venv/bin/python
```

回滚先主动关闭操作库许可，再核对文件没有被后续补丁改变，恢复原源码/导航/前端并撤下新增模块。不会恢复旧操作库快照、删除账本、清空事件或重发未知动作。后续档案页等包已改共享文件时会拒绝覆盖，须在整合副本撤下本模块自己的行。随后总指挥重启并复查原路由/页面。安装器与回滚器都不操作服务和网络。

## 老板开启第 1 级前要看的清单

- 先补齐并人工核对原渠道账号、身份、案子、供应商/货代关联；未绑定项不能自动发。审阅卡片中“给谁、为何、全文、来源、预计时间”，排除串单或重复。
- 缺口库已经落盘，priority 1/2 定义得到业务认可；每家合并一条，引用真实。当前缺口草稿只演练；核对并重新起草才能进入将来的发送流程。
- 联系人的产品/物流/配套分类、语言、IANA 时区及限额正确；当地 08:00–20:00，默认 6 段/小时、20 段/天、间隔 30 秒。无语言/时区不自动发。
- 第 1 级只要资料、问价、问运费、催交期，不下单、不谈付款。第 2/3 级保持关闭；确认收款永远由老板在原案子页点击。
- 看过夜间排队、额度、过期成本、未知回执及全停的演练；全停不撤回已入场请求，unknown 不重发，历史演练草稿不会因开关开启而释放。
- 真实模型免费池准入单独通过，草稿来源与准确性符合要求。**本包始终 FakeBridge**：仅打开第 1 级不会真实发送；后续必须另审真实桥、原账号绑定、渠道白名单及小范围联系人许可，不能把假桥结果当生产发送验收。

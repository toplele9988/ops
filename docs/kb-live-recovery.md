# KB 新增与停机补处理恢复方案（R3-C / H11，未上线）

2026-11-01 修订。老板已经决定：**恢复后的新增、停机期间积压都要处理。** R2-F 的“切换之前跳过、不补停机积压”已被取代；文末保留原文作为历史记录。固定自然增量边界、原 hold、未知预留、审计、来源权限与防重复约束继续有效。补积压不授权解除任何未知隔离，也不授权生产启动或付费调用。

本次全部施工在调度器提供的独立工作区；`/srv/imgw-dev/ops`、`/opt/imgw` 和生产数据只作只读参考。没有 Git 元数据，不能提供提交号或 `git diff`。SQLite 经 `mode=ro`、`query_only=ON` 和 `backup()` 进入 `/tmp` 私有目录后分析。未读取凭据、EnvironmentFile、Cookie 或授权头的值，未启动/停止服务，未调用真实模型/渠道，没有部署、commit、push 或网络变更。

## 本次缺口盘点口径

缺口按**来源证据与内容指纹**计算，再核对每个历史调用时点的窗口、持久响应、提取片段、事实及索引。当前窗口的 pending 不能证明其中每条来源从未请求；重新拼窗得到新哈希也不能推翻历史完成或未知结果。停机记录只用于界定核对范围，不能单靠任意日期选入。

两个可重复盘点工具只接受 `/tmp` 中当前用户持有的 0700 副本目录及 0600 普通文件，拒绝软硬链接；源码结构及版本不匹配即停止。它们不导入模型客户端、不读取密钥、不把来源正文打印出来。控制台只输出汇总与摘要标识，真实审核草案留在私有临时目录，禁止进入公开仓库。各库备份并非跨库原子快照；投产前由总指挥在写者受控的准备窗口重新备份、核对相同指纹，不能直接沿用本次统计当启动清单。

- 微信盘点及局限见 [微信盘点记录](r3c-wechat-inventory.md)。工具从真实源码基线抽取纯函数，按调用/片段当时的入库范围重建来源，避免用今天的 KC 窗口覆盖昨天的完成事实。没有读取含凭据的动态授权配置，盘点是来源候选集，**不是当前权限准入证明**。
- 阿里盘点及局限见 [阿里盘点记录](r3c-alibaba-inventory.md)。当前仍为 539 窗：519 validated、19 pending、1 未知。994 个旧版本 pending 中 970 个仍冻结，其余 24 个也不是重放授权。当前 19 窗共含 455 条来源，其中 349 条已完成，只有 106 条从未请求；只有 6 窗完全不含旧请求。必须先剔除已完成来源再规划稳定单元，不能把 19 窗直接重排。

阿里当前 14,182 条来源的互斥分类为：已完成 14,060、有可靠响应但本地未完成 0、从未请求 106、结果未知 16、来源时间待确认 0。16 条未知证据来自 1 条未知请求，与当前 19 个 pending 窗来源交集为 0。这里的“已完成”包括无可支持事实的成功空结果；来源时间非空且时序可核对，不代表 Win10 时钟已经校准。微信分类数量与双读者续做数量以同日微信盘点记录为准，不能用阿里单读者的计量混算。

微信 09:34:27 UTC 的副本在既有 14 个提取范围中有 8,245 条证据候选（供应商 882、学习 7,363），互斥分类如下。原 1,457 条调用中仅 674 条能以完整提示词及历史单元精确溯源，783 条未关联；未匹配片段/审计 1,019 条不能据此删除或判成可重跑。

| 分类 | 微信证据数 | 阿里来源数 | 处理 |
| --- | ---: | ---: | --- |
| 已完成 | 1,526 | 14,060 | 保留检查点与终态，不重跑 |
| 有可靠响应但本地未完成 | 3 | 0 | 仅核对后续本地步骤 |
| 确定从未请求 | 1,729 | 106 | 经授权、稳定拆分、预算与窗口审批后入清单 |
| 结果未知 | 39（3请求） | 16（1请求） | 55条源证据永久隔离 |
| 来源时间待确认（缺失/格式） | 0 | 0 | 时间可信度仍待上游验收 |
| 请求覆盖待确认 | 4,947 | 0 | 原调用未能全部溯源，禁止当作未请求 |
| 分块完整性待确认 | 1 | 0 | 原12块上限可能截断，禁止宣布全文完成 |

微信“从未请求”只对其入库晚于账本全部调用最后发起时点的来源成立；旧来源若不能被历史调用和片段可靠排除，一律留在覆盖待确认。三条 hold 全部精确溯源，为 19+14+6 条证据。最后未知之后，既有提取范围的来源归档 1,839 条，未入业务证据为 0；这只证明这14个既有范围内的副本对账，不证明所有新群/媒体/渠道已采齐。未知来源清单已经在私有临时目录合并为 3 微信+1 阿里，共55个来源摘要，两套校验器均通过；未发布、未改原账。

上表按原始证据ID列直接归类，尚需全局隔离交叉：微信1,729条停机后入库的“从未请求”候选中，**129条原始来源SHA或正文命中未知隔离**，排除后只剩1,600条候选；不能把这129条借新ID再发。原32个双响应本地候选中19个也命中未知来源/正文，安全本地候选降为13个单元，其中12个仍属于那份全文尾部待核的文档；另1个才无该文档限制。待冻结时仍须对两个渠道合并后的55条直接来源及其别名整体交叉，不能只分别检查各自的原窗口。

## 两条处理队列与冻结清单

自然新增继续使用老板在批准启动窗口时确认的固定 `after`，不前移、不清零、不重建旧账本。新增仍需来源发送时间与入库时间都严格晚于边界、原授权通过、稳定证据 ID、安定等待及来源指纹不变；相同时间戳晚到的证据按 ID 处理，水位不替代完成账本。

积压走 `kb-backlog-review-v1` → `kb-backlog-manifest-v1`，由 `deploy/kb/backlog.py build/verify` 生成/校验。每条绑定渠道、私有来源 ID、原始来源 `source_sha256`、来源元信息指纹、内容 SHA、稳定单元 prompt SHA、来源/入库时间、缺口原因、已完成步骤及原响应检查点（cache/model/response/checkpoint 摘要）。清单还绑定全部副本摘要、自然边界、审核稿摘要和四条未知来源隔离清单；冻结 SHA 由审阅者独立记录。清单被改、缺少源时间、检查点变动、范围不一致即拒绝，不能通过重算一份新清单覆盖既有 epoch。

清单保留五种原因：`complete`、`local_pending`、`never_requested`、`unknown`、`time_unconfirmed`。已完成项只登记终态；未知与时间待确认项列在排除区。可靠响应的未完成项先核对响应字节/摘要，只续事实校验、落库与索引，不再请求模型。微信需要两名独立读者：仅一份可靠响应不能冒充双读者本地续做条件；缺第二读者且涉及未知证据时整项继续隔离。多分块文件按全部必需分块核对，不能凭其中一个分块成功就标完整。

独立进度库默认 `/var/lib/imgw/ops-app/kb-backlog-recovery.sqlite3`，支持参数或 `OPS_KB_BACKLOG_DB`；测试全部指定临时目录。不升级 archive.sqlite3。私有路径检查、非阻塞单写锁、`BEGIN IMMEDIATE`、FULL 同步保护清单 epoch、来源状态、响应、预留与各队列进度。新增优先；每轮先处理最多 `policy.batch_size` 个新增，再处理独立的 `backlog_limit` 个积压，二者共享原请求/token预算和限速，持续有新增时积压仍有槽位；预算不足时不绕过闸门。批次完成后释放写者，下一轮重新优先新增。进度分别记录，持久计划不存来源正文；重启直接恢复未完成新增/积压，不靠向后调水位找任务。首次实际请求把预算及积压批大小共同绑定，不能每轮偷偷放大。

`Scheduler` 只接受显式注入的来源核验、原持久响应读取、本地幂等落库与请求函数，**没有默认生产 provider 或 CLI apply**。新请求前持久记 `provider_started` 与全部预留；响应持久后才续本地步骤。进程中断、异常或超时使结果未知，整队停止，重启不重发。落库适配器必须幂等，处理“本地已提交、进度未提交”的中断；不能让回调隐含模型请求。

## 四条未知请求永久隔离

`kb-source-quarantine-v1` 恰好绑定 3 条微信与 1 条阿里未知请求，每条包括原请求检查点摘要、prompt SHA、全部来源 ID 摘要、原始来源 `source_sha256` 和原文内容 SHA。任一交叉命中均拒绝，覆盖换窗口、换版本、换模型，以及换来源 ID 和解析正文但仍复用原始来源。若上游连原始 ID/原始字节都改变，必须先以已核验的平台别名映射归一，无法证明时停止，不猜成全新资料。微信原 hold、scope、三份未知预留及阿里原 infra_blocked/profile 熔断/未知预留/attempt 全部保留。不能凭有另一个读者成功就重发未知读者。

新增 `source-quarantine-v1.patch` 在前两份补丁后应用：loader 增加 root 配置 SHA 绑定；每轮在任何恢复库/水位写入前验证原来源；提取前及缓存/模型调用边界再次核验。`source_gate.py` 的微信请求摘要必须与原三条 hold 的 checkpoint/prompt 精确匹配。清单变化即阻断。发布生成器现在还要求 `source-quarantine.json` 审阅输入，输出四份互相绑定的私有待审配置；阿里那一条的来源与请求审计须由阿里副本独立核对，不能只凑四条数量。

这份补丁加强微信自然增量入口；阿里未知证据也参与离线积压协调器的全局隔离。**没有把协调器接入阿里现有 worker，也没有解除其 profile 熔断。** 禁止直接 `apply_recovery()`；该函数会将基础设施未知窗口重排 pending。阿里 106 条新证据拆分适配、精确恢复准入和发布步骤须在总指挥的候选装配阶段完成并单独演练；现有 timer 不另建、不改。

## 量、模型、预算、速度与停止条件

微信1,729条停机后入库候选的毛估量为1,732个稳定单元、3,464次双读者首次请求；其中129条命中未知隔离，**毛估量不能作执行预算**。排除后为1,600条证据、1,603个单元、最多3,206次双读者首次请求，仍需原动态授权、全局隔离和稳定清单审核。可靠双响应的本地续做候选按单元另计，详见微信记录；同一文档的分块不能当成多份资料，也不能用12块的响应证明尾部完成。

阿里可确认的未请求来源是106条；若适配器验证每条恰为一个稳定单读者单元，则首次请求上界为106次，额外分块将改变该估算。两边在各自未知交叉排除后合计1,706条未请求来源候选，在上述拆分假设下最多3,312次首次请求，仍需跨渠道全局隔离核验；不含未来自然新增、4,947条覆盖待确认和那1份全文完整性待确认文档。prompt token/输出预留仍需拆分后核算，完整恢复量目前不能确定。两个渠道均按必需读者数减去已核实的持久响应数计新请求；未知、已完成、缺时间与未授权来源计0，不进入调用预算。不能把所有盘点候选当可执行总数。

拟用模型：只从现有已核验技术契约中选择老板明确批准的模型，**本次具体选择未知**。微信三个冻结模型禁新请求，原 `free_tier_exhausted` 等封锁保留；其余模型有配置也不代表可用额度。阿里本地账本只确认累计 6 个 completed_calls、26,819 actual_total_tokens，以及 14,782 unknown_reserved_tokens；窗口审计是 6 次准入/5 次成功/1 次失败，口径不同。云端剩余额度、当前单价、未知请求实际计费与新增费用上限均为**未知**，没有代填预算，也没有用网上不匹配的型号价格估算真实费用。

拟议速度供老板拍板：新增优先，积压每批最多 **2 个单元**，模型物理请求之间至少 **30 秒**，每轮完成就重新检查新增；微信双读者的两个物理请求均计入限速和预留。这是候选速度，未生效。工具无预算时只允许本地续做；新请求必须显式批准模型名单、请求数/保守 token 预留上限、批大小、间隔与连续失败阈值。token 上限不能代替老板费用上限，真实适配器还须证明所选模型的免费额度或把可核实单价绑定批准的金额闸门。

停止条件：任一新未知结果立即停全队；原隔离/来源/清单/代码指纹变化立即停；原熔断打开或额度不确定不请求；预算触顶不再准入新模型请求；连续本地失败达到阈值停止并保留检查点。建议连续失败阈值 **1**，待审批。未知永不自动重试，内容失败不得自动换模型重发。流量超过成功速度时记录新增/积压各自等待量，不能提高并发绕预算。

维护时只读查看两队列 pending/local_pending/done/unknown、最近成功、保守预留、来源变更与停止原因。`local_failure` 达阈值后只能通过显式 `resume_local(expected_manifest_sha, proof_sha256, verify_local)` 恢复：核对原失败来源、全部检查点、当前授权与隔离、幂等落库收据，验证失败审计哈希链；先追加恢复审计，再在同一事务解除当前本地失败标记，旧停止记录永久保留。此方法没有 CLI、默认核对器或模型调用，任何 unknown/provider_started 均拒绝，模型预算不变。禁止手工删除 stop/epoch 或回灌旧库制造继续运行。新增来源再次进入审核时保留既有 ID/指纹；扩批用新审核修订和显式迁移设计，不重建进度库。

## 可重复验证与部署、回滚

工具命令只生成/核对私有候选，不写生产。`SNAPSHOT_*` 必须是已通过 SQLite backup 准备的 `/tmp` 私有副本；不能传生产路径。真实输出不进入仓库。

```sh
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/inventory_wechat.py --snapshot "$SNAPSHOT_WECHAT"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/inventory_alibaba.py --snapshot "$SNAPSHOT_ALIBABA" --private-review
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/verify_patch.py
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/backlog.py build --input "$REVIEW" --quarantine "$QUARANTINE"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/backlog.py verify --input "$MANIFEST" --quarantine "$QUARANTINE" --sha256 "$REVIEWED_SHA"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/draft_release.py --snapshot "$RELEASE_SNAPSHOT" --after "$APPROVED_AFTER"
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python -m pytest tests/kb_recovery -q -p no:cacheprovider
```

`baseline.json` 固定原七文件 SHA、三份补丁 SHA、顺序及新增 `kb_source_gate.py` 摘要。verify 只复制源码到新建临时目录，逐补丁 `patch --batch --fuzz=0 -p1 --dry-run`，再对副本应用并作无 pyc 的语法检查，没有部署目标参数。原有两份补丁的字节保持不变。

部署由总指挥执行，本轮零执行：先审阅缺口与四条未知来源绑定，完成真实来源/本地续做/两读者及阿里新单元适配；明确预算与窗口；在禁止真实调用的候选环境核对基线、dry-run 和所有虚构回归。再在批准窗口控制现有写者、backup 账本与 Catalog/独立进度、备份原七文件和配置的字节/权限/SHA。安装候选时新增 source gate 配置由 root 持有且不可被 imgw 修改；原目录权限若不符就选择经审阅的专用 0700 状态目录，不能随意放宽校验。完整只读预检通过后，由总指挥统一跑上线 e2e 并按批准的有限范围启动。真实新增与积压是否可用仍须后续验收。

回滚先阻止新模型调用，再保留所有新增调用、未知预留、响应、事实、审计与进度快照；恢复原 overlay/配置字节和权限并核对原 SHA，退出新增 source gate 引用。**不倒灌 calls、usage、Catalog 或两个恢复进度库，不删除 stop/epoch/hold，不前移边界。** 旧两条清单仍与现账三条不符，回滚后应继续阻断；回滚目标是停止调用和保留证据。

## 待批准与交付边界

需要老板点头的最小清单只有：① 本轮可用模型及新增费用上限/可核实免费额度；② 具体生产启动窗口（同时固定自然新增边界）；③ 是否同意积压每批 2 单元、物理请求至少间隔 30 秒及连续失败 1 次停止。新增与停机积压都处理已决定；四条未知永久隔离已是必须保留的约束，不再次把这两项当待决策。

需要总指挥合并的共享改动：只在 `web/src/data/requirements.json` 的 H11 更新说明为“R3-C 已修订新增+停机积压范围，来源级未知永久隔离，已交付副本盘点与虚构演练候选；待预算、窗口、速度审批及真实适配/上线验收”。本任务未改该文件、README、master-plan、其他业务模块、路由、导航或 rehearsal_server.py。

安全自查：无新增 HTTP 入口或权限；保留生产授权核验，盘点不授予使用权限。输入只读副本路径做权限/链接检查，补丁名拒绝绝对路径和 `..`，SQL 参数化且动态表名来自固定内部列表；不执行附件或模型内容。未读取/输出任何密钥值，仓库只含代码、虚构测试、源码 SHA 和脱敏汇总，真实清单/正文不进仓库或日志。没有渠道发送器；未知状态先持久化再阻断，源/内容/prompt 交叉隔离，已完成与可靠响应不重请求。

没有完成或无法证明的：生产恢复尚未执行；阿里稳定新证据单元与两渠道生产协调器适配尚未装配；微信动态授权、783条旧请求的精确溯源和1份文档尾部尚未补齐，不允许用推断放行4,947条覆盖待确认来源；免费余额、单价、真实模型质量、真实持续新增/积压吞吐、完整权限加载及真实浏览器未验收。下面测试成功只证明虚构数据上的行为，不代表真实资料可用。

### 本轮文件与验证记录

新增：`deploy/kb/backlog.py`、`inventory_wechat.py`、`inventory_alibaba.py`、`source_gate.py`、`source-quarantine-v1.patch`；`docs/r3c-wechat-inventory.md`、`r3c-alibaba-inventory.md`；`tests/kb_recovery/test_backlog.py`、`test_inventory_wechat.py`、`test_inventory_alibaba.py`、`test_source_gate.py`、`rehearse_backlog.py`。

修改：本文件；`deploy/kb/baseline.json`、`verify_patch.py`、`draft_release.py`；`tests/kb_recovery/recovery_fixtures.py`、`test_boundary.py`、`test_draft.py`、`test_rehearsal.py`。原隔离和自然增量补丁未改字节；新补丁顺序追加，原生产七文件 SHA 仍逐个匹配。

当前身份 imgw。`sudo -n -u imgw true` 因快照权限被拒，未改权限，按约定直接执行后续命令；原文为：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

| 命令（均设 `PYTHONDONTWRITEBYTECODE=1`） | 结果 |
| --- | --- |
| `PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python -m pytest tests/kb_recovery -q -p no:cacheprovider` | 117 passed，0 failed |
| `PYTHONPATH="src:tests/pipeline" /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider` | 77 passed，0 failed |
| `PYTHONPATH="src:tests/kb" /opt/imgw/venv/bin/python -m pytest tests/kb/test_core.py tests/kb/test_cli.py -q -p no:cacheprovider` | 22 passed，21 subtests passed，0 failed |
| `/opt/imgw/venv/bin/python deploy/kb/verify_patch.py` | 原基线、三份 dry-run 与隔离语法校验通过 |
| `PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python tests/kb_recovery/rehearse_backlog.py` | 重复演练结果一致：新增完成1、积压完成3、已完成跳过1、仅本地续做1、假runner请求3、重启重复0、未知重发0、生产写入0 |
| `PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python tests/kb_recovery/rehearse.py` | 原自然增量演练通过：原hold3不变、假runner2次、重复轮询新增请求0 |

回归通过合计216项，另21个subtests。完整已有 `tests/kb` 的同模式命令仍收集失败，未补占位包或改其他任务模块掩盖；原样关键信息如下，不能称全量回归通过：

```text
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/kb/test_api.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.38s
```

开发中曾有一次本任务夹具错误，已修复并重跑：

```text
AttributeError: 'types.SimpleNamespace' object has no attribute 'manifest'
FAILED tests/kb_recovery/test_draft.py::test_draft_three_linked_manifests_without_mutating_source
1 failed, 56 passed in 14.04s
```

其中真实副本仅用于只读盘点、响应/来源摘要比较及55条未知来源清单校验；调度、请求、事实续做、水位、失败重启和篡改测试全部使用虚构资料。没有运行 e2e_smoke、固定端口服务、tsc、前端构建、真实AI或渠道发送。

## 历史附注：R2-F 原文（已被 R3-C 取代）

以下为原交付全文，历史统计只代表当时副本。“只自然新增、跳过停机积压”、旧上线清单和原待批准项均不再作为当前方案执行；现以本文 R3-C 部分为准。

### 原题：KB 实时提取恢复方案（R2-F / H11，未上线）

2026-11-01，在独立开发快照核对。结论：**不能直接启动 `imgw-kb-live`，也不能只把两条限制改成三条。** 三条超时均没有第二读者的可靠响应检查点；旧记录、未知额度和审计必须原样保留。需要明确冻结三条未知请求，并固定只处理自然增量的边界，防止后来的新消息把旧窗口重新拼入模型。

本轮未操作任何服务、生产文件、生产数据库或网络配置，未读取 EnvironmentFile 或凭据，未调用真实模型、真实渠道。真实数据只用于只读副本溯源和统计；演练全部使用虚构证据、假 runner、临时 SQLite，并阻断 socket 连接。没有运行 e2e、固定端口服务、前端构建、commit 或 push。当前目录是调度器提供的独立快照，`.git` 为空，`git status --short` 返回 `fatal: not a git repository (or any of the parent directories): .git`，因此不声称有可核对的 Git 工作树差异。

已读 `CLAUDE.md`、`docs/master-plan.md`、`docs/data-pipelines.md`、`docs/knowledge-hub.md`、`docs/workflow-gaps.md` 的 WG-01 全文，以及开发接续入口、微信地图和真实生产源码。H11 属于知识层恢复；WG-01 中采集、媒体与真实持续性验收仍由原负责人完成。本交付不代表这些链路已接通。

## 服务入口、预检与直接根因

只读服务单元 `/etc/systemd/system/imgw-kb-live.service:8-22` 指定 imgw 身份、0700 运行边界、失败后 30 秒重启；drop-in `imgw-kb-live.service.d/dual-isolation.conf:3` 将入口换成 `/opt/imgw/wechat-kb-dual-live-v1/live_loader.py serve`。只读参考这两个文件；仅看 EnvironmentFile 的路径声明，没有打开其内容。

`live_loader.py:9-14` 校验文件类型、所有权、链接和可写权限，`:16-28` 校验模块 SHA 并保持同一模块图；`--preflight` **只验证模块装载，不验证模型账本、业务来源或水位**，不能用它作为上线验收。实际账本预检发生在 `kb_model_runtime.create_matrix():294-311`。`kb_unknown_isolation.capture():14-36` 和 `quota_isolation.install():5-25` 硬性要求两条未知；前者重算整个调用行及整个模型额度记录的 SHA，再要求与静态 manifest 完全一致。

`/run/imgw` 已修复不是这次恢复工作的待办，不更改目录、tmpfiles 或服务设置。真正阻塞是清单与现账不一致；若只放宽计数，旧隔离项还会因 `kb_extract.py:307-310` 的 `more=True` 持续待处理，且历史窗口会随新证据重新分组。以下调查给出第三条的来源与审计证据。


## 三条 hold 的只读核查（2026-11-01，快照时点）

实际读取的是挂载在本任务环境中的生产只读参考；不能据此声称已联系云端或确认真实账单。SQLite 均通过 `sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)`、`PRAGMA query_only=ON`、`source.backup(dest)` 复制到 `/tmp` 的 0700 私有目录，副本为 0600；只在副本执行下列分析，没有运行生产 worker。没有读 EnvironmentFile、API 配置或任何密钥。

### 统计与路径

- 模型 checkpoint：`/var/lib/imgw/wechat/kb-model-runtime/calls.sqlite3`。先从副本 `sqlite_master` 读 schema，再查询 `SELECT state,COUNT(*) FROM calls GROUP BY state`：`complete=1454, hold=3`，没有 `provider_started`。
- 模型额度账本：同目录 `usage.json`。只在内存提取统计。三条 hold 对应三个不同模型、三个不同 prompt 哈希，均 `attempt=1,state=hold,error_code=api_timeout`，`response_json IS NULL` 且 `response_sha256 IS NULL`。
- 三个对应额度记录均为 `blocked_reason=usage_uncertain,in_flight=false`，`unknown_reserved_tokens` 与 checkpoint 的 `reserved_tokens` 精确相等：两条旧记录分别 10510、9618，新增记录 7190。原始 JSON 和 checkpoint 不应改写、解冻或清空。
- scope 唯一记录仍为 `hold=api_timeout`。`deepseek-v4-flash-0731` 另有 `free_tier_exhausted` 和正的未知预留值；这不是本次第三 hold，不能仅按 `unknown_reserved_tokens>0` 生成隔离名单，也不得放松它原有的额度封锁。
- 旧隔离清单 `/etc/imgw/kb-two-unknown-manifest.json`：schema=`wechat-kb-two-unknown-v1`，holds=2。对两条清单记录重算 `SHA256(canonical_json(tuple(checkpoint)))` 和 `SHA256(canonical_json(model_usage_record))`，两类哈希均与旧清单完全一致。旧两条未变，是新增第三条使“精确两条”约束失败。
- 业务来源与 watermark：`/var/lib/imgw/video_knowledge_auto/equipment/catalog.sqlite3`。共享预算审计：同目录 `phase7-analysis-usage.sqlite3`。两库同样只读备份后分析。

### 来源与先后关系（UTC；群只给 SHA-256 前8位）

| 条目 | 群标识 | 请求模型 | 发起—终止时间 | 来源与证据统计 |
|---|---|---|---|---|
| 旧 hold A | G-3093ac1f | qwen3.8-max | 2026-10-28 00:02:05.686—00:02:50.764 | 来源窗口 2026-10-26 09:46:11—09:56:59；19个证据：17 message、1 business_attachment、1 business_supplier_attachment |
| 旧 hold B | G-3093ac1f | qwen3.8-2.4t-a95b | 2026-10-28 09:00:55.558—09:01:40.671 | 来源窗口 2026-10-26 09:57:04—10:10:23；14个证据：9 message、3 business_attachment、2 business_supplier_attachment |
| 新 hold C | G-1738b007 | kimi-k3 | 2026-10-29 07:27:25.630—07:28:10.710 | 供应商群，6个 message；来源窗口 06:39:59—07:25:39，入库 06:40:35.263—07:26:56.395；unit 脱敏标识 U-33629f79a4c8 |

来源不是按时间猜的：使用已读源码的 `_make_unit`、`_document_units`、`units` 算法，从 catalog 副本重建只在各调用开始前已入库的证据窗口，拼接供应商 PROMPT，再与 calls 的完整 `prompt_sha256` 精确比较，三个窗口分别命中。实际正文只在该隔离分析进程内参与哈希，没有输出、写仓库或日志。这项重建只证明来源关联，不替代授权策略验收。

旧清单文件 mtime 为 **2026-10-28 15:38:34.773 UTC**，晚于旧两条而早于新 hold C。清单生成函数是一次性读取当前账本并要求精确2条，既不自动增补也不跟随运行期新错误；所以10月29日产生的第三条不在10月28日静态清单中。mtime只能证明文件时序，不是独立的创建审计。

新 hold C 的同一完整 prompt 在 `calls` 中只有两条不同读者的记录：

1. 读者 A `deepseek-v4-pro-0813` 于 07:27:21.699 发起、07:27:25.606 成功 checkpoint，返回4个候选事实（未输出候选正文）。
2. 读者 B `kimi-k3` 紧接着发起，约45秒后超时并进入 hold。B 没有回复 checkpoint，额度账本保留7190未知预留，故 **B 的结果和实际计费仍未知**；A 成功不构成重新发送 B 的依据。

共享预算审计副本同一时段有 A 的 `complete` 和 B 的 `failed_reserved` 各一条；B 的 `failure_code=api_timeout`，created/finished 分别07:27:25.638和07:28:10.703，独立预算层保留预留5463。此表没有 prompt/model 外键，故与 B 的关系属于调用链和唯一时间窗吻合的辅助证据，不能声称有跨库强外键。它的不同预留口径不能拿来覆盖模型 checkpoint 7190。

### 与旧审计、watermark 的关系

- `kb_segment_audit` 总18条：`kb_segment=7,kb_learning_segment=11`。最晚归档时间2026-10-28 00:01:37.388 UTC，均早于新 hold C。
- G-1738b007 在旧审计有2条 `failed/model_exception`，原记录时间2026-10-25 01:01:02、归档时间2026-10-25 07:16:04.772；按完整 `unit_id` 关联，它们均不是新 hold C 的 unit。不能把这两条旧修复记录当成第三请求可重试的许可。
- 新 hold C 对应完整unit在当前 `kb_segment`、`kb_learning_segment` 和 `kb_segment_audit` 均无行。读者 B 的受控错误使提取在 `_record` 前退出，符合源码；新未知请求的现存审计在 calls、usage 和共享预算账本，不是旧 segment 失败行。所有这些原记录都应保留。
- G-1738b007 的 `kb_live_state.seen_at=1793258816.395419`，`processed_at=1793258644.7477236`。即07:26:56.395已看到新入库，但处理水位停在07:24:04.748；启动旧逻辑会再次把它判为 pending。
- 即便只按旧 prompt 哈希跳过，后续同群自然增量可能把6条窗口扩成7条、产生新prompt哈希；所以仅扩展“2改3”不够，必须加独立冻结的历史边界/显式源清单，让旧证据不被新窗口带回模型。隔离的窗口不能永远通过 `more=True` 卡住群水位造成每5秒重跑。

### 源码证据（行号为所读快照基线）

- `/opt/imgw/wechat-kb-dual-live-v1/video_knowledge/kb_model_runtime.py:19-21`：真实账本根目录与旧manifest路径；`:113-119`：calls/scope schema；`:141-151`：只读预检；`:217-245`：锁、检查完整旧checkpoint、未知请求阻断；`:249-287`：先写 provider_started、成功 checkpoint、超时 hold。
- `/opt/imgw/wechat-kb-dual-live-v1/video_knowledge/kb_unknown_isolation.py:14-36`：静态manifest生成、固定两条、行/额度哈希；`:38-40`：要求 capture 完全相等；`:54-63`：固定两条的窗口确认。
- `/opt/imgw/wechat-kb-dual-live-v1/video_knowledge/kb_extract.py:69-165`：来源窗口与哈希算法；`:294-323`：prompt、隔离跳过、受控模型错误使循环停止；`:340-357`：第二读者失败直接返回，不写成功segment。
- `/opt/imgw/wechat-kb-dual-live-v1/video_knowledge/kb_live.py:140-165`：watermark取来源时间与入库时间最大值；`:212-224`：先写seen并判pending；`:257-279`：完成全部窗口并重建后才推进processed。
- `/opt/imgw/wechat-kb-dual-live-v1/video_knowledge/kb_model_runtime.py:313-338`：精确source-bound修复准入与旧行归档，不能借旧准入重发新未知请求。
- `/opt/imgw/wechat/video_knowledge/cpq_batch.py:42-43`：catalog/state真实路径；`history_backfill.py:420-469`：来源授权入口；分析没有用凭据配置或调用该联网/业务入口。
- `/opt/imgw/wechat/video_knowledge/assistant_models.py:95-98,132-137`：共享预算先预留、失败保留为failed_reserved。

复核查询摘要（只能对新建私有副本执行）：`SELECT state,COUNT(*) FROM calls GROUP BY state`；`SELECT model,error_code,COUNT(*),SUM(response_json IS NULL),SUM(response_sha256 IS NULL) FROM calls WHERE state='hold' GROUP BY model,error_code`；`SELECT original_table,COUNT(*),MAX(archived_at) FROM kb_segment_audit GROUP BY original_table`；逐条解析审计 JSON 后只比完整unit，不打印JSON；完整prompt在内存重建并比SHA，不输出正文。分析脚本留在 `/tmp/r2f-reconstruct.py`，实际副本位置记在 `/tmp/r2f-holds-location`（均不纳入仓库）。

本调查没有把真实数据用于演练或测试，未启动任何服务，未对生产路径写入。云端结果和真实自然增量恢复仍需老板批准后由总指挥验证。



## 方案与隔离补丁

补丁只放在仓库，尚未装入生产：

- `deploy/kb/unknown-isolation-v2.patch`：改 `kb_unknown_isolation.py`、`quota_isolation.py`。
- `deploy/kb/live-increments-v1.patch`：改 `kb_extract.py`、`kb_live.py`、`kb_model_runtime.py`、`live_loader.py`。
- `deploy/kb/baseline.json`：上述 7 个原文件的完整 SHA-256、补丁 SHA 和顺序，以及原隔离/发布/运行策略与服务单元的只读参考 SHA，是部署核对基准。`isolation-baseline.json` 保留隔离两文件的基线核对；不含任何真实业务内容。
- `deploy/kb/verify_patch.py`：逐文件核对基线，复制到新建的 `/tmp/kb-recovery-code-*`，执行两份 `patch --batch --fuzz=0 -p1 --dry-run`，再只给临时副本打补丁；没有可配置的生产写入目标。
- `deploy/kb/draft_release.py`：只读 `/tmp` 内当前用户持有的 0700 副本目录及 0600、无软硬链接的三个输入文件，输出新的 0700 临时目录与三份 0600 待审清单。不会安装或调用模型。

### 三条未知请求：精确冻结，不解除原 hold

新隔离 schema 是 `wechat-kb-unknown-v2`，固定 `hold_count=3`，每条新增 `state=quarantine,error_code=api_timeout`。这里的 quarantine 是**清单上的永不自动重试标记**；原 `calls.state=hold`、scope、额度和审计都不改。

每条仍包含 model、cache_key、attempt、prompt_sha256、reserved_tokens、checkpoint_sha256、ledger_sha256。必须恰好三个不同模型、三个不同 prompt，均为没有 response/response_sha 的 api_timeout hold；scope 全表必须只有 `hold=api_timeout`，不能有 `provider_started`、第四条 hold 或其他 scope；任何模型 in_flight 都拒绝。每次启动预检及每次请求的调用锁内重新核对完整集合和哈希，新增或变动即停止，不自动刷新清单。

额度隔离仍只在内存将**三条哈希精确匹配的 usage_uncertain 记录**从准入检查的视图排除，不改原 usage.json，不释放任何未知预留，也不解除 free_tier_exhausted 等其他封锁。禁止向冻结模型发新请求；禁止任何读者调用冻结 prompt，即使另一个读者有成功缓存。正常请求先检查点、后响应缓存；缓存的完整性检查和调用锁仍沿用原实现。

### 只处理自然增量：固定起点、稳定单元、持久指纹

另有 root 持有的 `/etc/imgw/kb-live-recovery-v1.json`，只允许 `schema,after,isolation_sha256` 三个字段。`after` 是老板确认的 UTC Unix 整秒起点；`isolation_sha256` 绑定三条隔离清单原始字节。发布清单 `/etc/imgw/kb-live-dual-v1.json` 新增 `recovery_boundary_sha256`，同时重算所有模块 SHA。loader、每轮配置读取和每次模型调用前均校验边界 SHA，改边界即拒绝。

新增独立 SQLite 默认 `/var/lib/imgw/ops-app/kb-live-recovery.sqlite3`，可用 `OPS_KB_RECOVERY_DB` 指定其他绝对路径。沿用私有 SQLite 路径检查：父目录当前用户持有且 0700，文件 0600，无软硬链接；不替用户自动创建不可信父目录。外层保留 `kb-build.lock` 单写锁，独立库使用 `BEGIN IMMEDIATE`、FULL 同步及事务。

独立库仅保存 `recovery_epoch` 的切换边界摘要、`recovery_sources` 的证据 ID 摘要和来源指纹；不复制正文或真实身份。指纹覆盖正文、群、角色、来源/入库时间、来源种类、来源哈希、分类、owner/scope。持久 epoch 防止重启后把切换时间调早；已接纳证据的任一指纹变化都停止，不能借 KL/KD 单元切换或角色变更重新请求。构建提取单元时再次比较本轮审核行与当前授权行，读取之间的变化也拒绝。

自然增量须先通过原 `kb_authorized()` 权限与来源核验，再满足：

1. 有可靠 `source_created_at`，且来源时间和 `created_at` **都严格大于**固定起点；不以缺失来源时间的 received_at 代替。
2. 非 `historical_message`、非客户询价；起点前的原证据、停机期间历史积压、历史补录、缺时间证据均不补跑。
3. 按单条证据的来源/入库时间安定后再处理，避免持续活跃群使旧增量一直等待。文本自然增量用稳定的 `KL:<evidence_id>` 单元，文件继续用稳定的 KD 分块；不会将历史与新消息重新拼窗。
4. 未完成的自然单元决定是否调度，不能仅比较群时间水位；同一时间戳晚到的新证据仍能处理。成功响应先持久 checkpoint，本地写事实失败后复用缓存，不发第二次模型请求。
5. 原三条隔离项跳过后不再置 `more=True`。已失败的自然增量不使用旧 repair admission 自动重开。任一新未知会破坏精确清单并在下一请求前阻断；保留其 provider_started/hold 和未知预留，不重发。

预检在任何新增独立库、Catalog schema 或 watermark 写入之前执行。仅有历史、没有自然增量时，原 `kb_live_state` 不被抬高；首次有效运行只在独立库固定 epoch。`seen_at` 表示看见的已安定自然来源；`processed_at` 仅在本群候选全部到达成功终态、没有历史失败片段、且非配额暂停并完成本地重建后推进。失败片段不会被以后新消息的成功覆盖水位；新单元仍可独立处理，不对失败项自动重试。水位仍不能证明全部渠道已采齐、历史已处理或所有事实已核实。

原始持久存储是现有 Catalog 与调用账本；没有修改 archive.sqlite3、Catalog 的版本号或原表约束，新增恢复元数据放在独立库。正常业务提取仍沿用原事实和片段审计机制；恢复代码不删、清空或重置旧审计、旧事实、调用或额度。

### 清单格式与生成方式

以下仅示意格式，值为虚构；不可拿示例投产：

```json
{"schema":"wechat-kb-live-recovery-v1","after":1000,"isolation_sha256":"虚构的64位SHA摘要"}
```

在授权的隔离准备区先生成新一份 SQLite 备份，`calls.sqlite3`、`usage.json` 都来自稳定的同一审阅时点，且保留原文件。`quota-review.json` 只含经审阅的 `quota_baseline` 映射和 `legacy_quarantine` 列表，必须取实际矩阵基线，不能猜值或把旧隔离列表清空。生成器不导入模型客户端或凭据加载器。源码基线须仍匹配：

```sh
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/verify_patch.py
# SNAPSHOT_DIR 必须是 /tmp 中已准备好的私有副本；CUTOVER 是老板确认的秒数。
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/draft_release.py --snapshot "$SNAPSHOT_DIR" --after "$CUTOVER"
```

输出目录里是三份互相绑定的私有待审文件：`kb-two-unknown-manifest.json`（沿用现有路径名，内容升级为 v2 三条）、`kb-live-recovery-v1.json`、`kb-live-dual-v1.json`。**不得把真实输出复制进公开仓库，也不能直接发布。** 两个账本不是跨文件原子快照；准备期间应由总指挥确认所有写者边界，复核 source/calls/usage 的稳定性。最终只读预检再次重算全部哈希，不能用旧文件 mtime 或数量相同代替精确核对。脚本只生成草案，不证明预算、云端结果或生产可用性。

## 演练与验证结果

`tests/kb_recovery/` 使用真实生产源码基线打补丁后导入，调用、Catalog、独立恢复库和来源均为虚构；来源授权入口和画像重建以假服务替代，真实解析/事实存储/检查点/锁/事务/水位逻辑保留。网络由 socket 补丁禁止。边界 root 所有权在当前映射快照无法真实复现；相应读入用仅针对虚构文件的受控替身，原生产 owner/mode/nlink/软链接检查未放松。没有在生产启动入口运行任何 serve/once。

可重复演练：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python tests/kb_recovery/rehearse.py
```

结果：`结果=通过，历史请求次数=0，自然增量单元=1，假 runner 请求次数=2，成功响应检查点=2，重复轮询新增请求=0，原 hold 保持不变=3，生产写入=0`。两次请求是两个独立读者各一次，不是对同一读者重发。

新增套件覆盖精确三条预检、全部变动拒绝、来源时间缺失/历史补录排除、边界变更拒绝、同时间戳晚到、跨单元/角色/群/时间/正文变更拒绝、活跃群安定、真实 round 的全部 IO 重定向、候选事实带来源与时间、内容失败跨轮不推进水位、不使用 repair admission 重试、缓存恢复、新超时不重发，以及清单生成与源副本不变。

要求的 sudo 方式在本快照被禁止，先尝试后以现有 imgw 身份执行等价命令。错误原文：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

验证命令与结果：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python -m pytest tests/kb_recovery -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/pipeline" /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb" /opt/imgw/venv/bin/python -m pytest tests/kb -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb" /opt/imgw/venv/bin/python -m pytest tests/kb/test_core.py tests/kb/test_cli.py -q -p no:cacheprovider
```

新增恢复套件 `57 passed in 18.86s`；管道回归 `75 passed in 6.97s`；知识库核心/CLI 回归 `22 passed, 21 subtests passed in 11.35s`。完整知识库回归未跑通，原样失败摘要：

```text
ERROR tests/kb/test_api.py
E   ModuleNotFoundError: No module named 'console.media'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.69s
```

这是现有快照缺依赖造成的收集失败，未替其他任务补模块、伪造占位包或把失败说成通过。完整链路回归仍须总指挥在完整开发副本执行。

首次统一加载器还遇到本任务测试配置错误，已修复且重跑：`ModuleNotFoundError: No module named 'wtapi_events'`，`21 errors in 5.16s`；补入只读生产源码搜索路径后通过。一次演练断言错误地把实际以 `+` 分隔的 readers 当 JSON：`json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)`，`1 failed, 2 passed in 1.32s`；核对真实 `_record()` 格式后修正断言，最终套件通过。这些失败不涉及生产或真实请求。

此外，新隔离函数在真实 calls 的**只读备份副本**及内存额度统计上完成 `capture/assess`：`hold=3,complete=1454`，与副本前后统计一致；清单只留在内存，无真实模型调用。此项仅验证三条账本的契约匹配，不是实际 Matrix/loader 的完整权限、额度或自然增量验收。

## 逐步上线清单（本轮全部未执行）

1. **总指挥审查**：核对 `baseline.json` 全部原文件 SHA，审查两份补丁、清单生成器和本报告。任何基线变动先重新比对，不带 fuzz 强行套用。当前恢复套件、管道回归、核心/CLI 与 dry-run 可在快照运行；补全完整知识库回归缺依赖。统一 e2e 由总指挥在其环境另行安排，本轮禁令不变。
2. **需要老板点头：历史边界与未知保留。** 确认三条未知请求永久隔离、预留不释放；确认从哪个 UTC 时点起只处理自然增量，接受停机期间旧积压、源时间缺失或历史补录继续等待专项授权。不得通过调整时间、重建独立库或改源 ID 绕过隔离。
3. **需要老板点头：生产变更与预算。** 确认生产安装、服务操作窗口及允许的新模型预算。此次任务授权不包含这些动作。总指挥在单写者受控、禁止发送和禁止真实模型的准备阶段，分别备份原 overlay 七文件、原三份配置（新文件记为原先不存在）、calls/usage/Catalog/共享预算与现存独立恢复库；SQLite 使用 backup 接口而非拷贝正在使用的主文件。备份保存在私有区，记录 SHA 和权限，不能读 EnvironmentFile。
4. 在隔离候选对照相同基线打补丁，以固定 cutover 和新备份生成三份待审清单。先确认三条行和三个额度哈希仍一致、没有 provider_started 或第四未知；人工核对清单内容和互相绑定。默认独立库父目录必须 imgw:imgw 0700；若现有 ops-app 权限不满足，不在本任务改权限，部署方改用已有专用 0700 目录并设置 `OPS_KB_RECOVERY_DB`，或另外审查目录方案。不得复用其他业务 SQLite。
5. 经审查只安装候选 overlay 和三份 root 持有、不可被 imgw 改写的配置，保留原服务单元/drop-in，**不修改已有 EnvironmentFile**。新模块 SHA 必须与新发布清单一致；loader 本身和所有原源码须留好备份。对账本先执行完整只读隔离预检，不能只运行 loader 的 `--preflight`。隔离验证完成前不启服务、不抬水位。
6. **需要老板点头：真实新请求与服务启动。** 总指挥才可按批准的服务窗口启动。先观察三条原 hold 与未知预留完全不变，原 `processed_at` 不被平推，重复轮询没有旧请求；仅对批准 cutover 后自然新证据核对两个读者的持久响应、事实来源/时间和水位。观察至少覆盖真实自然增量与后续重复轮询，不能用假 runner 的通过替代。不能给供应商群或客户发送测试消息。
7. 新增未知、依赖/边界/源指纹变化、预算不确定、任何旧源再次请求：停止放行并由总指挥处理，保留全部新增审计和未知预留。不要自动重发、自动换模型或重新生成清单去掩盖问题。对长期运行还要观察积压增长、失败片段与水位落后，而不仅是 service active。

## 回滚与风险

- **回滚由批准的总指挥执行，本轮不操作服务。** 发生问题先按服务窗口阻止新模型请求，保留当前调用、额度、共享预算、Catalog 和独立恢复库的只读备份及日志统计。恢复部署前原七文件及原隔离/发布配置的字节、SHA 和权限；原先不存在的恢复配置可退出引用并保留私有副本供审计。
- 不把 calls、usage、共享预算或独立恢复库回灌成部署前版本；否则会遗忘已经发出的请求、未知结果和已接纳来源，造成重复调用。事实写入也不通过倒灌 Catalog 清空；保留新事实及审计，另做经审核的逻辑处置。
- 回到旧代码后，原 manifest 两条与现账三条仍不匹配，应保持不放行。**回滚目标是停止新增调用并恢复原代码可追溯状态，不是让旧版自动继续跑。** 不调整水位、清除 hold、删除审计或重开旧修复准入来制造“恢复”。
- 采用每证据双读者，可能增加小消息的请求次数；上线预算与限速需老板确认，保留现有每轮上限/额度熔断。模型是否可用、免费余额是否确定及云端旧请求是否计费，本轮都未核实。
- 切换时刻之前或时间不可靠的消息会被保守排除，旧积压没有被恢复处理；上游解析、授权、附件采集链路未在本任务验证。证据指纹要求上游来源不可变；变更会停止并等待人工审查，不偷偷重新解释旧证据。调用发出到响应持久化之间仍可能进程中断；此时保留 provider_started 并阻断，保障的是“不在未知时自动再发”，不是对外部模型执行结果作 exactly-once 承诺。
- 当前快照将 root 文件映射为 nobody，不能据此放宽生产 loader 的 owner 校验。真实 root 路径/权限及完整服务装载、自然数据验收留给部署环境；假服务验证不证明真实数据可用。

## 安全与交付边界

越权：原来源授权和写事实时角色核验保留，测试替身只存在于测试；没有公开 API、路由或导航变动。路径：原 root 依赖检查、SQLite 私有目录/文件/链接检查保留，离线工具只创建新临时目录，清单路径禁止绝对名和 `..`。注入：SQL 值参数化，表名仅沿用原内部角色映射，无任意命令拼接；模型原文按原提取契约当数据。泄露：公开文件只有代码、虚构 fixture、哈希基线和脱敏统计，真实业务副本只在 `/tmp` 私有目录；未输出凭据、群名、正文、真实价格或响应内容。重复发送：没有任何渠道发送路径，假 runner 演练禁止网络；模型原未知请求、额度预留和审计保留，缓存与显式隔离覆盖恢复场景。

需要总指挥合并的共享文件改动：**本轮共享文件、路由表、导航表和 rehearsal_server.py 均零改动。** 需求账本 H11 建议由总指挥记为“已交付只读调查、三条未知精确隔离补丁与虚构演练；未上线，真实自然增量待批准验收”，不能标成真实服务已恢复。README、master-plan 和其他任务模块未修改。

阿里部分只完成下述只读调查和最小方案，没有交付/安装阿里 worker 修复，没有新建 timer。其生产未知隔离、熔断确认和自然新窗口消化须单独审查授权。


## 阿里聊天分析窗口：只读调查

本节是 2026-11-01 07:54 UTC 左右的独立快照，不代表平台完整历史，也不是恢复成功证明。未运行任何分析 worker、真实模型、渠道请求或服务启停；没有读取 EnvironmentFile、凭据、聊天正文或模型响应正文。先核对真实表结构，再以 `sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)`、`PRAGMA query_only=ON` 和 SQLite `backup()` 将分析账本复制到 `/tmp` 的 0700 临时目录；所有后续 SQL 都只查询该副本。临时副本含私有数据，不进入仓库、测试或日志。

### 谁生成，谁消化，谁触发

* `channel-status` 的安全统计脚本 `/opt/imgw/scripts/claude_channels_status.py:11-16` 指向 `/var/lib/imgw/icbu/conversation-analysis/analysis.sqlite3`；`:33`、`:62-66` 直接统计 **全部版本** 的 `windows.state`，没有限定当前版本，而且 `infra_blocked` 会归入 `other`。它不是消费者。
* 已有 `/etc/systemd/system/icbu-conversation-analysis.service:8-14` 是 imgw 身份的 oneshot，执行 `/opt/imgw/scripts/icbu-conversation-analysis/icbu_conversation_quota.py run`。同名 `.timer:5-8` 已配置开机 180 秒后启动、每次服务结束 120 秒后再触发、10 秒精度。本轮没有新建、启用或更改 timer。
* `quota_runtime.py:57-76` 是现用编排：在原有 worker lock 中读取来源 → `refresh_preserving()` 规划 → 旧成功结果绑定 → 注册冻结清单 → 消化允许的新来源 → 刷新来源 → 在剩余预算内发布索引。`:63` 和 `analysis_multimodel.py:186-208` 说明新来源版本会新增 `analyses`、`windows`，源未变就保留原计划；旧版本留下作审计，不能按全部 pending 数批量重放。
* 来源来自入站归档库及历史索引账本：`analysis_runtime.py:73-96` 只读读取这两库，来源种类明确为 `archived_history`，排除非历史及单独 PWA 来源。这条分析链不是采集器，也不等于所有实时富消息已可分析。
* 消费者 `analysis_quota_pool.py:81-141` 只选 `current_conversations.analysis_version` 指向的窗口，处理 pending/retry_wait/infra_blocked，跳过冻结来源版本，跨模型内容失败最多 3 次。`:131-140` 遇一次失败就结束本轮；未知基础设施失败会打开 profile 熔断。发布走 `quota_runtime.py:70-74` 的 `publish_ready()`，仍是内部、有来源的候选知识。
* 本机非敏感预算配置仅为 `enabled=true, max_windows=10, max_index=5, seconds=600`。字段定义见 `analysis_runtime.py:54-65`；不能靠调高批量数绕过冻结或熔断。

### 实际计数与积压原因

只读备份副本查询摘要：

| 范围 | pending | validated | retry_wait | needs_repair | infra_blocked |
| --- | ---: | ---: | ---: | ---: | ---: |
| 全部版本（1555 窗口） | 1013 | 538 | 2 | 1 | 1 |
| 当前版本（539 窗口） | 19 | 519 | 0 | 0 | 1 |

`pending≈1010` 并非约千个可执行任务：其中 **994** 个 pending 是旧版本，970 个还属于显式冻结来源版本。两个 retry_wait 都是冻结的旧版本；一个 needs_repair 也是旧版本（内容类别校验失败、3 次尝试），当前不应把它们重排到执行队列。当前 19 个 pending 都属于非冻结的新来源版本。

冻结清单 `/var/lib/imgw/icbu/conversation-analysis/quota-pool-20261024/source-freeze.json` 有 471 个来源版本；`source_freeze.py:10-31` 以哈希、完整条目数、唯一性及窗口列表绑定，`analysis_quota_pool.py:94-102` 在选择和调用前跳过冻结版本。不要删除旧窗口、放宽冻结清单或切换模型/seed 来使历史再次进入模型。

真正阻止当前增量消化的是 **profile 级熔断**：

* `routing_audit` 中 2026-11-01 06:10:24.684493 UTC 的 `quota_pool_failed`，只取允许的字段后得到 `safe_code=infrastructure_unavailable, matrix_error_code=api_timeout, free_quota_known=false`；同一时刻随后写入 `provider_circuit_open`，scope=profile。该次调用 06:09:39 UTC 开始，约 45 秒后超时，不能判断云端是否已执行。
* `provider_circuits` 副本显示当前 profile 状态为 `open`、code=`infrastructure_unavailable`。`analysis_multimodel.py:318-331` 读取并打开此熔断；`analysis_quota_pool.py:88` 会直接返回 `provider_blocked=true, model_calls_attempted=0`。因此定时服务能 success 结束、扫描时间继续前进，模型不再消费。
* `scan_status.updated_at` 最近为 2026-11-01 07:53:34 UTC；最近模型尝试仍为 06:09:39 UTC。quota pool 累计 6 次准入、5 次成功、1 次失败，均由已有审计保留；没有“队列丢失”的证据。
* 相应矩阵实现明确先预留、后调用；超时保留未知预留并标 `usage_uncertain`，见 `/opt/imgw/wechat/supplier_knowledge/icbu_quota_only_models_v1.py:438-450`、`:462-507`。本轮未读取模型账本内容，也没有声称 API 超时等于免费额度耗尽、未发送或可安全重试。

只读 `systemctl show icbu-conversation-analysis.timer icbu-conversation-analysis.service -p LoadState -p ActiveState -p SubState -p Result -p LastTriggerUSec -p NextElapseUSecRealtime -p ExecMainStartTimestamp -p ExecMainExitTimestamp` 摘要：timer `loaded/active/waiting, Result=success`；service `loaded/inactive/dead, Result=success`，上次 00:53:24 PDT 开始、00:53:34 PDT 结束（07:53 UTC）。oneshot 的 inactive/dead 不是故障；没有证据需要增加另一套定时器。

### 最小稳定消化方案（仅设计，未执行）

1. 保留现有 timer、单写锁、来源冻结、内容失败次数及全部审计；健康统计分列“当前可执行 / 冻结旧来源 / 非当前审计记录 / 未知隔离”，并显示 profile 熔断原因。当前要恢复的实际积压是 19 个 pending 和后续自然新增，历史 994 个 pending 不作补跑目标。
2. **需要老板点头：未知结果隔离策略与生产账本处理。** 对 1 个超时窗口以及对应请求指纹、attempt 审计、来源版本、窗口哈希和矩阵未知预留建立显式、哈希绑定的不可自动重试隔离；保留原 `infra_blocked` 和原预留，不把 unknown 改成失败或成功。隔离必须参与后续来源版本规划与模型选择，防止同一批未知证据换个版本/模型再次请求。只隔离窗口 ID 而允许相同证据随新版本再发，不足以满足“不重发”。
3. **需要老板点头：核验后续模型预算和有限放行。** 先查明基础设施原因，使用另行授权的纯虚构小请求证明恢复及可用预算，绑定具体策略和证据收据；完成第 2 步后，由总指挥按新增的审计化、精确恢复方案确认 profile 熔断，仅放行从未请求过的新证据。保留每批上限与单次失败退出，未知请求永不进入自动重试。
4. 不可直接调用现有 `MultiModelStore.apply_recovery()`：`analysis_multimodel.py:401-429` 会把计划中的基础设施窗口设回 pending 并确认 profile 熔断，此次 `api_timeout` 属于结果未知，直接套用会导致同一请求重发。最小代码修复应是精确未知隔离 + 排除隔离证据 + 经证据绑定的熔断确认，而不是删行、清零或扩大可重试错误范围。
5. 经总指挥离线假 runner 验证这些护栏后，才允许按既有 timer 有限消化 19 个待办与自然增量。观察当前 pending 趋势、实际成功、冻结请求数=0、未知重发数=0、发布结果、最近扫描/最近模型成功分别计时；需求新增速度低于安全成功速度才能稳定消化。额度不确定时保持停止并显示可操作原因，不自动付费、不自动换模型重发未知请求。旧 needs_repair 的专项重做若以后确有业务需要，另列老板授权，不能混入恢复工作。

### 可复核命令和边界

统计 SQL（在只读备份副本执行）：

```sql
SELECT state, count(*) FROM windows GROUP BY state;
SELECT w.state, count(*) FROM windows w
JOIN current_conversations c ON c.analysis_version=w.version GROUP BY w.state;
SELECT status, count(*) FROM model_attempts GROUP BY status;
SELECT kind, count(*) FROM routing_audit GROUP BY kind;
SELECT max(updated_at) FROM scan_status;
SELECT max(started_at) FROM model_attempts;
```

额外分组只在内存将冻结清单的 `(conversation_key, source_version)` 与副本关联，输出统计；未输出账号、会话 ID、客户信息、源正文、响应正文或真实价格。错误仅输出源码已确认的枚举，不输出异常字符串。

`channel-status` 包装器因当前容器 sudo 失败，原样错误为：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

未更改容器权限。已以当前 imgw 身份核对它的只读源码，并使用相同数据库路径完成上述备份与安全统计。结论来自只读真实计数及源码；没有在真实数据上运行消化/恢复测试，阿里最小修复仅为方案，未提交补丁或部署。


## 新增文件清单

仓库没有修改已有模块；新增以下文件（补丁指向的生产文件尚未更改）：

- 文档：`docs/kb-live-recovery.md`。
- 补丁与工具：`deploy/kb/baseline.json`、`isolation-baseline.json`、`unknown-isolation-v2.patch`、`live-increments-v1.patch`、`verify_patch.py`、`draft_release.py`。
- 演练与测试：`tests/kb_recovery/conftest.py`、`recovery_fixtures.py`、`rehearse.py`、`test_isolation.py`、`test_runtime.py`、`test_boundary.py`、`test_rehearsal.py`、`test_draft.py`。

补丁 `dry-run` 返回 0，生产七文件 SHA 仍与 `baseline.json` 精确相同；独立演练已重复执行，结果一致。所有成功的模型/事实/水位行为只在虚构资料上验证过；真实自然增量、完整权限装载与阿里生产消化尚未完成。

## R5-A：独立生产协调器候选与上线手册（2026-11-01，未安装）

本节取代上文仍写“待老板批准”的费用上限、速度和失败阈值描述。老板已批准新增与积压都处理，费用不再另设人工上限；总指挥确定新增优先、积压每轮最多2单元、每个物理模型请求至少间隔30秒、任一新未知立即停全队、本地失败1次即停。额度耗尽即停，不自动转付费，不自动换模型，不重发未知。本次没有部署、启停服务、访问真实模型、发送渠道消息或改生产文件。

四项来源核实、逐条校验方法及不能确认的边界见 [R5-A 来源核实记录](r5a-provenance.md)。四未知直接来源仍为微信19/14/6、阿里16；私有校验清单由 `provenance.py` 在 `/tmp/kb-r5a-provenance-*` 输出，不放入仓库。阿里106条在旧副本中全部验证为一个完整稳定单来源单读者单元。全文待核资料已确定需要21块，旧12块遗漏9块、8452字符，整份及原12块继续排除。783条微信旧请求仍不能完整溯源，4947条覆盖待确认来源继续排除，不能叫作可重跑积压。

### 入口、账本与模型选择

`coordinator.py` 复用 `backlog.Scheduler` 的冻结清单、work/epoch和审计，再通过 `production_adapter.py` 调用原授权、提取和本地索引函数。独立 `PhysicalGate` 在每名读者调用前持久化预留和 `provider_started`，成功后先存响应；进程断电后未保存响应的请求即视为未知，重启禁止出站。微信两名读者分开保存，第一读者成功、第二读者尚未到间隔时只等待，不重发第一读者。保守间隔从上一可靠响应结束计算，慢请求、重启、两个渠道和两个读者共用一个物理闸门。

两个渠道共用一个进度库和物理请求库。每轮先检查原hold、新未知、来源隔离、代码和授权，再处理新增；积压最多2个实际处理单元。已知被熔断的渠道或耗尽的账户不占满另一方的积压槽位。没有本地写锁、清单/计划/响应损坏、权限或状态不确定时停止并保留检查点。既存阿里profile熔断继续阻断阿里，微信可在自己契约全部通过时继续；新未知仍停止两边。

| 渠道 | 固定读者 | 依据与角色 |
| --- | --- | --- |
| 微信 | `deepseek-v4-pro-0813`、`deepseek-v4.1-flash` | 现行百炼矩阵中已核验requested=actual的两个不同模型；第一读者提取，第二读者独立复核，只保留原逐字证据验证及一致事实 |
| 阿里 | `deepseek-v4.1-flash` | 沿用现行百炼结构化单读者契约及原来源/字符覆盖校验；不把单来源结果冒充整会话完成 |

以上两个 DeepSeek 型号的实际provider都是百炼 `dashscope`，**不使用官方DeepSeek预付费余额**。微信 `qwen3.8-max`、`qwen3.8-2.4t-a95b`、`kimi-k3` 三个冻结模型禁新请求；`deepseek-v4-flash-0731` 的 `free_tier_exhausted` 也保留。原矩阵额度与技术回执仍须逐次核验，候选名单不证明今日余额可用。

特别的上线阻断：现行免费矩阵成功元数据明确写 `billing_classification_verified=False`、`server_free_only_switch_verified=False`。本地quota_only和token cap不能证明云端仍免费、免费用尽不会转收费。示例 `verified=false` 必须保留，直到总指挥核实云端免费余额和用尽停策略；不能只看本地usage未blocked就改true。这里没有为省事放行潜在付费请求。

官方DeepSeek与Codex虽获老板批准使用，但当前两条KB生产接口没有可直接代入的已核验技术契约，本候选不为它们发明型号或绕路调用。示例分别保留 `deepseek_official` 预付费桶、`codex` 订阅桶，均默认禁用；百炼是独立 `dashscope_free` 免费桶。假runner验证了免费与预付费并行分账、某桶耗尽后另一桶可继续的机制；这不证明真实官方DeepSeek已接通。启用新provider必须另外交付其已核验KB契约与受审迁移。

`coordinator-config.example.json` 只放运行策略，没有凭据。默认积压2、新增2、间隔30秒、本地失败1次、未知停全队且禁止自动fallback；真实余额、保守请求预留、清单/发布/基线摘要均故意为null。免费桶需填实际核验的剩余资源；预付费桶另需余额及核验的最坏单位费率。它们是资源准入边界，不是重新向老板索要费用上限。保守累计预留不等于实际消费或云端余额；未知预留永不释放。余额未核实即不请求，策略不能在重启时偷偷扩额。

微信原 `kb-build.lock` 与阿里原worker锁在整个运行窗口内持有，避免旧worker穿插重复加工。微信另用受审calls副本建立历史栅栏，原调用整行不可变；新增原calls只允许有本协调器持久响应支持的精确模型/prompt/cache键，源入库时间须晚于原账最后发起时间。阿里每次拆分前仍对原父会话执行原 `source_freeze.require_new_source()`，单来源新版本不解除原471冻结版本。两个原worker必须停用；候选unit与原微信服务、阿里服务及timer互斥。

明确额度拒绝另有严格的已知收据：仅在物理gate保留原准入及quota_rejected审计、原calls为attempt1且错误恰为qwen_free_tier_exhausted、响应为空、模型/prompt/cache及预留完全对上时接受。它只允许保留拒绝状态并停止该账户，绝不重新发送；budget_wait、timeout、hold、provider_started或不匹配的新调用仍停止核查，不能扩allowed清单洗掉旧worker记录。

阿里有事实的单来源结果复用原 `catalog_plan()/verify_catalog()`，发布为独立 `icbu-analysis-recovery-source:*` 资产和原结构知识片段，标明 `owner_private`、候选、历史未核实、禁止自动发送、单来源覆盖；原整会话版本、窗口、熔断不变。统一知识库可索引这类资产，原阿里整会话专用查询不会把它当作完整会话。空结果用独立收据记 `no_supported_claims`。Catalog已提交而收据/进度未提交时先核对资产与片段，再补收据；微信已提交事实但索引未完成时只重建本地索引。

### 只读状态与首次空跑

状态入口默认不建库、不拿写锁、不恢复或清除任何状态。`status --json` 提供新增/积压各自的pending、local_pending、done、unknown，最近本地成功、最近物理成功、各桶保守token/金额预留、桶与全队停止原因及上次核对的渠道状态。物理库存在未知时顶层停止原因优先显示unknown，即使进程还没来得及把work改成unknown。它不是实时云端账单；渠道状态是上一轮核对结果，须结合时间判断。健康页本轮零改动。

```sh
# 以下仅为总指挥安装后的命令；本任务没有执行。
/opt/imgw/kb-recovery-coordinator/start-coordinator.sh inventory \
  --config /etc/imgw/kb-recovery-coordinator.json --config-sha256 "$CONFIG_SHA" --json
/opt/imgw/kb-recovery-coordinator/start-coordinator.sh status \
  --progress /var/lib/imgw/ops-app/kb-recovery-coordinator/progress.sqlite3 \
  --physical-db /var/lib/imgw/ops-app/kb-recovery-coordinator/physical.sqlite3 --json
```

首次只用 `inventory`：核对清单摘要、原模块图、固定after、动态授权、四未知和渠道状态，不创建进度/收据，不加载真实模型runner、不发模型或BGE请求。没有进度库时status应明确报未初始化，不能为了显示“零”创建库。阿里原熔断仍开时空跑必须显示该渠道阻断；不得清熔断凑一次成功。

### 总指挥安装前必须核对的事项

1. 在停写准备窗口确认全部旧KB写者与阿里timer已停止。重做R3-C规程的0700/0600只读备份，记录副本SHA和时间；跨库不是原子快照，必须复核写者边界。备份原七个overlay源码、相关root配置/权限及service/drop-in，另备份calls、原usage、Catalog、阿里analysis/usage、所有恢复进度与收据。备份留在私有运维区，不进仓库。
2. 运行 `deploy/kb/verify_patch.py` 和 `verify_coordinator.py`，逐个比较原 `baseline.json`、新 `coordinator-baseline.json`。前者仍是原三补丁与七文件基线，未改字节；后者另外固定新运行文件、原授权/模型/索引源码及打补丁后的overlay摘要。任一不同先重新审查，禁止fuzz套用。工具只在/tmp生成候选。
3. 重跑 `provenance.py`，逐条核对四未知的原行/attempt/execution、prompt、来源集合、原预留；确认完整文档、覆盖待确认、缺时间、未知及别名交叉全部排除。阿里106数量若变化重新审核，不沿用旧统计。
4. 用原动态授权入口核对当前来源范围、owner/scope/account/binding、群生命周期和试点范围，生成稳定单元审核稿。`plan_review.py --config ... --config-sha256 ...` 只生成0700/0600私有候选，不授予权限；随后用原 `backlog.py build/verify` 冻结manifest及canonical SHA。首次可用相同after、真实snapshot摘要和空entries生成一个只供审阅装配的空manifest；空manifest不包含可执行积压，不能把统计候选直接填成执行清单。可靠响应的本地候选必须有完整双读者检查点，不能把只有一读者的单元改为never_requested。
5. 把受审calls副本放入专用0700状态目录，0600、普通单链接文件，root配置绑定其完整文件SHA。冻结配置的manifest/quarantine摘要采用canonical JSON SHA；`release_sha256` 与 `loader_sha256`、`approved_calls_sha256`、命令行 `--config-sha256` 均按原始文件字节计算，不能混用。
6. 核实百炼原账各模型剩余额度、未知保守预留和本轮原始请求payload的最坏预留。免费耗尽不换官方DeepSeek；官方余额或新provider契约未知仍禁用。固定after由总指挥写入原边界与本候选清单，二者必须相同；不前移以捞积压。
7. root将运行文件安装到 `/opt/imgw/kb-recovery-coordinator/`，目录/文件禁止imgw改写，启动脚本0755；配置root持有、不可被工作进程改写。新状态目录 `/var/lib/imgw/ops-app/kb-recovery-coordinator/` 由imgw持有0700，库0600。安装原三补丁候选及配套root隔离/边界/发布配置后，用 `verify_coordinator.py --installed` 只读核对。不要覆盖其他业务模块或升级archive。
8. 仅由总指挥安装候选 `imgw-kb-recovery-coordinator.service`，替换ExecStart中的受审配置SHA；确认ReadWritePaths仅覆盖新状态、原模型账本、Catalog及确切旧锁/阿里额度目录，路径均存在，原输入/analysis只读。首次只做inventory；systemd沙箱、BGE、本地索引权限及现有WAL/SHM行为仍需在安装窗口验证。本任务没有尝试安装或启动服务。

### 首批两单元验收、停止与回滚

首次执行选两条明确never_requested或可靠local_pending的已审稳定单元，记录来源/提示词摘要、读者、原检查点和预计保守预留。保持旧worker停止，先用 `once` 逐轮运行、查看只读状态，在双读者间等待至少30秒；总指挥监看实际新增队列，任何额外待请求来源出现就先停并重新核定首批范围，不能把“每轮2积压”误当“整个服务只会处理2个”。正式 `serve` 会持续处理新增与积压，不能把它当首批限额工具。

两单元验收必须同时满足：新增优先；首批来源与冻结摘要一致；每次物理请求有独立预留和持久响应，任意两请求保守间隔不少于30秒；微信两不同读者、阿里单读者；原55来源及别名零请求；可靠旧响应零重新出站；事实/片段/收据与原源字节可核对；重启后已完成不重跑。阿里profile仍开时应验收为“阻断且零请求”，不能写成阿里生产成功。真实模型质量、真来源完整性及真实吞吐须另验，虚构测试通过不能代替这些验收。

停止：总指挥向该独立service发送SIGTERM，当前物理调用最多完成一次并持久响应，再退出；外部强杀/断电没有可靠响应即unknown，全队停止，原预留保留。候选 `Restart=no`，停止或未知不会自动拉起重发。`status`和私有只读备份保留停止原因及审计。任何未知、原hold变化、代码/清单变化、本地失败1次或额度不确定都不通过删除stop/epoch、重建库、换模型、换源ID恢复。

回滚：先停止新协调器并确认没有在途请求；保留全部新calls、未知预留、物理库、进度、收据、事实、片段和审计快照，再恢复已备份的原代码/config/service字节与权限、核对原SHA。**不回灌任何调用、usage、Catalog或恢复库，不删除原熔断/hold/stop。旧worker和阿里timer继续停用**，因为旧代码不认识新收据，直接恢复它们可能重发已经处理的新单元；后续复开须单独核对新旧覆盖及所有未知。回滚目标是安全停止并保留证据。

### 交付与仍未确认的范围

本批只新增deploy候选、来源报告和虚构测试，并追加本节；没有改健康页、业务模块、archive版本、前端路由/导航、演练服务、README、master-plan或共享需求账本。需要总指挥合并的共享文件改动：H11说明可更新为“R5-A已交独立协调器候选与四未知/单元复核，待真实授权、额度、原熔断与安装验收”，状态仍不得改done。

安全自查：无HTTP权限或CSRF面变动；真实执行逐次走原来源授权，来源正文不作命令，SQL值参数化；私有路径拒绝上级目录、软硬链接及不符权限；源码与清单均固定SHA。异常不打印模型正文、客户、供应商、价格、凭据、Cookie或授权头；假runner没有发送器，真实渠道发送为零。逐读者先持久provider_started再请求；结果未知禁止自动重发，可靠响应只做本地幂等续做。

仍无法确认的事：783条旧调用完整来源、真实当前动态授权名单、Win10来源时间真实性、云端未知请求实际计费/余额、真实模型输出质量、真实新增/积压持续消化和systemd安装后运行。官方DeepSeek/Codex尚无本候选可执行的已核验KB接入；阿里原profile熔断没有解除。本任务只在虚构资料上演练请求与落库，真实库副本只盘点，没有宣布生产资料可用。

### R5-A 文件清单与最终验证记录

新增18个文件：

- `deploy/kb/`：`coordinator.py`、`physical_gate.py`、`history_fence.py`、`production_adapter.py`、`provenance.py`、`plan_review.py`、`verify_coordinator.py`、`coordinator-baseline.json`、`coordinator-config.example.json`、`start-coordinator.sh`、`imgw-kb-recovery-coordinator.service`。
- 文档：`docs/r5a-provenance.md`。
- `tests/kb_recovery/`：`test_coordinator.py`、`test_coordinator_runtime.py`、`test_physical_gate.py`、`test_history_fence.py`、`test_production_adapter.py`、`test_provenance.py`。

修改仅本文件（追加R5-A节）。与只读开发副本比较，既有deploy/kb文件和既有kb_recovery测试字节均未改变；`recovery_fixtures.py`也未改。没有Git元数据，不声称有commit或git diff。新发布基线 `coordinator-baseline.json` 的字节SHA-256为 `7cb2f493518fb31cb875144f8ae4064d0667aff49f398d080711611921ec7185`；包含19个运行文件、29个生产参考源码、8个补丁后overlay文件的精确SHA。原三补丁、七文件基线保持不变。

本轮尝试 `sudo -n -u imgw true` 后被快照限制拒绝，原文：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

没有修改权限，随后按约定以当前imgw身份直接执行：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python -m pytest tests/kb_recovery -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/pipeline" /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb" /opt/imgw/venv/bin/python -m pytest tests/kb/test_core.py tests/kb/test_cli.py -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/verify_coordinator.py
sh -n deploy/kb/start-coordinator.sh
```

| 最终验证 | 结果 |
| --- | --- |
| 完整kb_recovery（含既有117项及新增115项） | 232 passed in 22.73s，0 failed |
| 既有pipeline | 117 passed in 18.56s，0 failed |
| 既有KB core/CLI | 22 passed, 21 subtests passed in 6.20s，0 failed |
| 原生产基线、三补丁dry-run及新发布SHA | 通过；只在/tmp处理候选，未部署 |
| 启动脚本语法、全部候选Python无pyc语法编译、示例JSON解析 | 通过 |

合计 **371 passed，0 failed，另21 subtests passed**。新增真实候选模块图→生产WechatAdapter→原Matrix→HistoryFence→PhysicalGate→Coordinator的演练仍只接虚构来源和假runner，验证双读者间隔、响应检查点、跨重启完成、第四条未知停全队与原三hold不变。阿里有事实的假结果实际使用临时真实Catalog结构、原验证/发布函数及假embedding，验证Catalog已提交而独立收据未提交的断电续做；没有连接真实BGE或模型。

开发中发生过两次测试夹具失败，修正夹具后上述最终整套已通过。失败原文摘要保留如下，不把它们说成最初就通过：

```text
FAILED tests/kb_recovery/test_production_adapter.py::test_wechat_each_callback_requests_exactly_one_reader
FAILED tests/kb_recovery/test_production_adapter.py::test_completed_facts_still_rebuild_after_local_restart
E KeyError: 'fictional-ready-2'
2 failed, 7 passed in 1.29s

FAILED tests/kb_recovery/test_production_adapter.py::test_alibaba_catalog_commit_then_restart_writes_only_missing_receipt
E Failed: DID NOT RAISE ValueError
1 failed, 14 passed in 2.55s
```

前者补齐虚构额度账本的第二模型；后者因umask077使测试mkdir0755实际成为0700，改为显式chmod0755后验证路径拒绝，未放宽产品权限。没有运行e2e_smoke、固定端口演练服务、真实浏览器、前端构建或tsc。所有模型请求、落库恢复与预算分账成功只在虚构数据上验证过；真实副本只验证盘点与哈希，不能据此标真实资料可用。

## R5-D：官方 DeepSeek 预付费读者、额度路由与估算（候选，未部署）

本节补齐 R5-A 的官方读者契约，取代其“官方 DeepSeek 尚无可执行接入”和“明确免费耗尽后仍禁止续接官方”的描述。老板已批准使用独立预付费余额，费用不设人工上限；本地金额限制表示经过核实的可用资源，不是另设费用审批。预付费耗尽停该桶，不改走百炼付费、Codex、其他账号或其他型号。四条旧未知、来源隔离、阿里原 profile 熔断和旧写者互斥全部继续生效。未启动任何 KB 服务，未请求真实模型或余额。

### 已核验的入口和回执

只读核对 `/srv/imgw-dev/context/MODEL-POLICY.md`、`/usr/local/bin/ai-task` → `/usr/local/libexec/imgw-ai-task` → `ai_task_gateway_candidate.py` → `tools/swarm/swarm_dispatch.py` → `tools/deepseek_cli.py` → `domestic_digest/client.py`、`worker.py`、`inventory.py`。源码摘要已追加进 `coordinator-baseline.json`；没有打开 swarm 配置、EnvironmentFile、token 文件或读取环境密钥值。`MODEL-POLICY.md` 记录两个官方型号已做虚构短文本验收，但不构成本轮真实 KB 验收。

`ai-task run deepseek_official` 的现行入口有 20000 字符限制，使用通用 TEXT_SCHEMA，且旧 worker 丢弃 HTTP 原文、把官方 402 压成普通拒绝，未暴露余额接口。因此生产候选新增 `official_deepseek.py`，在隔离子进程复用已绑定 client 的真实 `request_payload()` 和固定 `worker.read_credential()`；不调用 swarm 的后备路由，不修改生产入口。只有未来实际执行 worker 才会读原凭据；本轮测试均注入假 runner，未执行真实 worker。

固定主机 `api.deepseek.com`，completion 为 `POST /chat/completions`，余额为 `GET /user/balance`；不跟随重定向，不重试，不接受配置指定任意主机。官方文档提供[余额字段契约](https://api-docs.deepseek.com/api/get-user-balance/)与[错误码](https://api-docs.deepseek.com/quick_start/error_codes/)。不改变任何代理、DNS、路由或 NO_PROXY；已有直连主机授权不符就停止。安装窗口由总指挥确认源码权限与授权，不能为凑成功放宽。

每次请求先由 gate 提交 `provider_started` 和预留，再得到包含 HTTP 状态、base64 原文字节、请求型号、接收时间及 Retry-After 的私有收据。gate 先 FULL 同步提交原收据和 SHA，之后才运行官方本地校验：

- `requested_model == actual_model`，精确比较原 API `model` 字段；不按官方别名映射猜测身份。
- 只接受一次完整、非截断的 JSON object；拒绝重复 JSON 键、非有限数字、工具调用、拒绝字段和 reasoning 输出。按真实微信/阿里 schema 校验。
- usage 必须有非负整数 `prompt_tokens`、`completion_tokens`、`total_tokens`，和正确，输出不超过 4096（微信）或原 `pool.OUTPUT=6000`（阿里），总量不超过本次保守预留。
- 微信逐字事实仍走原 `_facts()`；所有输出事实都必须通过，之后原 `_agrees()` 做两读者一致性。阿里仍走原 `core.validate_response()`、证据 span 原文与 coverage 校验。无证据事实不进入本地落库。

HTTP 402 为 `insufficient_balance`，401/403 为 `authentication_failed`，429 为 `rate_limited`；完整成功响应的模型漂移为 `model_drift`，非法 JSON/结构/证据为 `invalid_response`。这些明确响应保留原文并只停所属桶。429 保存 Retry-After 为最少人工核对等待时间，下一轮及重启均不自动重查或重发；这与全队物理间隔未满的本地等待不同。completion 超时、传输中断、没有完整可持久回执、断电后仅有 provider_started，均结果未知，立即永久停全队，保留预留，禁止重发或换通道。

官方 raw 收据保存在独立物理库，不写旧 Matrix calls。百炼 `qwen_free_tier_exhausted` 仍由原 Matrix 先落 attempt=1 的明确拒绝；gate 保存安全错误收据及原审计，旧 gate_calls 响应保持 NULL，使 R5-A HistoryFence 的精确拒绝核验继续成立。旧 calls、hold、scope、usage 和来源集合不清空、不重建。

### 路由建议和配置

所有路由在示例 `routes` 中，协调器不写死型号。`auto_fallback=false` 继续禁止泛化换路；本节只允许已受审 route 的明确免费耗尽后继：

| 角色 | 先用百炼免费池 | 明确免费耗尽后用独立官方余额 |
| --- | --- | --- |
| 微信第一读者，提取 | deepseek-v4-pro-0813 | deepseek-v4-pro |
| 微信第二读者，独立复核 | deepseek-v4.1-flash | deepseek-flash |
| 阿里单读者 | deepseek-v4.1-flash | deepseek-flash |

微信两条 route 的所有可选型号集合必须互斥；任意切换组合仍是两个不同模型。免费额度按模型拆 `dashscope_free_pro`、`dashscope_free_flash`，以免一个免费型号耗尽就阻断另一个尚有余额的型号；两个官方模型共用 `deepseek_official` 桶，按同一预付费账户合计预留。旧冻结型号留在停用免费桶。Codex 桶未接入、未启用。

只有真实 runner 返回的明确 `qwen_free_tier_exhausted` 收据，或本协调器已有且原审计完全匹配的同一免费型号拒绝，才授权该 route 的后继。gate 核对拒绝状态、类型、原文 SHA、请求键、桶及审计事件；新官方请求另有 `route_selected` 审计，绑定前驱终态拒绝收据。原拒绝请求不重发，未知或仍在途请求没有后继。首个明确拒绝到后继仍须等待至少 30 秒。先查本单元主读者已保存的成功响应，再查全桶额度拒绝；其他单元耗尽免费池不会让已完成的读者重新出站。

**本地 cap/预留不足、观察过期、认证失败、欠费、限流和模型过期不等于云端明确免费耗尽。** 如果本地保守预算先阻断，就没有可授权后继的免费耗尽收据，该 route 保持停止；本交付不能保证所有“余额不足”情形都无缝续接，也不允许人为伪造拒绝或发探测请求凑收据。需要新决策时总指挥审查资源与已请求覆盖，不能删除 epoch/stop 或换空库。

### 每轮余额与保守预留

有官方 route 时，每轮来源/隔离核验后、任何模型请求前，只读查一次官方余额，先持久 raw，再解析 `is_available` 和所选 `balance_infos.currency` 的 `total_balance`、`granted_balance`、`topped_up_balance`。金额用 Decimal 向下取整为该币种的百万分之一；不换汇、不混币。官方已提供查询 API，本候选生产装配强制 `balance_mode=api`，没有利用“本地账本”绕过查询。GET 超时或格式不明也保守停全队；GET 的明确 402/401/429 则只停官方桶且不自动再查。

物理准入同时检查现场核实的初始资源、最新余额和本轮以来保守金额预留；两个官方模型共桶累计。未知和拒绝不释放原预留，不把实际 usage 当作退还预算的依据。余额观察不能证明其他软件没有同时消费，总指挥应避免共账号并发消费者，并在首批重新核实。停止原因不会被后续普通本地额度拒绝覆盖。

示例的 `currency`、各模型最坏费率、token/请求/金额资源、保守单次预留、清单/源码 SHA 全为 null，桶 `verified=false`。币种要与费率及金额资源一致，费率必须为正整数；按较贵的输入/输出费率覆盖每个预留 token。总指挥现场核实后填写，不能把公开价格、历史页面观察或虚构测试金额直接装入生产。

完整 payload 保守下界是 `len(紧凑 UTF-8 request_payload)+max_output_tokens+1024`；schema 在 system 消息内，不能只算来源正文。估算报告见 [R5-D 用量估算与假设](r5d-usage-estimate.md)：微信 1600 来源/1603 单元，阿里 106 单元；每单元动态口径总预留 23,226,457 tokens，观察免费余额若全部有效可抵扣 1,835,630，余约 21,390,827。此为输出取满上限的保守计划，不是消费预测、今日余额或请求授权。现行配置统一固定 `adapter.request_tokens`；例如经现场批准填写 15000 时，3312 个物理请求按固定值累计为 49,680,000，不能拿按单元估算冒充这个配置的账本额度。历史查询、隔离和真实动态授权仍须另验。

### 安装核对、首批两单元与受控真实小测

本轮只交候选，以下均由总指挥在审查后执行，本任务零执行：

1. 按 R5-A 安装前清单控制旧微信 worker、阿里 worker/timer 及其他同账号消费者，重做 0700/0600 只读副本、四未知、783 条覆盖待核和完整文档排除核验；保留阿里 profile 熔断。先确认现行 api_groupbot 配置权限已正确，避免旧 `load_config()` 的权限修正副作用。本轮没有执行真实 build_adapter/inventory，避免其读取真实秘密配置。
2. 核对本发布基线、原三补丁及全部固定生产源码。新基线内 `previous_runtime_sha256` 保留 R5-A 候选字节 SHA；旧发布基线为 `7cb2f493518fb31cb875144f8ae4064d0667aff49f398d080711611921ec7185`。任一不同先审查，不覆盖、不 fuzz。仅在私有运维区备份原代码、配置、权限、service、calls/usage/Catalog、恢复库和收据。
3. 在虚构数据上跑完整本套及相关回归，重算估算；现场核对官方凭据配置存在、固定读取方式权限、已有主机授权、余额币种/可用额及最坏费率。不要打印或复制凭据值。确认官方 raw model 真正精确回显，不能因官网说两个 alias 对应相同底层版本就放行漂移。
4. 受控真实小测只使用新建 0700 临时目录中的 0600 **虚构短文本**。例如“仅根据虚构资料回答：样品编号 TEST-R5D，没有客户和真实价格。只返回 JSON，含标记 TEST-R5D。”由总指挥单次执行 `ai-task run deepseek_official --model deepseek-flash < 私有虚构任务文件`，不启动协调器、不导入来源、不写 KB/Catalog/恢复账本；严格核对 task_id/provider/requested/actual/usage。只要超时、回执缺失或结果未知就停止，绝不重复同一小测。复杂读者若还需核验，另用新虚构任务单次指定 deepseek-v4-pro，不得循环重试。
5. `ai-task` 会保留其原 swarm 任务审计；“不进入生产账本”在此指不进入 KB/恢复账本，不能宣称 ai-task 完全不落盘。若要求连 swarm 也不写，现行入口没有这个能力，须另审隔离入口，不能靠删除审计实现。ai-task 小测也不能替代新增 raw HTTP/余额契约的安装窗口验收。
6. 安装到 R5-A 独立目录，加入 `official_deepseek.py`；root 固定发布和完整运行配置文件 SHA，再只读核对。不升级 archive，不覆盖 /opt/imgw 其他模块。原进度/物理库均绑定旧策略，**不得直接用 R5-D 新配置打开旧库、删除旧 epoch 或建立空账本重跑来源**。已有 R5-A 请求时需总指挥先审计所有 work、calls、响应、未知和资源的完整迁移；本候选没有提供自动策略迁移工具。尚未启动的 R5-A 可按新基线一次性初始化独立账本。
7. 首批冻结**总共两单元**，优先选择一个微信、一个阿里；阿里 profile 仍开时验收为阻断零请求，实际可执行两单元可选微信。新清单只含本批可执行范围，控制新增；用 `once` 逐轮及只读 status 验收，不用 serve 当两单元总限额。每次原文、usage、源/prompt/plan SHA 和桶金额预留均可追溯；微信两不同读者；旧未知/别名零请求，来源逐字证据与事实对应，重启零重复。
8. 安装窗口必须实际核验免费耗尽的已知回执路径和官方余额不足的安全停桶；不能为真实验收主动烧空余额或制造未知。尚无此现场条件时保留“假服务验证通过、真实耗尽未验收”。真实资料质量、吞吐、费用与通道可用性单独验收，由总指挥统一执行上线 e2e。本任务没有运行 e2e、固定端口服务或浏览器。

停止与回滚先禁止新请求，保留并核对所有在途/未知；备份新旧物理/进度、raw、金额预留、route/rejection/balance 审计、Catalog 和来源收据。恢复安装前备份的代码/config/service字节与权限并核对 R5-A/生产原 SHA；**不回灌调用或业务库，不删 stop/epoch/hold，不自动重新开启旧 worker/timer**。旧代码不认识官方响应和路由，回滚目标是安全停止、保留证据；继续处理必须另审覆盖与策略迁移。

### R5-D 文件、测试和边界

新增 `deploy/kb/official_deepseek.py`、`estimate_usage.py`；`docs/r5d-usage-estimate.md`；`tests/kb_recovery/test_official_deepseek.py`、`test_estimate_usage.py`、`test_prepaid_routing.py`、`test_prepaid_balance.py`、`test_official_adapter.py`。修改 `coordinator.py`、`physical_gate.py`、`production_adapter.py`、示例配置、`coordinator-baseline.json` 和本手册。原三补丁、`baseline.json`、HistoryFence 及原业务模块未改。

需要总指挥合并的共享文件改动：H11 可补充“R5-D 已交官方预付费读者、已知额度拒绝续路由、余额与用量估算候选；虚构测试通过，待真实模型身份/余额、旧账迁移及安装验收”，不标 done。本任务未修改 requirements、README、master-plan、前端路由/导航或 rehearsal_server.py，确切路由/导航新增位置：无。

安全自查：没有新增 HTTP 权限面或 CSRF 入口；原动态授权、隔离、单写锁、事务及审计链保留。私有路径拒绝上级路径、软硬链接和不符权限，SQL 值参数化；不执行正文、附件或模型输出，不接受任意请求主机/模块/命令。真实副本只做聚合计数和哈希，不含来源正文的报告才进入仓库。无真实客户/供应商/群发送，无凭据值读取、打印或复制，无 commit/push、网络改动或生产写入。明确拒绝与未知分开；原成功优先复用，后继绑定拒绝审计，未知全队停止，零自动重发。

没有完成：生产安装、真实模型/余额调用与计费核账、真实免费耗尽后续接和余额耗尽停桶、真实来源动态授权/质量/吞吐、已有 R5-A 账本策略迁移、Codex KB 技术契约。真实副本统计不等于真实数据可用；所有请求、余额、切换、证据落库及重启验证均为虚构数据、假 runner 和临时 SQLite。

本轮按要求先尝试 `sudo -n -u imgw true`，未修改权限，原样失败为：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

随后以当前 imgw 身份执行同样环境的测试，最终结果如下，无测试失败记录：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python -m pytest tests/kb_recovery -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/pipeline" /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb" /opt/imgw/venv/bin/python -m pytest tests/kb/test_core.py tests/kb/test_cli.py -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/verify_coordinator.py
sh -n deploy/kb/start-coordinator.sh
```

| 最终验证 | 原样结果 |
| --- | --- |
| 完整 kb_recovery，原 232 项加 R5-D 新增 68 项 | `300 passed in 25.57s`，0 failed |
| 既有 pipeline | `117 passed in 19.44s`，0 failed |
| 既有 KB core/CLI | `22 passed, 21 subtests passed in 6.44s`，0 failed |
| 21 个运行文件、37 个生产参考源码、8 个补丁后 overlay 的 SHA 与三补丁 dry-run | 通过，仅 /tmp 候选处理 |
| 全部候选 Python AST 语法、示例 JSON、启动脚本语法 | 通过，没有生成 pyc |

合计 **439 passed，0 failed，另 21 subtests passed**。R5-D 发布基线原始字节 SHA-256 为 `402af2e3fe47e51a039bd4e672587723b4c0ec36de8c721dd9d6596ff77d53fd`；配置的 `release_sha256` 由总指挥核对后填写，示例继续 null。`.git` 在调度快照不可用，没有 commit/push，也不声称有可用 git diff。生产原基线及旧 R5-A SHA 均由发布清单保留，安装和回滚不能依赖测试结果代替现场字节核对。

## R6-A：可重复的安装前准备（候选，未安装）

`deploy/kb/prepare_install.py` 把副本、盘点、冻结、配置、摘要核对和 root 命令输出分成六个阶段。以普通 imgw 运行，只在新建 `/tmp/kb-prepare-install-*` 目录写产物；目录 0700、文件 0600。它不安装、不启停服务、不调用模型或渠道；唯一允许的现场网络入口是 `official_deepseek.OfficialDeepSeek.read_balance()` 的只读余额查询，沿用原隔离传输和凭据读取方式，不自行读取凭据，也不自动重试。`dry-run-all` 连这个入口也注入假实现，并禁止网络。

本节关联 WG-01 的知识层来源证明，不接管渠道采集或媒体接入。第一步始终是总指挥停止旧微信 worker、阿里 worker/timer 及其他同账号消费者；最后才可能安装，旧写者在首次盘点、后续验收和回滚期间都继续停用。脚本里的 `--writers-stopped` 是现场人工确认，WAL 静止也不能独立证明所有写者都已停止。

### 输入和执行顺序

先在 `/tmp` 建一个 imgw 持有的 0700 输入目录，路径清单、现场值及原授权审核稿各为 0600。不要把这些文件放回仓库。来源路径必须逐个现场核实；脚本没有生产库路径默认值，也不会扫描目录或猜字段。`sources.json` 只允许以下文件名，值填写各个原文件的绝对路径：

```json
{
  "wechat": {
    "catalog.sqlite3": "/填写已核实的微信目录/catalog.sqlite3",
    "calls.sqlite3": "/填写已核实的微信目录/calls.sqlite3",
    "state.sqlite3": "/填写已核实的微信目录/state.sqlite3",
    "usage.json": "/填写已核实的微信目录/usage.json",
    "quota-review.json": "/填写已审阅的私有目录/quota-review.json"
  },
  "alibaba": {
    "analysis.sqlite3": "/填写已核实的阿里目录/analysis.sqlite3",
    "inbound.sqlite3": "/填写已核实的阿里目录/inbound.sqlite3",
    "ledger.sqlite3": "/填写已核实的知识同步目录/knowledge-sync.sqlite3",
    "catalog.sqlite3": "/填写已核实的阿里目录/catalog.sqlite3",
    "source-freeze.json": "/填写已核实的阿里目录/source-freeze.json",
    "usage.json": "/填写已核实的阿里目录/usage.json"
  }
}
```

`ledger.sqlite3` 是既有 `inventory_alibaba` 使用的副本名，对应现场 knowledge-sync 库，不是新建业务表。`quota-review.json` 必须来自受审矩阵的真实 `quota_baseline` 与 `legacy_quarantine`，不能编造或清空旧隔离。其格式沿用本手册旧生成器。

在仓库根目录执行，下面环境变量均由操作人填入已核实路径或摘要；`SESSION` 使用第一步打印的新目录：

```sh
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py snapshot \
  --sources "$INPUT/sources.json" --writers-stopped
# 将上一步打印的私有目录填入 SESSION，后续命令都用同一个 SESSION。
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py inventory --session "$SESSION"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py plan \
  --session "$SESSION" --after "$AFTER" --review "$REVIEW" --review-sha256 "$REVIEW_SHA"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py render-config \
  --session "$SESSION" --values "$INPUT/values.json"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py verify --session "$SESSION"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py root-commands --session "$SESSION"
```

每阶段用独占文件锁和完成标记发布结果；前序阶段、输入绑定或任一产物 SHA 不符就停止。同参数重跑只复核原产物，不重复盘点、生成清单或查询余额。改变已冻结输入、重新观察余额或需要新备份时，新建会话；不覆盖旧清单。失败目录以 `failed-阶段名-随机串` 保留在会话内，没有通过标记，后续阶段不能使用；余额收据在验收前已写盘，失败也保留。标准输出只含私有目录、状态、计数和 SHA；详细来源审阅文件只在私有目录内。

### 每阶段通过标准与人工核对

| 阶段 | 自动通过标准 | 总指挥仍须人工看什么 |
| --- | --- | --- |
| snapshot | 完整输入集；数据库与 WAL 两次采样及备份后复查均稳定；SQLite `backup()` 与副本 `quick_check` 通过；每份副本记录开始/结束时间和原字节 SHA | 全部写者确已停止；JSON 与七个数据库属于同一准备窗口；跨库不是原子快照 |
| inventory | 调用原微信/阿里盘点、provenance 四未知核验与 R5-D 纯提示词候选统计；默认微信1600来源/1603单元、阿里106来源/106单元逐项相等 | 四未知逐行、原预留、783旧调用、4947覆盖待核和整份截断文档继续排除；数量变化的原因与当前动态授权范围 |
| plan | 审核稿原字节 SHA、固定 after 和本次全部副本 SHA 一致；复用 `plan_review.write_review` 验证及 `backlog.make_manifest/validate_manifest` 冻结/复核；原 `draft_release.draft` 输出四份联动配置 | 稳定单元来源、别名交叉、双读者检查点和原授权；不能把盘点数量当作执行许可 |
| render-config | 必填现场值齐全且未过期；官方余额由受审入口查询并先保存收据；以示例模板渲染，绑定 release、loader、approved-calls、manifest/quarantine 和配置 SHA | 账户币种、官方余额收据、最高输入/输出费率、免费云端用尽即停、旧未知与其他消费者预留、单次完整 payload 最坏预留 |
| verify | `verify_coordinator.verify` 及 `verify_patch.prepare` 重核原源码和补丁；核对全部运行文件、overlay、原隔离/边界/模块发布绑定及余额有效期 | 安装后的沙箱、原权限与本地索引仍待现场验收；本阶段没有连接模型或 BGE |
| root-commands | 再核 SHA 与观察有效期，输出精确文件目标、安装与回滚脚本；不执行 | 阅读两个脚本并独立记录其 SHA；核对旧写者、父目录权限、备份空间、状态目录是否首次安装 |

数量偏差默认容忍度为零，比“差异过大才停”更严格。仅在重新审阅差异后，才可在新的会话用 `--expected-wechat`、`--expected-wechat-units`、`--expected-alibaba` 明确设置新期望；这些值会进入冻结输入绑定。没有静默放宽开关。

为保证生产文件连 SHM 都不写，备份连接使用 `mode=ro&immutable=1` 和 `query_only`。因此只接受不存在、空或仅32字节头的 WAL；发现数据帧就停止，不能忽略 WAL 获得陈旧副本。由总指挥先核实停写并按现场规程完成 checkpoint，再新建快照；本脚本不执行 checkpoint、不复制 `-wal/-shm`。副本可单独切为 DELETE 日志模式，所有写入只发生在新建 `/tmp` 副本。采样不能排除两次采样之间的隐蔽变更，仍须人工确认停写窗口。

### 首次授权装配的边界

现有 `plan_review.py` CLI 经 `read_config/build_adapter` 强制要求 root 持有的运行配置、manifest/quarantine 和已安装 loader。它不能在尚未安装的 imgw 临时目录里直接完成首次动态授权读取。R6-A 不降低这道权限检查，不读取其他秘密配置，不把副本盘点伪装成现场授权。

`plan --review ... --review-sha256 ...` 导入原授权入口生成并经人工核对的审核稿，复用现有审核输出函数，再执行与 `backlog.py build/verify` 相同的公共函数。审核稿的 `snapshots` 必须绑定本次 `snapshot/snapshots.json` 中的所有文件：键将 `渠道/文件名` 的 `/` 替换为 `_`，再去掉 `.sqlite3` 或 `.json`，值取该条 `sha256`；例如 `wechat_catalog`、`wechat_quota-review`、`alibaba_ledger`。`after` 必须和现场原边界一致。

确实没有预先受审的 root 装配时，可把 plan 命令改为 `--after "$AFTER" --bootstrap-empty`，生成只供首次装配和只读 inventory 的空审核稿，`plan/summary.json` 明确 `bootstrap_empty:true`、`items:0`。这条分支也会走冻结、四未知和 draft_release 检查；它没有验证真实动态授权，不能宣称积压审核已完成，更不能直接启动 serve。总指挥完成 root 准备后通过原入口生成实际审核稿，再做新的受审准备。R6-A 没有提供在未安装环境里自动绕过此依赖的工具，也没有提供已有 R5-A/R5-D 运行账本的策略迁移工具。

### 现场值格式

百炼余额由操作人从 `ai-task status` 观察后作为参数文件传入；脚本不运行该命令，也不解析未经审阅的任意输出。两个型号必须分别有剩余 tokens、观察 UTC 秒、过期 UTC 秒、未知预留、其他消费者预留、云端用尽即停是否已核实。过期日取现场套餐实际日期，观察有效时限 `max_age_seconds` 由现场明确填写（1～86400秒），不能用免费套餐到期日代替观察新鲜度。`free_only_verified:false` 会保留桶未核实状态，不因有观察余额就自动开放。

`values.json` 的五个顶层字段必须全部提供：

| 字段 | 含义 |
| --- | --- |
| currency | `CNY` 或 `USD`，与官方余额和全部金额费率一致 |
| request_tokens | 正整数；核准完整请求封装、schema、输出上限和余量后的保守单次预留，不照抄报告示例 |
| max_age_seconds | 余额观察有效时限；过期时 verify、重复核对和 root 执行都拒绝 |
| rates | 四个实际路由型号各自的 `cost_micros_per_1m_tokens`；账户币种微元/百万tokens；官方型号必须大于零，免费型号的零值也须显式传入 |
| free_balances | `deepseek-v4-pro-0813` 与 `deepseek-v4.1-flash` 两个对象；每个含 `tokens`、`unknown_reserved_tokens`、`other_reserved_tokens`、`observed_at`、`expires_at`、`free_only_verified` |

四个费率键是两个百炼型号及官方 `deepseek-v4-pro`、`deepseek-flash`。免费桶上限等于观察 tokens 扣除未知及其他预留，扣后为负就拒绝。官方预付费桶金额上限取本次受审查询的实际余额；不凭报告估价、汇率或旧观察填数。未使用的冻结模型和订阅桶继续保持模板中的禁用状态。配置里不保存凭据；原始余额收据只保存在 0600 私有文件，不能复制到仓库或日志。

### root 清单、回滚与虚构演练

`root-commands/install-root.sh` 和 `rollback-root.sh` 均为 0600 文本，供总指挥审阅后用 `sh` 执行；本任务没有执行。安装脚本在任何覆盖前检查观察有效期、生产参考源码及原/候选 overlay SHA、全部待安装文件 SHA、旧写者 inactive 和目标父链；备份将覆盖文件的字节、原 uid/gid/mode/时间及不存在记录到新建的 root 0700 `/var/backups/imgw-kb-prepare-*` 目录。状态目录只支持首装：已有任何内容就拒绝，不覆盖旧账本。

安装清单明确列出 `/opt/imgw/kb-recovery-coordinator/` 中发布基线固定的运行文件、八个 overlay、六个 root JSON、绑定配置 SHA 的 systemd unit 和 imgw 0600 的 approved-calls 副本；状态目录 imgw 0700，运行文件 root 持有且不可被 imgw 改写。完成后只列出 daemon-reload 和 installed SHA 检查，不含 enable/start。普通准备工具、测试夹具无需装入生产，也不改变原发布基线。

回滚脚本先停止独立协调器，要求总指挥人工确认无在途请求且原 calls/usage/Catalog、阿里账本、收据等已另行保全；它只自动保全新状态目录的当前字节，然后精确恢复旧代码/config/unit 的字节和权限，或移除首次新增且仍匹配安装 SHA 的确切文件。approved-calls 和任何运行账本不回灌，新状态目录及未知预留保留；旧 worker/timer 不重新开启。部分安装失败时也可用已生成的私有备份核对回滚，不能删库重跑。

```sh
# 只用虚构数据，六阶段各跑两遍；假盘点与假余额注入，其余原补丁/草案/SHA 核验实际运行。
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py dry-run-all
# 单元/集成测试；若 sudo 被快照限制拒绝，按共同纪律以当前 imgw 运行相同 env/python 命令。
sudo -n -u imgw env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" \
  /opt/imgw/venv/bin/python -m pytest tests/kb_recovery -q -p no:cacheprovider
```

演练复用 `recovery_fixtures` 的三 hold、四未知隔离和临时 SQLite；余额使用原 `OfficialDeepSeek` 收据校验加假传输，盘点现场返回值显式注入。它证明编排、冻结、缺值/偏差/篡改停止和幂等，不证明真实库结构、动态授权、真实模型、余额或客户数据可用。root 命令仅生成和静态核对，没有执行安装或回滚。

本轮新增准备脚本、root 命令生成器及对应虚构测试/夹具，手册只追加本节。没有修改共享需求账本、README、master-plan、前端路由/导航或 rehearsal_server.py；无需总指挥合并路由/导航。手册追加前原字节 SHA-256 为 `d723e65b0118f4a8f6b5ca15cd9fdf1915b2bf609fe20e82f97f3303c36483ea`；R5-D 发布基线仍为 `402af2e3fe47e51a039bd4e672587723b4c0ec36de8c721dd9d6596ff77d53fd`，原补丁基线仍为 `4d79e2e14265b8f696b92cca36f20a4a7e2f6b36176c3e5850d675f27fe14e3a`。

### R6-A 最终验证与交付记录

按共同纪律尝试 sudo，快照返回原文如下，随后以当前 imgw 身份运行相同 env/python 命令，没有调整生产权限：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

| 命令（仓库根目录，均设置 PYTHONDONTWRITEBYTECODE=1） | 最终结果 |
| --- | --- |
| `PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python -m pytest tests/kb_recovery -q -p no:cacheprovider` | `366 passed in 52.40s`，0 failed；含原300项和新增66项 |
| `PYTHONPATH="src:tests/pipeline" /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider` | `117 passed in 22.55s`，0 failed |
| `PYTHONPATH="src:tests/kb" /opt/imgw/venv/bin/python -m pytest tests/kb/test_core.py tests/kb/test_cli.py -q -p no:cacheprovider` | `22 passed, 21 subtests passed in 7.08s`，0 failed |
| `/opt/imgw/venv/bin/python deploy/kb/prepare_install.py dry-run-all` | 六阶段各两遍、2个虚构单元、4项停止检查、假余额1次，真实网络/模型请求0 |
| `/opt/imgw/venv/bin/python deploy/kb/verify_coordinator.py`、`verify_patch.py` | 原源码、21个运行文件、37个生产参考文件和补丁核验通过，只在 /tmp 生成候选 |
| `sh -n` 两份虚构生成的 root 脚本；五个新增 Python 文件无 pyc 语法编译 | 通过，未执行 root 脚本 |
| 中文 CLI 帮助完善后，重跑 `test_prepare_install.py test_prepare_install_root.py` | `66 passed in 15.40s`，0 failed |

总计最终相关套件 **505 passed，0 failed，另21 subtests passed**。调试时修复了余额查询后的时间验收、超时中文停止和测试目录受 umask 影响的问题；最终没有剩余测试失败。手册追加前的全部原文 SHA 重算一致，原发布和补丁基线未改。

新增文件为 `deploy/kb/prepare_install.py`、`deploy/kb/prepare_install_root.py`、`tests/kb_recovery/test_prepare_install.py`、`tests/kb_recovery/test_prepare_install_root.py`、`tests/kb_recovery/prepare_install_fixtures.py`；修改文件只有本手册（追加 R6-A）。普通准备脚本 SHA-256 为 `6ebfdb172997afab5f4afcaa4e9241588e56154938af90810207d69dfd08d8f2`，root 命令生成器为 `ef3dfd2d8bb595ccdbbe25272021056f67ed5f860a7611393341a9cf059559d3`。总指挥合入开发副本后按上节在私有目录准备、人工审阅，再决定是否执行 root 清单；本轮未部署。仅撤回本次开发改动时移除五个新增文件及本节，保留私有证据，不回灌任何生产库。

安全自查：没有 HTTP 或渠道发送入口，无越权授权扩张；审核稿仍需原授权及人工确认，空清单不等于放行。路径拒绝 `..`、软硬链接和不符的私有权限；SQL 不接收任意表名或执行输入，root 安装目标使用固定允许列表，路径按 shell 数据引用。没有读取、打印或复制密钥值，错误不回显异常正文；真实资料只允许留在新私有副本，stdout 不打印正文、群名、客户或价格。没有模型请求、自动重试、清除未知预留或重复发送的代码；没有运行 e2e_smoke、固定端口服务、安装、启停、网络修改、commit 或 push。

仍未完成真实安装、真实余额查询、真实来源盘点及授权验收、首次未安装环境的自动动态授权装配、已有恢复账本迁移，以及模型质量和吞吐验收。虚构全流程通过不能写成真实数据可用；现场必须先解决上述依赖再决定启动。

## R8-A：首次动态授权审核稿生成器（候选，未安装）

`deploy/kb/authorization_review.py` 补上 R6-A 首次装配前的审核稿入口：普通 imgw 进程使用本次 `snapshot` 的只读副本，并在进程内调用现行原授权函数。它不依赖协调器的 root 运行配置或已安装 loader，不安装文件、不启动服务、不调用模型、余额或渠道。配置只在内存中用于判断，不写入审核产物、日志或标准输出。原模块不能导入、原配置不能读取、原授权发生异常或任一快照摘要变化时整批停止，只输出错误代码，不重试或降级为空授权。

本节仍对应 WG-01 的知识层来源证明。审核稿只证明本次候选在核对时通过了哪些边界；不能证明真实渠道、模型质量、额度、持续增量或吞吐已经可用。

### 停写前置与运行顺序

停用操作由总指挥在既有维护窗口执行，生成器不会代为启停。先停止会重新触发任务的阿里 timer，再停止或等待旧微信 KB worker、阿里 worker 和其他同账号消费者安全退出；确认没有在途请求，保全全部未知与预留。按 R5-A/R6-A 核对全部写者均已停用，必要的 checkpoint 也由总指挥按现场规程另行处理。随后新建 R6-A 快照并完成 `inventory`；旧写者在出稿、人工核对、安装验收及回滚期间继续保持停用。`--writers-stopped` 是人工确认，不能仅凭 WAL 静止就填写。

原微信配置必须已是正确权限；生成器先检查，防止原 `load_config()` 的权限修正写入。现行群开关、授权文件和原源码必须可被 imgw 按原规则只读访问。缺失或权限不符时由总指挥核对现场，不能复制凭据到 `/tmp`、放宽生产权限或用旧群名推测授权。

以下在仓库根目录、以普通 imgw 运行。`INPUT`、`SESSION`、`AFTER` 均沿用 R6-A 人工核准的值；如果本次 `snapshot` 和 `inventory` 已通过，从第三条命令开始。

```sh
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py snapshot \
  --sources "$INPUT/sources.json" --writers-stopped
# 将 snapshot 打印的私有目录填入 SESSION。
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py inventory \
  --session "$SESSION"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/authorization_review.py \
  --session "$SESSION" --after "$AFTER" --writers-stopped
# 将出稿结果的“私有目录”填入 REVIEW_DIR；保留该目录，不复制进仓库。
# 人工核对下表五份产物；核对完成后，将出稿结果的 review_sha256 填入 REVIEW_SHA。
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/prepare_install.py plan \
  --session "$SESSION" --after "$AFTER" \
  --review "$REVIEW_DIR/review.json" --review-sha256 "$REVIEW_SHA"
```

`AFTER` 必须在出稿与 `plan` 两次调用中相同。`REVIEW_SHA` 是 `review.json` 的原字节 SHA-256；生成器采用无换行的 canonical JSON，其文件 SHA 与该对象的 `backlog.digest()` 相同。`plan` 继续走原 `plan_review.write_review()`、清单冻结和摘要核验，不修改 R6-A 的验收规则。通过后才按 R6-A 继续 `render-config`、`verify`、`root-commands`；这些后续阶段的余额查询、人工审查及安装边界没有改变。

### 私有产物与人工核对

生成器只在新建 `/tmp/kb-authorization-review-*` 的 0700 目录发布产物，文件均为 0600。标准输出仅含私有目录、计数、SHA 和脱敏理由代码；不得把私有文件复制到公开仓库、测试、工单正文或日志。

| 文件 | 核对要点 |
| --- | --- |
| `review.json` | 顶层严格只有 `schema`、`after`、`snapshots`、`entries`，`schema` 为 `kb-backlog-review-v1`。逐单元核对稳定来源、原来源/正文/提示词摘要和时间；`entries` 不包含当前未授权、未知交叉或待人工来源。计数不等于模型执行许可。 |
| `exclusions.json` | 排除/待人工区独立保存，避免给审核稿增加不兼容字段。每条只有渠道、来源键 SHA、理由代码和单元数；核对排除总数及原因，不能手改 `review.json` 把它们加回。 |
| `quarantine.json` | 恰三条微信、一条阿里，逐条与原 hold/attempt/execution、预留及审计对上，保持永久隔离。不能只核对“四条”。 |
| `provenance.json` | 新鲜副本重算结果与本会话 `inventory/provenance.json` 的四未知检查清单及隔离内容一致；未知请求、提示词、来源集合摘要和来源数必须逐项相符。整份截断文档、旧覆盖待确认来源继续排除。 |
| `summary.json` | 核对可入审核稿单元数、排除单元数、理由计数、11份快照和四未知数量，并保存 `review_sha256`。任何与人工预期不同的数量都先核查，不直接执行。 |

`snapshots` 绑定本次 `snapshot/snapshots.json` 的全部11份文件，不只绑定数据库。键名仍按 R6-A 将 `/` 替换为 `_`，再去掉 `.sqlite3` 或 `.json`：微信为 `wechat_catalog`、`wechat_calls`、`wechat_state`、`wechat_usage`、`wechat_quota-review`；阿里为 `alibaba_analysis`、`alibaba_inbound`、`alibaba_ledger`、`alibaba_catalog`、`alibaba_source-freeze`、`alibaba_usage`。每个值必须等于相应文件的 SHA。出稿前后均核对阶段产物与快照，拒绝新增 WAL/SHM、链接或不符的私有权限。

微信候选沿用 R6-A 的1600来源/1603稳定单元口径，阿里沿用106单来源候选；这些是本会话盘点基线。生成器逐单元复核当前原授权，不把这个数量当作必须放行数。微信保留 `history_backfill.eligible()`、`kb_context()`、`kb_authorized()` 链上的群生命周期、当前绑定、owner scope、passive 排除、supplier/learning 角色和客户询价限制；原 `pilot2/all` 开关仍收窄范围。学习事实仍只允许原 `learning_fact_allowed()` 的规格/新品用途，本工具不生成事实或扩大用途。阿里保留原 `authority()/boundary()`、`load_authority()/check_authority()`、`source_record()`、精确索引对账和原父会话版本冻结；不因新 MV3/PWA 已有入站就授予分析许可。

每个候选还交叉 `provenance.py` 四未知清单与全局 `source_gate`：来源键、原始来源 SHA、正文 SHA、提示词 SHA 或已核验的平台消息别名命中即排除。已有微信稳定单元或阿里旧请求来源留人工核对，不借新单元重发；两个时间都在 `AFTER` 之后的来源不混入本次积压审核。常见排除码包括 `AUTH_NOT_CURRENT`、`AUTH_ROLE_CONFLICT`、`UNKNOWN_CROSS`、`PRIOR_UNIT_REVIEW_REQUIRED`、`PRIOR_REQUEST_REVIEW_REQUIRED`、`SOURCE_TIME_REQUIRED`、`NATURAL_INCREMENT`。授权缺失、配置损坏和未知异常导致的整批停止不能解释成“全部候选正常撤权”。

人工核对仍须确认：跨库快照不是原子事务；所有旧写者确实停止；原生命周期、绑定和角色范围符合现行授权；783条旧调用与4947条覆盖待确认来源未被新稿放行；截断文档整份排除；阿里原471冻结版本和 profile 熔断、四未知预留保持。生成器不清除熔断，不执行 `apply_recovery()`，审核稿也不解除请求前的授权、来源、额度、锁和未知复核。授权或来源变化后应重新准备受审会话，不能沿用旧 SHA 或修改通过文件。

### 合入、部署与回滚边界

本轮仅交付普通准备工具、虚构测试和本节追加，不覆盖任何生产代码、配置或数据库，也没有执行真实出稿。总指挥审阅后合入开发副本，在受控窗口按上述顺序验证，再决定是否继续 R6-A 的安装准备。本工具无需装入 `/opt/imgw`，不改生产发布基线；后续 root 安装和回滚仍按 R6-A 生成并人工审阅的清单执行。

撤回本次开发交付时移除 R8-A 新增工具/测试及本节即可，保留私有审核证据；不回灌调用、usage、Catalog、业务或恢复库，不删除 hold/stop/熔断和预留，不重新开启旧 worker/timer。本节追加前手册原字节 SHA-256 为 `d66fd18d66ca687a646a9c05e0066142067bae3474dbee35065f462469ad2fe9`。共享需求账本、README、master-plan、路由、导航和演练服务均不在本轮修改范围。

安全边界：无新增 HTTP 权限面，无模型、渠道发送或自动重试；来源与配置作为数据，不作为代码或命令执行。输入路径拒绝上级目录、软硬链接和不符权限，SQL 值参数化；配置与凭据值不进入输出，未知异常正文不回显。动态授权阶段禁止网络、子进程和写入，即便原函数吞掉违规异常也停止。仍未完成真实来源动态授权验收、真实数据质量、余额/计费、吞吐、生产安装和旧运行账本迁移；虚构测试不能证明这些事项已可用。

原 `assistant_policy.authority()` 还可能调用可选的 WhatsApp `owner_pairs()`。本工具保留原判断；仅在原函数实际读取固定绑定库时，将该只读连接改为 `immutable=1`，仍只执行原绑定查询，不读取聊天正文。连接前必须无 WAL 数据帧和 rollback journal，并在审核结束复核数据库及旁文件元信息；不符即停，不能忽略 WAL、绕开授权或写生产 SHM。该路径没有命令行覆盖选项；测试只用临时路径替身，没有读取现场绑定或凭据。

### R8-A 最终验证与文件清单

本轮新增 `deploy/kb/authorization_review.py`、`deploy/kb/authorization_runtime.py`、`tests/kb_recovery/authorization_review_fixtures.py`、`tests/kb_recovery/test_authorization_review.py`、`tests/kb_recovery/test_authorization_runtime.py`，只修改本手册的追加部分。新增54项测试包括全通过、部分排除、配置缺失、四未知四类摘要跨渠道交叉、角色冲突、快照篡改、平台消息别名、旧请求防重发、父会话版本变化、原函数吞异常、日志抑制及原 `prepare_install plan` 实接。生成器测试把 `open`、`io.open`、`os.open`、`sqlite3.connect` 限于临时目录；原授权层使用假模块、虚构配置与临时 SQLite，另验证绑定库只许四列读取、拒绝正文及写入。没有运行真实出稿或读取真实配置/凭据/业务库。

当前身份为 imgw；`sudo -n -u imgw true` 被快照限制拒绝，原文如下，随后按共同纪律直接运行相同 env/python 命令，未修改权限：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" /opt/imgw/venv/bin/python -m pytest tests/kb_recovery -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/pipeline" /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb" /opt/imgw/venv/bin/python -m pytest tests/kb/test_core.py tests/kb/test_cli.py -q -p no:cacheprovider
```

| 最终检查 | 原样结果 |
| --- | --- |
| 完整 kb_recovery，含原366项及新增54项 | `420 passed in 53.24s` |
| 已有 pipeline | `121 passed in 25.21s` |
| 已有 KB core/CLI | `22 passed, 21 subtests passed in 5.96s` |
| 五个新增 Python AST、中文 CLI 帮助、手册原文 SHA | 通过；没有生成 pyc，手册原文基线不变 |

最终合计 **563 passed、0 failed，另21 subtests passed**。开发中隔离闸门先后报告 `14 failed, 8 passed in 5.08s`（`AssertionError: 测试禁止读取任何真实路径`，静默输出尝试打开 `/dev/null`）和 `16 failed, 12 passed in 8.07s`（`AUTH_SIDE_EFFECT`，审计未识别 `file:///tmp/` URI）；已改为丢弃输出的内存 sink 和严格 URI 解析，未放宽测试真实路径限制。手册前缀核验命令一度误用半角冒号匹配全角标题而触发 `AssertionError`，修正核验表达式后原文 SHA 一致，原文未改。没有剩余测试失败。未运行 e2e、固定端口服务、前端构建、模型、真实发送、安装、commit 或 push。

两个准备工具的最终 SHA-256 分别为 `370a55309cc4352213c617ddefec2534bb5d03b1667dd2ba6e05949dccb6aef1`（authorization_review）和 `b37e2c5d868110b8367bc02924c7669a8c9c95d1ded93179b408e28203e63b88`（authorization_runtime）。原 `prepare_install.py`、`plan_review.py`、`production_adapter.py` 及 R5-D 发布基线 SHA 全部与开工一致；无需总指挥合并其他共享文件或路由/导航改动。快照没有 Git 元数据，未声称有 Git diff 或提交号。本轮只在虚构数据上验证生成器，不把测试通过写成真实授权或真实数据已经可用。

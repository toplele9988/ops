# R7-B：微信媒体失败诊断与修复补丁

2026-11-01，开发候选，未部署。**本轮发现的是旧错误无法细分、已有内容与作业状态不一致，以及下载前校验失败被误记为“下载结果未知”。没有证据支持批量重新下载或重新调用模型。**

关键结论：

- 任务书中的 230 个 `source_io_error` 是历史轮次的聚合码。本轮当前进程完整只读核验了 702 个候选源文件，全部可读、哈希已登记且与本地归档字节一致。最近扫描服务退出码为 0。不能把历史 230 个继续描述为本轮仍然读不出的源文件，也不能凭当前成功反推历史错误原因。
- 视频号 46 个 `blocked` 全部已有任务 ID、下载回执文件标识、源哈希、唯一且哈希一致的 NAS 关联，并在 Catalog 找到该资产同 owner/scope 的知识块。它们适合先做来源与授权对账，不适合重新下载或重新提炼。**旧错误码不能证明分别被哪条规则挡住，当前授权也没有核验，因此不能直接改成 indexed。**
- 19 个 `download_unknown` 均无持久任务 ID、回执或源哈希。旧代码会把下载前的来源校验异常也归入这个状态，所以既不能断言 19 个都发起过下载，也不能断言没有发起过。保留冻结，不自动重试。
- 公司资料附件 58/106 正文 ready；实际有 92/106 个具有归档元数据，现存对应字节也全部通过只读哈希、长度与私有文件检查。34 个“已归档、未 ready”需要分别处理解析、回执或业务判断。
- 接收采集的 261 个 complete 中只有 232 个 ready；另有 29 个下载失败、空正文或不支持格式。**正文 ready 比例为 232/341＝68.04%，不是 complete 的 76.54%。**

## 证据来源与只读边界

当前工作区是 `/srv/imgw-dev/ops` 的独立源码快照，没有可用的 Git 历史，因此以文件 SHA-256 固定生产基线。开工已读 `CLAUDE.md`、`docs/master-plan.md`、`docs/data-pipelines.md`、`docs/workflow-gaps.md` 的 WG-01、WG-13 全文，以及微信清点、媒体与渠道实现。共享文件没有修改。

作业入口通过 `systemctl cat` 核对，只提取执行文件、模块、运行身份和代码搜索路径，不输出 Environment 中的凭据或完整运行配置：

| 服务 | 当前真实执行入口 | 本轮观察 |
| --- | --- | --- |
| `imgw-wechat-local-media-scan.service` | `/opt/imgw/wechat/video_knowledge/private_media_register.py` | imgw；2026-11-01 15:38:14–15:38:49 UTC 的最近一次运行，Result=success、ExecMainStatus=0、无 SuccessExitStatus 覆盖 |
| `vk-ingest-cards.service` | 工作目录 `/opt/imgw/wechat`，`-m video_knowledge.video_card_jobs` | active/running；本轮没有重启、执行 tick 或访问下载器 |
| `imgw-receive-collection.service` | 工作目录 `/opt/imgw/wechat`，`-m video_knowledge.receive_collection` | active/running；本轮没有执行采集、解析或提炼 |
| `imgw-video-card-pager.service` | `-m video_knowledge.card_backfill_service` | 该模块导入原 video_card_jobs；未运行补采 |

2026-11-01 15:19 UTC 先通过 SQLite `mode=ro`、`PRAGMA query_only=ON`、`backup()` 取得 state 与私有媒体登记副本，随后取得 Catalog 副本。目录由当前 imgw 用户持有，0700；数据库 0600，全部位于 `/tmp`。没有复制任何 `-wal`、`-shm`。临时备份的日志模式在副本上转为 DELETE，避免仅打开 WAL 模式备份时生成空旁文件；生产连接始终只读。交付工具对已完成的副本使用 `mode=ro&immutable=1` 和 query_only，拒绝旁文件与链接，不接受生产数据库路径。`immutable` 只用于完整 backup 的结果，绝不用于在线源库。

三个备份不是跨库原子快照；源文件核验和服务属性也发生在之后，不能据此生成可直接执行的恢复清单。所有 SQL 先核对真实 `sqlite_master`/`PRAGMA table_info`；正文、附件标签、群名、账号、消息 ID、原始文件名和来源哈希均未写入仓库或输出。

生产库与核查结构：

| 只读源 | 已核对的相关结构 |
| --- | --- |
| `/var/lib/imgw/wechat/api_state.sqlite3` | `video_card_jobs`、`vk_nas_sources`、`received_callback_archive`、`company_note_assets`、`company_note_office_resume`、`company_original_intake`、`company_media`、`messages`、`message_scopes`、`receive_collection_jobs`、`receive_collection_results` |
| `/var/lib/imgw/wechat/local-media-registry-v2/media-registry.sqlite3` | `assets`、`observations`、`roots`，既有版本 2 |
| `/var/lib/imgw/video_knowledge_auto/equipment/catalog.sqlite3` | `multimedia_assets`、`knowledge_chunks`、`schema_migrations` |

另尝试对已存在的视频恢复库执行只读 SQLite backup，得到 `sqlite3.OperationalError: unable to open database file`，没有读取其业务行。没有改权限、复制 WAL/SHM 或用不完整主文件强行读取。`journalctl` 读取扫描历史退出 1，本轮没有取得历史原始错误。两项限制不能解释为“没有历史任务”。

未读凭据配置、密钥、令牌、Cookie、授权头或模型配置中的值；没有网络请求、消息发送、模型调用、真实下载、服务控制、网络改动、提交或推送。

## 230 个 source_io_error：当前证据与无法恢复的历史

当前单元的源根目录是 `/mnt/nas229/edge/wechatmove/xwechat_files`，源文件系统为 nfs4；输出根目录是 `/var/lib/imgw/wechat/local-media-registry-v2`，与源设备不同。报告只给目录根和类型统计，不给任何业务子目录名或文件名。

| 口径 | 数量或结果 |
| --- | ---: |
| 任务书历史每轮 source_io_error / rejected | 230 / 230，错误类别占当轮拒绝项 100% |
| 历史候选 701–702 时，230 项占候选比例 | 32.76%–32.81% |
| 当前预检 jpg/mp4 候选 | 702，合计 495281215 字节 |
| 当前预检符号链接、非普通文件、零字节、超过 128 MiB 的拒绝项 | 0 |
| 当前打开、完整读取、前后 stat/路径稳定性核验成功 | 702 / 702 |
| 当前源 SHA-256 已在私有登记库中 | 702 / 702 |
| 当前与本地私有归档文件的完整 SHA-256、长度、单链接核验一致 | 702 / 702 |
| 当前私有登记独立哈希 / 路径观察 | 631 / 702；路径比独立内容多 71，不能把路径数当唯一内容数 |
| 当前登记中 source_verified 非零 | 0；这个隔离登记库并不证明已获业务来源授权或已进入知识库 |

只读文件核验没有调用扫描器 `run()`/`process_one()`，未创建临时附件、锁、blob 或登记记录，读出的字节直接丢弃。公司附件的字节核验同样只读，不调用带锁和容量维护功能的 `media_archive.verify()`。

旧扫描器 `run()` 把 `process_one()` 中的 OSError 统一计成 `source_io_error`，还会把不符合固定码格式的 ValueError（包括编码异常）归入同一个码；但 `process_one()` 同时包含源打开/读取、输出临时文件写入、硬链接发布、归档复核和 fsync。因此这个码**不能证明错误发生在源盘**。旧统计没有 errno、失败阶段或失败路径标识，`observations` 又只保存成功观察，无法恢复“原先那 230 个”的路径清单，也不能证明跨轮一定是同一批文件。

本轮可排除的是“当前进程仍无法读取这 702 个现存候选”：权限拒绝、文件删除、当前挂载不可读、源符号链接、大小门限或名称编码都没有导致本轮完整读取失败。仍不能排除历史 NFS stale handle/I/O、服务命名空间差异、当时已删除/变更的文件，或本地输出存储故障。只读核验也没有测试生产输出目录的创建、写入、发布与 fsync。

**根因结论：历史 230 项的具体 errno 和路径不可从现存证据还原，未查明；不是已确认的权限问题或 NAS 问题。** 修复补丁先把源和输出阶段分开，并将 EACCES/EPERM、ENOENT、ESTALE、EIO、ELOOP、EFBIG、ENAMETOOLONG、EILSEQ、ENOSPC、EDQUOT、EROFS 和文件描述符上限映射成固定码。未覆盖的 I/O 阶段明确标为 unclassified，不继续误叫 source。不能为这批历史数据编造各 errno 的占比。

## 视频号：46 个 blocked 与 19 个结果未知

| 状态与现存错误码 | 数量 | 占 102 项 |
| --- | ---: | ---: |
| indexed，错误为空 | 37 | 36.27% |
| blocked / source_or_receipt_invalid | 46 | 45.10% |
| download_unknown / download_outcome_unknown | 19 | 18.63% |

102 项的原始回调见证均存在，回调原始字节哈希与固定见证匹配。46 个 blocked 与 37 个 indexed 分别全部具有持久任务 ID、回执标识及源哈希；按原回执文件、长度、观察时间找到的 NAS 哈希集合均恰好等于该作业固定源哈希。两组也全部命中 Catalog 中该资产 owner/source_scope 一致的知识块。

这说明“作业未 indexed”与“完全没有下载/索引内容”不能等同。Catalog 的 owner/scope 一致是资产和知识块之间的一致，**不是已证明这个作业当前具有该范围的读取授权**；本轮未读取当前配置并运行 authority，也没有把其他范围的同哈希内容当作可用授权。

原 worker 在每个阶段先重新调用 `source()`，之后才校验对应回执、NAS 关联或索引；任何后续 ValueError 都会变成同一个 blocked 错误码。46 项都有源哈希，字段形态与现行状态机中到达 archive_verified 后的阶段一致；本轮没有取得逐项历史回填审计，不能声称已还原每一行的历史写入路径。现存下载、NAS 与索引证据不能被统计成首次下载失败。可能的具体阻断点如下；旧行没有保存子原因，**无法给这几条规则各分配历史数量**：

| 源码规则 | 是否应保留 | 处理边界 |
| --- | --- | --- |
| 当前人员、静默采集授权、激活时间或原始卡片来源改变 | 保留 | 授权撤销就不能放行；不得修改原回调、授权时间或来源绑定以绕过 |
| 原回调缺失、哈希不符，卡片对象、消息或范围改变 | 保留 | 必须有原见证，不能凭标题或文件名建立新见证 |
| 下载任务 ID / content_id / 固定文件派生名 / Windows 目录 / 完成状态 / 字节数不符 | 保留 | 先核对下载器契约，不能泛化接受任意后缀、目录或大小 |
| NAS 同回执关联有多个不同哈希 | 保留 | 不能按列表第一项猜；当前副本中这 46 项均未见该歧义，不能据此反推历史 |
| Catalog 私有文件、schema 或完整性校验失败，或不在当前授权范围 | 保留 | 当前迁移见证匹配；仍不能证明历史迁移/权限未变，也不能代替 authority |

新补丁在既有 `error_code` 字段中保留上述固定子码。未知校验异常只记 source_validation_failed、download_receipt_invalid、nas_receipt_invalid 或 catalog_validation_failed，不落原异常文本。

19 个结果未知均没有 task_id、receipt_filename、source_sha256。旧 `tick()` 在下载前先将 queued 写成 submitting，然后调用 `source()`；只要这个 try 中出异常且局部 state 仍为 queued，就写 download_unknown，即使尚未调用 RPC。相反，调用已到达下载器但本地超时、回执无合法任务 ID，也会落同一状态。旧码无法区分两者。

补丁增加仅在本次函数栈内使用的 `submission_attempted`：调用下载 RPC 前才置真；下载前拒绝记 blocked；一旦尝试 RPC，任何不确定结果仍记 download_unknown。启动时 submitting 转 unknown 的保守逻辑保持原样。**这只修复将来的错误分类，不修改或释放旧 19 项。**

未来如需核对未知结果，只能先取得下载器的持久任务/文件证据并按原卡片对象、请求派生文件名、目录、长度和 SHA 对账；本轮没有向下载器发送只读查询。无任务 ID 时不能按返回列表顺序猜，也不能把“没有搜到”当作确定未受理。确认不了就继续冻结。

## 公司资料附件：48 个未 ready 的具体分类

分母固定为 106 个登记附件，未 ready 共 48 个。ready 58 个（54.72%）；有归档元数据且当前字节核验通过的共 92 个（86.79%），两者不能互相替代。ready 中有 17 张 JPEG、41 个文件；不公布名称、正文或业务身份。

| 状态 | 数量 / 占全部 / 占未 ready | 可证原因与限制 | 下一步 |
| --- | --- | --- | --- |
| archive_unavailable | 3 / 2.83% / 6.25% | 均为图片，无归档回执；`media_archive._code()` 将多种未细分异常映射成此码。具体是路径、权限、挂载还是其他 I/O，历史未记录，不能断言 | 核对当时归档存储及来源；恢复只处理确定未保存、未受理的项，不能直接改 ready |
| attachment_invalid | 1 / 0.94% / 2.08% | 文件，未归档。可来自描述符、入口约束或归档校验不合格；没有可区分的字段级原因。本轮不读取 CDN 密钥/凭据值来猜 | 保留拦截，由管理员核对原始来源；不伪造字段或绕过校验 |
| download_failed | 3 / 2.83% / 6.25% | 均为图片，无归档回执；码不足以区分具体传输错误 | 在原来源、授权、无已存字节及有限次数条件下评估下载恢复；不与未知下载混跑 |
| empty | 13 / 12.26% / 27.08% | 原文件均为可校验 JPEG，尺寸有效且像素不超过 2500 万；已归档。原结果没有可用正文，不等于图片没有业务价值，也不证明值得再次 OCR | 人工决定是否需要 OCR；先查既有调用缓存/未知隔离和费用授权 |
| extract_failed | 10 / 9.43% / 20.83% | 8 JPEG、2 PNG 均校验有效，低于像素上限，字节范围 29469–321541，低于 20 MiB 内存解析限额。恢复账本均 complete/extract_failed。不是当前缺字节或单纯超大；异常被 catch-all/结果契约抹平，历史具体解析/OCR 原因未查明 | 先核对解析器契约及原调用账本，不重新下载；有未知模型请求就隔离 |
| failed_interrupted | 7 / 6.60% / 14.58% | 文件，114295–278399 字节，当前哈希/长度均正确；恢复账本全部 blocked/saved_original_unavailable。该码捕获保存回执、来源变化、解析/提交等多种异常，不能当作原文件现已丢失 | 核对原 intake 回执与来源固定值，再决定本地续做；不能清掉 blocked 或伪造回执 |
| failed_invalid | 4 / 3.77% / 8.33% | 均为 R20，4664–8026 字节。实际字节不能 ASCII 解码，也不能直接按 UTF-8-sig/GB18030 解码，未见 UTF-16 BOM；现有 R20 解析器要求 ASCII，故可复现此校验拒绝 | 本轮保留规则；需人工确认真实格式或重新导出。不要改扩展名、忽略非法字节或直接放宽编码 |
| unsupported_attachment | 7 / 6.60% / 14.58% | 6 视频、1 未知类型；`describe_assets()` 的子附件采集只接 image/file，这些项未尝试下载 | 规则与当前实现一致；如需视频/未知格式，另行设计接入和预算，不伪报 ready |

本轮对图片只运行了本地 Pillow 文件校验与尺寸检查，没有调用 OCR、解析出业务正文、转存图片或执行附件。当前 Python 环境能发现 PIL、defusedxml、openpyxl、xlrd；bwrap、pdfinfo、pdftotext、pdftoppm、prlimit 文件存在。**这只是当前依赖存在性，不证明历史依赖齐全、专用 XLS vendor 路径可用、沙箱能运行或解析器全链路通过。** 本批失败中没有足以确认“缺某个解析器”的记录，不通过安装软件或调用模型试错。

## 接收采集：complete 和 RAW_ARCHIVED_NO_TEXT 的真实含义

| state / result_status | 数量 | 占 341 项 | 含义 |
| --- | ---: | ---: | --- |
| complete / ready | 232 | 68.04% | 持久正文结果表也恰有 232 行；不代表已验证向量索引或真实业务可用 |
| complete / download_invalid_url | 10 | 2.93% | 文件下载来源未通过 URL 规则；不能放宽到任意 URL 或关闭重定向/目的地址约束 |
| complete / download_failed | 2 | 0.59% | 视频下载失败，均已到 attempts=4；不是重新置零计数的授权 |
| complete / download_rejected | 2 | 0.59% | 文件下载被拒；需核对原拒绝原因，不能当作可无限重试 |
| complete / empty | 10 | 2.93% | 3 JPEG、7 PNG 没有可用正文；不等于没有原始价值 |
| complete / unsupported_format | 5 | 1.47% | 当前未支持的文件格式；complete 仅表示该作业处理结束 |
| RAW_ARCHIVED_NO_TEXT / waiting_cloud_budget | 68 | 19.94% | 61 图片（38 JPEG、23 PNG）、7 语音；在等待云解析预算，不是 68 个原文件下载失败 |
| RAW_ARCHIVED_NO_TEXT / unsupported_format | 10 | 2.93% | 6 视频、4 文件；有原文件但当前解析器不支持 |
| RAW_ARCHIVED_NO_TEXT / cloud_response_rejected | 1 | 0.29% | JPEG 的云响应未通过契约；终态不能当作“未发起”重新调用 |
| interrupted / failed_interrupted | 1 | 0.29% | 图片作业中断；需先核对已存原件、回执与调用结果，不能盲目恢复 processing |

RAW_ARCHIVED_NO_TEXT 共 79 个（23.17%）：预算等待占其中 86.08%，格式不支持占 12.66%，响应拒绝占 1.27%。state=complete 共 261 个（76.54%）中，29 个并非 ready，占 complete 的 11.11%。这些是不同队列/附件登记粒度，可能与公司资料子附件重叠，不能跨表相加为总失败数。

原 `receive_collection.tick()` 在结果不 ready 且原件核验成功时才使用 RAW_ARCHIVED_NO_TEXT；其预算等待分支可等待后续处理，未知模型结果不在普通重试集合中。历史归档状态是当时 worker 的判断，本轮没有对这 79 项逐一验证现存原件；公司资料 92 项和私有扫描 702 项的字节核验不能替代它们。

## 哪些能修后继续，哪些应保留拦截

| 分类 | 涉及项 | 可以做的事及重跑前条件 |
| --- | --- | --- |
| 可安全重复核验 | 当前扫描候选、各表脱敏统计 | 只读 backup/哈希/统计；没有下载、发送、模型请求或业务状态写入 |
| 可修复后只做本地对账 | 46 个 blocked 视频 | 重新核对当前 authority、原回调、任务回执、唯一 NAS SHA、Catalog 资产/知识块与授权范围；全部满足后，由总指挥审阅精确旧状态条件的事务方案。无需重下视频或再提炼；本轮不提供批量改状态 SQL |
| 有条件恢复原始下载 | 3 archive_unavailable、3 download_failed 等确定未保存项 | 确认原始来源仍有效、没有已保存原件、没有 prepared/未知下载、原尝试次数未超限。原 `retry_one()` 包含后续解析，可能调用模型，**不能在本任务“禁止模型”条件下直接运行它**；如要恢复须另审只保留原字节的执行方案 |
| 有条件本地续做 | 7 个已存原文件但恢复账本 blocked 的文件 | 先修复可证明的回执/来源不一致；原件哈希正确也不能补造回执。若涉及 OCR 或嵌入图片，先核对持久请求账本，不能把本地解析等同于零模型调用 |
| 当前规则正确，不应放行 | R20 非法输入、未支持附件/文件、来源/身份/回执/URL 校验失败、未知模型结果 | 保留限制和原始状态；新增格式支持必须另做解析安全与虚构测试，禁止改后缀、放宽任意路径/URL、抹掉旧未知记录 |
| 需要人工或老板决定 | 19 个下载未知、68 个预算等待、13 个 empty、10 个 extract_failed、1 个响应拒绝、其他中断 | 未知结果先对账；预算等待、重新 OCR 和格式扩展需明确授权、额度与业务优先级。当前不能承诺费用、质量或真实可恢复数量 |

幂等边界：扫描继续沿用单写者 flock、SQLite 事务、内容 SHA 主键、观察复合主键与不覆盖发布；同一输入再次运行只复核同一归档与观察。视频队列沿用 account/scope/object 唯一键、不可变身份/任务/源哈希触发器、持久 submitting/unknown 冻结和确定性文件派生名；下载器 existing_action=error 只是防覆盖，**不是远端 exactly-once 保证**。真正的未知保护来自不再选择 unknown/submitting/blocked 发起下载。禁止换账号/范围/作业 ID、删账本或重建队列绕过冻结。

模型原实现使用稳定 source_id、kind 与输入 SHA 生成 request_id，先持久预留再请求；已完成复用缓存，started/unknown/rejected 不当作新请求。改变输入预处理、来源 ID 或模型也可能改变请求身份，因此不能据“新 ID 没有记录”绕过旧未知。补丁不触及该实现、预算、凭据或模型路由。本轮外部 RPC、发送与模型调用均为 0，没有安排任何恢复执行。

## 补丁、基线与虚构测试

所有生产改动仅在仓库提供候选副本和 unified diff；没有修改 `/opt/imgw`、生产 `/var/lib/imgw`、archive.sqlite3 版本、任何共享模块或挂载逻辑。两个补丁都不新增持久表或字段。

| 生产相对路径（根 `/opt/imgw/wechat`） | 生产基线 SHA-256 | 候选 SHA-256 |
| --- | --- | --- |
| `video_knowledge/private_media_register.py` | `b9687de73cdb6ad73852fbd95182c7426a923e8f00071e0ccc6cb0988b36891f` | `c5edb4c5ce47c11a6e83398b2b18b7470342215f04243d313536d28c12e6caae` |
| `video_knowledge/video_card_jobs.py` | `97e7251cb9d0a819c5d267830531686ee33516aeecb6d65add024e1660dbed98` | `89026b099d04d9b846a2d0b923ee77082a67d180dead5b1d75fac63c08b43d00` |

`deploy/wechat-media/baseline.sha256` 保存原文件的相对路径与 SHA。`private-media-errors.patch` 增加固定阶段/errno，不记录路径或异常正文；`video-card-errors.patch` 修复下载前误判未知，并保留固定来源/回执/索引错误子码。没有修复历史媒体、解除规则、重建回执或触发重跑。新错误码可能影响按旧码写死的外部告警，总指挥需将它们按阶段分组；现有状态列仍保持原契约。

新增文件共 10 个：

- `docs/wechat-media-failures.md`
- `scripts/wechat_media_failures.py`
- `deploy/wechat-media/baseline.sha256`
- `deploy/wechat-media/private-media-errors.patch`
- `deploy/wechat-media/private_media_register.py`
- `deploy/wechat-media/video-card-errors.patch`
- `deploy/wechat-media/video_card_jobs.py`
- `tests/wechat_media/conftest.py`
- `tests/wechat_media/test_inventory.py`
- `tests/wechat_media/test_patches.py`

`sudo -n -u imgw true` 被容器限制拒绝，当前进程本身为 imgw（uid 995），因此按任务约定直接运行同一 Python/pytest 命令。拒绝原文：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

最终验证：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/wechat_media" /opt/imgw/venv/bin/python -m pytest tests/wechat_media -q -p no:cacheprovider
# 50 passed in 3.07s

env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/pipeline" OPS_MEDIA_KEY_PATH=/tmp/r7b-pipeline-media.key /opt/imgw/venv/bin/python -m pytest tests/pipeline -q -p no:cacheprovider
# 117 passed in 17.49s

env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/audit" OPS_MEDIA_KEY_PATH=/tmp/r7b-audit-media.key /opt/imgw/venv/bin/python -m pytest tests/audit/test_wechat.py -q -p no:cacheprovider
# 下述既有快照依赖错误，未算通过

env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/tmp/r7b-support-kpff4jno:src:tests/audit" OPS_MEDIA_KEY_PATH=/tmp/r7b-audit-media.key /opt/imgw/venv/bin/python -m pytest tests/audit/test_wechat.py -q -p no:cacheprovider
# 80 passed in 11.13s；仅临时补齐源码依赖后的结果
```

未补依赖的失败原文：

```text
==================================== ERRORS ====================================
_________________ ERROR collecting tests/audit/test_wechat.py __________________
ImportError while importing test module '/var/lib/imgw/sessions/codex/dev-jobs/20261101T151724Z-81e12403238944b8/workspace/tests/audit/test_wechat.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/audit/test_wechat.py:11: in <module>
    from console.audit import wechat
src/console/audit/__init__.py:3: in <module>
    from .service import Audit
src/console/audit/service.py:11: in <module>
    from console.directory.sources import CONFIG_PATH, DISCOVERY_PATH, STATE_PATH
src/console/directory/__init__.py:3: in <module>
    from .service import WechatDirectory
src/console/directory/service.py:9: in <module>
    from console.channels.common import ROLES, timestamp
src/console/channels/__init__.py:10: in <module>
    from .alibaba import AlibabaAdapter
src/console/channels/alibaba.py:6: in <module>
    from console.media import tokens
E   ModuleNotFoundError: No module named 'console.media'
=========================== short test summary info ============================
ERROR tests/audit/test_wechat.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.22s
```

这与 WG-13 一致。只把开发副本中真实的 `console/media/{__init__,tokens,files,alibaba}.py` 源码复制到 `/tmp/r7b-support-kpff4jno/console/media`，`sitecustomize.py` 仅扩展 console.__path__，没有生产业务数据或配置。四份临时依赖 SHA-256 依次为：

```text
ced5f3a0f932bbaaa6140ae8ceb081161800f896b998260729deb97fef5880f8
25440e4ce6ad69f2e952aaa2c1d55e2d0070807f9731f898faba828ee58ed184
7c6a84e3819dc6a50e3e11c8e9f92ba6e5f941c656ba6eda9b6e808b5a21917b
282823a19e141425fa2f352eccc9dff2ab019721f67d06a48cc13e2811f76c93
```

50 个新测试覆盖补丁反向还原生产 SHA 再正向应用、重复扫描不增加归档/观察、errno 和未知阶段脱敏、合法字符构成的非白名单异常仍不泄露、源和输出错误分开、发布失败只清本次临时文件、软链接/超大文件/挂载变化拒绝、私有副本权限/硬链接/旁文件拒绝、WAL 模式完整备份不产生旁文件、跨范围知识块不误算、NAS 唯一项仍须哈希相同、下载前零 RPC、RPC 超时/非法回执只调用一次、历史 unknown/blocked/submitting 不重选、只读轮询重试以及路径逃逸回执拒绝。视频状态机仅从候选 AST 加载实际函数，提供假来源、假下载器和临时 SQLite；不导入真实配置或模型。测试禁用网络与生产数据库连接。

这些通过项全部基于虚构数据。上文生产挂载的只读统计与字节核验是诊断证据，不是恢复测试、真实 OCR 质量、真实下载或完整业务验收。没有运行 e2e_smoke、演练服务器、固定端口服务、前端构建或 tsc。

## 复核工具与部署、回滚

`scripts/wechat_media_failures.py` 仅接受 `/tmp` 当前用户持有的 0700 目录，内含 0600、单链接的 `state.sqlite3`、`registry.sqlite3`，可选 `catalog.sqlite3`。必须来自 SQLite backup，不能复制在线主文件冒充完整副本。所有字段分组输出均走固定白名单，未识别值汇总为“其他”；不打印原始 ID、哈希、标签、正文、相对路径或错误原文。

复核方式（本轮已运行；变量由执行者设置为私有 backup 目录，不放进仓库）：

```sh
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python scripts/wechat_media_failures.py --snapshot "$R7B_SNAPSHOT"
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python scripts/wechat_media_failures.py --snapshot "$R7B_SNAPSHOT" --source-root /mnt/nas229/edge/wechatmove/xwechat_files --blob-root /var/lib/imgw/wechat/local-media-registry-v2
```

第二条只读完整源字节和已有归档，可能产生 NAS 读取负载，不测试生产输出写入或调用任何采集入口。工具不创建副本、不读取配置、不运行 worker，也不生成待执行恢复清单。本轮临时副本在 `/tmp/r7b-wechat-k5p3uvk7`，0700；不属于仓库交付物，交接后按私有诊断数据留存规则清理，禁止复制到公开仓库。

以下步骤**仅供总指挥审阅后部署，本轮未执行**：

1. 先在开发副本合入这 10 个新文件，核对上述两个生产 SHA 与统一基线是否一致；有任何漂移就停止应用并重做差异审查。按 WG-13 补齐同版本媒体公共依赖，在虚构环境重跑本套件、pipeline、wechat audit；统一上线前的真实只读演练和 e2e 由总指挥安排。
2. 由总指挥统一停扫描 timer/正在运行的扫描服务、视频 pager timer 与 `vk-ingest-cards.service`，等待进程退出，避免替换中的混合版本或恢复执行。若实际还有其他导入该 worker 的常驻进程，也纳入统一窗口。没有授权时不要为了验证代码启动真实 main：它会补作业、调用下载器并唤醒后续处理。
3. 在私有备份目录保存两个原源码的字节、权限、属主、SHA，以及相关服务/定时器的原运行状态；需要数据库留档时用 SQLite backup，只作审计，不为回滚覆盖新数据。不要把备份、数据库或配置放入仓库。
4. 将两个源码副本放在专用 staging 目录下的 `video_knowledge/`。**只在 staging** 先运行 `sha256sum -c <仓库>/deploy/wechat-media/baseline.sha256`，再对两个 patch 运行 `patch --dry-run --fuzz=0 -p1`；通过后在 staging 应用，核对候选 SHA。总指挥审阅该具体差异后，才按既有安装流程复制候选到对应生产路径并保持原权限。无需库迁移、路由挂载、前端构建或新的单元文件。
5. 安装完代码，先复核进程导入路径和文件 SHA。保持视频下载服务停止，直到总指挥另外确认恢复窗口及队列边界；只安装分类修复不等于批准发起下载。扫描恢复也由总指挥安排，届时看固定阶段/errno 分布。保留原告警对新错误码的分类兼容；不得以“服务成功”或新码消失代替业务验收。

回滚：在同样的统一停止窗口恢复备份的两个源码文件、权限和属主，核对原 SHA；恢复经总指挥确认的原定时器/服务状态。没有 schema 变化、没有新业务库需要降级。**不还原旧业务数据库，不删除新登记，不把 unknown/blocked 改回 queued，不重放任何下载、模型或发送。** 开发副本回滚则移除本轮 10 个新增文件；临时测试源码依赖可独立清理。

## 共享交接、安全自查与未完成项

**需要总指挥合并的共享文件改动：** 本轮共享文件实际改动为零。WG-13 的完整快照导出仍需总指挥补齐同版本 `src/console/media/{__init__,tokens,files,alibaba}.py`，否则原命令的审计收集错误仍在。需求账本中登记本次诊断、未知原因和“未部署/未恢复”状态由总指挥处理；本轮不编辑 requirements.json、README、master-plan 或其他任务模块。web 路由表、导航表、`scripts/rehearsal_server.py` 均为零改动。

安全自查：

- 越权：没有新增 HTTP 入口、权限或放行规则；原来源授权、同范围索引与不可变身份触发器保持原样，统计不生成执行授权。
- 路径穿越：副本拒绝 `..`、软链接、硬链接及非私有权限；源逐级 nofollow，校验挂载和读取前后身份；文件名不成为输出路径。下载回执仍只接受固定派生名和原 Windows 目录。
- 注入：SQL 表和字段来自已核对的固定源码，业务值用参数；新错误字段只落白名单码；没有 shell 执行附件、XML 或业务文本。
- 泄露：报告只有聚合量、公共代码路径和代码 SHA。未读凭据配置或实际凭据值；生产正文、文件名、群名、哈希及附件字节没有进入仓库、测试或日志。副本保留在私有 /tmp。
- 重复动作：没有真实发送、下载或模型调用；历史未知继续冻结，补丁不改旧状态。单写者、事务、SHA 去重和原调用账本机制不变；不会因“无回执”自动重试。

**尚未完成且不能用测试通过代替的事项：** 历史 230 项具体路径/errno、46 个 blocked 的历史子规则、3 个 archive_unavailable 和 3 个 download_failed 的底层错误、10 个图片的历史解析异常、7 个原件恢复账本阻断的具体异常仍缺原始证据；19 个未知下载没有取得可核对的远端回执；当前授权、真实模型账本逐项对账、预算与解析质量没有验证。未修复这些历史记录、未让真实媒体成功率上升、未部署、未恢复服务或队列。交付的是有证据边界的诊断、后续分类修复和虚构验证，不能宣称真实数据已恢复可用。

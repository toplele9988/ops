# 供应商档案每日增量

本包接续第一批 `dossier-v1` 侧库，不依赖已停用的按消息知识库服务。没有新增路由、定时器或服务，不会下载附件或发送消息；默认对本地已归档的无文本图片执行 OCR。默认侧库路径直接复用 `console.dossiers.store.DB_PATH`，微信输入默认 `/var/lib/imgw/wechat/api_state.sqlite3`。

## 流程与数据边界

1. **发现**：读取 `messages.rowid`，只接收 `sd_supplier.contacts_json` 中登记的供应商群，以及按 `(app_id, own_wxid, peer_id)` 连接后 `business_role='supplier'` 的私聊。首次运行只登记当前最大 rowid，不回放第一批历史。保存设备/inode、尾部消息身份哈希和 rowid；换库、截断或锚点改变会报 `source_cursor_reconciliation_required`，不会静默从头重放。
2. **解析复用**：读取消息文字，以及 `company_media/company_note_assets` 成功状态 `ready/ok/success/completed` 的文本列。不从业务库读取 original/blob 列；本地 OCR 仅通过安全文件描述符读取已归档原图。载荷中有附件但尚无解析行，也登记 `parse_state='no_text'`；空文本、失败状态、截断均进入覆盖统计。已由本 worker 登记且最近七天观察过的消息会再次检查附件版本，处理解析晚于消息入库的情况。此回看不会扫描第一批历史；超过七天才完成的附件需要另行审计回放。
3. **分窗**：按供应商、北京时间自然日分组，再按 UTF-8 大小切分；长文本使用重叠片段，原文不静默截断。同一消息可出现在多个片段中，报价幂等键消除重叠输出。我方发出的文字保留来源记录，不送入报价抽取。
4. **抽取**：一律经 `console.ai_gateway.run` 调用 `ai-task run bailian_free --model auto`，提示通过 stdin 传递，读取网关 `answer` 字段中的 JSON。账本保留 Flash/Pro 逻辑层级，仅当模型明确返回难例时进行第二读；免费池的实际模型由网关选择，每窗最多两次调用。不转付费池。JSON 顶层、条目结构、大小均检查；每个字段必须携带 `source_id` 与原文连续摘录。摘录不能在该来源文字中逐字找到，就丢弃该字段。型号、配置、条款的值也需出现在摘录中，金额须为非负十进制文字；JSON 数字、千分位或科学计数法不会作为金额落库。币种还需明确 ISO/中文币种证据，单独的 `$`/`¥` 不推断币种。
5. **匹配**：仅比较本供应商已关联的 canonical_model/model_aliases。规则只做 NFKC、大小写与空白/横线/下划线归一化；配置必须兼容且候选唯一。无法匹配时追加供应商独立的产品族候选，标记“待核”。不按同名私聊联系人合并供应商；没有明确账户绑定的供应商私聊建立独立待核档案。不跨供应商合并同款。多个身份或型号候选冲突进入 `merge_conflict`；无文本、未知字段、新产品候选不进入人工异常队列。
6. **投影**：追加 `sd_source/sd_offer/sd_evidence` 和必要的产品/关联行，不修改第一批行。新报价的 `supersedes_offer_id` 指向同供应商、同配置、同币税/单位/数量/交期/包装等条件的旧报价；旧报价不改。缺少明示报价日期时，可用来源消息的北京时间日期，依据单列为 `message_date`，不会称为明示报价日期。有效期不推断。新证据完整覆盖已知缺口类型时，追加 `sd_gap_fill`，记录来源、报价和逐字段证据。未知或冲突型缺口保留，`quote_terms` 需币种、税口径、单位、MOQ、交期全部有证据才补齐。
7. **异常**：仅对可比历史报价，以 Decimal 计算历史中位数，变化绝对值严格大于 30% 才建立 `price_anomaly`。等于 30% 不异常；中位数为零而新报价非零则异常。无价格、币种/条件不可比时不制造异常。

`sd_offer.tax_basis` 是第一批的 **NOT NULL** 列，因此未知税口径必须保留 `unknown`；未知币种、税率、金额、单位、MOQ、交期、有效期均为 SQL NULL。这是既有结构约束，无法在“不改旧表结构”的同时把 tax_basis 写成 SQL NULL。

## 增量表与事务

| 新表 | 用途 |
| --- | --- |
| sd_cursor | rowid 水位、来源锚点、最近发现时间 |
| sd_job | 稳定输入/内容 revision/工具 revision、状态、租约、退避、可恢复窗口快照 |
| sd_run | 每次模型调用的请求/实际模型、revision、提示哈希、数字用量、预留、失败码 |
| sd_review | CHECK 约束只允许 merge_conflict / price_anomaly |
| sd_gap_fill | 缺口补齐覆盖层，保留旧 sd_gap 原行 |
| sd_projection | 本 worker 追加行的主键清单，用于摘要和精确退回第一批 |
| sd_ocr | 图片哈希唯一的本地 OCR 结果、引擎 revision、状态、行框/置信度及固定失败码 |

迁移幂等，不 ALTER 旧表。发现任务与推进游标同一事务提交，所以 `--max-windows`、预算停止、单窗口失败不会丢掉已发现输入。来源读取事务在模型调用前关闭。任务认领使用十分钟租约；未退出的调用以 token 预留计费，租约到期后可重新接管。

窗口的来源、产品、报价、证据、补齐、异常以及任务完成状态在同一事务提交，投影失败全部回滚。调用前先单独持久化预算预留；调用结束的实际用量与投影完成一起提交，失败时与 job 失败一起记账。失败只保留固定/白名单错误码，不保留 stderr、异常正文或上游任意文本。退避从 60 秒指数增长，最高一天，其他窗口继续。

`sd_job.cursor_json` 包含恢复所需的已解析文字，侧库与 JSON 报告都属于内部私有数据。CLI stdout 只打印数字与不透明会话标识。`service.updates()` 为只读最近七天摘要；`supplier()` 和 `family()` 原有报价投影已经保留 quoted_on、quoted_on_basis、source_id、field_sources 和 evidence。缺口列表、计数、筛选值、家庭卡片和草稿查询均应用补齐覆盖层。读取第一批未迁移侧库时不创建新表，updates 返回 ready=false。

## 单次运行和报告

在包含 `src/console` 的环境运行；开发副本还需让现有运营台公共模块可导入，例如设置 `PYTHONPATH=src:/opt/imgw/wechat`。以下均为命令说明，本次未运行线上写入命令。

```bash
python -m console.dossiers.incremental run --once \
  --database /path/to/dossiers.sqlite3 --wechat /path/to/wechat.sqlite3 \
  --daily-budget-tokens 300000 --report /path/to/private_out/incremental
```

只读演练必须指定报告目录，可覆盖 rowid 回看：

```bash
python -m console.dossiers.incremental run --once --dry-run \
  --database /var/lib/imgw/ops-app/dossiers/dossiers.sqlite3 \
  --wechat /var/lib/imgw/wechat/api_state.sqlite3 \
  --since-rowid ROWID_BEFORE_REVIEW_RANGE --max-windows 15 \
  --report /srv/imgw-dev/ops/private_out/dossier-incremental/
```

`--since-rowid` 仅允许 dry-run，读取指定值之后的 rowid，包括迟到消息；它不会改变持久游标。dry-run 从只读连接备份到内存，迁移、续链、预算与投影都只发生在内存，线上两库只使用 `mode=ro` 和 `query_only`。首次不覆盖游标的 dry-run 也是从尾部起步，不会历史回放。模型调用仍会真实发生，并消耗免费额度；演练内存中的账单不会跨进程持久化。

报告为以 session_id 命名的 JSON 和中文 Markdown，文件权限 0600。JSON 包含来源覆盖、新档案/产品候选、完整拟新增报价、替代关系、缺口补齐、人工异常和逐调用记账。先检查报告目录可写，再调用模型；路径不可写会在消耗额度前退出。Python API 可以省略 report，在纯内存中运行并由调用方仅取数字摘要；CLI 为保证常规演练可审查，仍要求 `--report`。

返回码：窗口失败或整体不可用为 1；处理成功、首跑登记或正常预算停止为 0。预算停止时 summary 中 budget_exhausted=true，任务保存为 budget_wait。`--max-windows 0` 只发现/排队，不调用抽取模型；仍可能执行本地 OCR。完全停用 OCR 使用 `--no-ocr`。报告缺失、参数无效由 CLI 提前拒绝。

## 本地图片 OCR

默认 `--ocr --max-ocr 200`；`--no-ocr` 跳过本轮图片补文，保持已有解析文本的抽取流程。`--max-ocr N` 限制本轮不同图片的未命中尝试数，安全校验失败也占一次；缓存命中不占限额，`0` 只复用已有缓存。已有排队窗口保留发现时的文本快照，`--no-ocr` 不会重写这些历史任务。

只处理供应商发来的 `company_media` 图片：文本为空、format 为 jpeg/png/webp/bmp、archive_status=ready 且有 archive_sha256。不会处理 note_asset、未归档图片或我方发图，也不覆盖已有成功解析文本。微信只读快照关闭后才识别。图片格式与归档状态进入来源 coverage；`coverage.ocr` 与 `coverage.no_text` 分别统计 OCR 成功补文和仍无文本的来源，discovered_coverage 表示本轮发现范围，coverage 表示本轮新增来源。

引擎使用宿主已安装的 rapidocr_onnxruntime 1.4.4 / ONNX Runtime，`RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)`，惰性加载、进程内串行复用；只使用包内现有模型，不下载模型或安装依赖。逐级目录 FD 加 `O_NOFOLLOW` 打开 `/var/lib/imgw/wechat/owner_workspace/media_archive/<sha256>.blob`，不解析符号链接。文件必须为普通文件，最大 20 MiB；读取前后核对文件状态并验证 SHA256。Pillow 限定 JPEG/PNG/WEBP/BMP，verify 后完整解码，最多 25,000,000 像素，拒绝解压炸弹告警、损坏和多帧图片。原图字节和解码图像全程在内存，不创建原图副本。

丢弃置信度小于 0.5 的行（0.5 保留），按 bbox 最小 y、自左至右的最小 x 排序，再以换行拼接；lines_json 保留过滤后行框、文字、置信度，mean_confidence 为保留行的算术平均。sd_ocr 的 archive_sha256 是主键，engine_revision 固定记录所用算法版本；ready/no_text/failed 均缓存，同图跨消息、跨运行不自动重识别或按引擎版本自动覆盖。失败只保留固定 error_code，例如 unsafe_archive、blob_too_large、invalid_image、image_too_large、blob_hash_mismatch、engine_unavailable、ocr_failed，不保存异常正文。缓存未命中受同一发现事务串行保护。

OCR 文本在分窗前补入来源，method=ocr，同时生成与原无文本版本不同的稳定 source_id；原文件哈希和消息关联保持可追溯。逐字段证据按其引用来源分别标记 `method=ocr, evidence_state=ocr_candidate`，即使同一报价混合消息文字和 OCR 也不会整条误标。报价和缺口补齐按既有规则投影，价格异常仍使用历史中位数和严格 >30% 阈值，人工队列仍仅 merge_conflict/price_anomaly。前端显示“图片识别，待核对”，来源弹层继续通过现有签名媒体接口显示原图。dry-run 可读已有缓存，新识别结果仅放内存字典，连内存快照的 sd_ocr 也不写入，更不写线上侧库。

2026-11-05 本地实测（imgw，真实引擎、内存现画的 1200×360 合成中文报价图片）：初始化 0.6143 秒，三次识别分别 1.6067 / 0.9577 / 0.8054 秒，每次保留 4 行。识别均值约 1.12 秒/张（不含初始化）；重复同一合成输入仅用于测速度，不代表真实供应商图片吞吐或正确率。真实演练在指定报告目录的 EROFS 预检处退出，识别张数和模型调用数均为 0；未转存报告。

局限：置信度不是价格正确率；模糊截图、小数点、币种符号、倾斜和复杂表格可能错读或把列次序拼错，候选需看原图核对。仅处理单帧受限大小图片，不解析 PDF、笔记图、云端附件或未归档原图。达到本轮上限的图片暂时保持 no_text，沿用最近七天已登记消息的回看规则逐轮补文；超过回看期需显式审计回放。失败缓存不自动重试。OCR 文本可能进入模型提示、任务快照和私有报告，仍遵循档案私有数据边界。

## 用量和成本估计

默认日预算 300,000 token，按北京时间自然日汇总所有持久 `sd_run`，包含其他进程的预留。每次调用先预留“提示 UTF-8 字节数 + 8192 输出 token”，防止额度不足还发起请求；成功后以模型实际 usage 结算。usage 缺失、崩溃或调用结果未知时保留预留，不能宣称为实际消耗。预算不足则暂停后续窗口，次日继续。

现有 `ai-task` 没有最大输出 token 参数，因此预留属于保守调度上限，并非服务端严格生成上限。若实际用量超过预留，仍全额记账并阻止后续调用；不能承诺任意供应商响应都绝不会使当日实际值超过预算。没有做隐藏重试，难例的第二次调用也需独立预算。

成本可先用 `窗口数 ×（平均输入 token + 平均输出 token）+ 难例第二读` 估算。例：40 个窗口、每窗 3000 输入/1000 输出、10% 难例，约 176,000 token；这是容量假设，不是本次实测。仅使用免费池，免费池不可用时失败退避，不转付费。串行运行时间约为调用数乘以单次响应耗时；并发扩容由后续宿主决定，当前通过数据库事务与租约避免重复认领。真实演练没有获取成功模型 usage，尚不能用它校准提取成本或准确率。

## 后续定时接入

由总指挥在运营台宿主或既有计时器中，每日以 imgw 身份调用一次上述 `run --once` 命令，并配置私有报告目录、日预算和可写侧库。首次应单独运行以登记尾部水位，再接入日常执行；两次执行之间新收到的消息才属于本 worker。人工异常消费可随后接 `service.updates()`，本包没有新增 HTTP 路由。不要接回已停用的知识库任务。

网关已采用 imgw 身份直连路径，免费池使用 `model=auto`。本次 OCR 任务未修改 ai_gateway.py。当前沙箱的指定演练报告目录返回 EROFS，可写预检失败，未到达模型调用阶段；宿主需按 INTEGRATION.md 的完整命令补做真实演练。

## 回滚到第一批

先停止后续调度。旧表结构与旧行一直保留，删除补齐覆盖表即可让旧缺口重新显示。但是，**只执行 DROP 新增表不能清除已经追加到 sd_offer 等表中的增量行**，也会丢失清理依据；不能将其称为完整恢复第一批。

提供 `incremental.rollback.rollback(db)`：在同一事务内，先按 sd_projection 删除仅由本 worker 追加的行，再删除七张新增表（含 sd_ocr）。旧行逐值不变，原第一批视图恢复；外部后来新增的引用若阻止删除，外键检查会中止整个事务。不要提前删除 sd_projection。虚构临时库测试已验证整个第一批行集合恢复。

以下仅是由后续负责人执行的回滚方式，本次没有连接线上写库：

```python
import sqlite3
from console.dossiers.incremental.rollback import rollback
from console.dossiers.incremental.worker import transaction

with sqlite3.connect('/path/to/dossiers.sqlite3', isolation_level=None) as db:
    db.execute('PRAGMA foreign_keys=ON')
    with transaction(db):
        removed = rollback(db)
```

## 验证

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/ops-deals-venv/bin/python -m pytest tests/dossiers -q -p no:cacheprovider
```

模型测试使用注入的假 runner，另拦截 subprocess.run，禁止真实 ai-task。库连接只允许虚构临时文件和内存快照。演练环境限制与数字结果记录在根目录 INTEGRATION.md，不包含真实消息、报价或供应商名。

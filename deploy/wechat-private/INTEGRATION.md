# 微信业务私聊接入候选

本包针对 2026-11-03 只读读取的生产源码生成，尚未安装或回放生产数据。真实入口为 `/opt/imgw/wechat/api_groupbot.py`；`baseline.sha256` 固定入口及相关依赖的逐文件 SHA256，`candidate.sha256` 固定补丁后的源码。工作区 `.git` 无法作为 Git 仓库读取，因此这里的基线是生产文件哈希，不声称核验了 Git commit。

本任务依据老板 2026-11-03「存私聊」确认执行。需求和完成状态记录在本文件及 `docs/wechat-private.md`；按本次文件范围，没有改需求账本、总纲或项目进度。

## 改动与数据路径

| 生产文件 | 行为 |
| --- | --- |
| `api_groupbot.py` | Store 初始化主动建私聊表；外部私聊在既有指令分派之前被动存储，收到/本人发出的回调分别保留原有归档证据；失败返回 503，可安全重试。 |
| 新 `wechat_private.py` | 使用现有 `normalize_private_message`、`normalize_owner_media_message` 和 `GroupMessage` 存储契约；统一无符号消息 ID，保留真实 sender、peer、时间、正文及原始媒体描述符。 |
| `company_materials.py` | 对非群来源校验私聊身份、方向、scope、当前账号、profile、终态及完整 payload；随后复用原下载、原件归档、提取、重试、笔记子附件流程。群来源走原校验。 |
| `console/channels/wechat.py` 与新 `wechat_private.py` | 读取同一主库，私聊标 `is_group=False`；签名媒体引用沿用 `wx/app/message/ordinal` 和 `company_media`。角色来源库变化会使适配器缓存失效。 |
| `console/ops_queue/inbox.py`、`service.py` | 本地投影同样读私聊；unknown 对象直接进入「待你确认归属」，不等待模型扫描，不重复已有待确认卡片。 |
| `console/kb/search.py` | 在实际 `Search.search` 入口合入只读私聊关键词命中，含 ready 附件与笔记子附件文本。`owner` 可读全部业务私聊；`product_facts` 仅供应侧；`customer_reply` 排除全部私聊命中。 |

新增三张表：`wechat_private_contacts` 保存五类角色和识别依据；`wechat_private_messages` 保存方向、对方、账号绑定、scope 及回调哈希；`wechat_private_receipts` 保存每个归档回调的回放结果。主消息写入现有 `messages`、`message_scopes`，状态固定为 `collected_private`，不占用 pending/processing/sending 队列。`group_id` 列在这些行存对方原始 ID，并非伪造群 ID；原群消息不修改。

逻辑幂等键为 `(app_id, message_id)`；逐回调回执键为 `(account_sha256, callback_sha256)`。相同 ID 的不同正文记 conflict，保留原始归档，不覆盖已存正文/原件。74→6 文件完成回调是限定例外：仅同账号、对方、方向、时间且尚无原件时升级描述符，使用现有 `media_completion_pending` 精确唤醒原媒体处理函数，不回退全局游标。迁移与入库使用显式事务。

业务范围是登录微信与外部客户、供应商的一对一聊天。系统身份、公众号、群回调以及既有 `owner_wxid`、`group_member_wxids` 指令联系人沿用原路径；不把内部指令私聊重新当作客户业务消息。对方身份优先取往来单位 `identities`（含合并投影）和旧 `conversation_ref` 的精确映射，再用账号匹配的本地联系人备注/昵称及现有 `directory.service.naming`、`roles.common.party_name_parts`、triage 规则。混合 SUP/CUS 或无法识别时为 unknown，不自动建单位或绑定身份。

`business_role` 保留 customer / supplier / forwarder / vendor / unknown；为兼容现有收件箱筛选，投影 `counterpart_role` 将 forwarder/vendor 归入 supplier，unknown 归入 unclassified。确认后的真实身份映射优先于旧入库快照。客户/供应商页实际通过 `console.customers.operations.Parties.read` 的共享 inbox + identities 读取时间线，确认关联单位后可显示私聊双方消息；仅名称猜测不会自动挂到一个单位。

## 历史能力核查

已核对本地 `reference_docs/wtapi/20261012/llms.txt`、141 页接口快照及 `wtapi_client.py` 的方法、联系人和下载接口。没有找到普通微信私聊按联系人/时间/游标拉取历史消息的已记载接口，也没有相应客户端方法。`347297255e0` 是 `/finder/v2/api/finder/syncPrivateLetterMsg`，属于视频号私信，不用于微信好友聊天。回调文档 `7369026m0` 描述历史同步/重试可能重复推送，但未提供可由本客户端调用的普通私聊历史拉取协议。

因此本包只回放已保留的本地回调，不编造 HTTP 接口、不建立服务商补拉定时器；不能恢复未收到/未保留的历史。本人在手机发出的消息仅在服务商回调包含可靠 From/To 字段时可采集；服务商主动发送接口不保证产生本人回调。

## 回放数量预估

| 口径 | 数量 |
| --- | ---: |
| 收到私聊归档 | 279 |
| 本人发出私聊归档 | 45 |
| 可回放外部业务回调 | 69 |
| 去重消息 | 58 |
| 排除的内部指令或非业务回调 | 255 |

上表为本次单库只读快照。回放命令输出 scanned / inserted / upgraded / duplicate / conflict / invalid / skipped 数字，不打印聊天、账号、媒体票据或配置值。数量随自然流入变化；重复回调和文件版本不能按归档行数当消息数。

## 隔离验证

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest tests/wechat_private -q -p no:cacheprovider
```

本轮结果：35 passed。测试只读生产源码来构建对应哈希候选，所有测试配置、消息、单位、票据、原件和 SQLite 都在临时目录且为虚构数据。网络连接、子进程和测试临时目录以外的 SQLite 连接均被禁止。测试实际执行接收钩子、原件落盘、笔记正文/子附件处理、回放、检索入口、客户/供应商页真实单位时间线及安装/失败恢复/回滚；服务管理和下载器均注入虚构实现。其他机器可用 `WECHAT_PRIVATE_BASELINE` 指向相同哈希的源码副本。

## 上线步骤

本轮交付安装包，不执行上线。不要把 `install.sh` 当只读检查。

1. 在有业务服务安装权限的运维环境先准备隔离候选并核验哈希：

   ```sh
   python3 deploy/wechat-private/prepare.py --destination /tmp/wechat-private-review
   cd /tmp/wechat-private-review
   sha256sum -c /path/to/ops/deploy/wechat-private/candidate.sha256
   ```

2. 在授权演练环境以该候选为接收器/运营台源码，运行上面的隔离测试和项目要求的 `scripts/e2e_smoke.sh`；桌面、手机全部通过后才可安装。**当前 smoke 脚本包含 sudo、生产静态目录复制和演练进程管理，本轮只读任务未运行，也未声称通过全站/真实浏览器验收。** 它生成的 `artifacts/ux-smoke/result.json` 必须 `checked>0`、`failures=[]`。不要用单元测试替代该上线前检查。

3. 从仓库根目录安装：

   ```sh
   deploy/wechat-private/install.sh --smoke-result /path/to/verified/result.json
   ```

   脚本顺序为：核验所有生产基线 → 准备候选 → 记录原服务状态 → 停止正在运行的 `wechat-receiver.service`、`imgw-console.service`、`imgw-console-lan.service` → 再核验基线 → 私有目录备份改动源码与 SQLite 在线快照 → 原子替换 8 个源码文件 → 核验候选 → **立即主动迁移三张私聊表和索引** → 按原运行状态启动服务 → 检查 active。

   `manage.py migrate` 使用独立 SQLite 连接，不构造生产 Store、不碰发送队列或授权。代码/迁移失败会恢复原源码并尝试重启；服务启动失败返回错误，按下方回滚命令恢复。备份默认 `/var/lib/imgw/wechat-private-rollouts/<UTC时间>/`，目录 0700、数据库 0600，不放进公开仓库。

   接收服务预计中断 10–30 秒，首次启动/数据库锁等待可能更长；运营台两个进程预计中断 5–30 秒。此为估算，未做生产停机实测。停机期间回调可能由服务商重试，但不能保证其重试覆盖，需核对前后时间段；脚本不改代理、路由、防火墙、DNS 或发送配置。

4. 启动后核对有效入口、服务 active 和现有运营 API 路由。此次没有新增 HTTP 路由，仍走收件箱、单位、运营台与知识检索路由。先核对真实收到的一条外部私聊方向、对方、时间、来源与终态，确认群消息自然增量仍正常。只做读取，不向任何客户发送测试消息。

5. 以服务原有 `imgw` 身份低频分批回放；每次最多 200 个归档回调，间隔至少 30 秒，直到 `scanned=0`：

   ```sh
   deploy/wechat-private/replay.sh --config /var/lib/imgw/wechat/api_config.json --limit 200
   ```

   回放本身只读本地归档并写消息/回执，不连接服务商、不执行指令或模型。接收器已有媒体 worker 随后依原配额下载和归档附件；回放消息入库不等于附件下载、OCR 或建档全部完成。重复执行幂等；不修改不可变 received/outbound 归档及其 retained_only 状态。

6. 以数字核对回放各状态、私聊终态数、unknown 联系人数、媒体 ready/失败数；确认单位关联后的客户/供应商时间线和关键词检索，核对给其他客户的 `customer_reply` 无私聊证据。已有群回调的规则、scope、发送状态和媒体行为不得因本包改变。

## 风险与回滚

- 私聊新增存储和媒体下载会增加磁盘、解析和现有云 OCR/ASR 配额消耗；沿用原媒体大小/格式/并发/未知结果处理限制。暂不支持的类型显示待解析占位，原始回调仍保留，不能报告为已提取。
- 历史不受原群 active_after 截断，范围仅本账号已保留外部私聊；将来时间异常的回调记 invalid。命名只是角色提示；不会自动给客户提供供应商聊天或报价。
- 私聊关键词检索直接读消息及 ready 媒体文本；不新增向量索引或模型调用。现有 catalog 统计不包含这部分新增消息，不能拿旧向量数作为私聊验收。
- 联系人库/单位库不可读时退回命名或 unknown，不扩权限、不调用服务商补名字。确认归属后，单位页复用已有身份确认机制；并非全自动建档。
- 相同消息 ID 的冲突不会覆盖原文，计入 conflict 待人工核对。基线或依赖哈希漂移时安装拒绝；必须重新读真实源码生成补丁，不能跳过验证或模糊套用。

回滚命令（使用安装时打印的备份目录）：

```sh
deploy/wechat-private/rollback.sh --backup /var/lib/imgw/wechat-private-rollouts/<UTC时间>
```

脚本停止相关运行服务，先检查当前源码仅为此候选/原基线（发现后续人工改动会拒绝覆盖），恢复原源码并移除两个新增模块，再按记录启动服务。**回滚保留新增表、回放回执、已采消息和附件，不以旧数据库覆盖当前库**，避免丢失安装后的群回调或触碰发送队列。旧适配器过滤非群消息，旧媒体校验不处理这些私聊；消息的终态也不会被旧发送 worker 消费。SQLite 快照仅供人工灾难恢复，不是常规回滚步骤；不得在线覆盖活动数据库或删除 WAL。

# 阿里国际站接收端 v2：附件、商品与询盘卡片

v2 **已于 2026-11-01 部署**，对应需求账本 H2、WG-01。本页保留 R3-A 的 v2.1 契约与历史验证。R3-D 以当前仓库两份 v2.1 文件为基线，在 `deploy/icbu/v2.2/` 交付完整候选和 `receiver-v2.2.patch`，**本轮未部署**；存储故障行为、当前基线与部署办法见第8节。测试只使用虚构数据，不能据此确认真实浏览器采集、真实附件或生产数据可用。

接收端只归档消息、附件和卡片；不抓取来源 URL，不解析或执行附件，不向阿里网页或客户发送消息。Windows 正式采集基线为 MV3 扩展与 Node/SQLite 本地网关，原有稳定事件 ID、receipt、持久 ACK、去重键和游标全部保留。

历史 v2→v2.1 补丁使用的生产 v2 基线（不是 R3-D 的补丁基线）：

| 文件 | SHA-256 |
| --- | --- |
| `/opt/imgw/scripts/icbu_inbound_receiver.py` | `d2bc8fbb487ee1c4cf8ee3f13af31438dce3484773b44de84a8f48028234fe1b` |
| `/opt/imgw/scripts/icbu_attachments.py` | `b76a97da8f478c6d5244c5a2cba4371cbf8c676136e5f2929afad8c9985efb37` |

`receiver-v2.patch` 保留 v1→v2 历史，`receiver-v2.1.patch` 保留 v2→v2.1 历史。R3-D 只使用第8节的 v2.1→v2.2 补丁。部署前须再次核对两份生产文件，任一哈希不同都应停止应用，重新比对。

## 1. 给 Win10 采集脚本开发者

本节可以直接交给 Win10 侧联调。地址与临时测试令牌由总指挥提供；不要从生产配置复制令牌，也不要使用生产接收端做自测。

### 1.1 请求顺序与身份

1. 每条平台消息保留稳定的 `message_id`、`event_id` 和 `sent_at`。事件 ID 在补采、补附件和重试时不能变。消息方向用 `role=buyer/seller`，不能把我们发出的资料当成客户来信。
2. `POST /api/inbound/alibaba` 发送 JSON，鉴权沿用现有入站口。正文请求不超过 65536 字节；正文、附件、卡片中的未知字段均拒绝。POST 正文 `event_id` 与 `X-Idempotency-Key` **始终使用旧 ID 原文**；两者都有时必须完全一致，不能在其中一个位置换成传输别名。旧非 SAFE_ID 富消息须在正文明确携带原 ID；SAFE_ID 仍可仅由请求头补入，旧纯文字规则不变。
3. 文件用 `PUT /api/inbound/alibaba/attachments/{event}/{attachment_id}` 发送完整原始字节，不能用 multipart、base64 或下载地址代替。使用唯一的 `Content-Length`、白名单 `Content-Type` 和 `X-Content-SHA256`；哈希为完整原始字节的 64 位小写十六进制 SHA-256。
4. 用 `GET /api/inbound/alibaba/attachments/{event}/{attachment_id}` 读回登记与关联结果；鉴权、来源与 Origin 限制同入站口。此接口只返回状态 JSON，**不返回内容字节**。
5. 消息与文件谁先到都可以；内部用规范事件键与附件 ID 关联，旧 ID 和它的传输别名命中同一个键与 `inbound_id`。消息先到时控制台先显示附件占位，上传和关联完成后下次会话读取重新投影媒体令牌。缺失或损坏的文件仍需核对。
6. 同一事件最多登记 20 个不同附件 ID，包含仅上传、尚未在消息声明的文件；同 ID 重传不增加数量。附件 ID 始终为 `[A-Za-z0-9_-]{1,128}`，不能放文件名、聊天正文或目录。

#### 旧事件 ID 与传输别名

POST 的合法旧事件 ID 规则仍为 `[A-Za-z0-9._:/@+\-=]{1,256}`。满足 `SAFE_ID=[A-Za-z0-9_-]{1,128}` 的原生 ID 保持原样；保留全部 `e1_` 前缀，任何 POST 都不能将它作为原生事件 ID 提交，返回 `event_id_reserved_prefix`，避免把同一字符串解释为两种身份。除此保留命名空间外，纯文字 v1 继续沿用旧 ID 校验。

对不满足 SAFE_ID 的合法旧 ID，以 UTF-8 字节计算 `e1_` + `base64url(event_id)`，**去掉所有 `=` 填充**。只有 UTF-8 长度不超过 93 字节的旧 ID 支持富消息和路径传输，生成的别名最多 127 字符。超过限制返回 400 `event_id_alias_too_long`；旧的纯文字兼容范围不因此缩短。不得截断、哈希替换或重新生成事件 ID。

Windows 常用的 `icbu:v1:<64位小写hex>` 是 72 个 ASCII 字节，可直接采用上述编码。别名只用于 PUT/GET 路径；POST、receipt、ACK、游标仍引用旧 ID。PUT/GET 的事件段接受原生 SAFE_ID 或规范 `e1_` 别名；旧 ID 的路径传输应使用别名，不能拼出额外路径段。服务端严格校验 base64url、UTF-8、原始 ID 合法性、字节上限和重新编码一致性；无填充、无替代编码。非 UTF-8、改动后不再规范的别名返回 400 `invalid_event_alias`。

传输别名是可逆编码，不是签名。一个改动后仍规范且解出另一个合法旧 ID 的字符串，只能指向另一个事件，绝不能命中原事件；仍须使用登记读回的身份回显和 `inbound_id` 核对，不能凭前缀认定关联成功。

### 1.2 虚构示例

下例全部为虚构数据。`size` 和 `sha256` 必须以实际上传的字节计算，不能照抄占位值。纯附件消息可以把 `content` 设成空字符串，前提是 `attachments` 或 `cards` 非空。

```json
{
  "account": "Amy",
  "conversation_id": "tm:fictional-v2-demo",
  "buyer_name": "虚构联调客户",
  "country": "虚构地区",
  "content": "请查看这份虚构资料",
  "url": "https://message.alibaba.com/",
  "message_id": "fictional-platform-message-001",
  "event_id": "fictional-event-001",
  "sent_at": "2026-10-31T08:00:00Z",
  "observed_at": "2026-10-31T08:00:01Z",
  "history": true,
  "role": "seller",
  "probe_version": "2.0.0",
  "message_kind": "file",
  "attachments": [
    {
      "attachment_id": "fictional-spec-001",
      "kind": "file",
      "filename": "fictional-spec.txt",
      "mime": "text/plain",
      "size": 9
    }
  ],
  "cards": [
    {
      "card_type": "product",
      "title": "虚构演示商品",
      "url": "https://www.alibaba.com/product-detail/fictional.html",
      "product_id": "fictional-product-001",
      "price_text": "待核实",
      "moq_text": "待核实",
      "subtitle": "只用于协议联调"
    }
  ]
}
```

上传示意（头部中的值均为占位说明）：

```http
PUT /api/inbound/alibaba/attachments/fictional-event-001/fictional-spec-001 HTTP/1.1
Authorization: Bearer <测试实例专用临时令牌>
Content-Type: text/plain
Content-Length: <实际字节数>
X-Content-SHA256: <完整文件的64位小写十六进制哈希>

<原始文件字节>
```

### 1.3 自测工具

使用 Python 3.10 或更新版本运行仓库的 `scripts/icbu_v2_selftest.py`。它没有默认目标，只接受 IP 字面值，禁止任何 8791 端口，禁止所有 HTTP 重定向，不读取代理环境变量，不使用生产配置。默认只允许回环地址；显式 `--allow-lan` 只放行 RFC1918 或 IPv6 ULA 地址，并先回显目标，公网地址始终拒绝。

先检查目标；此命令不会读令牌，也不会联网：

```sh
python scripts/icbu_v2_selftest.py --url http://127.0.0.1:18792 --dry-run
```

实际测试时由标准输入提供测试实例专用的临时令牌。不要把令牌写进命令行、仓库、聊天或日志。可以把专用测试令牌文件重定向到标准输入；该文件必须仅属于隔离测试实例。

```sh
python scripts/icbu_v2_selftest.py --url http://127.0.0.1:18792 --v21 < /临时测试目录/test-only-token
```

Win10 连接总指挥提供的局域网测试实例时，在上述命令增加 `--allow-lan` 并替换 `--url`。工具不会启动接收端。测试实例须使用临时 SQLite、临时附件目录和假服务，不能指向真实库或队列。

默认保留旧有场景；验收 v2.1 时须增加 `--v21`，检查旧冒号事件 ID→补附件和卡片→别名 PUT→GET 读回，确认同一条消息，另测过长 ID、别名篡改和非 UTF-8 别名。旧场景覆盖消息先于文件、文件先于消息、重复上传、重复消息、错误哈希、可疑文件名、非 HTTPS 卡片链接和原消息查询。测试消息一律 `history=true`，同时覆盖客户和我们两个方向。每一步只输出中文结果与 HTTP 状态，不输出令牌、服务端错误原文或响应正文。网络中断后立即停止，不自动重试。

通过这些步骤只能证明测试实例在虚构数据上符合协议。真实浏览器抓取覆盖、账号绑定、附件字节可获得性、标签页休眠和真实数据展示仍需分别验收。

### 1.4 心跳：严格 v1

心跳路径为 `/api/inbound/alibaba/probe-heartbeat`，鉴权同入站口。继续使用 `icbu_probe_heartbeat_v1`，**恰好 9 个顶层键、6 个 counter**，不扩展字段。本轮不修改 `icbu_probe_heartbeat.py`。建议每 60 秒上报；成功心跳只证明探针状态已保存，不能代替单条消息的持久 ACK。

| 顶层键 | v1 规则 |
| --- | --- |
| `schema` | 固定 `icbu_probe_heartbeat_v1` |
| `account` | 当前固定绑定 `Amy` |
| `instance_id` | 小写 UUID v4，实例稳定标识 |
| `version` | 三段数字版本，每段 1–3 位 |
| `state` | `starting/running/idle/paused` |
| `range` | 恰好 `date_start/date_end`，ISO 日期；2000-01-01 起、2100-01-01 前，起≤止，相差≤366天 |
| `counters` | 恰好下表6键；整数0–1000000，布尔值不算整数 |
| `last_error` | `null` 或 `[a-z][a-z0-9_]{0,95}`；只传小写蛇形错误码 |
| `cycle_complete` | 布尔值；为 true 时 `contacts_scanned` 必须等于 `contacts_loaded` |

| counter | 固定口径 |
| --- | --- |
| `queued` | 浏览器端尚未获得本机 ACK 的事件数；不是 Node 待向 221 投递的队列长度 |
| `blocked` | 浏览器端当前被阻塞的事件数 |
| `metadata_skipped` | 最近一次扫描跳过的数量，非累计 |
| `unbound` | 最近一次周期未绑定的数量，非累计 |
| `contacts_loaded` | 最近一次周期加载的联系人数量，非累计 |
| `contacts_scanned` | 最近一次周期扫描的联系人数量，非累计；不得大于 `contacts_loaded` |

不要把最后捕获时间、Node 队列、中文建议或任意新键塞进 v1。中文原因与下一步建议留在 Windows 本地管理页；心跳不能包含聊天正文、附件字节、完整下载 URL、Cookie 或令牌。

GET 心跳路径成功返回 `ok=true`、`server_time_utc`（带时区 UTC ISO 时间）、`server_day_shanghai`（上海日期）、`poll_after_seconds=120`；POST 成功返回上述字段并加 `persisted=true`。GET 的轮询建议不改变约定的心跳发送周期。

### 1.5 时钟策略

Windows 使用鉴权 GET 心跳的 `server_time_utc` 与本机 UTC 计算偏差，按绝对值处理：

| 绝对偏差 | Windows 行为 |
| --- | --- |
| `<30` 秒 | 正常采集 |
| `30–<300` 秒 | 告警但继续采集；在本地管理页及心跳 `last_error` 之外的位置提示，不增加心跳字段，也不占用 `last_error` |
| `≥300` 秒 | 暂停新采集；心跳 `state=paused`、`last_error=clock_skew`；已入队事件继续保留与投递，不丢弃 |

校准本机时间后，应重新检查偏差和新采集时间；不能“自动修正后继续用旧 observed_at”来掩盖错误。已入队事件保留原始时间和稳定 ID，不能为了通过校验重写历史观察时间。接收端为每条入站记录保存“到达时间−observed_at”的偏差秒数，供管道健康读取；这是受控内部数据，不新增请求字段，不改消息指纹，也不在对外成功响应增加时钟字段。`observed_at` 缺失时内部偏差为空，不能伪造为零。入站库独立表 `inbound_observations(inbound_id,received_at,observed_at,clock_skew_seconds,request_sha256)` 以 `(inbound_id,request_sha256)` 幂等记录，重复请求保留首次到达，不制造多条观测；不升级 `archive.sqlite3`，不改 payload 的原指纹。

## 2. 消息字段表

`POST /api/inbound/alibaba` 的旧字段行为保持兼容。字符串长度按字符，整个 JSON 请求按 UTF-8 字节计；重复 JSON 键拒绝，不接受 NaN 等非标准 JSON 常量。URL 只作来源，保存时去掉查询参数与片段，不外联抓取。

| 字段 | 类型与范围 | 必填与说明 |
| --- | --- | --- |
| `account` | 字符串 | 必填；沿用现有接收端绑定账号，不能自行增加账号 |
| `conversation_id` | 非空字符串，≤256 | 必填 |
| `buyer_name` | 非空字符串，≤200 | 必填 |
| `content` | 字符串，≤16000 | 必填；旧消息非空，v2 有非空附件或卡片时允许空字符串 |
| `url` | HTTPS 阿里域名来源 URL，≤4096 | 必填；禁止凭据、反斜线、空白和非 443 显式端口 |
| `country` | 字符串，≤100 | 可选，默认空 |
| `message_id` | 现有 ID 规则，≤256 | 可选；实时采集必须携带平台消息 ID |
| `event_id` | 现有 ID 规则，≤256；富消息支持 SAFE_ID 或可生成传输别名的旧 ID | 可选；富消息必须提供；SAFE_ID 可由单个 `X-Idempotency-Key` 补入；富消息的旧非 SAFE_ID 须在正文提供，两处都有时必须一致 |
| `sent_at`、`observed_at` | 带时区的 ISO 8601 字符串，≤64 | 可选；实时采集必须提供 `sent_at`；转换为 UTC |
| `history` | 布尔值 | 可选，默认 `false`；历史补采用 `true` |
| `role` | `buyer/seller/system/unknown` | 可选；旧五字段默认 `buyer`，现代采集必须明确方向 |
| `probe_version` | 非空字符串，≤32 | 可选，建议必传 |
| `live_provenance` | 现有实时证明对象 | 沿用真实基线校验，不因 v2 绕过草稿准入条件 |
| `message_kind` | `text/image/video/file/voice/product_card/inquiry_card/order_card/link_card/system` | 新增可选字段，不推断消息方向 |
| `attachments` | 对象数组，最多 20 个 | 新增可选字段；同一消息中 `attachment_id` 不能重复 |
| `cards` | 对象数组，最多 10 个 | 新增可选字段；同次提交的卡片身份不能重复，规则见下文 |

| 附件字段 | 类型与范围 | 必填与说明 |
| --- | --- | --- |
| `attachment_id` | `[A-Za-z0-9_-]{1,128}` | 必填，补采与上传都复用 |
| `kind` | `image/video/file/voice` | 必填，与媒体类别一致 |
| `filename` | 非空字符串，≤200 | 必填；必须为普通文件名，禁止目录分隔符、控制字符、穿越和危险后缀 |
| `mime` | 白名单 MIME 字符串 | 必填；与上传 `Content-Type` 一致 |
| `size` | 整数，1–52428800 | 必填，布尔值不算整数；等于完整字节数 |
| `sha256` | 64 位小写十六进制字符串 | 可选；已知时必须与实际上传一致 |

| 卡片字段 | 类型与范围 | 必填与说明 |
| --- | --- | --- |
| `card_type` | `product/inquiry/rfq/order/link` | 必填 |
| `title` | 非空字符串，≤300 | 必填 |
| `url` | HTTPS URL，≤4096 | 可选；禁止凭据、空白、反斜线和非 443 显式端口，去查询与片段 |
| `product_id` | 字符串，≤128 | 可选 |
| `image_attachment_id` | `[A-Za-z0-9_-]{1,128}` | 可选，建议引用本消息中的图片附件；接收端只校验编号格式 |
| `price_text`、`moq_text` | 字符串，各≤200 | 可选；仅记录来源文本，不代表已核实价格或 MOQ |
| `subtitle` | 字符串，≤1000 | 可选 |

同次提交的卡片先规范化 URL（去掉查询参数和片段），再以 `(card_type, product_id or url or title)` 作为身份：优先非空 `product_id`，其次非空规范 URL，最后 `title`。身份重复即返回 400 `duplicate_card_identity`，完全相同的卡片也不能在同一个数组中重复。采集侧应保持身份依据稳定，避免通过新增另一套身份把同一卡片重复登记。

## 3. 文件上传、容量与幂等

每次 PUT 上传完整文件，大小 1–50 MiB（52428800 字节）。`Content-Length`、`Content-Type`、`X-Content-SHA256` 各只能有一个，长度必填；拒绝 `Transfer-Encoding`、`Content-Encoding` 和 `Content-Range`。不支持分片或按偏移续传。中途断开只留下未确认状态；重新上传必须使用相同的稳定 ID 和完整字节，不建立第二个文件或消息。

白名单采用精确 MIME 名，不接受 `; charset=...` 等参数。上传头和附件元数据必须一致，文件名最后一个扩展名必须匹配 MIME；`kind=image/video/voice` 还须匹配图片、视频或音频 MIME。`kind=file` 可用于任一白名单类型。

| 类型 | MIME | 文件扩展名 |
| --- | --- | --- |
| 图片 | `image/jpeg`、`image/png`、`image/gif`、`image/webp`、`image/bmp`、`image/tiff`、`image/avif`、`image/heic`、`image/heif` | `.jpg/.jpeg`、`.png`、`.gif`、`.webp`、`.bmp`、`.tif/.tiff`、`.avif`、`.heic`、`.heif` |
| 视频 | `video/mp4`、`video/quicktime`、`video/webm` | `.mp4`、`.mov`、`.webm` |
| PDF / Office | `application/pdf`、`application/msword`、`application/vnd.ms-excel`、`application/vnd.ms-powerpoint` | `.pdf`、`.doc`、`.xls`、`.ppt` |
| Office 新格式 | `application/vnd.openxmlformats-officedocument.wordprocessingml.document`、`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`、`application/vnd.openxmlformats-officedocument.presentationml.presentation` | `.docx`、`.xlsx`、`.pptx` |
| 压缩 / 文本 | `application/zip`、`application/x-zip-compressed`、`text/plain` | `.zip`、`.zip`、`.txt` |
| 音频 | `audio/mpeg`、`audio/mp4`、`audio/ogg`、`audio/wav`、`audio/x-wav`、`audio/webm`、`audio/aac`、`audio/flac` | `.mp3`、`.m4a`、`.ogg/.opus`、`.wav`、`.wav`、`.webm`、`.aac`、`.flac` |

拒绝 SVG、HTML、JavaScript、可执行文件、脚本和宏 Office 后缀；即使危险后缀藏在双扩展名中也拒绝。Windows 保留名、点开头、尾部点/空格、`..`、目录分隔符、百分号、控制或格式字符及 `:<>"|?*` 等名字均拒绝。浏览器无法原生显示的白名单图片不会被转换或解析，仍按下载处理。

附件按 SHA-256 内容寻址，原始文件名仅作为经过校验的元数据，不能决定落盘路径。目录 0700、文件 0600，拒绝符号链接和非普通文件，使用 `O_NOFOLLOW`。存储默认总容量 10 GiB，可调。容量不足或满盘返回明确失败，不把未落盘文件确认成成功。启动、处理消息/上传及服务每 60 秒维护检查时，会把已超过默认 24 小时且未关联消息的附件标记待清理；不自动删除，迟到消息仍可关联。`/healthz` 的 `attachments_cleanup_ok` 表示最近维护是否成功；这不是删除权限。

崩溃可能在文件落盘、登记完成前发生。维护扫描会把未被附件记录引用的合法 `<sha256>.bin` 与 `.upload-<uuid>` 文件记入独立库的 `cleanup_candidates(filename, first_seen_at, cleanup_pending)`；从首次发现起超过 N 小时才标记待清理。重新上传并成功登记后清除该候选记录，文件继续保留。正常上传孤儿按附件登记的 `created_at` 计算时限；上述遗留文件按 `first_seen_at` 计算，不猜测其业务来源，不执行或自动删除。

新字段不参与旧消息指纹。相同平台消息后来补带附件或卡片时，更新同一条入站记录，保留原业务状态、去重键、草稿状态和首次消息内容。省略新字段或提交空数组不会删除既有附件、卡片。附件按 `attachment_id`、卡片按上述身份合并；同身份可补缺失字段，不能替换已经登记的非空或空字段值。`message_kind` 可以从未设置或 `text` 补成具体类别，已登记的具体类别不能换成另一种具体类别。累计附件最多 20 个、卡片最多 10 个；每个事件的 20 个附件名额也包含仅上传尚未声明的附件，同 ID 重传不增加名额；合并后的整条规范化消息仍须不超过 64 KiB。稳定事件或附件 ID 指向不同字节、不同消息或不兼容元数据时返回 409，不能以覆盖旧资料的方式“修复”。同一平台消息出现多个事件别名时，即使载荷只有卡片、尚未声明附件，也会校验各别名下已上传的同附件 ID 是否为相同字节；不一致返回 409 `attachment_metadata_conflict`。

接收端会从原有去重键中恢复同一消息所有已经提交且支持富消息的事件 ID（包括旧冒号 ID），在消息提交前统一检查附件冲突，在附件库的同一事务中关联这些别名。主库提交后附件库失败时，下次富消息重放会补齐全部已提交别名，保留原消息与业务状态。此恢复机制用于兼容补采；采集侧仍须遵守稳定事件 ID 规则，不能把换 ID 当作重试办法。孤儿扫描由后台线程触发，等待上传锁不会阻塞 HTTP 接收线程。

规范内部键保存旧事件 ID 原文；路径别名只在边界解码，不另建第二条消息或第二份附件身份。独立附件库的 `enrichment_ledger` 按 `(event_id,inbound_id,request_sha256)` 幂等登记旧规范 ID、`event_alias`、`inbound_id`、请求 SHA-256 摘要与 `created_at`；HTTP 请求摘要按原始 JSON 字节计算；内部直接调用未提供摘要时按规范化 JSON 计算。账本不存请求正文或令牌。富消息增补只改同一条入站记录的富元数据，原状态、去重指纹、去重键和 receipt 关联保持不变。

入站消息库与附件登记库是两个独立 SQLite。消息先提交，随后关联附件登记库，成功响应在二者完成之后才返回；跨库不宣称原子提交。若消息已提交而关联失败，接口可能返回 503 或连接中断。此时保留相同 `event_id`、`message_id` 与附件 ID，经核对后重放原请求可修复关联，不重新生成消息，不重开旧业务状态或草稿。不要换 ID，也不要因结果未知自动重发任何真实渠道消息。

## 4. HTTP 响应与错误处理

### 4.1 成功响应：字段与身份回显

`POST /api/inbound/alibaba`：新消息返回 202，重放同一消息返回 200。完整成功字段：

| 字段 | 含义 |
| --- | --- |
| `ok`、`persisted` | 均为 true；消息已提交，当前请求要求的附件关联也已完成 |
| `inbound_id` | 原有入站记录 UUID；补附件、卡片与重放均不改变 |
| `event_id` | 本次 POST 使用的旧事件 ID 原文，未提供则为 null；不换成传输别名 |
| `status` | 当前业务状态，沿用旧接收端状态；增补不重开草稿或业务 |
| `duplicate` | 该消息之前已登记为 true，不代表每个附件文件已到 |
| `dedup_mode` | `message_id/event_id/none` |

`PUT /api/inbound/alibaba/attachments/{event}/{attachment_id}`：首次保存该内容文件返回 201；相同 SHA-256 文件已存在返回 200，即使此次事件或附件 ID 是新的。完整成功字段：

| 字段 | 含义 |
| --- | --- |
| `ok`、`persisted` | 均为 true；内容与本次附件登记均已持久化 |
| `event_id` | 规范旧事件 ID，路径为传输别名时仍回显旧原文 |
| `event_alias` | 可用于路径的稳定 ID：SAFE_ID 保持原样，旧 ID 为规范 `e1_` 别名 |
| `attachment_id` | 本次附件稳定 ID |
| `inbound_id` | 已关联的入站 UUID；消息尚未关联时为 null |
| `sha256` | 实际字节 SHA-256，小写64位hex |
| `size_bytes` | 完整字节数，沿用 PUT 旧字段名 |
| `kind` | 已通过消息元数据声明的附件类别；先上传、尚未声明时为 null |
| `duplicate` | 只表示内容文件已经存在，**不表示消息与附件关联已完成** |
| `status` | 有 `inbound_id` 时 `linked`，否则 `orphan` |

`GET /api/inbound/alibaba/attachments/{event}/{attachment_id}`：合法路径无论有无登记都返回 200；完整响应字段如下。无内容字节、文件名、内部路径、原下载地址或客户正文。

| 字段 | 含义 |
| --- | --- |
| `ok` | true，状态查询成功 |
| `persisted`、`registered` | 是否已有该事件与附件的登记行；仅 PUT 先到的孤儿登记也为 true |
| `file_received` | 本事件该附件的上传 SHA-256 已落库，且内容文件大小、哈希核对通过为 true；只有元数据声明、或仅其他事件上传过同内容时为 false |
| `size` | 登记字节数；没有登记为 null；此 GET 使用 `size`，不要与 PUT 的 `size_bytes` 混淆 |
| `sha256` | 已上传或已声明的 SHA-256；未知为 null；存在声明哈希不等于文件已到 |
| `kind` | 已通过消息元数据登记的 `image/video/file/voice`；旧 v2 登记没有类别表时只读原入站 payload 补回，无需重放；未声明为 null，不按 MIME 猜测 |
| `inbound_id` | 已关联入站 UUID；有事件关联时即使本附件未登记也可回显；无关联为 null |
| `event_id`、`event_alias`、`attachment_id` | 规范旧事件 ID、对应路径 ID/别名与附件 ID |
| `duplicate` | 等于 `file_received`，仅表示这次读回确认内容已存在；查询不会上传或新增关联 |

确认完成必须同时核对 `registered=true`、`file_received=true`、`inbound_id` 等于原持久 ACK 的 UUID、旧 `event_id` 与 `attachment_id` 一致，并核对大小、SHA-256、kind。单看 PUT/GET 的 `duplicate=true` 不能确认消息关联。原有 `GET /api/inbound/alibaba/{inbound_id}` 仍按既有鉴权查询消息状态，完整字段保持 `ok,persisted,inbound_id,event_id,account,conversation_id,role_assumption,status,archive_reason,attempts,error_code,draft,needs_review,auto_send,search_result,public_sources,timings,created_at,ready_at`；`needs_review=true`、`auto_send=false`，不新增别名或时钟字段。

### 4.2 超时与响应丢失后的核对

1. 保留原 `event_id`、平台 `message_id`、附件 ID、字节大小与 SHA-256。暂停自动重试；不要变更 ID 或再次发送真实渠道消息。
2. 先用同一事件路径 ID/别名 GET 附件状态，再使用已拿到的 `inbound_id` 查询原有消息状态。读回关联完成且哈希一致即可确认，不必重传内容。
3. 有文件但 `inbound_id=null`，或消息已提交而关联未完成：核对旧消息后，由调用方重放相同的富消息 POST 修复关联，不能把 PUT duplicate 当成修复完成。
4. 已登记但 `file_received=false`：核对存储健康与原文件，经明确确认后用相同事件/附件 ID、完整相同字节 PUT；它是幂等的。随后再次 GET 确认。登记不存在同样先核对消息和稳定 ID，再决定恢复步骤。
5. 读回返回冲突、503 或连接仍中断时保留未知状态，由管理员检查；不能把超时解释为服务端没提交，也不能盲目重传。重传的幂等性不能替代先读回。

### 4.3 错误响应与每个错误码

错误响应保留旧机器字段，另给固定中文说明；不要依赖中文文案做程序判断：

```json
{
  "ok": false,
  "persisted": false,
  "error": "invalid_attachment_filename",
  "message": "附件文件名格式不正确，请按 v2 契约核对长度、类型和内容。"
}
```

| HTTP 状态 | 机器码或类别 | 处理 |
| --- | --- | --- |
| 400 | `invalid_fields`、`duplicate_json_key`、`invalid_json` | 修正对象、未知/重复字段或 UTF-8 JSON |
| 400 | `invalid_conversation_id`、`invalid_buyer_name`、`invalid_content`、`invalid_country`、`invalid_message_id`、`invalid_event_id`、`invalid_sent_at`、`invalid_observed_at`、`invalid_history`、`invalid_role`、`invalid_probe_version`、`invalid_live_provenance`、`invalid_url`、`invalid_idempotency_key` | 按第2节修正对应字段的类型、长度、格式、来源域名或实时证明 |
| 400 | `invalid_attachment_path` | 核对事件和附件的两个路径段；旧事件用规范别名 |
| 400 | `event_id_reserved_prefix`、`event_id_alias_too_long`、`invalid_event_alias` | 正文保留旧 ID；原生 ID 不得占用 e1_ 前缀；旧非SAFE ID不超过93字节，路径使用规范无填充base64url |
| 400 | `too_many_attachments`、`too_many_cards` | 同一消息累计元数据超限，核对稳定编号，不能靠多次补报绕过上限 |
| 400 | `event_id_header_mismatch`、`encoding_not_supported`、`incomplete_body` | 修正 ID、编码或完整请求体 |
| 401 | `unauthorized` | 核对测试实例授权配置，不反复尝试真实令牌 |
| 403 | `origin_not_allowed`、`account_not_bound_to_token`、`invalid_preflight`、`preflight_headers_not_allowed` | 使用既有允许来源与绑定账号；按现有允许方法/头做预检，不放宽白名单 |
| 404 | `not_found` | 核对路径与稳定 ID |
| 408 | `request_timeout` | 请求结果可能未知；先核对稳定 ID，不盲目重试 |
| 409 | `idempotency_payload_conflict`、`idempotency_alias_conflict`、`idempotency_role_conflict`、`idempotency_message_kind_conflict`、`idempotency_attachments_conflict`、`idempotency_cards_conflict` | 同一键发生冲突；核对采集侧身份、方向和原始内容 |
| 411 | `content_length_required`、`attachment_content_length_required` | 补齐单个合法 `Content-Length` |
| 413 | `body_too_large`、`attachment_body_too_large`、`v2_metadata_too_large` | JSON 最多 64 KiB，附件最多 50 MiB；不要拆成伪造多个同 ID 文件 |
| 415 | `json_required`、`attachment_content_type_not_supported` | 使用正确 Content-Type，危险类型不能上传 |
| 503 | `temporarily_unavailable`、`attachment_storage_unavailable`、`attachment_storage_unsafe` | 检查测试实例存储和权限；结果不明时先核对，不更换稳定 ID |

附件专用错误码如下，`error` 不包含输入原文、文件路径或凭据。所有字段校验错误均为 400；同一类别里每个名称均为独立机器码。

| HTTP 状态 | 附件专用机器码 | 处理 |
| --- | --- | --- |
| 400 | `attachment_event_limit` | 同一事件已有 20 个不同附件 ID；核对稳定编号及仅上传未声明的记录 |
| 400 | `invalid_message_kind`、`invalid_attachments`、`invalid_cards`、`invalid_event_id` | 核对新字段类型、数量与事件 ID |
| 400 | `invalid_attachment_fields`、`duplicate_attachment_id`、`invalid_attachment_id`、`invalid_attachment_kind`、`invalid_attachment_mime`、`invalid_attachment_size`、`invalid_attachment_sha256`、`invalid_attachment_filename`、`attachment_kind_mime_mismatch` | 核对附件字段与精确白名单 |
| 400 | `duplicate_card_identity`、`invalid_card_fields`、`invalid_card_type`、`invalid_card_title`、`invalid_card_url`、`invalid_card_product_id`、`invalid_card_image_attachment_id`、`invalid_card_price_text`、`invalid_card_moq_text`、`invalid_card_subtitle` | 核对卡片字段、URL 与长度 |
| 400 | `attachment_encoding_not_supported`、`attachment_sha256_required`、`attachment_sha256_mismatch`、`attachment_incomplete_body` | 按完整字节和稳定 ID 修正上传；不使用分片 |
| 408 | `attachment_upload_timeout` | 上传超时，先核对结果后人工决定同键完整重传 |
| 409 | `attachment_metadata_conflict`、`attachment_event_conflict`、`attachment_blob_conflict` | 稳定 ID 或内容冲突，保留原记录并核对 |
| 411 / 413 / 415 | `attachment_content_length_required` / `attachment_body_too_large` / `attachment_content_type_not_supported` | 分别修正长度、文件大小和 MIME |
| 507 | `attachment_capacity_exceeded`、`attachment_disk_full` | 管理员审核容量或磁盘，不自动删孤儿文件 |
| 503 | `attachment_storage_unsafe`、`attachment_storage_unavailable`、`attachment_configuration_invalid`、`attachment_database_version_invalid` | 管理员核对路径、权限、配置和独立库版本 |

接入侧只应把明确成功当作已持久化。HTTP 超时或连接中断时，不能仅凭工具是否显示成功推断服务端未提交。

心跳沿用 v1 的独立错误码，未新增时钟告警字段或放宽校验：

| HTTP 状态 | 心跳机器码 | 处理 |
| --- | --- | --- |
| 400 | `heartbeat_invalid_fields`、`heartbeat_schema_invalid`、`heartbeat_instance_invalid`、`heartbeat_version_invalid`、`heartbeat_state_invalid`、`heartbeat_range_invalid`、`heartbeat_counters_invalid`、`heartbeat_error_code_invalid`、`heartbeat_cycle_flag_invalid`、`heartbeat_completed_cycle_inconsistent` | 严格按9键、6计数器和固定范围修正 |
| 400 | `heartbeat_encoding_not_supported`、`heartbeat_incomplete_body`、`heartbeat_duplicate_json_key`、`heartbeat_invalid_json` | 提交完整 UTF-8 JSON，去掉重复字段与分块/压缩编码 |
| 403 | `heartbeat_account_not_bound` | 核对当前绑定账号 |
| 411 / 413 / 415 | `heartbeat_content_length_required` / `heartbeat_body_too_large` / `heartbeat_json_required` | 唯一有效长度；最多4096字节；JSON Content-Type |
| 503 | `heartbeat_server_time_invalid`、`heartbeat_unsafe_path`、`heartbeat_unsafe_parent`、`heartbeat_unsafe_file`、`heartbeat_file_replaced`、`heartbeat_database_version_invalid` | 管理员核对心跳服务时钟、独立库版本、路径与权限 |


## 5. 控制台与存储边界

AlibabaAdapter 将附件投影成已有图片、视频、文件块，将商品、询盘、RFQ、订单和链接卡片投影为已有链接卡片块；`image_attachment_id` 通过相同媒体令牌补一个图片块，重复图片只显示一次；会话预览使用 `[图片]`、`[视频]`、`[文件]` 等中文占位。图片、视频可在已有组件中展示；其他类型一律下载，并设置 `Content-Disposition: attachment`。媒体访问复用登录、重新校验和不透明签名令牌，不把内部路径或原始下载 URL 交给前端。

附件登记使用独立 SQLite；不升级 `archive.sqlite3` 版本。控制台以同一 `imgw` 身份只读目录和登记库，接收端通过单写者事务登记。0700 权限要求服务身份一致；无需为控制台开放其他用户或扩大生产目录权限。附件登记库继续使用本机路径；NAS 附件根通过 systemd drop-in 的 `ICBU_ATTACHMENTS_ROOT` 配置。接收端须允许写入 NAS，控制台只读同一根目录；不要为此扩大其他目录权限。

媒体签名密钥路径由现有 `OPS_MEDIA_KEY_PATH` 配置，默认 `/var/lib/imgw/ops-app/media.key`。部署沿用既有文件，本轮不读取、输出或复制其值。隔离测试须显式设置临时路径，或向 `build()` / `AlibabaAdapter` 注入测试 `key_path`；不要让测试读取默认生产文件。密钥文件沿用既有部署权限，本轮没有新增该文件的权限校验，不为了联调放宽权限。

## 6. 总指挥部署与回滚（历史 v2→v2.1）

以下保留 R3-A 的历史步骤，其中第7步是 v2.1 的启动限制。R3-D 已取消对附件存储的启动依赖；当前 v2.1→v2.2 部署与回滚以第8节为准，不运行以下历史补丁命令。

以下步骤供总指挥审查后执行。本轮没有部署、重启、修改生产文件/数据库或运行真实数据演练、全页面冒烟。

1. 只读核对两份生产文件 SHA-256 与页首 v2 基线一致。审查 `deploy/icbu/icbu_inbound_receiver.py`、`icbu_attachments.py`、`receiver-v2.1.patch`、测试和本页契约。任一基线变化，停止应用补丁并重新生成差异；不要使用历史 `receiver-v2.patch`。
2. 在临时目录复制这两份基线源码，对副本执行 `patch --dry-run -p1`、实际应用，再用 `cmp` 与两份完整候选逐字节核对；反向 `patch --dry-run -R -p1` 验证可撤销。补丁头同时记录基线 SHA-256，隔离验证不能写入 `/opt/imgw` 或 `/var/lib/imgw`。
   在仓库根目录验证源码副本的命令如下，只复制两份明确的源码，不涉及配置、令牌或业务库。需先核对页首两份基线哈希；以下所有 patch 操作均针对临时目录。

   ```sh
   repo=$(pwd)
   stage=$(mktemp -d /tmp/icbu-v21-patch.XXXXXX)
   chmod 700 "$stage"
   cp /opt/imgw/scripts/icbu_inbound_receiver.py "$stage/icbu_inbound_receiver.py"
   cp /opt/imgw/scripts/icbu_attachments.py "$stage/icbu_attachments.py"
   patch --dry-run -p1 -d "$stage" < "$repo/deploy/icbu/receiver-v2.1.patch"
   patch -p1 -d "$stage" < "$repo/deploy/icbu/receiver-v2.1.patch"
   cmp "$stage/icbu_inbound_receiver.py" "$repo/deploy/icbu/icbu_inbound_receiver.py"
   cmp "$stage/icbu_attachments.py" "$repo/deploy/icbu/icbu_attachments.py"
   patch --dry-run -R -p1 -d "$stage" < "$repo/deploy/icbu/receiver-v2.1.patch"
   ```

3. 用临时 SQLite、0700 临时目录、回环动态端口、假导入/心跳/下游服务运行 `tests/icbu_v2`、已有相关套件和 `icbu_v2_selftest.py --v21`。自测永远拒绝 8791，也不会自动启动实例。不运行固定端口演练服务。
4. 总指挥按统一流程完成真实数据只读演练、浏览器检查和 `scripts/e2e_smoke.sh`。虚构测试通过不等于真实浏览器附件已采集。
5. 上线前备份两份接收端文件、相关启动配置；按 SQLite 一致性方式备份入站库与本机附件登记库，记录文件 SHA-256、数据库版本和原启动参数。备份及业务资料不得进公开仓库。
6. NAS 根由既有 `scripts/icbu_attachments_nas.sh` 产出的 systemd drop-in 提供 `ICBU_ATTACHMENTS_ROOT=/mnt/nas229/edge/imgw-archive/icbu/attachments`。审查当前 drop-in，确认 `RequiresMountsFor`、接收端 `ReadWritePaths` 与 `ExecStartPre=/usr/bin/test -f /mnt/nas229/edge/imgw-archive/.imgw-archive-id`。脚本会改 systemd 配置，**本轮不执行**；已有文件不覆盖，须由总指挥核对。
7. 启动前必须确认 NAS 已挂载且哨兵 `.imgw-archive-id` 存在，缺少时拒绝启动。NAS 附件目录须预建、属 `imgw`、0700，文件0600；本机附件登记库保持在本机。运行中若哨兵/挂载消失、根目录被替换或无法访问，上传与富消息写入返回 503 `attachment_storage_unavailable`，**绝不自动创建本地回落目录，也不回落写本机**。管理员恢复原挂载后再读回核对，不能仅因路径同名就认定原内容还在。
8. 审查后由总指挥同时安装两份完整候选，保留其他依赖与鉴权机制；本轮不需要更改 Web 路由、导航或演练挂载。附件库只新增内部表，原有 `user_version=1` 保持；入站库原 `user_version=2` 保持，主动初始化新增观测表。不改变档案库版本。重启后核对原有来源与认证、POST/PUT/GET、OPTIONS、心跳、v1 兼容和控制台富消息投影。
9. 验收至少包括旧冒号 ID 原消息→富消息增补→别名 PUT→GET 关联，检查原 `inbound_id`、状态、指纹和 receipt 不变；文件/消息乱序；同键重放；NAS 失联返回503且本机无新目录。全部通过后再由总指挥开放采集端使用新契约。

回滚由总指挥先暂停新的 v2.1 富消息写入，保留 Node 已排队事件，再恢复备份的两份 v2 接收端文件和原启动配置并重启。保留 NAS 附件、本机登记库、增补账本与入站观测表，不删除或恢复旧库覆盖新增消息。新增内部表对旧接收端无须读取；v2 无旧 ID 附件兼容与读回接口，Windows 应暂停相关新流程，不能换 ID 补发。若回滚 NAS drop-in，须先明确附件仍在 NAS、控制台如何读取，不能通过恢复本机默认根制造“空库正常”的假象。确需恢复数据库时先备份增量，由总指挥逐项核对防止丢消息。

候选参数与生产配置：

| 参数或环境变量 | 默认值 / 生产配置 | 说明 |
| --- | --- | --- |
| `--attachments-db` / `ICBU_ATTACHMENTS_DB` | 默认 `/var/lib/imgw/icbu/attachments.sqlite3` | 附件独立登记库仍在本机，不放 NAS |
| `--attachments-root` / `ICBU_ATTACHMENTS_ROOT` | 代码默认 `/var/lib/imgw/icbu/attachments`；生产 drop-in 配置 `/mnt/nas229/edge/imgw-archive/icbu/attachments` | 生产必须保留环境配置和哨兵检查，不能在挂载失败时使用默认值回落 |
| `--attachments-max-total-bytes` / `ICBU_ATTACHMENTS_MAX_BYTES` | 10737418240 | 10 GiB 总容量 |
| `--orphan-hours` / `ICBU_ATTACHMENTS_ORPHAN_HOURS` | 24 | 超时只标记，不删除 |
| `OPS_MEDIA_KEY_PATH` | 默认 `/var/lib/imgw/ops-app/media.key` | 沿用既有媒体签名文件；测试显式改成临时路径，不读取生产值 |

部署后的真实业务验收仍包括客户与我们两个方向、各种媒体和卡片、补元数据、乱序、同键完整重传、登录控制台查看和退出后拒绝访问；真实资料不得进入公开仓库、测试或日志。

## 7. 本轮交付与隔离验证

本轮修改 `deploy/icbu/icbu_inbound_receiver.py`、`deploy/icbu/icbu_attachments.py`、`scripts/icbu_v2_selftest.py` 和本页，新增 `deploy/icbu/receiver-v2.1.patch`、`tests/icbu_v2/test_attachments_v21.py`、`test_receiver_v21.py`、`test_selftest_v21.py` 与 `test_patch_v21.py`，覆盖旧 ID、读回、别名负例、时钟观测、自测扩展与补丁逐字节验证。完整文件对应候选，历史 v2 补丁保留。精确测试文件、补丁 SHA-256、dry-run/应用后逐字节验证证据和本轮测试结果以本次交付报告为准；上一轮的229条通过统计不作为本轮结果。

隔离测试命令：优先按任务书使用 `sudo -n -u imgw`；快照没有 sudo 权限时，以当前身份执行同样环境与 Python 命令，不修改生产权限。媒体签名必须指向临时文件。

```sh
sudo -n -u imgw env PYTHONDONTWRITEBYTECODE=1 \
  OPS_MEDIA_KEY_PATH=/tmp/r3a-test-media.key \
  PYTHONPATH='src:tests/icbu_v2' /opt/imgw/venv/bin/python \
  -m pytest tests/icbu_v2 -q -p no:cacheprovider
```

本次在执行器提供的独立 workspace 快照完成，未写 `/srv/imgw-dev/ops` 原副本。`.git` 没有可用元数据，因此以 SHA-256 固定基线，不报告提交号，也未 commit/push。`sudo` 被容器限制拒绝，按任务书以当前身份运行：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

最初直接运行完整套件，在收集旧投影测试时遇到快照依赖缺失，原样错误摘要：

```text
ERROR collecting tests/icbu_v2/test_projection.py
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/icbu_v2/test_projection.py
1 error in 0.55s
```

没有为此修改其他任务模块。仅将 `/opt/imgw/wechat/console/media/` 的四份 `.py` 源码复制到 0700 临时覆盖层 `/tmp/icbu-v21-review-23zj1il0/console-extra/media`，不复制配置、签名文件或业务数据。临时 `sitecustomize.py` 只执行以下语句：

```python
import console
from pathlib import Path
console.__path__.append(str(Path(__file__).parent / 'console-extra'))
```

四份只读源码的实际 SHA-256：

| 源文件 | SHA-256 |
| --- | --- |
| `__init__.py` | `ced5f3a0f932bbaaa6140ae8ceb081161800f896b998260729deb97fef5880f8` |
| `alibaba.py` | `282823a19e141425fa2f352eccc9dff2ab019721f67d06a48cc13e2811f76c93` |
| `files.py` | `7c6a84e3819dc6a50e3e11c8e9f92ba6e5f941c656ba6eda9b6e808b5a21917b` |
| `tokens.py` | `25440e4ce6ad69f2e952aaa2c1d55e2d0070807f9731f898faba828ee58ed184` |

最终完整套件的实际命令与结果：

```sh
env PYTHONDONTWRITEBYTECODE=1 \
  OPS_MEDIA_KEY_PATH=/tmp/icbu-v21-review-23zj1il0/media-test.key \
  PYTHONPATH=/tmp/icbu-v21-review-23zj1il0:src:tests/icbu_v2 \
  /opt/imgw/venv/bin/python -m pytest tests/icbu_v2 -q -p no:cacheprovider
# 296 passed in 12.09s
```

已有相关套件同样使用此临时源码覆盖层、`PYTHONDONTWRITEBYTECODE=1`、各自临时 `OPS_MEDIA_KEY_PATH`、相同 Python 与 `-q -p no:cacheprovider`；按表中路径替换 PYTHONPATH 的最后一段与 pytest 目标：

| 范围 | PYTHONPATH 最后一段 | pytest 目标 | 最终结果 |
| --- | --- | --- | --- |
| 渠道 | `tests/channels` | `tests/channels/test_adapters.py tests/channels/test_api.py` | 48 passed，14 subtests passed |
| 收件箱 | `tests/inbox_api` | `tests/inbox_api` | 37 passed，99 subtests passed |
| 阿里示例 | `examples/icbu` | `examples/icbu/test_receiver.py examples/icbu/test_snapshot.py` | 12 passed，9 subtests passed |
| 阿里审核 | `tests/icbu` | `tests/icbu` | 17 passed |
| 管道健康 | `tests/pipeline` | `tests/pipeline` | 75 passed |

最终没有测试失败。原有测试文件与断言均未修改。`test_receiver_v21.py::test_complete_cli_selftest_uses_temporary_candidate_and_dynamic_loopback_port` 实际以子进程运行 `scripts/icbu_v2_selftest.py --url http://127.0.0.1:<动态端口> --v21`，测试凭据仅通过标准输入传入，库、附件和服务都由临时夹具创建并关闭；最终只产生3条虚构入站消息。另用相同隔离条件直接运行旧场景与 v2.1 场景共23个 HTTP 请求，全部通过。

补丁验证直接复制页首两份只读生产基线到0700临时目录，`patch --batch --fuzz=0 -p1 --dry-run`、实际应用、两份候选逐字节比较与反向 dry-run 全部返回0，无偏移和模糊匹配；`test_patch_v21.py` 还在不依赖生产目录的临时副本中反向恢复基线并校验哈希，再正向应用比较字节。最终产物哈希：

| 产物 | SHA-256 |
| --- | --- |
| `receiver-v2.1.patch` | `f57dec31353da17a39947d83c98f1055fb83f6a5a6096ee5750cda8c26cbc04b` |
| 候选 `icbu_inbound_receiver.py` | `49481feb23f44d381418ac4de7eed3d05f429e3b1e2c155b02efacd76b287f5d` |
| 候选 `icbu_attachments.py` | `f81a670a3f67e34004c64d8d0ac12eba7c85572857c4ba3a040970ed476900ae` |

暂存实例必须由独立测试夹具/运行器创建，临时目录0700、临时 SQLite、假依赖，监听 `127.0.0.1` 的动态端口并明确拒绝8791；运行结束关闭实例并清理临时目录。自测由调用方显式提供暂存地址与仅测试用临时令牌，命令增加 `--v21`；工具不读取生产配置、不跟随重定向、不自动重试、不自行启动服务。`--dry-run` 仅检查地址。

需要总指挥合并的共享文件改动：需求账本 H2/H12 可补记本轮旧 ID、读回和时钟策略候选及虚构验收状态；`docs/data-pipelines.md` 第5节的“草案”与当前已部署 v2 状态应由总指挥统一刷新。本轮不改 `web/src/data/requirements.json`、README、master-plan 或其他任务模块；Web 路由、导航、`scripts/rehearsal_server.py` 均零改动。管道健康页读取新增偏差观测由其负责人后续接入，本轮提供内部数据，不改其模块。

安全核对覆盖来源与鉴权、保留前缀、严格别名解码、路径穿越/符号链接、文件名与 MIME 白名单、参数化 SQL、错误脱敏、单写者事务与幂等、关联恢复、NAS 挂载失联不回落。没有真实渠道发送、生产部署、网络改动或自动重发；所有通过项只在虚构数据和临时存储上验证，不能据此声称真实数据已可用。无法完成的基线核对或已有相关套件失败必须在交付报告原样说明。

## 8. 存储不可用时的行为（R3-D / v2.2）

本轮只修改仓库快照，未部署。`deploy/icbu/icbu_inbound_receiver.py` 与 `icbu_attachments.py` 保留为 v2.1 基线，历史补丁和旧测试断言原样保留；新完整候选在 `deploy/icbu/v2.2/`，不能误装父目录的旧文件。生产两份源码仅做 SHA-256 只读核对，本次与仓库基线一致：

| v2.1 基线文件 | SHA-256 |
| --- | --- |
| `icbu_inbound_receiver.py` | `49481feb23f44d381418ac4de7eed3d05f429e3b1e2c155b02efacd76b287f5d` |
| `icbu_attachments.py` | `f81a670a3f67e34004c64d8d0ac12eba7c85572857c4ba3a040970ed476900ae` |

### 启动、运行与恢复

NAS 目录不存在、哨兵缺失、附件根尚未预建或暂时无法访问，均不再阻止接收端启动。附件独立库在首次可用操作时初始化；NAS 不可用时不创建 NAS 目录、附件锁或回落目录。配置无效、数据库版本不支持、不安全权限、符号链接等安全错误仍明确拒绝，不能当作普通离线绕过。NAS 模式仍由附件根位于 `/mnt/nas229/edge/imgw-archive` 下自动判定，哨兵与挂载身份检查保持，每次操作重新检查。

文字 POST、消息状态 GET、心跳、导入、探针下载和既有运营接口沿用原来源限制、鉴权、字段、状态码和语义。纯文字首次仍202、同事件重放仍200；不会为文字请求访问附件存储。没有增加任何对外发送能力。

| 操作 | 存储不可用时 | 原存储恢复后 |
| --- | --- | --- |
| `PUT /api/inbound/alibaba/attachments/{event}/{attachment_id}` | 503 `attachment_storage_unavailable`，不确认上传成功 | 下一次操作重新检查并接受合法上传，无需重启 |
| 相同附件路径 GET | 503 `attachment_storage_unavailable`，不把离线报告成“附件不存在” | 下一次操作恢复原登记与内容校验 |
| 带非空附件或卡片列表的合法 POST | 主消息库提交成功后202，明确标记附件关联待补 | 核对原事件后重放相同富消息，补齐原消息关联；重复不新增消息 |
| `/healthz` | 仍200，新增 `attachments_storage: "unavailable"` | 下一次健康查询返回 `attachments_storage: "ok"` |

`/healthz` 原 `ok/service/protocol_schema_version/database_schema_version/attachments_cleanup_ok` 的名字与含义不变。`attachments_storage` 是当前操作检查结果；`attachments_cleanup_ok` 仍表示最近一次维护结果，恢复后可能要等下一次维护才变为 true，二者不互相代替。启动日志固定输出“附件存储：可用（ok）”或“附件存储：暂不可用（unavailable），文字入库继续；请恢复附件存储后核对并补全附件”，不输出凭据、正文、请求头、数据库内容或异常堆栈。

固定身份仍有效：运行中把挂载、哨兵或附件根换成另一个同名对象，不能视为恢复；必须恢复原对象。启动时尚未见过任何有效存储对象时，首次可用操作固定其身份。运行中离线不会创建本机替代目录，也不会修改或删除原附件。

### 富消息部分成功与补齐

选择最小方案：**返回明确202部分成功，不增加新的持久库或表**。完整且经过校验的正文、附件/卡片元数据仍在原本机入站库 `inbound.payload` 内以事务持久化；旧指纹、去重键、状态、草稿准入条件与首次正文保持原语义。仅跳过依赖附件存储的预检、关联与附件增补账本写入。成功响应保留原字段，增加：

```json
{
  "ok": true,
  "persisted": true,
  "attachments_pending": true,
  "attachments_storage": "unavailable",
  "message": "消息已保存，附件关联待补。请恢复附件存储后，用原事件编号核对并补全资料"
}
```

实际响应仍包含原 `inbound_id/event_id/status/duplicate/dedup_mode`。即使事件已存在，只要附件关联待补，HTTP 也为202，`duplicate=true`；不能把这条 ACK 理解为附件已登记或文件已到达。纯附件或卡片消息允许原有空正文，元数据不会丢弃。若主库提交失败，不得返回上述成功语义。若存储在主库提交与附件提交之间失联，同事件去重后仍可给出部分成功，不新建第二条消息。

接入方须保存稳定事件编号、消息编号和待补富元数据。存储恢复后，先查原消息，再重放同一富消息 POST；成功补齐返回原200重复响应，不再携带 `attachments_pending`。原有增补账本在成功关联事务中幂等登记；所有已提交事件别名一起修复。该方案没有后台自动重放队列，GET消息状态接口也不增加待补字段；调用方不能在202后丢掉待补任务。仅恢复NAS不会自动替此前待补消息写附件登记。PUT先于补齐POST时可先形成孤儿登记，随后仍须同事件POST完成关联，并按第4.2节读回核对。

离线期间仍执行所有请求校验和主库已知的正文、方向、元数据冲突检查；无法检查的既有上传内容冲突，在恢复后的预检报告409，保留已保存消息及原附件，由管理员核对，不能自动覆盖。存储容量不足、满盘、不安全路径等其他错误保留原错误语义，不一律吞成202。超时或断开仍先核对原稳定ID，工具与接收端均不自动向真实渠道重发。

### 临时实例的 `--v22` 自测

自测沿用旧场景，可同时使用 `--v21 --v22`。`--v22` 只允许同机回环IP和非8791端口；即使有 `--allow-lan` 也不能放行局域网。接收端与自测进程须显式共用环境变量 `ICBU_V22_TEST_DIR`，指向 `/tmp` 下当前身份拥有、权限0700的独立临时目录。接收端启用该变量时必须 `--bind 127.0.0.1` 且端口不是8791。生产不得设置这个故障演练变量。

目录须由测试运行器创建；双方拒绝符号链接和路径穿越。自测以独占创建的0600 `attachments-unavailable` 标记模拟失联，不覆盖已有标记，仅清理自己创建且身份不变的标记；无故障控制HTTP接口。标记存在时仅附件操作返回503，删除后下一次操作恢复。已有NAS检查仍执行，测试钩子不能绕过NAS身份检查或让离线目录落盘。

```sh
# 两个进程使用同一个已创建的0700临时目录；实例端口由运行器动态分配。
# 不使用生产配置、库、凭据、8791或固定端口演练服务。
env ICBU_V22_TEST_DIR="$test_fault_dir" \
  /opt/imgw/venv/bin/python scripts/icbu_v2_selftest.py \
  --url "http://127.0.0.1:$test_port" --v21 --v22 < "$test_only_token_file"
```

`test_receiver_v22.py::test_v22_cli_on_temporary_loopback_instance` 自动创建这些临时资源，以假导入/心跳/下游依赖启动真实候选HTTP Server，动态回环端口运行完整命令后关闭清理。覆盖离线时文字与富消息持久化、重复POST、附件PUT/GET 503、健康切换、恢复关联/上传、再次失联再恢复。启动缺挂载、缺哨兵、缺附件根、环境标记在启动前已存在和 `main()` 启动路径另有测试。NAS模拟用临时目录重命名与原对象恢复，不修改真实挂载或网络。

### 补丁验证、部署与回滚

`deploy/icbu/receiver-v2.2.patch` 只接受本节两份 v2.1 基线，不能直接用于历史v2。以下命令全部在临时源码副本操作，不触碰生产：

```sh
repo=$(pwd)
stage=$(mktemp -d /tmp/icbu-v22-patch.XXXXXX)
chmod 700 "$stage"
cp "$repo/deploy/icbu/icbu_inbound_receiver.py" "$stage/"
cp "$repo/deploy/icbu/icbu_attachments.py" "$stage/"
sha256sum "$stage/icbu_inbound_receiver.py" "$stage/icbu_attachments.py"
patch --batch --fuzz=0 --dry-run -p1 -d "$stage" < "$repo/deploy/icbu/receiver-v2.2.patch"
patch --batch --fuzz=0 -p1 -d "$stage" < "$repo/deploy/icbu/receiver-v2.2.patch"
cmp "$stage/icbu_inbound_receiver.py" "$repo/deploy/icbu/v2.2/icbu_inbound_receiver.py"
cmp "$stage/icbu_attachments.py" "$repo/deploy/icbu/v2.2/icbu_attachments.py"
patch --batch --fuzz=0 --dry-run -R -p1 -d "$stage" < "$repo/deploy/icbu/receiver-v2.2.patch"
```

自动测试还反向实际应用并核对两份基线哈希，正反应用无offset、无模糊匹配。部署仅由总指挥完成：

1. 再次核对生产两份源码哈希，任一变化就停下重新生成补丁；审阅候选、本文、测试及临时副本应用结果。
2. 完成统一真实只读演练、浏览器与全页面冒烟；本任务没有运行 `scripts/e2e_smoke.sh`，虚构验证不代替真实验收。
3. 备份两份源码、服务配置和本机入站/附件库，保留所有新增消息。核对现有systemd配置：NAS应为软依赖，不能有NAS哨兵 `ExecStartPre`、硬性 `RequiresMountsFor` 或不带 `-` 的必需 `ReadWritePaths` 继续阻断进程。当前仓库 `scripts/icbu_attachments_nas.sh` 已使用软依赖与 `ReadWritePaths=-`，本轮没有运行、改动该脚本或任何生产单元；若现场仍有旧硬约束，需要总指挥审查后移除相关约束，不能靠更换Python消除systemd前置失败。
4. 保留原NAS根配置和本机库路径，同时安装 `deploy/icbu/v2.2/` 内两份完整候选；不要安装父目录基线，不要设置 `ICBU_V22_TEST_DIR`。无档案库升级、无新增表、无Web路由或导航变更。接收端重启后核对日志、健康新增字段、原认证/心跳/导入/文字状态，以及附件失联与原存储恢复；重新核对待补事件。
5. 回滚时保留采集端待补队列与全部数据库/附件增量，恢复备份的两份v2.1源码和原配置并重启；不回退数据库，不删除元数据、账本或NAS附件。v2.1在NAS离线时仍无法启动，因此回滚前须恢复原NAS或由总指挥安排其他保留队列方案，不能回落到本机目录。补丁在副本通过反向验证，不直接在生产执行patch。

需要总指挥合并的共享改动：需求账本H2/WG-01记录“v2.2虚构断盘验收通过、未部署，采集端仍需保留202待补任务”；管道健康页负责人可读取新增 `attachments_storage`，本轮不修改其模块。README、master-plan、requirements、Web路由/导航及 `scripts/rehearsal_server.py` 均零修改。没有生产文件、生产库、网络或真实渠道写入。

### R3-D 实际验证记录与交付清单

当前执行身份为 `imgw`；sudo仍被容器限制拒绝，按任务书直接运行。快照缺少 `src/console/media`，首次未补依赖的完整命令原样失败摘要：

```text
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/icbu_v2/test_projection.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.65s
```

为跑已有投影/相关回归，只将 `/opt/imgw/wechat/console/media/` 四份 `.py` 源码复制到0700临时覆盖层 `/tmp/r3d-review-g4ryqarb/console-extra/media`，没有复制配置、密钥或业务数据。四份源码哈希与第7节所列相同；临时 `sitecustomize.py` 仅扩展 `console.__path__`，不替换被测业务函数。不能称未补依赖的干净快照已全绿。

复用旧断言验证新候选时，曾发现符号链接错误被宽泛归入普通离线；另有新测试受 `main()` 的进程umask影响。中间失败原样摘要：

```text
FAILED tests/icbu_v2/test_candidate_v22_regression.py::test_existing_assertion_on_v22_candidate[test_attachments-test_symlink_in_root_or_parent_is_rejected_without_touching_target]
FAILED tests/icbu_v2/test_candidate_v22_regression.py::test_existing_assertion_on_v22_candidate[test_attachments-test_symlink_db_or_blob_and_insecure_root_are_rejected]
FAILED tests/icbu_v2/test_selftest_v22.py::test_fault_directory_rejects_symlinks_and_weak_permissions[permissions]
3 failed, 32 passed in 3.19s
```

已将 `ELOOP/ENOTDIR` 明确映射为 `attachment_storage_unsafe`，不吞掉这类初始化错误；新测试隔离umask并显式设置权限。未修改任何旧测试断言。最终命令与结果：

```sh
env PYTHONDONTWRITEBYTECODE=1 \
  OPS_MEDIA_KEY_PATH=/tmp/r3d-review-g4ryqarb/media-test.key \
  PYTHONPATH=/tmp/r3d-review-g4ryqarb:src:tests/icbu_v2 \
  /opt/imgw/venv/bin/python -m pytest tests/icbu_v2 -q -p no:cacheprovider
# 347 passed in 13.86s
```

其中296项为原v2.1基线回归，新增51项检查v2.2候选/工具，包含直接在候选上复用的25项旧兼容与安全断言；不是把旧的“初始化必须失败”断言改成新语义。候选新增测试另涵盖主库失败不得返回部分成功、并发离线重放只一条消息、存储恢复后所有事件别名修复。

已有相关套件使用同一临时源码覆盖层，命令模式如下；`SUITE` 与目标按表替换，媒体测试文件同样位于临时覆盖层：

```sh
env PYTHONDONTWRITEBYTECODE=1 \
  OPS_MEDIA_KEY_PATH=/tmp/r3d-review-g4ryqarb/related-media.key \
  PYTHONPATH="/tmp/r3d-review-g4ryqarb:src:tests/$SUITE" \
  /opt/imgw/venv/bin/python -m pytest $TARGET -q -p no:cacheprovider
```

| SUITE | TARGET | 最终结果 |
| --- | --- | --- |
| `pipeline` | `tests/pipeline` | 77 passed，0 failed |
| `icbu` | `tests/icbu` | 17 passed，0 failed |
| `channels` | `tests/channels/test_adapters.py tests/channels/test_api.py` | 48 passed，0 failed，14 subtests passed |
| `inbox_api` | `tests/inbox_api` | 37 passed，0 failed，99 subtests passed |
| 合计（含icbu_v2） | 五组独立进程 | **526 passed，0 failed，113 subtests passed** |

对两份只读生产源码复制到临时目录后实际执行patch验证：基线哈希符合本节表格；`patch --batch --fuzz=0 -p1 --dry-run`、实际应用、两份与候选逐字节相同、反向dry-run均通过，无offset。临时源码副本已清理；测试还验证实际反向应用后的基线哈希。

| R3-D产物 | SHA-256 |
| --- | --- |
| `deploy/icbu/receiver-v2.2.patch` | `dadff61b9a7c4d9cbea56aa6da3e309518b8792eee6c5fdc0c7c2ae8cd1da9d2` |
| `deploy/icbu/v2.2/icbu_inbound_receiver.py` | `0e5b0f4b787922e981e812309b7604b657adbde0a2545b1d7782a53157c244e7` |
| `deploy/icbu/v2.2/icbu_attachments.py` | `3017e47b04d2c2989fafadeb0b1aad9de299f8c23113282935bd25a16c4f4393` |

新增文件：上述补丁、两份完整候选、`tests/icbu_v2/test_receiver_v22.py`、`test_candidate_v22_regression.py`、`test_selftest_v22.py`。修改文件仅 `scripts/icbu_v2_selftest.py` 和本文。父目录两份基线、历史补丁与旧测试未改。当前workspace没有可用Git元数据，使用文件哈希固定基线，未commit/push。

安全自查：原鉴权与Origin限制保持，离线状态没有绕过输入校验；路径与文件名校验、O_NOFOLLOW、固定挂载身份、线程/文件锁、事务和幂等保留；SQL值参数化，附件不解析执行、URL不抓取；日志和错误不泄露正文、凭据或库内容。故障模拟只操作显式临时目录，拒绝符号链接、穿越和已有标记覆盖；原挂载失联无本机回落写入。没有新增真实发送、自动重试或重复发送风险。

本轮已完成候选、补丁与虚构暂存验收。未做：生产部署、真实NAS断网/挂载切换、真实客户附件与浏览器采集验收、真实数据只读演练和全页面冒烟；没有修改Windows采集端来保存202待补任务，没有替管道健康页接入新字段。上述事项由总指挥与对应负责人继续，不能把526项通过解释为真实数据可用。

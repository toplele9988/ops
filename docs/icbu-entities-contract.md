# 阿里业务实体快照契约 v1

交付状态：221 接收端候选，尚未安装到生产。完整候选在 `deploy/icbu/entities/`；补丁 `deploy/icbu/receiver-entities.patch` 对准本轮只读核对的生产源码 SHA，不能应用历史 v2.1/v2.2 补丁代替。实际测试、部署前提和回滚见根目录 `INTEGRATION.md`。

本入口接收 Win10 已登录页面的只读采集结果，不登录阿里、不下载 URL、不发布商品、不调用消息发送或执行队列。阿里 Cookie、登录令牌留在 Win10；不能放入 `fields`、`related`、附件引用或错误日志。生产接收凭据只由已有安全配置交给本地网关，不写仓库、命令行或浏览器页面脚本。

## HTTP 与鉴权

| 方法、路径 | 行为 |
| --- | --- |
| POST `/api/inbound/alibaba/entities` | 提交一个原子批次，1–100 个实体，整个 UTF-8 请求不超过 65536 字节 |
| GET `/api/inbound/alibaba/entities/stats` | 全部 12 类计数、观察时间范围、版本数、最近 20 个已提交批次 |
| GET `/api/inbound/alibaba/entities/{entity_type}/{entity_id}` | 当前快照；ID 必须作为一个路径段编码，JS 使用 `encodeURIComponent(entity_id)` |
| OPTIONS 同上 | 沿用允许来源的跨域预检；批量入口允许 POST，读回入口允许 GET |

所有实体 POST/GET 都使用现有 `Authorization: Bearer <接收凭据>`，与原聊天入口共用来源限制、Origin 检查和常量时间令牌比较。只读核对的生产基线**没有独立请求 HMAC 签名**，不新增或假设签名字段。凭据继续绑定既有单账号，不接受客户端指定其他账号。未来多账号需要另行扩展身份模型，不能把账号塞进 `entity_id` 冒充已支持。

服务只接受原允许的局域网及回环来源；有 `Origin` 时仍只允许既有两个聊天站点 `https://message.alibaba.com` 和 `https://onetalk.alibaba.com`，不扩大到后台其他域名。Win10 其他后台页面应由已授权的本地采集器/网关投递，不能伪造或移除浏览器 Origin 绕过限制。

唯一有效 `Content-Length` 必填；唯一 `Content-Type` 为 `application/json` 或 `application/json; charset=utf-8`。不接受分块或压缩编码、重复 JSON 键、NaN/Infinity、非有限数字或非法 Unicode。可选单个 `X-Idempotency-Key` 必须等于正文 `batch_id`；它不是聊天事件编号。响应为 JSON，带 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。读回含业务数据，只能在授权内部使用。

沿用现有 24 并发连接上限、8 秒头部/空闲超时和鉴权后 70 秒请求总期限。超过连接上限会关闭连接，**没有新增每分钟配额，也不承诺返回 429**。采集器宜串行低频投递；不要因连接关闭增加并发。自测使用临时库与动态回环端口，始终拒绝 8791，包括 GET。

## JSON 模式

下列 JSON Schema 描述结构；服务另外检查实体身份在同批次内不重复、真实日历日期、UTC 时区、非有限数字、总 UTF-8 字节数，以及正文/请求头批次一致性。未知顶层/实体/附件引用键拒绝；`fields` 和 `related` 的内部业务键不做映射或白名单筛选。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "ICBU entity batch v1",
  "type": "object",
  "additionalProperties": false,
  "required": ["batch_id", "entities"],
  "properties": {
    "batch_id": {"type": "string", "pattern": "^[A-Za-z0-9._:-]{1,128}$"},
    "entities": {
      "type": "array",
      "minItems": 1,
      "maxItems": 100,
      "items": {"$ref": "#/$defs/entity"}
    }
  },
  "$defs": {
    "id": {"type": "string", "pattern": "^[A-Za-z0-9._:/@+=-]{1,256}$"},
    "entity": {
      "type": "object",
      "additionalProperties": false,
      "required": ["entity_type", "entity_id", "observed_at", "source_page", "collector_version", "fields"],
      "properties": {
        "entity_type": {
          "enum": ["order", "order_logistics", "inquiry", "rfq", "opportunity", "customer", "customer_followup", "product_listing", "ad_campaign_report", "ad_keyword_report", "shop_report", "other"]
        },
        "entity_id": {"$ref": "#/$defs/id"},
        "observed_at": {
          "type": "string",
          "format": "date-time",
          "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?(Z|\\+00:00)$"
        },
        "source_page": {"type": "string", "minLength": 1, "maxLength": 2048, "pattern": "^/(?!/)[^\\\\\\s\\u0000-\\u001f\\u007f]*$"},
        "collector_version": {"type": "string", "pattern": "^[A-Za-z0-9._+\u002d]{1,64}$"},
        "fields": {"type": "object", "additionalProperties": true},
        "related": {"type": "object", "additionalProperties": true},
        "attachments": {
          "type": "array",
          "maxItems": 20,
          "uniqueItems": true,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["event_id", "attachment_id"],
            "properties": {
              "event_id": {"$ref": "#/$defs/id"},
              "attachment_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"}
            }
          }
        }
      }
    }
  }
}
```

`entity_id` 必须是平台稳定号，区分大小写，不能使用列表行号、页码、买家显示名或每次生成的 UUID。同一 ID 可在不同 `entity_type` 下分别存在。缺少平台独立 ID 的物流、跟进、日/关键词报表，应先确定可复现组合键，例如 `订单号:物流号`、`客户号:跟进记录号`、`计划号:日期`、`计划号:关键词稳定号:日期`；不能合成随机号来掩盖缺失身份。组合键须满足 ID 格式，`other` 也必须有稳定号与来源。

`observed_at` 表示页面实际被观察的 UTC 时间，接受 `Z` 或 `+00:00`，小数秒 0–6 位，存储统一为带 6 位微秒的 `+00:00`。不接受本地无时区时间或其他时区偏移。页面里的订单日期、报表日期、付款时间等保留在 `fields`，不能当成 `observed_at`。

`source_page` 由页面 pathname 得到，例如 `/orders/detail`；提交前去掉查询参数与片段，不提交完整 URL。接收端也会丢弃误带的 `?query` 和 `#fragment`，并拒绝完整 URL、`//host`、反斜线和空白/控制字符；路径仅为来源，不用于访问文件或联网。

`fields` 必须是**完整快照**，不是局部更新：未知业务字段、嵌套对象、数组、空值与字符串按 JSON 值保存，不改名、不推算、不换币种、不验证价格事实。原请求的空白、对象键顺序不保留；数字采用 Python JSON 的表示，超高精度金额/编号建议采集为来源字符串，避免任何 JSON 浮点转换。省略一个既有业务键表示新快照中没有该键；采集器须区分“页面未加载完成”和真实空值，未完整加载时不要误报完整快照。

`related` 可用 `conversation_id`、`buyer_id`、`order_id` 等采集器自己的键保存关联标识；本轮仅保存 JSON，不自动合并客户、订单或聊天。`attachments` 只保存既有聊天附件的原始 `event_id` 与 `attachment_id` 引用，不使用路径传输别名；不接受 URL、Cookie、本地文件路径或文件字节。不查附件是否存在、不创建聊天附件关联、不代表已上传。没有既有附件引用时省略；业务实体专属文件上传尚未实现，不能借用聊天上传制造虚构消息。

## 全部为虚构的例子

本例的字段名只是业务原文示意，不是阿里页面或 API 已确认的字段。

```json
{
  "batch_id": "fictional-win1-orders-20261102-0001",
  "entities": [
    {
      "entity_type": "order",
      "entity_id": "fictional-order-1001",
      "observed_at": "2026-11-02T03:00:00Z",
      "source_page": "/orders/detail",
      "collector_version": "win-1.0.0",
      "fields": {
        "page_status_text": "虚构：待发货",
        "page_total_text": "USD 123.45（虚构）",
        "page_order_date": "2026-10-01",
        "items": [{"page_title": "虚构灯具", "page_quantity_text": "3 pcs"}]
      },
      "related": {"conversation_id": "tm:fictional-buyer-1", "buyer_id": "fictional-buyer-1"},
      "attachments": [{"event_id": "fictional-chat-event-1", "attachment_id": "fictional-document-1"}]
    },
    {
      "entity_type": "customer",
      "entity_id": "fictional-buyer-1",
      "observed_at": "2026-11-02T03:01:00Z",
      "source_page": "/customers/detail",
      "collector_version": "win-1.0.0",
      "fields": {"page_name": "虚构客户", "groups": ["虚构工程组"], "tags": ["虚构标签"]}
    }
  ]
}
```

第一次成功为 HTTP **201**，表示整个批次已事务提交。返回 `ok=true,persisted=true,batch_id,duplicate=false,entity_count,versions_added,current_updated,results`。每个 `results` 包含 `entity_type,entity_id,version_id,version_created,current_updated`，按请求顺序返回。一个新批次即使全部内容已知，仍返回 201，但 `versions_added=0`。同一批次重放返回 **200**、`duplicate=true`，其余为首次提交的 ACK，结果中的计数不是这次重新写入数量。

## 保存、版本、幂等和读回

独立 `/var/lib/imgw/icbu/entities.sqlite3`，目录 0700、文件 0600，WAL、FULL 同步、外键、单批次 `BEGIN IMMEDIATE` 事务。拒绝符号链接、不安全权限、文件/目录替换及其他应用数据库；不更改现有聊天库和附件库。初始实体库为 `application_id=1229144645,user_version=1`。

| 表 | 口径 |
| --- | --- |
| `entity_current` | 每个 `(entity_type,entity_id)` 一行，选择最新 `observed_at` 的完整快照，并记录所有已接收观察的最早/最新时间 |
| `entity_versions` | 每个身份下每种不同 `fields` 规范 JSON 的 SHA-256 一行，保存首次收到该内容时的完整快照与批次来源；内容版本不可变 |
| `entity_batches` | 一个 `batch_id` 一行，保存请求摘要、首次收到时间、数量/范围/结果 ACK；不保存令牌或重复请求正文 |

只有 `fields` 的内容不同才新增内容版本，时间、来源页、采集器版本、关联与附件引用变化都不增加版本。对象键顺序不影响摘要；数组顺序、字符串空白、空值与缺少键、数字 `1`/`1.0` 不做语义合并。内容 A→B→A 再次观察时，当前快照可回到 A，复用已有 A 内容版本，历史表**不是每次状态跳转流水**。版本行的 `observed_at` 是首次收到该内容的观察时间；当前行是最新观察，二者可能不同。元数据单独变更只更新当前快照，不形成历史元数据审计。

旧观察可以补入此前未见的内容版本，并扩展最早时间，但不能覆盖更新的当前快照。最新时间完全相同而 `fields` 不同时返回 409 `entity_observation_conflict`，整批不提交；不要通过伪造时间绕过冲突。完全相同时间与内容保留首次当前元数据。

批次幂等使用规范化后的完整批次摘要（包括实体顺序、观察时间及元数据）；同一个 `batch_id` 对应不同载荷返回 409。重试必须保留原 ID、原观察时间、原内容、原顺序；新采集使用新的稳定批次号。**所有实体上报都是幂等的，可以安全重试**；即使上次已提交但 ACK 丢失，重放不会新增该批次/内容版本。同内容放到新批次不会新增版本，但会多一条批次记录，所以重试不要换批次号。

整批校验和写入原子完成：任一项无效或冲突，整批回滚；没有部分成功 ACK。客户端收到 `persisted=false` 或网络超时，只能认为未确认；断开发生在提交之后仍可能已保存，用同一批次安全重试即可确认。

统计读回返回 `ok,types,total_entities,total_versions,total_batches,recent_batches`：`types` 总是包含全部 12 类，各有 `entity_type,count,versions,earliest_observed_at,latest_observed_at`，从未收到的类型数量为 0、时间为 null。时间范围是**采集观察时间范围，不是订单业务日期覆盖范围**。最近批次按服务端首次收到时间倒序，最多 20 个；每个只有 `batch_id,received_at,entity_count,versions_added,current_updated,earliest_observed_at,latest_observed_at`，不带业务正文。

单条读回返回 `ok,entity,version_id,fields_sha256,first_observed_at,last_observed_at,batch_id`，`entity` 是完整最新快照。查询缺失为 404，存储故障为 503，不能把不可用显示为 0。读回连接为 SQLite `mode=ro`、`query_only`，不建表、不增批次、不更新观察时间。本轮未提供历史列表、实体枚举分页或 Web 总览接线。

## 错误与重试

错误格式沿用 `{"ok":false,"persisted":false,"error":"机器码","message":"固定中文说明"}`，不回显输入、数据库路径、凭据或堆栈。程序按 HTTP 状态和 `error` 判断。

| HTTP | 机器码 | 采集器动作 |
| --- | --- | --- |
| 400 | `invalid_entity_batch`, `invalid_entity_batch_id`, `invalid_entity_batch_size`, `duplicate_entity_in_batch`, `invalid_entity_fields`, `invalid_entity_type`, `invalid_entity_id` | 修正结构、数量、身份或未知字段；不要反复发送原错误载荷 |
| 400 | `invalid_entity_observed_at`, `invalid_entity_source_page`, `invalid_collector_version`, `invalid_entity_data`, `invalid_entity_related`, `invalid_entity_attachments`, `invalid_entity_attachment_event_id`, `invalid_entity_json` | 修正时间、路径、JSON 值或引用；保留本地错误记录，其他批次可继续 |
| 400 | `entity_batch_header_mismatch`, `duplicate_json_key`, `invalid_json`, `encoding_not_supported`, `incomplete_body` | 核对唯一请求头、标准 UTF-8 JSON 和完整请求体；修正后重投未提交批次 |
| 401 / 403 | `unauthorized` / `origin_not_allowed`, `invalid_preflight`, `preflight_headers_not_allowed` | 暂停投递并检查既有授权/网关；不更换账号、不扩大白名单 |
| 404 | `not_found` | 核对路径、类型与 ID；缺记录不代表此前批次未提交 |
| 408 / 网络断开 | `request_timeout` 或没有 ACK | 按原批次和原载荷重试；即使已提交也安全 |
| 409 | `entity_batch_conflict` | 相同批次号已有不同载荷，保留原队列、人工核对，不自动换 ID |
| 409 | `entity_observation_conflict` | 相同最新观察时间对应不同业务内容；检查采集重复/并发/时间精度，不伪造时间 |
| 411 / 413 / 415 | `content_length_required` / `body_too_large` / `json_required` | 修正请求长度/大小/类型；413 拆成新批次，每批≤100且≤64 KiB。单实体过大时保留本地待处理，不截断字段冒充完整快照 |
| 503 | `entity_storage_unavailable`, `temporarily_unavailable` | 可能结果未知；保留原批次退避重试 |
| 503 | `entity_storage_unsafe`, `entity_database_version_invalid` | 暂停投递，让管理员核对存储/版本，不能改权限自动绕过 |
| 507 | `entity_disk_full` | 暂停并报告容量问题；释放/扩容后用相同批次恢复，不自动删历史 |

可重试错误用带抖动指数退避，例如 5、10、20、40、80 秒，最大 300 秒，每轮有重试上限；持续失败保留磁盘队列并告警，下次恢复继续，不丢弃。429 若由上游网关给出则遵循 `Retry-After`。401/403/409、格式和存储安全错误不自动死循环。可以在重试前 GET 对应实体辅助核对，但不同新旧快照可能并存，不能用一条当前记录推断整个批次已确认；原批次重放的持久 ACK 才是确认依据。

## Win10 分页补采与持续采集建议

1. 只读已登录页面，低频翻页、点详情、必要时导出/下载。禁止发送、修改、删除、发布、确认收款；登录状态不得导出到 221。
2. 先盘点各后台页面实际可读的稳定号和时间字段，用页面实测字段，不能按示例猜。分别维护订单、物流、询盘、RFQ、商机、客户/分组标签、跟进、发品历史、计划报表、关键词报表和店铺报表的采集清单。
3. 按业务日期从新到旧，先补近几天，再逐段覆盖两年。按页面支持拆日/周/月窗口，低频串行，建议每页随机等待 3–10 秒、详情 2–5 秒，窗口之间适当休息；这些是起点建议，遇验证码、风险提示或会话过期立即暂停，不绕过风控。
4. 本地持久化游标：实体类型、日期窗口、排序、筛选、页码或稳定游标、已看 ID、详情完成状态、待投递批次、原始快照与 ACK。页码变化不能成为实体身份；报表必须保留日期粒度和筛选条件。页面重排可回退一页或一天重叠扫描，由服务端幂等去重。
5. 读取完整详情后先落本地队列，再投递 221。只有收到 `persisted=true` 的对应批次 ACK，才推进交付游标；暂停/重启后用原批次恢复。把一页按 UTF-8 字节数分批，每批建议 10–50 条且≤64 KiB；最后残余条也要提交。
6. 热数据小窗口低频轮询，历史窗口按断点继续，不每轮重扫两年。分别记录页面扫描覆盖与投递 ACK 覆盖；221 的观察时间统计不能证明两年业务日期无缺口。客户分组/标签和跟进、物流状态、发品只读记录也须独立核对覆盖。
7. 平台不可见、已删、权限不足、缺稳定号、详情未完整加载、单实体超限时，记录本地缺口和原因；不编造空快照、不把“没有权限”当作 0，不阻断其他已完整且合法的实体批次。

Windows 采集器与真实后台覆盖、221 总览接线、实体到客户/案子档案的业务投影均在本轮范围外，必须另行联调和验收。

# WhatsApp 后台发送候选

本模块实现 `docs/omnichannel-design.md` 第 3 节的后台流水线，支持 WhatsApp 纯文本和附件发送候选。
附件功能尚未完整装配到宿主、未部署、未进行真实发送验收。所有开发验证使用临时库、虚构身份和注入假函数；
真实发送验收只能由用户本人在后台确认并点击完成。

## 接入

新增 `console.v3_outbox_api.build(app_module, ...)` 返回七个 Starlette Route。
宿主须使用现有认证及全局 CSRF 中间件。GET 读取后 recheck；POST 读正文和任何写入前 recheck。
认证失败不建库、不记审计、不调用任何传输。所有响应补充 no-store、nosniff；异常只返回固定中文。
SQLite、网关调用和 recheck 在线程池执行。

推荐宿主共享同一收件箱、同一 Outbox（以下仅为装配示意，不在此任务执行）：

```python
from console.channels import build_inbox
from console.outbox import BridgeClient, Outbox
from console import v3_inbox_api, v3_outbox_api

shared = build_inbox(inbound_path, whatsapp_config_path=config_path, fetch=fetch)
bridge = BridgeClient(config_path, post=post, get=get)
sends = Outbox(outbox_path, bridge=bridge, inbox=shared)
routes = (
    v3_inbox_api.build(app_module, inbox=shared, outbox=sends)
    + v3_outbox_api.build(app_module, outbox=sends)
)
```

测试必须注入假 `fetch`、`post`、`get`，并指定临时路径。构造对象、查询开关和读取发送配置不产生网络调用。
outbox builder 也接受 `inbox/outbox_path/config_path/api/post/get/stat_fn/group_gid/clock`，
或 `inbound_path/wechat_path/fetch` 创建收件箱。
显式指定非默认 inbound_path 时，未指定的配置和 outbox 路径隔离到同目录，避免回落到生产来源。
收件箱 builder 新增 `outbox/outbox_path/bridge`；共享 Outbox 是保证配置完全一致的推荐方式。

## API 与开关

* `GET /api/v1/outbox/settings`：`{channels:[...]}`，WhatsApp 默认关，缺行视为关。
  WhatsApp 的 `available:true` 表示本阶段具备发送实现；实际配置可用性见详情的 send_state。
* `POST /api/v1/outbox/settings`：URL 编码表单 `channel,enabled=0|1`。只允许 WhatsApp；
  其它渠道一律 400，返回中文 `error` 与 `reason`，并审计。阿里保留 Win10 复制发送说明，微信须另行授权。
* `POST /api/v1/inbox/conversations/{key}/send`：表单 `text,request_id,confirm=1`，
  可选 `confirm_new_recipient=0|1`（默认 0）、正整数 `draft_id`。
  request_id 为每次明确点击产生的 UUID，允许 8–80 位字母、数字及连字符。
  正文 trim 后 1–4000 字，任何位置含 `【待确认` 均拒绝。其它渠道统一拒绝。
  key 必须合法且对应实际收件箱会话；不能通过伪造编码 key 指定任意收件人。
  默认不发送附件、不生成草稿、不修改草稿 used 状态；draft_id 仅作关联记录。
* 发送响应：`{message:{id,state,state_text,created_at},needs_new_recipient_confirmation}`；
  失败和不确定时另有固定中文 `error_text`。网关结果已被本地记录时返回 200，state 才表示结果。
  本地参数、渠道、开关、缺会话、冲突及限流分别为 400/403/404/409/429。
* `GET /api/v1/outbox?conversation={key}`：该会话最近 30 条；
  `GET /api/v1/outbox/recent`：全局最近 50 条。返回 `{items:[...]}`，created_at DESC、id DESC。
  项目仅含 `id,conversation_key,channel,text,state,state_text,error_text,created_at`，不返回 chat_ref、
  request_id、text_sha 或网关幂等键。所有 GET 需要认证；缺库查询不创建目录或文件。

会话详情新增顶层 `send_state:{enabled,available,reason}`。
conversation.can_send 仅在 WhatsApp、开关打开且发送配置通过安全检查时为 true，copy_hint 同步变化。
此处 available 只检查本地配置，不探测网关在线、配对状态或收件人绑定；不能视为发送成功保证。

## 状态机、幂等与不确定结果

`sending → sent | failed | unknown`。入站归档和本地发送记录分别保存，不伪造入站消息。

1. 完成认证、会话/渠道、开关、显式确认、正文及其余参数校验；失败不插入 outbox、不调用网关。
2. SQLite `BEGIN IMMEDIATE` 内先查 request_id，再复核开关和配额，然后插入 sending 并提交。
   每会话滚动一分钟最多 5 次，全局滚动一小时最多 60 次，所有已预约尝试（含失败/不确定）都计数。
   429 带 `retry_after` 与 `Retry-After`；重复请求不消耗配额，即使配额已满也返回旧记录。
3. request_id 全局唯一，网关键为 `ops-` + `sha256(request_id + conversation_key)[:40]`。
   相同 request_id、同一会话返回原始记录（包括发送中、失败及不确定），不重新调用绑定查询或发送。
   同一 request_id 改正文仍返回原记录；跨会话复用为 409。UNIQUE 约束也阻止并发插入造成重复出站。
   校验和开关仍先于重放；例如关闭开关后重提发送返回 403，可通过 GET 查询历史。
   已有记录直接从本地读取，不刷新入站来源；来源暂时不可用也不会导致旧请求重新出站。
4. 本地 sending 持久化后才 GET `/api/binding`，只接受同 session 的合法 account_id，发送包固定
   `actor=owner_explicit,auto_send=false`，POST `/api/sendText`。
5. 以只读核对的 bridge.py 协议为准：2xx、匹配的幂等键、`state=accepted` 才映射 sent。
   `accepted` 表示网关明确接受发送，不代表对方设备送达或已读。
   已知 4xx 拒绝，以及 HTTP 200 中 `state=rejected,error_code=provider_http_4xx` 映射 failed。
   重放的 accepted 可确认为 sent；重放的 rejected 缺错误码时无法确定，保守记 unknown。
6. 超时、连接错误、5xx、重定向、响应无法解析、字段不符、未知错误码或网关 in_flight 均为 unknown。
   返回“结果不确定：请先到 WhatsApp 里确认这条是否已发出，不要重复发送”。**绝不自动重试**。
   用户须先到原渠道核对，确认需要再发后，另一次明确点击使用新的 request_id。

首次联系人唯一例外：仅当 `/api/sendText` 明确返回 HTTP 409、`error=recipient_not_bound`，
且本次 `confirm_new_recipient=1`，才 POST `/api/recipients/bind`，固定 confirmed=true、
reason=`后台首次发送，用户已确认`。绑定明确成功后，原包（含相同幂等键）仅重发一次。
其它错误不绑定；绑定结果不确定也不重发；第二次仍拒绝则停止。
未勾选时返回 failed、needs_new_recipient_confirmation=true，不绑定、不重发。
对这条失败记录补勾选仍属于重放；用户重新确认的发送须生成新的 request_id。

网络操作不持有数据库事务。进程崩溃或网关已返回而本地结果写入失败时，记录可能保留 sending，
再次提交也绝不自动再发；结果落库失败返回固定 503。没有自动恢复或重放任务，用户需去 WhatsApp 核实。

## 网关与私有存储

默认 API 为 `http://127.0.0.1:8788`，来自 bridge.py main() 和服务单元；不使用 QR 的 8789 端口。
可由 BridgeClient / outbox builder 的 `api` 参数覆盖，或私有配置可选 `api` 字段覆盖。
只允许 http 的 127.0.0.1/localhost，拒绝认证 URL、路径型基地址、查询串及重定向，禁用环境代理。
每次调用 timeout=20 秒。两个注入函数签名均为 `(url,headers,body,timeout)`，返回 `(HTTP状态码,JSON字典)`，
GET 的 body 为 None。默认 urllib 实现不重试；本次所有测试只经过假传输或假 opener。

配置读取沿用 channels/whatsapp.py 的边界：root:imgw、精确 0640 普通文件、拒绝符号链接，
O_NOFOLLOW 打开并 fstat 检查身份与权限，最大 64 KiB。
发送客户端只取 bridge_api_key、session、可选 api；不读取或使用 WAHA 凭据发送。
密钥仅在内部形成 Authorization 头，不记录、不持久化、不跨越结果及异常边界。
`stat_fn/group_gid` 是与渠道适配器一致的测试注入点，宿主不应将其暴露为请求参数。

默认库 `/var/lib/imgw/ops-app/outbox.sqlite3`，路径可注入；写入 umask 0077、文件 0600、WAL、
synchronous=FULL。send_settings 保存开关，outbox 保存正文、正文 SHA256、状态、draft_id、
request_id、幂等键、会话 key 与时间；chat_ref 仅保存聊天 id 的 SHA256 前 16 位。
会话 key 沿用收件箱可逆的 canonical base64url 编码，**不是加密**；不额外存储明文电话号码列。
正文是业务原文，库与 WAL 都属于私有运行数据，不应加入 Git。

audit 记录设置变更/拒绝、校验拒绝、限流拒绝、发送尝试与结果、绑定尝试与结果、重复请求。
detail 仅用固定动作、owner_explicit、已知错误码、状态、布尔值及哈希引用，不保存网关响应、
异常字符串、凭据或完整号码。未认证及 recheck 失败不写库。当前 actor 固定为 owner_explicit，
未新增人员权限体系；审计与业务记录同库，不提供跨库不可篡改保证及保留期清理。

## 验证与局限

tests/outbox 全部为虚构身份和临时数据；每例拦截并断言未调用真实 socket、DNS、urlopen，
注入假网关和假配置所有权。覆盖并发防重、两级限流、错误映射、绑定唯一例外、权限、审计、
列表字段、认证与 recheck、收件箱联动；缺 Starlette 时仅跳过 API 测试。

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/outbox -p 'test_*.py'
PYTHONPATH=src python3 -m unittest discover -s tests/outbox -p 'test_*.py'
```

附件前端已有开发候选；宿主限额、生产部署、实际账号连通检查、送达/已读回执及自动恢复尚未完成。
阿里、微信、独立站发送均明确拒绝。不群发、不自动发送、不替用户发任何真实测试消息。

## WhatsApp 附件发送候选（开发候选，未部署）

选择文件只创建候选，不发消息。页面显示收件人、文件名和大小，老板再次确认才发送；仅允许个人会话，拒绝群会话。`POST /api/v1/inbox/conversations/{key}/attachments` 使用 URL 编码表单 `filename,mimetype,data,request_id`，其中 data 是文件的 base64，返回 `candidate:{id,filename,mimetype,size}`。候选 request_id 使用 8–80 位字母、数字或连字符，相同标识和相同内容重复提交返回同一候选，内容不同则拒绝。上传失败可使用相同候选 request_id 重试；不需要发送开关就可创建候选。

`POST /api/v1/inbox/conversations/{key}/attachments/send` 使用表单 `candidate_id,request_id,confirm=1`，可选 `confirm_new_recipient=0|1`；发送 request_id 与候选 request_id 独立。发送沿用默认关闭的 WhatsApp 开关、认证、CSRF、会话存在校验、速率限制、桥接幂等、审计和原有四态；发送结果不确定时绝不自动重试。一个候选只能对应一次发送请求；只有网关明确返回 `recipient_not_bound`、未发送，且老板再次确认首次联系人时，允许使用新的发送 request_id 继续同一候选。任何其他失败或结果不确定均不能换标识重发同一候选。候选创建和发送均不得用于群发。

接受 JPEG、PNG、WebP、MP4、PDF、纯文本、ZIP、DOCX、XLSX、PPTX 的声明 MIME，单文件上限 20 MiB；不解析或执行文件，MIME 由上传方声明，并非对文件内容安全或真实类型的鉴定。图片、视频、其他文件分别向 bridge 的 `/api/sendImage`、`/api/sendVideo`、`/api/sendFile` 发送 file `{filename,mimetype,data}`。已只读核对现场 bridge 的图片和文件接口及 20 MiB 限制，视频接口需要随本任务补丁合入；WAHA 2026.9.2 / WEBJS 现场兼容性待现场确认。bridge 现有实现会在其私有出站库中保存整个请求，包括文件 base64；该库须按私有附件同等保护和备份。

候选字节和元数据保存在独立私有 SQLite，默认与 outbox 库同目录的 `whatsapp-attachment-candidates.sqlite3`，可通过 `Outbox(..., candidate_path=...)` 或 `v3_outbox_api.build(..., candidate_path=...)` 指定；事务加锁，文件权限 0600，WAL，网络请求不持有事务。候选没有自动清理，管理员需要管理磁盘空间和私有数据保留期限。

**需要总指挥合并宿主改动：** 只读生产基线 `/opt/imgw/wechat/console/app.py` 认证和 CSRF 中间件的普通 POST 分支（约 669–677 行）把 URL 编码正文限制为 8192 字节，因此目前大文件会在到达本路由之前收到 413。宿主须仅对已认证、精确匹配 `/api/v1/inbox/conversations/{key}/attachments` 的 POST 设置上限 `12 * ((20 * 1024 * 1024 + 2) // 3) + 8192` 字节（包含 URL 编码膨胀），保留现有 CSRF 头校验、单值约束、严格 UTF-8、缓存 `request.state.form` 和请求体回放，不得放宽其他路由；并检查反向代理等上游正文上限。该路由会优先读取宿主已校验的 `request.state.form`，独立装配时才限制并读取请求流。此改动未经本任务合并或部署，20 MiB 上传尚未端到端验证。

测试只使用临时库、虚构文件和注入假 bridge，不代表真实账号、真实媒体、WAHA 版本或生产环境已验收。

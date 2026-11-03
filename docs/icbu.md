# 阿里国际站旧版草稿审核 API

本批新增 `/api/v1/icbu/*` JSON 后端，供新版 `/app/` 接入。它沿用旧版实时草稿来源和执行队列；执行队列仍由现有 Win10 执行器取走。新版接口不会直接连接阿里网页，也不会自行发送消息。路由尚未部署到生产 `/app/`；演练脚本会自动挂载，并强制禁用设置写入和入队。

## 接口

所有接口要求新版控制台账号认证。POST 继续依赖宿主 CSRF 中间件，账号 recheck 在读取表单和执行操作前完成；未登录写请求保留机器码 `operation_requires_console_login`。状态接口按旧版 `inquiry_owner(read_json_mapping(data.CONFIG))` 解析 owner，但绝不返回配置对象。成功与错误响应均为 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。表单仅接受 URL 编码格式，原文和规范编码后均不超过 8KB；拒绝重复字段、未知字段和非法格式。错误文案为中文，不返回异常、路径、策略文件或凭据。

| 方法与路径 | 用途 |
| --- | --- |
| GET `/api/v1/icbu/drafts?page=1` | 每页沿用旧版 25 条；返回会话摘要、草稿状态、批准/阻塞状态和更新时间。 |
| GET `/api/v1/icbu/drafts/{identifier}` | 返回旧版草稿正文、会话内容、来源、审核备注、当前 SHA、动作历史和命令状态。 |
| POST `/api/v1/icbu/drafts/{identifier}/command` | 必填 `action`、`draft_sha`、`text`；`action` 只允许 `fill` 或 `send`。仅人工提交此路由可进入旧执行队列。 |
| GET `/api/v1/icbu/settings` | 只返回 `mode` 与 `conversations`。 |
| POST `/api/v1/icbu/settings` | 接受 `mode` 和换行分隔的 `conversations`，调用旧 `configure`。 |
| GET `/api/v1/icbu/status` | 只返回草稿/队列/Win10 执行器计数，不返回客户名、消息、草稿正文、执行器 ID 或配置。 |
| GET `/api/v1/icbu/validation` | 若旧 Phase 8 合成验收文件存在且明确标记 `synthetic=true`、`never_send=true`，返回不含真实收件人的验收草稿；该条目不可执行。 |

草稿响应包含 `conversation_key`，按新版 `make_key('conversation', 'alibaba', account, conversation_id)` 生成。列表逐条复用旧只读详情获取 `conversation_id`；如果详情不可用，key 为 `null`。可据此从草稿跳到新版统一收件箱；详情同样返回该 key。

旧路由对应关系：`GET /v3/icbu-drafts` → drafts 列表；`GET /v3/icbu-drafts/{identifier}` → 详情；旧审核表单 `POST /v3/icbu-drafts/{identifier}/command` → command；`POST /v3/icbu-drafts/settings` → settings；`GET /v3/icbu-drafts/validation` → 合成验收；旧 `GET /v3/operations/status` → status。

## 旧版对应与校验

只读草稿列表/详情复用 `operations_readonly.draft_list(page=1, paths=DEFAULT)` 和 `draft_detail(identifier, paths=DEFAULT)` 的投影，状态摘要复用 `overview(owner, paths=DEFAULT)` 的 `drafts`、`probe` 计数。验证数据仍遵守旧路由的合成/不发送门槛。状态额外用只读 SQLite 汇总 `commands.state` 和已出现执行器数量；旧版 `Operations` 没有 `progress` 方法，旧 `/v3/operations/status` 实际经 `safe_panel` 调用 `overview(owner)`。

已只读检查的旧版 `icbu_operations.py` 接口签名：

```python
Operations(db=DB, inbound=INBOUND, policy=POLICY)
Operations.configuration(self, policy=None)
Operations.configure(self, mode, conversations, actor='owner_console')
Operations.inbound_row(self, identifier)
Operations.enqueue(self, identifier, action, expected_sha, text=None,
                    origin='owner_console', *, retry_blocked=False)
Operations.poll(self, payload)
Operations.acknowledge(self, payload)
Operations.list_commands(self, inbound_id=None)
Operations.enqueue_automatic(self, policy)
digest(v)
```

旧模式值是 `review`、`test_auto`、`auto`。旧版 `enqueue` 的动作值是 `fill`、`send`；正文去首尾空白后需 1–6000 字符且不能含 NUL。实时会话标识须符合 `tm:[A-Za-z0-9_-]{1,128}`。旧 writer 再次读取实时非合成买家草稿并校验 SHA。新接口先对只读详情比对 `draft_sha`，不匹配返回 409；通过校验后只调用一次 `enqueue(..., retry_blocked=True)`。`retry_blocked=True` 只出现在这条有登录、CSRF、recheck 的人工入口，自动队列不经此路由。

旧 `human_pause_ack` 只认可绑定了 client/lease 且明确报告 `operations_user_active` 的阻塞回执。状态响应只呈现一个固定中文原因，不暴露执行器原始回执；其他 blocked/unknown/sent 状态继续由旧队列围栏处理，不在新代码中擅自重发。

## 安全与限制

- GET 配置和命令状态通过 `mode=ro`、`query_only` 连接读取。不能调用旧版 `list_commands()` 或 `configuration()` 做 GET：旧 `connect()` 会运行建表脚本。GET 不调用 `configure`、`enqueue`，不写草稿、配置或队列。
- 写请求要求现有账号认证、控制台登录、recheck 和宿主 CSRF；演练脚本传 `allow_writes=False`，且 `IMGW_REHEARSAL=1` 会在 API 内再次强制禁写，在读取请求体之前对两个 POST 一律返回 403。
- 草稿 SHA 使用旧 `digest` 的规则：草稿 UTF-8 正文的 SHA-256 十六进制值。SHA 不匹配返回 409，旧 `enqueue` 事务内还会二次校验，避免审核与入队间的草稿竞争。
- settings POST 仅调用旧 `configure(mode, conversations)`；新版不提供定时自动审批，也不会调用 `enqueue_automatic`。`auto`/`test_auto` 是旧配置模式，可能影响部署中已有的旧自动调度行为，老板应按旧系统语义设置。
- GET `/status` 的命令计数来自 `commands` 状态聚合，包含完整命令表，并带 `available` 标记；Win10 采集器计数来自旧 `overview` 的 probe 投影，读取不可用时为 `null`。草稿投影也带可用性标记。旧源码没有单独 `progress` 函数，也没有旧 API 对队列全量分组的函数。
- API 不记录请求正文或表单值。旧 `configure` 的设置写入仍调用原方法；适配层将其 `mode_changed` 事件详情替换为固定的 `operations_settings_updated` 标记，不记录 mode、会话标识或散列。GET/错误响应不返回策略文件、散列、凭据或原始异常。
- 测试全部注入虚构 Operations 和假只读源；没有对真实队列入队、没有发送阿里消息、没有改 `/opt/imgw` 或真实数据库。新版前端未改。

# 统一收件箱渠道适配器

本次是后端开发候选，未挂载、未部署，也没有用真实聊天或真实 WAHA 验收。
所有渠道仅收取、查看、生成草稿；`can_send=false`。本阶段范围以本次任务为准，
设计文档中的后续 WhatsApp 发送流程不在本次实现内。已有前端及生产程序未修改。

## 适配器契约

`console.channels.common.ChannelAdapter` 是文档化 Protocol：

```python
name: str
label: str
cache_key: object
list_conversations(now: float) -> dict[str, dict]
messages(key: str) -> list[dict]
health(now: float) -> dict
```

时间均为 Unix 秒。会话沿用 key/channel/account/buyer_name/country/first_seen/last_seen/
source_url/n_messages/messages(tuple)/waiting_reply/waiting_since，增加 channel_label、
counterpart_role（customer/supplier/learning/internal/unclassified/unknown）、dormant、is_group。
key 始终通过 `make_key('conversation', channel, account, identity)` 生成；不透明 key 不是加密。
消息为 role（buyer/seller/unknown）、text、sent_at、sender_label；unknown 不重置等待时钟。
附件只展示中文类型占位，不提供路径、下载或解析。health 只返回可公开给已认证运营用户的运行元数据。

`waiting_state` 是三渠道共享规则：最后有效消息来自对方、不是结束语、距今不超过 30 天才待回复。
恰好 30 天仍可等待；超过边界 dormant=true、waiting_reply=false。休眠与结束语分别判断。
客户待办包含 customer/unclassified；supplier 单列 supplier_waiting。

## 宿主接入与缓存

推荐一次创建，共享三个 API 的缓存：

```python
from console.channels import build_inbox
from console import v3_inbox_api, v3_customers_api, v3_channels_api

shared = build_inbox()  # 部署后才使用默认来源；测试必须显式传临时路径和假 fetch
routes = (
    v3_inbox_api.build(app_module, inbox=shared)
    + v3_customers_api.build(app_module, inbox=shared)
    + v3_channels_api.build(app_module, inbox=shared)
)
```

保留现有认证、recheck、线程池、CSRF 宿主边界及本地草稿存储。默认来源仅供部署时使用；
非默认 inbound_path 下，未指定的微信/WhatsApp 默认隔离到同目录的 disabled 占位路径，不访问生产来源。
可注入 wechat_path、whatsapp_config_path、fetch、单调 clock、测试用 stat_fn/group_gid；
微信 include_roles 可覆盖默认群角色集合；`v3_channels_api.build(..., plan=..., plan_path=...)` 可注入渠道运营计划或其只读文件路径。自定义适配器可通过 `UnifiedInbox(adapters)` 组装。

## 渠道运营计划

默认计划在代码中，不记录账号名称、账号标识或凭据：WhatsApp=`not_started`，阿里 `main=operating`、`others=planned`，微信=`operating`。计划文件路径默认为 `/etc/imgw/channel-operation-plan.json`，可通过 `plan_path` 注入；文件缺失、不可读、结构不符或包含未允许字段时回退代码默认值。计划配置只允许状态与 `main/others` 角色。阿里账号计数是固定业务约定：总计 6 个、主账号 1 个、其余 5 个。

system/channels 保留原来的 `ready`、`state`、`summary`、`diagnosis`、`checklist` 和 `notes` 字段，并新增总览及逐渠道 `plan`。默认情况下主账号有数据时，notes 显示“主账号正常采集；另外 5 个子账号计划在自动发品和 AI 运营跑通后启用，目前没有数据是正常的”，仍保留 6 个账号中当前有数据的账号数。只有一个数据账号时按业务约定视作主账号；若没有数据，主账号仍显示为待核对。计划中的账号不改变真实采集统计。

每个适配器缓存自己的读取和聚合。阿里沿用按绝对路径共享的 inbound_cache；
微信检查主库及 WAL 的 mtime_ns、大小、设备与 inode；WhatsApp 缓存 overview/session 30 秒、
单聊天消息 15 秒。统一层使用各适配器 cache_key 和 ready 组合，只在变化时重建合并结果与客户聚合。
跨过最早的 30 天到期点会更新待回复投影，无需重读消息；24 小时计数随请求时间计算。
所有缓存只在本进程内，多 worker 不共享。文件指纹不能识别刻意保持元信息不变的篡改。

兼容已有调用：system/channels 显式传旧 whatsapp_path（SQLite 文件或目录）且未传新配置、fetch、inbox，
保留旧三渠道归档计数，供原调用迁移；该模式的微信缺 payload 也能计数，不能视为完整会话接入。
新入口使用 whatsapp_config_path（或 whatsapp_path=*.json 别名），返回三渠道及独立站预留。

## 阿里国际站

数据来源 `/var/lib/imgw/icbu/inbound.sqlite3` 的 inbound.payload。
复用既有 aggregate_messages / inbound_cache，保留账号、会话 key、消息内去重与稳定顺序、来源 URL 规则。
counterpart_role 固定 customer；sender_label 为 null。不补采账号、其它页面历史或附件。
缺表/缺列返回 ready:false；损坏数据库的收件箱读取沿用既有 503，渠道健康仍隔离为不可用。

probe 来源 `/var/lib/imgw/icbu/probe-status.sqlite3`，可注入路径。实现先 PRAGMA 检查已确认的
probe_state(account,snapshot_json,first_seen_at,received_at,last_complete_at)；
用 received_at 表示服务接收心跳时间，snapshot_json 只提取 version/state，不输出 last_error 或原 JSON。
缺库、缺列、空表或不可读时 probe=null，不创建文件。

主账号心跳超过 6 小时会生成 Win10 检查诊断和四项 checklist，即使最近消息仍新鲜；计划中的子账号无心跳不会被当成故障。健康心跳但消息旧时说明消息较少；无匹配的主账号 probe 时说明心跳暂不可用。状态不能证明账号登录或发送可用。notes 保留“6 个账号只有 N 个有数据”，账号总数 6 来自已给定业务约定，N 来自有效归档，不从平台发现账号。

## 微信

只读 `/var/lib/imgw/wechat/api_state.sqlite3`；连接固定 mode=ro + query_only。
messages.payload JSON 使用 group_id/message_id/sender_id/own_wxid/text/created_at/attachments。
只纳入 @chatroom 群，sender_id==own_wxid 是 seller，其余 buyer。account 固定“微信”。
无文本且有附件时按 kind/msg_type/mimetype 推断“[图片]/[文件]/[视频]/[语音]”，未知为“[附件]”。

message_scopes 的 message_id/scope_id/business_role 必须存在；两表都有 app_id 时按 app_id 关联，
每群消息的角色取众数，平票按角色字典序稳定选择，无角色为 unclassified。默认收件箱适配器纳入
customer/supplier/unclassified；learning/internal/unknown 不纳入，可用 include_roles 覆盖。
API 列表默认再只取 customer/unclassified；role=all 或 supplier 才显示供应商。

群名首先读取 onboarding_candidates.group_name，再通过 PRAGMA 发现其它含
group_name/room_name/chatroom_name 及群标识列的表；都没有时“微信群 ·”加 group_id 末四字符。
成员昵称按发现的 wxid/sender_id 等标识列和 nickname/nick_name/display_name 等名称列匹配；
没有时“成员 ”加 sender_id 末四字符。每条消息始终带 sender_label，不返回 own_wxid 或原附件路径。
当前没有完整联系人同步；昵称可能缺失或过时，同名、多个身份下同群仍可能显示合并名称。

health 的 groups_by_role、总消息和 24 小时数包含所有可读群，包括收件箱隐藏群；
收件箱 summary 仅计 include_roles 允许的群。客户列表只接受 customer 群作为“客户群”条目；
供应商、unclassified 群和学习/内部群均不作为客户。此适配器不支持微信个人聊天。

## WhatsApp

后台登录/重新扫码的独立候选接口见 [F1 WhatsApp 扫码登录](whatsapp-login.md)。
计划为 `not_started` 时，未登录显示“还没开始运营：准备开始时到 系统后台 → 渠道接入 扫码登录”，状态归为 quiet，不提示收件可能中断；登录成功但无聊天显示“已登录，等待第一批联系人和会话”。计划改为 `operating` 后，断线继续按原规则报警。此处的收件箱适配器仍保持 GET-only。
新接口当前的现场核对阻碍、演练禁写限制和前端接入约定以该文档为准。

已只读确认 bridge.py 的 CONFIG 为 `/etc/imgw/whatsapp-bridge.json`；从不执行或导入生产 bridge。
配置必须为 root:imgw 所有、精确 0640 的普通文件；拒绝符号链接，打开用 O_NOFOLLOW 并核对文件身份。
只保留 waha_url/authorization/session；其它配置不进入适配器。不打印或返回凭据、原始错误、会话配置或 WAHA 原对象。
account 显示“WhatsApp · ****”加 session me.id 的末四位；健康账号名固定“WhatsApp 主号”。

本地提供的 waha-openapi.json 确认以下只读路径和响应：

| GET | 数据 |
| --- | --- |
| /api/sessions/{session} | SessionInfo.status、me.id |
| /api/{session}/chats/overview?limit=500 | ChatSummary 数组，id/name/lastMessage |
| /api/{session}/chats/{chatId}/messages?limit=100&downloadMedia=false | WAMessage 数组，id/body/fromMe/timestamp/hasMedia 等 |

所有调用经 `fetch(method,url,headers,timeout)`，返回已解析 JSON；失败抛异常即可，异常内容不跨出渠道边界。
默认 urllib 只接受 http://127.0.0.1 或 http://localhost，禁止认证 URL、环境代理、重定向与非 GET。
session/chatId 均进行 URL 编码；chatId 必须先 fullmatch `^[0-9A-Za-z._-]+@(c\.us|g\.us|lid)$`，
不合法 id 不进入 key 或传输。fromMe=true 为 seller，其余为 buyer；counterpart_role 固定 customer。
群聊也进入收件箱，但不进入客户列表。媒体只使用类型提示生成占位，不下载媒体、不返回媒体 URL。

列表只读取 overview 的最新摘要，不为 500 个聊天逐一拉历史：

- 列表 n_messages、summary 的 messages/new_24h/messages_24h 是已观测摘要数，最多每聊天一条，不能当完整历史量。
- 详情和草稿按需取最近 100 条，去重、时间升序，详情 n_messages 表示这次有效结果数，不代表远端历史总数。
- chats_total 是 overview 中有效 id 的聊天数（包含没有可用摘要的聊天）；最多覆盖 500 个，不做分页补采。
- chats_7d 是最新有效摘要在近 7 天的聊天数；无摘要聊天无法判断活跃时间。
- 401、超时、不可达、非 WORKING 会话或配置不合要求：ready:false，不提供旧数据；请求失败短暂缓存 30 秒，之后重试。
- 缓存与健康读不检查或启动 WAHA，不扫码、不变更配对、不发消息；单次远端请求 timeout=5 秒。

## 独立站接入建议

WebsiteAdapter 是保留实现。路径可配置，即使文件或空目录存在也 ready:false，不建库、不写入、不开放发送。
将来网站聊天组件通过自有 webhook 服务写入 `/var/lib/imgw/ops-app/website-inbound.sqlite3`，
此适配器只读该库；HTTP 接收和写入必须由独立服务实现。本次没有实现接收端。

建议 schema（未来迁移，不是本次已部署表）：

```sql
CREATE TABLE conversations (
  site_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
  visitor_id TEXT NOT NULL, display_name TEXT NOT NULL, country TEXT,
  source_url TEXT, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
  PRIMARY KEY (site_id, conversation_id)
);
CREATE TABLE messages (
  site_id TEXT NOT NULL, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('buyer','seller','unknown')),
  text TEXT NOT NULL, sent_at REAL NOT NULL, received_at REAL NOT NULL,
  sender_label TEXT, attachments_json TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (site_id, conversation_id, message_id),
  FOREIGN KEY (site_id, conversation_id) REFERENCES conversations
);
CREATE INDEX messages_timeline ON messages(site_id, conversation_id, sent_at);
```

建议 webhook 验证站点签名和时间、防重放，以 site_id/conversation_id/message_id 幂等写入；
签名密钥独立存储，不写消息库。访客身份用不可猜随机标识，附件只存受控引用，勿让浏览器提交服务器路径。
使用 UTC 秒级时间、事务更新 first_seen/last_seen，并记录 received_at 以区分消息时间与接收时间。
网站来源需验证为所属站点 URL。适配器以 site_id 作为 account、conversation_id 作为 identity，
复用统一等待规则、健康统计和草稿流程。将来的发送需要单独实现已确认、可审计的流程。

## 离线验证

tests/channels 全部使用虚构 SQLite、虚构账号、假配置 stat、假时钟和注入 fake fetch；
禁用测试进程的 socket.connect，默认 urllib 不调用。缺 Starlette 时跳过 HTTP 测试，纯适配器继续测。
本次校准调整了旧文案断言：阿里单个有数据账号的缺失子账号由故障提示改为计划说明；WhatsApp 未登录与零历史消息不再按断流告警；operating 状态、自动发送及主账号过期心跳仍需告警。2 万行虚构消息冷聚合断言小于 5 秒；这不是对真实数据库、线上延迟或服务状态的验收。
本次复归：`tests/channels/` 193 passed、14 subtests；相关工作台文案断言 `tests/inbox_api/` 37 passed、99 subtests；审计计划逻辑见 `tests/audit/` 204 passed。所有套件均分开运行，未联网。
具体验收命令见 [inbox-api.md](inbox-api.md)。

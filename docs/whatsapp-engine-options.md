# WhatsApp 历史补采与引擎选型

核查日期：2026-11-01。任务 R7-E；对应 [workflow-gaps.md](workflow-gaps.md) 的 WG-01。交付是研究文档，包含离线检查脚本和隔离演练方案；没有切换引擎、部署、扫码或连接 WhatsApp。本轮不修改业务源码、生产文件、会话、数据库、网络或共享需求账本，不 commit、不 push。

**建议现在保留 A（WEBJS 实时采集），下一步先用备用号演练 B（NOWEB），通过验收后再决定生产切换。C（GOWS）保留为第二候选。** NOWEB、GOWS 都绕开当前浏览器注入故障，但不能保证恢复手机里的全部历史或过期媒体，也没有证据证明它们比 WEBJS 更不容易封号。没有备用号时，停在离线核查，不拿业务号试引擎。

## 1. 证据范围与尚未核实的部分

已阅读 CLAUDE.md、README、master-plan、data-pipelines（含阿里 v2 契约）、project-roadmap、project-status、WG-01 全文，以及 WhatsApp 登录、媒体文档、部署候选、渠道/媒体源码和相关测试。下面区分三类证据：

| 证据 | 本轮能说明什么 | 不能说明什么 |
| --- | --- | --- |
| 任务书提供的现场情况 | 221 的 `waha221`、WAHA 2026.9.2/WEBJS；实时文字/图片/视频已进 bridge 并按 SHA-256 归档；历史/按消息取媒体报 500，日志出现 `window.require is not a function`；2026.10.1/dev 相同 fork 升级已试无效 | 本轮没有重现错误、查看容器日志或检查实时数据；“Web 更新导致注入失效”仍是有依据的诊断方向，不是已定位的上游修复 |
| 仓库及 `/opt/imgw` 只读源码 | 实际接口、字段、边界、候选之间的差异 | 文件存在不代表该版正在运行；没有读服务配置、凭据或任何业务数据库 |
| WAHA 官方 2026.9.2 标签源码与在线文档 | 公开版本的实现、默认配置、许可与能力边界 | 标签源码不能代替给定镜像摘要的构建内容，在线文档也可能比 2026.9.2 新 |

公开标签对应提交为 [`90ba445a0e765701c8910f0fac08176250c3e0fd`](https://github.com/devlikeapro/waha/commit/90ba445a0e765701c8910f0fac08176250c3e0fd)。下文源码链接固定到这个提交。

指定镜像是 `devlikeapro/waha@sha256:280bdc8f860267a2488a45d00ec88529646f7468c0bf0283c595d6efdd1b5e3b`。本轮尝试只读镜像检查，Docker socket 无权限，错误为：

```text
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

因此**没有取得该镜像内 Swagger、实际依赖或构建版本证明**，也没有启动或拉取容器、绕过权限。第 6 节给出由总指挥在有权限环境补做的离线检查脚本。本文提供可评审的选择与验收条件，不能据此立即切生产。

## 2. 三个引擎实际有什么差别

### 2.1 运行栈与 Core 可用性

| 项目 | WEBJS | NOWEB | GOWS |
| --- | --- | --- | --- |
| 通信方式 | Chromium + whatsapp-web.js，依赖 WhatsApp Web 页面与注入 | Node/TypeScript + Baileys，直接 WebSocket，无浏览器 | Go/gRPC 组件，直接 WebSocket，无浏览器 |
| 2026.9.2 公开版本依赖 | `fork-main-2026-06-26` | `fork-master-2026-04-28` | `devlikeapro/gows-plus` 的 `v1.0.48`；未取得底层 whatsmeow 提交，不能把上游最新版能力当成镜像已有 |
| 当前问题关联 | 故障出在这条运行栈；相同 fork 换标签不足以证明会修好 | 不执行 WEBJS 的页面注入，理论上避开本次故障点；自身协议兼容性仍须验 | 同样避开本次故障点；多一层 Go 组件与构建版本核对 |

运行方式依据 [WAHA 引擎说明](https://waha.devlike.pro/docs/how-to/engines/)；依赖依据固定提交的 [package.json](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/package.json)、[waha.config.json](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/waha.config.json) 和 [Dockerfile](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/Dockerfile)。避开故障点是根据运行栈作出的推断，不是本轮连接验证。

**不要沿用“媒体和历史必须买 Plus”的旧结论。** 官方说明自 2026.6.1 起 Plus 并入 Core，原 Plus 的多会话、媒体与存储等能力免费，使用 `devlikeapro/waha`。因此公开 2026.9.2 的 NOWEB/GOWS 不应再按旧版收费墙排除；指定摘要内二进制、端点及配置仍须补验。[Core/Plus 官方说明](https://waha.devlike.pro/docs/how-to/waha-plus/)

### 2.2 历史：同步到本地以后能读，不等于无限回溯

| 项目 | A：WEBJS 现状 | B：NOWEB | C：GOWS |
| --- | --- | --- | --- |
| 历史来源 | 浏览器里可取得的历史；任务书报告当前相关读取 500 | 手机/关联设备初始化与后续同步写入本地 store，再由 API 分页读取 | 同步后经 Go 组件存储，WAHA 通过 gRPC 分页读取 |
| 开关 | 当前故障不能靠 reconcile 的页数补救 | 必须在首次扫码前设置 `config.noweb.store.enabled=true`；`fullSync` 也在首次扫码前决定，两者源码默认都是 false | `config.gows.storage.messages/groups/chats` 未设或 null 时保持启用；不能为省空间禁用后还承诺相应历史 |
| 覆盖边界 | 本轮不能补历史 | `getChatMessages` 查本地 store。没有同步进来的历史，反复 offset 翻页也不会变出来 | `getChatMessages` 调 Go 的 `GetMessages`；不是承诺任意日期的云端恢复口 |
| 主要不确定性 | 何时有真实兼容修复 | 手机实际交付范围、同步是否结束、群/LID 的覆盖、存储容量 | 同样受同步范围限制；给定二进制的历史同步控制与接口兼容尚未核实 |

配置依据 [会话 DTO](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/structures/sessions.dto.ts#L77)；读取实现依据 [NOWEB getChatMessages](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/noweb/session.noweb.core.ts#L1579) 与 [GOWS getChatMessages](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/gows/session.gows.core.ts#L2576)。

NOWEB 官方文档给出的经验范围是：`fullSync=false` 约三个月，true 约一年、单聊天最多约 100K 条。这些是产品描述，**不是账号历史完整性的保证，也不是服务器一定交付的下限**。应报告实际最早/最晚消息时间、覆盖了哪些聊天、缺了什么。扫码后不要原地改 store/fullSync 期待补齐，官方提示这样可能丢历史；需要对比时使用另一份全新测试会话。[NOWEB 存储说明](https://waha.devlike.pro/docs/engines/noweb/#store)

NOWEB 初始历史通过 `messaging-history.set` 单独入 store，而现有消息 webhook 路径订阅 `messages.upsert`。**不能假定新会话一同步，历史就会逐条进入 bridge 的 `message.any`。** 应在同步趋稳后用历史分页接口主动补采，再与实时来源核对。[NOWEB 历史存储实现](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/noweb/store/NowebPersistentStore.ts#L97)

当前 [GOWS 在线说明](https://waha.devlike.pro/docs/engines/gows/) 另有实验性的同步深度配置，并提示默认同步可能较大。本文不提供把这些新配置直接套到 2026.9.2 的命令：尚未核对指定镜像/Go 组件是否识别。GOWS 要先通过离线配置检查，再评估首次同步的流量、磁盘和时间。

### 2.3 媒体：消息存在，文件也可能已经取不到

NOWEB、GOWS 公开源码都有下载路径。NOWEB 下载可调用 `sock.updateMediaMessage` 请求重新上传；GOWS 调用 Go `DownloadMedia`，源码注释包含 media-retry 回退。这会涉及 WhatsApp 协议与本号主手机，不是只查询本机数据库。[NOWEB 下载实现](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/noweb/session.noweb.core.ts#L3641)、[GOWS 下载实现](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/gows/session.gows.core.ts#L3237)

官方 2026.9 发布说明也提到过期媒体会向主手机请求重新上传。手机不在线、原文件已删除、上游拒绝或引用信息不足，仍可能失败；不能承诺找回旧图/视频。[2026.9 发布说明](https://waha.devlike.pro/blog/waha-2026-9/)

所以演练先用 `downloadMedia=false` 读取文字与媒体元数据，之后仅对清单中的单条附件做一次下载。`GET ...?downloadMedia=true` 也有下载/重上传请求与落盘副作用，本轮不对生产调用；它不等于向联系人发送聊天，但也不能称为完全无副作用读取。保留失败原因，禁止循环触发下载或重新配对来碰运气。已归档字节仍是首选，切引擎不能取代本地归档。

### 2.4 LID、普通号、群聊

LID 是独立标识，不能截取数字当手机号。公开 2026.9.2 有下列读取接口，NOWEB 从本地 store 找已知关系，GOWS 向 Go 组件查询；未命中允许 null。**支持查询不代表每个联系人都有映射，也不授权枚举陌生号码。**[LID 路由](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/api/lids.controller.ts#L29)、[NOWEB 实现](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/noweb/session.noweb.core.ts#L1994)、[GOWS 实现](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/gows/session.gows.core.ts#L2436)

| 只读路径 | 用途 |
| --- | --- |
| `GET /api/{session}/lids`、`/lids/count` | 已知关系分页与数量 |
| `GET /api/{session}/lids/{lid}` | 已知 LID 对应的普通号 |
| `GET /api/{session}/lids/pn/{phoneNumber}` | 已知普通号对应的 LID |

现 bridge 和控制台正则都接受 `@c.us`、`@lid`、`@g.us`，这只是格式允许。底层 NOWEB 原始数据还可能出现 `@s.whatsapp.net`；转换必须按引擎已证明的普通号规则处理，不把 LID 或群标识按后缀猜成同一人。任何别名映射保留来源和会话范围，未命中不合并；不得依据同名、末四位或相似正文自动关联客户、语言设置和案子。

三个引擎的公共能力都包含群聊，但群历史仍受同步范围、成员身份和可用性限制，见[官方引擎能力矩阵](https://waha.devlike.pro/docs/how-to/engines/)。NOWEB/GOWS 的完整消息 ID 可能带群成员段，群 `chatId`、成员 `participant`、方向及原始消息 ID 要分别核对。[NOWEB ID 实现](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/noweb/utils.ts#L8)、[GOWS ID 实现](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/core/engines/gows/session.gows.core.ts#L3313)。本任务不调用建群、入群、共享群历史或其他修改群状态的接口；官方的“共享群历史”功能涉及向成员分发，不能当成补采手段。

还要注意 WAHA 自身的身份归并：固定版本的聊天、overview 和消息查询 DTO 都有 `merge`，默认 true，可能合并已知 LID/普通号聊天。现控制台和补采请求未显式指定它；应在假服务及备用号样本上核查返回的聊天上下文，必要时比较 `merge=false` 的结果并单独审查请求白名单改动。不能把 API 默认归并当作业务身份已经核实，也不能遇到上下文校验失败就删掉校验。[聊天查询 DTO](https://github.com/devlikeapro/waha/blob/90ba445a0e765701c8910f0fac08176250c3e0fd/src/structures/chats.dto.ts#L96)

## 3. 对现有系统的改动面

### 3.1 已核对的本地契约

| 模块/源码位置 | 当前实际行为 | B/C 切换前必须处理或验收 |
| --- | --- | --- |
| `src/console/channels/whatsapp.py:200`、`:223`、`:264` | 读 `/api/sessions/{session}`、`/api/{session}/chats/overview?limit=500`、聊天消息 `limit=100&downloadMedia=false`；文字不从 bridge 库取 | 验证 overview 与消息响应。A 只保实时归档不代表收件箱历史能用；消息读取失败会清缓存并抛 `ChannelUnavailable`，不能显示成“没有消息” |
| 同文件 `:122`、`:276` | 投影 `id/body/timestamp/fromMe/hasMedia/media/participant/author`；单次结果按 id 去重 | 验证字段、类型、群成员和消息顺序；页面单次去重不等于持久库跨引擎去重 |
| `/opt/imgw/whatsapp/bridge.py:75` | HMAC 校验，严格匹配 session，只接收 `message.any/session.status/message.ack/message.revoked` | 独立测试 webhook 接收器，不能指向生产 webhook；保留签名、绑定、事件白名单，不订阅 `*` 后直接灌入 |
| 同文件 `:89`；`/opt/imgw/waha-reconcile-v1/bridge.py:117` | 前者消息键含 payload 的 `source_sha`；后者候选在事务中按 session/account/message id 归并来源、隔离冲突 | 两份源码不同，不宣称候选已安装。同消息不同 JSON 表征可能重复；新 session、LID/普通号与完整 ID 变化都不能靠现键自动合并 |
| `/opt/imgw/waha-reconcile-v1/bridge.py:61` | `history_projection` 某些聊天上下文证明依赖 WEBJS `_data.id.remote/_serialized/fromMe` | 为新引擎定义有源码依据的证明分支，不能删检查、猜映射或用正文/时间强行合并 |
| `/opt/imgw/waha-reconcile-v1/{history,reconcile}.py` | GET 聊天列表；消息分页用 offset、`filter.timestamp.gte/lte`、`downloadMedia=false`；增量搭接 300 秒、定期全量回看、两轮扫描 | 校验排序、分页、时间过滤和同步中结果变化；先等待 store 趋稳。两轮结束仍是可见范围，源码明确 `coverage_guaranteed=False` |
| `/opt/imgw/waha-reconcile-v1/{private_history_entry,reconcile_worker}.py` | 入口使用固定私有路径；锁与时间片控制；500 等源错误暂停，冷却 900 秒；没有逐页 sleep | 不能直接拿生产入口跑隔离测试。改造另交参数化候选；页预算与间隔单独增加。不能把时间片当作已有请求限速 |
| `src/console/wamedia/{bridge,ingest}.py` | 优先取 bridge 的 `media_jobs.status='archived'` 与 digest 字节；校验长度、哈希和路径；只读 bridge；队列独立持久化 | 延续归档优先。归档存在但损坏时拒绝，不借切引擎绕过；历史来源不能冒充实时 |
| `src/console/wamedia/ingest.py:55` | 精确消息 GET `.../messages/{messageId}?downloadMedia=true`，再 GET 同源 `/api/files/` 安全文件名；禁止代理/跳转 | 验证新引擎完整 ID 与 URL。若形状不同，单独审查适配，禁止改成任意 URL 下载 |
| `src/console/wamedia/store.py:44` | 媒体索引 identity 按 session/message id；字节按 SHA-256 | 新会话不会自动继承旧媒体索引，字节去重与索引关联分开处理；保留旧归档，不重置队列 |
| `src/console/channels/whatsapp_rich.py:46` | 链接卡片依赖 WEBJS `_data.subtype/matchedText/canonicalUrl/title/description/thumbnail` | 新引擎可能退化成文字，必须明确验收；不为补预览抓取任意网页 |
| `src/console/channels/whatsapp_login.py:176`、`:257`；`src/console/v3_whatsapp_login_api.py:66` | 已识别三个引擎；只有状态、二维码、start/logout，无创建/选引擎功能；手机号脱敏只识别数字 `@c.us` | 管理员离线配引擎与新会话，登录页只显示实际引擎/等待扫码。保留认证、CSRF、recheck、审计、节流、单次写请求和未知结果不重试 |

同一公共 REST/webhook 外观，不保证原始 `_data`、消息 ID、事件触发时机和媒体地址等价。[WAHA 官方引擎警告](https://waha.devlike.pro/docs/how-to/engines/)

会话 key 由渠道、账号和聊天标识构成，见 `src/console/channels/common.py:62`、`src/console/inbox/model.py:18`；账号展示又来自 `me.id`。切引擎可能影响旧笔记、案子来源、联系人和语言绑定。先建立可核对的别名关系，不批量重写历史 key，不覆盖实时，不另建语言表；语言只通过既有 `src/console/lang` 公共服务读取。

后续确需新增切换/别名/演练持久状态时，设计为独立、参数化 SQLite，默认在 `/var/lib/imgw/ops-app/`，单写者、事务与幂等；测试全用临时目录。不升级 `archive.sqlite3`，也不手工读写 WAHA 运行中的内部 store 来绕过 API。

### 3.2 只读参考基线

下列 SHA-256 只是本轮读过的源码基线，**不是生产部署清单**。没有产出覆盖生产的补丁；后续改造仍须重新核对基线、审查补丁与部署/回滚。

| 参考文件 | SHA-256 |
| --- | --- |
| `/opt/imgw/whatsapp/bridge.py` | `0f23743b95e5feea6277504c8caa8aeddae816a25a7c78f600a2c2b151016a69` |
| `/opt/imgw/waha-reconcile-v1/bridge.py` | `e1c4f9727f20d1d6a52cfa78db17ca969c5cd3b87082e175f9a6ee3e70da8592` |
| `/opt/imgw/waha-reconcile-v1/reconcile.py` | `cc4c01a3c94a6c8b948a402a04e832d054b060d4b20346fdf7b0f0f05f967766` |
| `/opt/imgw/waha-reconcile-v1/reconcile_worker.py` | `68b988002c87190b64a502fb1915598b060315646fa5023712c59da493a2597a` |
| `/opt/imgw/waha-reconcile-v1/history.py` | `c283cff274c61ee5ad645762f2ae3bc2b6a42d252e38c47106c2101f06b9a41e` |
| `/opt/imgw/waha-reconcile-v1/private_history_entry.py` | `4a699902c56385ff9558d9a533be830f621ed47f722e011c24753035dbec82ca` |
| `/opt/imgw/waha-reconcile-v1/media_recovery.py` | `b552e7c62acd5167e617b7ded0834a2cd7ff7c602d3ff108f644082b2f2a92af` |

## 4. 风险，特别是账号安全

三者均不是 WhatsApp 官方 Business Platform 客户端。使用非官方客户端本身就有账号限制风险；“只收不发”减少误发和骚扰风险，不能消除登录、协议兼容和平台风控风险。官方没有提供适用于本账号的安全请求阈值，也没有三引擎封号率对比，不能给出 NOWEB/GOWS 更安全的概率。[WhatsApp 关于临时封禁的说明](https://faq.whatsapp.com/1848531392146538)、[WAHA 风险说明](https://waha.devlike.pro/docs/overview/how-to-avoid-blocking/)

| 诱因或故障 | 影响 | 本方案的控制与停止条件 |
| --- | --- | --- |
| 非官方协议、上游版本变化 | 三引擎都可能掉线、临时限制或封号；无浏览器不代表官方认可 | 先用老板明确提供的备用号；手机出现限制/异常验证立即停，不重复扫码、不换号绕过限制 |
| 反复扫码、退出、换引擎、自动重连风暴 | 关联设备异常、账号验证增加、实时中断 | 不在业务号试错；完整演练一次，状态异常人工检查，不循环 start/logout/restart |
| 初始大历史同步、并发翻页、批量媒体下载 | 流量/磁盘/手机负担；平台可能限流。具体风控因果不能量化 | 演练仅一个消费者，页与媒体都有预算；429 尊重上游等待要求并停批次，持续 5xx/连接异常不推进成功游标 |
| 新号、异常账号状态、重复/批量对外行为、被举报 | 可能触发账号限制 | 本任务没有任何发送、养号、加人、建群、广播或共享群历史步骤，不用消息测试账号健康 |
| HTTP GET 的协议副作用 | 媒体下载可能向本号手机请求重新上传；某些状态/群接口也可能有动作 | 闭集只读清单；下载单独由人开始一次；排除标已读、presence、群修改、主动历史分发等调用 |
| 切换时两个消费者/发送器同时工作 | 重复入库、错绑账号、误发或未知结果重试 | 同一账号一次只允许一条正式采集写链；发送关闭；旧 outbox/bridge 未知结果保留，不清库、不重放 |

建议的**本地演练预算**：元数据每页 25 条，单并发、请求间至少 2 秒，一批最多 10 页；媒体单并发、一批最多 5 条，每条只尝试一次。它只是便于审查、缩小负载的起点，不是 WAHA/WhatsApp 的安全上限。同步稳定前不轮询全历史；预算耗尽、限流、鉴权/绑定变化、冲突、磁盘不足时停止并保留游标，下一批由人决定。现 reconcile 没有实现这些逐页限速，不能直接照搬生产 worker。

这个预算只约束自己的消费者，**不约束引擎首次 fullSync、内置自动媒体下载或内部重上传请求**。首次连接前必须从指定镜像核对这些默认行为及可用控制；不能确定后台负载、容量和停止水位时不开始。NOWEB/GOWS 不运行浏览器可以减少资源消耗，但不构成放大并发或放宽账号风险判断的理由。

## 5. A/B/C 比较、切换与回滚

| 方案 | 历史补采与媒体 | 改动规模/主要风险 | 老板扫码次数（正常情况） | 推荐位置 |
| --- | --- | --- | --- | --- |
| A：继续 WEBJS，只保实时 | 保留已到 bridge 的实时文字/媒体；当前 API 故障导致缺失历史无法补，旧媒体按消息恢复受阻；收件箱历史也可能失败 | 引擎不变；要明确显示补采暂停/覆盖缺口。不能把归档存在当作页面可读或历史齐全 | 0；原会话失效时才另行处理 | 当前保护现有链路 |
| B：切 NOWEB | 启用 store 后可分页取实际同步历史；媒体可下载/请求重上传，但不保证旧附件恢复 | 中到大：新会话、历史上下文证明、ID/LID、webhook、媒体URL、页面读取与身份衔接；初始同步与账号风险 | 备用号演练 1 次；批准生产迁移时业务号另 1 次。若另外比较 fullSync 两种设置，再新增一次测试扫码 | 首选演练，验收后再决策 |
| C：切 GOWS | 存储历史分页与媒体路径存在；同样不保证全部历史/过期文件 | 至少与 B 相当，还需核对 Go 构建/同步控制；没有证据表明账号风险更低 | 新测试会话 1 次；生产另 1 次。不是复用 NOWEB 扫码 | B 不满足时的备选 |

任何回滚若原 WEBJS 会话仍有效，通常不需扫码；若关联设备被注销/失效，业务号还需再扫 1 次。不能保证“切回必定零扫码”，也不应为回滚预先退出原会话。测试会话删除重建、配置重测或手机要求重配，会增加扫码次数，先由老板决定是否继续。

先选 B 演练的理由是已有明确的首次 store/fullSync 配置和可核对的 Node 存储链，便于限制验证范围。C 还需补 Go 组件与同步控制证据；这个选择是当前证据和改造成本的判断，不是引擎安全性或长期优劣排名。

### A：保留现状

步骤：不改引擎/会话和现有实时 webhook；总指挥确认实时入库及归档心跳，单独展示“历史补采暂停：引擎读取失败，等待修复或备用号演练”。若要暂停反复失败的补采调度，只由总指挥按真实单元/任务清单处理；本轮不提供猜测的服务命令、不改调度。已归档媒体可按既有安全链读取，但还需核对控制台候选是否实际部署。

回滚：本轮 A 没有配置变更，无生产回滚动作。后续若仅改补采调度，恢复总指挥保存的原调度配置，仍禁止对未知发送结果重试。代价是历史缺口继续存在，需要持续标记，不能显示“全部同步成功”。

### B：NOWEB 生产迁移的前提与顺序（仅供总指挥评审）

1. 先完成第 6 节，保留指定镜像摘要、兼容性清单、实际覆盖和缺失报告；准备所需 bridge/reconcile/媒体候选补丁及各自基线 SHA、隔离测试、部署和回滚文档。本文没有实现这些改造。
2. 总指挥制定维护窗口，记录真实镜像、会话与服务依赖，准备私有备份区，先保存非运行状态的部署定义。列出待备份的会话目录 `/var/lib/waha221/sessions` 及 bridge/媒体/补采状态；凭据和会话备份不显示、不进仓库，不将会话复制给新引擎。维护前确认旧队列无待自动发送动作，保留所有未知结果。
3. 发送保持关闭；先暂停旧补采及相关写者、安全停止旧 WAHA，随后制作会话与数据库的一致备份，核验可恢复后才能启新引擎。不能直接热拷贝浏览器资料或漏掉 SQLite 的 WAL；由总指挥按实际存储使用备份 API 或完整停写快照。不能让业务号双引擎同时作为正式采集源。不调用旧会话 logout、不删原目录；新引擎使用完全独立的空会话数据区与已审核配置。新链还需独立 bridge 配置、账号绑定及状态库/明确的会话命名空间，旧库只读保留；另容器即使也叫 `default`，也不能误用旧 binding、补采游标或媒体队列。控制台与媒体读取源在验收后按已审核配置一并切换，不能新旧混读造成串号。
4. 首次扫码前定好 NOWEB 的 store/fullSync。管理员配置新会话并核对登录页显示目标会话/引擎，老板用业务号扫码一次；如 Core 在指定镜像有会话名约束，以离线证据为准，另容器的同名会话也必须使用独立目录，不能指向原会话。
5. 先确认账号绑定、实时链路，再等待历史趋稳，按预算读取历史元数据，逐批媒体归档。身份、ID、LID、分页、签名或媒体闭集不符立即停止。显示实际历史范围与失败数，不冒充完整恢复。
6. 总指挥完成统一只读演练、既定全页面验收后再决定正式接管；本任务不运行 e2e、不开放发送、不做任何客户测试。切换窗口遗漏的消息列为待补，不声称回滚能自动补齐。

回滚：停新消费者与新引擎，保留其新增消息、媒体及来源证据；恢复原镜像/部署定义、原 WEBJS 会话数据区和原采集配置，只启原正式链路。先检查状态，旧关联仍有效则零扫码，否则由老板扫码一次。**不要用旧业务库快照覆盖切换后新增记录，不清空 outbox、媒体队列或未知结果，不把新引擎历史重放为实时事件。** 后续对缺口做有来源的人工核对和幂等补采。

### C：GOWS 的差异步骤

沿用 B 的维护、备份、单链路、身份核对、页面和回滚顺序，但使用全新 GOWS 数据区和新扫码，不复用 NOWEB/WEBJS 会话。先离线确认该摘要内 Go 组件版本、存储配置和历史控制能力，再用测试号验证 gRPC 历史、LID、媒体和群方向。没有匹配 2026.9.2 的证据就不套用在线文档的实验配置。若同步规模不可控或兼容性不达标，停在测试环境；回滚恢复原 WEBJS，扫码规则与 B 相同。

## 6. 隔离演练：先离线，后由老板决定是否提供备用号

### 6.1 资源与隔离

| 所需资源 | 要求 |
| --- | --- |
| 测试身份与手机 | 一个老板明确同意使用、可接受关联设备/账号风险的备用号，不能是业务号。最好已有纯虚构历史及图片、视频、文件、群消息；手机可在线、有足够电量 |
| 样本 | 使用备用号现有的纯测试记录，由老板在手机本地做数量/时间/媒体可用性清单。没有相应旧样本就记“该项未验证”，不为补样本安排任何发送、加人、建群或共享历史 |
| 隔离计算资源 | 优先单独测试主机/虚拟机；固定镜像摘要，有 Docker 权限。若只能同机，由总指挥核对资源与端口，使用独立容器和动态宿主 loopback 端口，不复用生产容器/服务名、生产网络模式或固定服务端口 |
| 私有磁盘与数据库 | 全新 0700 测试目录，空会话数据区、媒体区、临时 SQLite 和测试专用配置；不挂任何生产目录/库/NAS，不升级 archive，不开 WAHA 内部运行库。容量先评估并设置停止水位；不足则不开始 |
| 接收与观测 | 先 fake WAHA/假接收器离线校验；真正测试 webhook 只到专用隔离接收器。关闭所有发送插件、机器人/自动回复和外部应用集成，禁止接到生产 bridge 或生产 webhook |
| 凭据与访问 | 由管理员在私有环境配置测试专用凭据，不读取/显示/复制生产凭据。浏览器只本地访问测试界面；QR 只瞬时展示不落盘、不截图、不入日志 |
| 人员与时间 | 老板一次测试扫码；总指挥负责环境、预算、停止/回滚与结果审查。首次同步耗时由实测决定，没有固定完成时限承诺 |

没有备用号或样本时，第 6.3 节保持未执行。不要改生产会话、网络或用业务号临时顶替。

### 6.2 离线镜像检查脚本（本轮因权限未执行）

由有权限的总指挥运行。脚本只检查已存在镜像，不拉取、不启动 WAHA、不加载任何会话、不发布端口、不联网、不读配置和环境变量值。首次仅列候选源码/文档路径；之后按列表定点读取公开源码，核对构建、DTO、控制器和引擎实现，不 dump 容器环境或整个镜像文件系统。

```sh
#!/bin/sh
set -eu
r7_image='devlikeapro/waha@sha256:280bdc8f860267a2488a45d00ec88529646f7468c0bf0283c595d6efdd1b5e3b'
docker image inspect --format '{{.Id}}' "$r7_image"
docker run --rm --pull=never --network=none --read-only \
  --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=32 \
  --entrypoint sh "$r7_image" -c '
    find /app -maxdepth 6 -type f \( \
      -iname "*swagger*" -o -iname "*openapi*" -o \
      -name "package.json" -o -name "waha.config.json" -o \
      -name "sessions.dto.js" -o -name "chats.controller.js" -o \
      -name "lids.controller.js" -o -name "session.noweb.core.js" -o \
      -name "session.gows.core.js" \
    \) -print
  '
```

镜像可能动态生成 OpenAPI，找不到静态 JSON 不代表端点不存在；在不启动应用的条件下读 Nest 的路由装饰器/DTO 及编译源码。若 `/app` 路径或工具不同，先核对镜像公开工作目录和构建定义，不能顺手启动入口来“试一下”。记录是否包含 NOWEB/GOWS、Core 限制、历史/媒体/LID 路径与配置，再与第 2 节固定源码比对。缺权限、缺镜像或版本不符就停止。

### 6.3 NOWEB 连通演练顺序（本轮未执行）

以下只有方案，没有自动连接/扫码脚本，也没有发送命令；资源和账号由老板决定提供后，另由总指挥安排执行。

1. **离线预检。** 使用虚构事件、假 WAHA、临时 SQLite 校验 webhook 签名/绑定、ID/LID 未命中、群方向、历史与实时乱序、重复消息、媒体摘要、穿越/任意 URL 拒绝、500/超时停止。不能直接运行固定私有路径的 `private_history_entry.py` 或生产 worker。
2. **环境确认。** 全新测试目录/空会话；端口动态 loopback，生产路径零挂载。核对发送/应用集成关闭，下载器只有被审核的 GET 路径；测试接收器只写临时库，单消费者与事务。对外发布界面、代理/DNS/路由调整都不在本方案内。
3. **创建测试会话前定配置。** 先离线核对后台同步、自动媒体下载的默认行为和可控范围，定好容量/停止水位；消费者页数不能限制引擎内部负载，无法控制就不开始。引擎选择 `NOWEB`；首次建议 store 启用、fullSync 启用，用一次扫码评估可获得的较深历史。若老板只同意较小同步范围，可事先选 false，但不能把浅范围结果当成深历史测试。这个决定必须在扫码前完成。

   配置片段只表示已核对的引擎配置，不是完整部署配置，不包含密钥：

   ```json
   {"config":{"noweb":{"store":{"enabled":true,"fullSync":true}}}}
   ```

4. **老板扫码一次。** 只扫测试会话。读取状态与引擎、核对备用号身份；未知/FAILED/手机警告时停止并人工检查，不能连续重启或重新扫码。登录页的既有权限与二维码不落盘规则继续生效。
5. **观察初始同步。** 记录开始/观察时间、匿名计数、资源使用和状态。WORKING 只说明连接，不代表历史同步完毕；固定间隔观察同一小范围计数/时间是否仍在变化。多次趋稳仅是经验停止条件，报告保留“覆盖不保证”。不查询所有陌生联系人、不请求在线状态、不标已读。
6. **小批历史读取。** 用指定镜像核对后的 `GET /api/{session}/chats`、`/chats/overview` 和聊天消息 GET；`downloadMedia=false`，按第 4 节预算分页。比较手机样本、最早/最晚时间、offset 与时间过滤；重读同页、跨页搭接必须幂等。同步中返回空页只标“当前 store 无可见数据”，不能写全历史完成。
7. **身份与群核对。** 仅对样本已有 LID/普通号查询已知映射；未命中保留原标识。检查群 chat/member/fromMe、完整 ID 和历史来源。没有 LID/群样本就明确未验证，不主动联系或加入真实群。
8. **媒体分开验证。** 先验证已有归档字节主路径。对样本中手机本地确认存在的图片/视频/文件，各选择少量单条，经过精确消息 GET，再按同源 `/api/files/` 路径取字节，校验大小、MIME、SHA-256 与重复归档。过期媒体恢复失败保留元数据，不循环请求、不重新扫码。单个 GET 可能在上游等待较久，超时后记“下载/重上传结果待核对”；不立即重发下载请求。
9. **断点与重启。** 由总指挥在测试环境做一次正常重启并核查恢复，不 logout、不删会话；记录正常情况下无需再次扫码。用 fake 注入限流、500、超时、绑错号、磁盘不足与并发竞争，不能通过破坏生产网络制造故障。
10. **收尾。** 停测试消费者/容器，保持生产不变。测试号会话、QR 和凭据不进公开结果；按老板决定保留受保护样本或销毁测试数据。报告只包含匿名汇总、版本、覆盖范围、哈希校验是否通过和失败分类，不带聊天、号码、附件、原始 ID、URL 或密钥值。

只读调用范围：状态、二维码（仅登录界面显示）、样本聊天/消息、已知 LID 映射、审核后的本地媒体文件。创建/启动测试会话与扫码属于有状态操作，必须由总指挥单独执行。没有消息发送、标已读、presence、建群、入群、广播、共享群历史或主动历史分发步骤。

### 6.4 验收与转生产门槛

| 验收项 | 通过条件 | 不能通过时 |
| --- | --- | --- |
| 历史与稳定性 | 能证明样本历史实际入 store、分页/时间范围有效，历史不冒充实时，重启可恢复 | 标明最早时间和缺口；不得写“完整补采” |
| 幂等与来源 | 同页/搭接/实时与历史重复只保留一个已证实身份，多来源可追；跨 session 不误合并，冲突隔离 | 先修候选，不接生产库 |
| 媒体 | 归档优先；新下载校验与安全闭集通过；损坏/过期/缺失有确定原因且不无限重试 | 记录失败类型，不能用成功的文字读取代替媒体验收 |
| LID/群 | 已有样本映射有证据，未知保持未知，群方向/成员不串；无样本明示缺测 | 不迁移对应身份关联 |
| 页面与登录 | 新引擎状态、二维码、聊天/历史/媒体显示与中文错误正确；链接卡片退化已说明 | 不以 API 200 当页面可用 |
| 账号与零误发 | 没有手机账号警告；所有测试组件发送调用数为零，无发送插件/生产 webhook 连接 | 立即停；“无警告”也不承诺将来不封号 |

建议观察至少一个完整业务日再评审；这是项目观察窗口，不是平台安全保证。备用号试验只能证明备用号样本的兼容性，业务号历史范围和实际媒体成功率仍需在获批迁移后另行验收。

## 7. 本轮测试、交付与安全自查

本轮唯一新增文件为 `docs/whatsapp-engine-options.md`；没有业务代码或独立脚本文件，离线脚本位于第 6.2 节。没有修改 README、master-plan、requirements、路由、导航、`scripts/rehearsal_server.py`。该快照 `.git` 不提供有效 Git 工作树，未 commit/push。

### 7.1 回归命令与结果

先执行 `sudo -n -u imgw true` 检查按任务书身份运行是否可用，原始失败如下；因此后续直接用当前 `imgw` 身份运行相同测试环境，没有调整 sudo/权限：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

首次按快照原样运行：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests/channels \
  OPS_MEDIA_KEY_PATH=/tmp/r7e-baseline-media.key \
  /opt/imgw/venv/bin/python -m pytest tests/channels -q -p no:cacheprovider
```

退出 2，收集失败，属于既有 WG-13 缺依赖；原始错误与汇总摘录如下，没有把这次当作通过：

```text
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/channels/test_adapters.py
ERROR tests/channels/test_api.py
ERROR tests/channels/test_whatsapp_login.py
ERROR tests/channels/test_whatsapp_login_api.py
!!!!!!!!!!!!!!!!!!! Interrupted: 4 errors during collection !!!!!!!!!!!!!!!!!!!!
4 errors in 0.51s
```

按 [wamedia.md](wamedia.md) 已有办法，仅把 `/srv/imgw-dev/ops/src/console/media/{__init__,tokens,files,alibaba}.py` 四份公共源码复制到 `/tmp/r7e-support/console/media`，不复制配置、业务库或任何凭据。临时 `sitecustomize.py` 扩展 `console.__path__` 并令 `socket.socket.connect/connect_ex` 抛错，覆盖测试及 Python 子进程；媒体 key 路径只在 `/tmp`。仓库没有补入他人模块。

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests/channels:/tmp/r7e-support \
  OPS_MEDIA_KEY_PATH=/tmp/r7e-support/media.key \
  /opt/imgw/venv/bin/python -m pytest tests/channels -q -p no:cacheprovider

env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests/wamedia_bridge:/tmp/r7e-support \
  OPS_MEDIA_KEY_PATH=/tmp/r7e-support/media.key \
  /opt/imgw/venv/bin/python -m pytest tests/wamedia_bridge tests/wamedia -q -p no:cacheprovider
```

| 范围 | 本轮实际结果 |
| --- | --- |
| 渠道、登录与演练保护 | `193 passed, 14 subtests passed in 12.25s`，0 failed |
| bridge/媒体候选与媒体归档 | `72 passed in 14.95s`，0 failed |

文档内三组 shell 代码块均经过 `/bin/sh -n` 语法检查，0 失败；这只检查语法，未执行其中的镜像脚本。相对文档链接存在。

这些测试只验证既有代码在虚构事件、fake WAHA、临时 SQLite/媒体字节上的契约与隔离。部分测试只读导入 `/opt/imgw/whatsapp/bridge.py` 或在临时副本应用既有补丁，调用的配置、数据库与传输全部为假。**265 项通过和 14 个子测试通过不代表 NOWEB/GOWS 已实测，也不代表真实数据可用。** 没有启动固定端口服务、跑 e2e、构建前端或运行生产补采/下载。

### 7.2 共享文件、部署与回滚

**需要总指挥合并的共享文件改动：本轮没有必需改动。** 若记录需求进度，由总指挥在账本写“引擎研究完成；NOWEB 备用号演练、指定镜像核验和生产迁移未做”，不能把 H4/WG-01 标成真实历史已补齐。WG-13 的公共依赖仍由快照负责人统一补齐；本轮不代改。

文档交付无需生产安装、服务重启或数据库迁移。总指挥审阅后只把本文件纳入开发副本；回滚仅移除本新增文档或恢复其审阅前版本，不触碰生产。未来 B/C 的执行前提、维护/回滚顺序见第 5 节，届时另交准确补丁、部署目标及更新后的基线 SHA；不能把本文当作已批准的生产运行脚本。

### 7.3 安全与未完成项

- 越权：没有新 API 或权限变更；只读参考源码，没有读运行凭据/客户库。后续沿用认证、CSRF、绑定、审计与最小调用清单。
- 路径穿越/注入：没有新增文件下载或 SQL 执行逻辑。方案保留同源 URL、禁跳转、digest 文件名、逐级禁链接与参数化 SQL，不通过放宽校验适配新引擎。
- 泄露：仓库只放公开资料、源码哈希和虚构测试结果；没有客户聊天、附件、订单、价格、号码、会话凭据或 QR。测试临时 key 不读取/打印、不入仓库。
- 重复发送：本轮无真实渠道连接、无真实发送调用或真实发送重试；既有发送回归只调用假服务。方案没有任何向客户/供应商/群发送的步骤。原 outbox/bridge 未知结果与持久幂等记录必须保留，切换/回滚不回放。
- 未完成：指定镜像内 Swagger/二进制核查因 Docker 权限未做；没有备用号扫码、NOWEB/GOWS 连通/历史/媒体/LID/群实测，没有改造或部署 bridge/reconcile/登录页，没有业务号完整性、账号安全或页面真实验收。这些是下一阶段的明确门槛，不由本轮测试通过替代。

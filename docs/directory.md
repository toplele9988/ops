# F2 / F2b 微信通讯录总览与只读名称刷新

这是后端开发候选，尚未部署。目录用于解释“通讯录中见过这个群”和“系统已经接入、归档并投影这个群的消息”之间的差别。本次不修改微信采集配置、发现库、消息库、roles 或前端，不实施任何接入、发消息、补采或授权变更。

## 现有接入机制：源码核查

下述为 2026-10-29 只读核查本机源码得到的机制，不代表已验证现场授权值或接口当前可用性。没有调用生产接口，也没有导出运行配置中的身份、凭据、授权内容或业务正文。

| 环节 | 入口与作用 |
| --- | --- |
| 联系人发现 | `/opt/imgw/wechat-contact-discovery/launcher.py` 的任务调用联系人只读接口，并结合已有回调更新自己的发现库。它不向接收器登记群，不写采集配置，也不发送消息。 |
| 原生回调发现新群 | `api_groupbot.py` 调用 `group_onboarding.ingest_candidate()`，先校验账号、时间、关闭状态和已有绑定。未知群仅进入有期限的候选，不因群名或任意群成员的一句话获得工作权限。 |
| 明确指定用途 | `group_onboarding.request_private()` 接收已授权工作私聊内的准确群名与用途；群内用途声明也必须来自已有权限的操作者。候选必须已核验，同名、未发现、过期、停用或已有用途冲突会拒绝。 |
| 已有群设置 | `owner_control` 完成真实发送者与工作入口认证后调用 `group_management.handle_group_command()`。启用新接入流程后，旧命令有额外限制，不能跳过候选确认去启用新群。 |
| 持续采集授权 | `auto_group_collection` 只有在新群接入、自动采集及只采集策略均已授权启用时工作。来源必须是授权时点后的新鲜原生回调，或可信调用方显式登记的新鲜通讯录元数据。 |
| 写入与启用 | `group_onboarding._activate()`、`auto_group_collection._activate()` 以及旧群设置路径通过 `group_management` 的锁与原子写入函数更新 `api_config.json` 的 `groups`。先写关闭的暂存项，建立独立记忆绑定，再复核群名、成员、权限与配置版本，最后启用；失败保留关闭状态。 |

明确指定用途的流程限定原有两名授权操作者，且仍须符合现有工作入口权限。成员核验要求助手当前仍在群内，并有授权操作者在群内；不会按昵称、短码或文字内容推断操作者身份。新群的客户、供应商和学习用途默认只收集分析；内部工作入口有单独的权限与绑定检查。

`standing_group_collection` 是持续采集来源标记。它的权限来自老板既有的持续采集授权，群名 SUP/CUS 等只提供分类线索。新鲜回调不会自行产生授权。自动路径会两次核对准确群及助手成员身份，分配独立记忆，保存来源与状态审计；不会重开普通已停用群，不导入旧消息正文，也不因自动采集扩大操作者权限。它还检查队列暂停、来源有效期、配置变更与上限。

`auto_group_collection.register_saved_metadata()` 是会写入接收器候选与来源记录的入口，不是纯读取函数。核查的正式代码中未找到发现任务调用该入口；函数存在不能证明全部发现群已接入。F2 刷新只更新自己的缓存，**绝不调用这个登记函数或任何接入 worker**。

另有 `video_knowledge.naming_rules` 中老板授权的命名规则路径，可为候选生成用途请求及接入通知。因此复用整套接入流程可能产生配置写入、数据库写入和待发送通知；不能把它当“只读刷新通讯录”。F2 复用 `console.roles.triage.named_kind()`、`rules()` 与 `party_name_parts()` 的纯命名识别逻辑；关键词兜底给空消息、空业务关联，不运行模型或生产接入规则。

### 锁、并发与启用判断

`group_management._lock()` 使用与配置同目录的 `api_config.json.groups.lock`，以 `O_NOFOLLOW` 打开普通私有锁文件，取得独占 `flock`，等待最多约 3 秒。写入先在同目录建立私有临时 JSON、验证完整配置、刷新到磁盘，再比较原配置是否仍为预期版本，最后 `os.replace()` 原子替换。绕过该锁直接修改 `groups` 会与现有 worker 竞态，且可能留下不完整的记忆绑定。

外部群完成接入后通常 `enabled=true`。历史内部工作群可能保持 `enabled=false`，由 `collection_policy.group_view()` 在额外工作入口、时间及独立绑定校验后形成可采集运行视图。F2 获准读取的群字段只有 id、`enabled`、`business_role`、`auto_collection_source` 和可选 `group_name`，不能读取额外权限配置来完整推断运行授权。因此“登记”“启用标记”“有历史消息”“最近收到消息”必须分别理解；最近有消息也不证明此刻在线、持续无漏采或所有权限仍有效。

## 联系人接口与刷新边界

已核对 `/opt/imgw/wechat/wtapi_client.py` 的真实只读签名：

```python
WTAPIClient.fetch_contacts_list(app_id)
WTAPIClient.get_brief_info(app_id, wxids)
WTAPIClient.get_chatroom_info(app_id, chatroom_id)
```

联系人列表经 `POST /finder/v2/api/contacts/fetchContactsList` 读取元数据，成功结果为整数 `ret=200`，`data` 含字符串数组 `friends`、`chatrooms`、`ghs`。该接口本身不返回名称。`get_brief_info` 仅接受 1–50 个不重复的准确群 ID，不支持拿它查询个人好友；群详情接口只接受一个准确群 ID。F2b 先读一次联系人列表，再按最多 50 个群一批补名称；不逐群拉详情、不补好友详情。

2026-10-30 静态核查现行调用方（没有读取实际凭据或发出请求）：

- `/opt/imgw/wechat-contact-discovery/launcher.py` 调用 `group_onboarding._client()`。配置的 `app_id` 是请求账号；凭据通过 `token_file` 定位，支持相对配置目录及 `expanduser()`，以 `O_NOFOLLOW` 打开私有普通文件，最多 8192 字节，去除首尾空白，拒绝空值及内含空白的凭据。`api_groupbot.py` 也使用 `token_file`。没有发现内嵌 `token` 用法，因此不猜测这个字段、不兼容它。
- `/opt/imgw/wechat/group_management.py::_resolve()` 读取 `ret=200/data`，联系人里的 `chatrooms` 分成最多 50 个 ID；简要信息的 `data` 是对象数组，`userName` 必须对应请求 ID，`nickName` 是群名。详情使用 `chatroomId/nickName`，但本功能不调用详情接口。
- `/opt/imgw/wechat-contact-discovery/discovery.py::apply_brief()` 明确解析 `nickName → nickname` 和可选 `remark → remark`，允许部分返回，拒绝额外 ID、重复 ID、非文本、首尾空白、超过 128 字符及控制字符。F2b 复用这些字段和校验方式，缺字段保留未知，不猜 `displayName`、`nickname` 等别名。显示优先用昵称、再用备注，两者独立保存；无效批次整批不保存名称。

`directory/wtapi_source.py::build_readonly_refresh_client(config_path=...)` 只保留账号及凭据，不导入接入 worker。它固定使用现有管理接口限定的 `https://wx.chuapi.com`，不采纳配置中的任意远端源；单次客户端超时为 20 秒。配置只读，不复制、不改权限。构造失败使用固定中文 503，不附源错误、路径内容或凭据。真实 `WTAPIClient` 延迟导入，缺模块时明确提示检查 `sys.path` 中的 `/opt/imgw/wechat`。返回对象的公共属性白名单只有 `fetch_contacts_list()` 和 `get_brief_info(wxids)`，没有底层客户端句柄、凭据、发送、下载、详情或管理方法；这是应用接口限制，不是防 Python 反射的沙箱。

现有客户端把管理元数据请求限定到固定 HTTPS 源，禁止代理与重定向，响应最多 1 MiB，单次总超时最多 20 秒，不自动重试。超时、传输错误或畸形响应可能都返回 `None`，此时必须报告“结果不明”，不能声称已刷新成功，也不能盲目重试。

本机服务商快照 `/var/lib/imgw/wechat/reference_docs/wtapi/20261012/pages/347297154e0.md`、`347297155e0.md` 说明列表是长耗时接口，耗时随好友数量增加，结果缓存约 10 分钟。群列表只包含保存到通讯录的群；未保存的群可能通过正常消息回调才被发现。故目录并集不等于微信账号全部群，也不能仅凭一个空列表删掉已发现联系人。

核对的这两份本地接口页未标示收费或套餐限制；这不能证明当前账号免费或必然可用。F2b 的生产宿主在通过认证、确认及节流之后才构造只读客户端；挂载和 GET 不构造客户端。缺配置、缺凭据或缺模块仅影响刷新，本地总览仍可读取。如果未来核实套餐或账号限制不允许调用，应继续使用发现库，不试探、付费开通或修改权限。

## 为什么分拣员看不到未接入群

`console.roles.inputs.Inputs` 从现有统一收件箱读取会话。微信会话来源是消息归档与消息角色投影，不读取联系人发现库。一个仅出现在通讯录、从未进入消息归档的群没有会话可供分拣。分拣员还会按最新对方消息、消息时效、已关联案子等条件筛选，因此“分拣出多少个 SUP 群”不是“微信共有多少个群”。

微信适配器默认投影客户、供应商、未分类群；学习、内部等角色被隐藏。收件箱列表默认再只显示客户及未分类，供应商须选择“同时显示供应商群”对应的 `role=all` 或 `role=supplier`。命名线索和配置角色都不直接替代归档消息的 `counterpart_role`。个人聊天和公众号尚未由当前微信收件箱适配器提供会话投影。

## 下一步接入的建议安全流程

1. 老板先按准确群 ID 核对目录中的名称、用途和冲突，确认仅接入哪些群，是否只收集新消息与附件，以及内部群和资料的隔离范围。目录发现和 SUP/CUS 名称本身不是授权。
2. 对指定群走现有授权操作者的候选接入流程；若要持续纳入新群，应单独确认持续采集范围、规则、生效时间和关闭办法。不得因已有通讯录权限推定具有采集、工作入口或发送权限。
3. 在独立后续任务中准备脱敏变更预览、备份和回滚方案；复用现有锁、成员核验、独立绑定、暂存与最终复核机制。禁止直接批量拼写 `groups` 绕过检查。
4. 接入后通过正常业务消息核对新消息、角色投影及收件箱可见性。历史未收到的消息不因接入自动补齐。未知写入或发送结果先核对审计与实际状态，不能盲目重复。

F2 不提供“全部群目录”给 roles 的新入口、不修改分拣逻辑、不实现接入写入，也不执行上述后续流程。

## 数据来源与口径

服务为 `console.directory.WechatDirectory`，默认读取以下来源：

| 来源 | 仅读取与使用 |
| --- | --- |
| `/var/lib/imgw/wechat/contact-discovery-v1/index.sqlite3` | `identities` 的 `identity/kind/first_seen/last_seen`，不返回账号指纹或原始分类记录 |
| `/var/lib/imgw/wechat/api_config.json` | 总览只投影 `groups` 下每个群的 id、`enabled/business_role/auto_collection_source/group_name`；刷新适配器另读取 `app_id/token_file` 来构造只读客户端，不输出凭据或其他配置 |
| `/var/lib/imgw/wechat/api_state.sqlite3` | 群消息数量、时间、现有群名，以及现有微信适配器的有效会话和消息角色投影；不在目录输出消息正文、附件或身份授权数据 |
| `/var/lib/imgw/ops-app/wechat-directory.sqlite3` | 独立缓存保存 id、kind、首次/最近发现时间、`nickname/remark/name_updated_at` 及刷新预约时间；不保存原始响应或凭据 |

所有来源库使用 `mode=ro` 和 `PRAGMA query_only=ON`。GET 不创建库、迁移或生成文件。复用微信适配器时显式关闭媒体令牌生成，避免附件读取意外创建媒体密钥；原收件箱保持已有行为。缓存路径不得指向三个来源文件，刷新拒绝符号链接、硬链接和非目录缓存库。未提供远端读取函数时不会创建缓存。

目录按 id 合并以上来源，名称优先级为配置 `group_name` → 刷新缓存（`nickname` 优先、`remark` 兜底）→ 已有会话/群名称元数据 → “未命名群”（个人和公众号分别为“未命名联系人”“未命名公众号”）。行上的 `name_source` 标明来源。采用缓存名称时返回 `name_updated_at` 及 `name_update_label`（“名称更新于 YYYY-MM-DD HH:MM:SS UTC”）；列表和摘要还返回已展示缓存名称的最新更新时间。联系人列表本身没有名称，个人和公众号不额外请求详情，未知名称仍明确显示未知。`first_seen/last_seen` 是发现、缓存和消息中可用观察时间的最早/最近值；配置没有时间时可为 null。`last_message_at` 单独表示消息时间，均为 Unix 秒。

刷新先保存联系人列表，再从发现库与缓存并集选取没有配置名、没有有效缓存名的群，因此不在本次保存联系人列表中的已发现群也能补名。已有会话名不阻止补名，它仍是失败时的回退。当前只补缺失名称，已有缓存名称不重复查询；其获取时间不会因再次拉联系人列表而伪装为刚更新。旧版四列缓存可直接只读显示，新增列只在确认刷新后的事务中迁移；空返回不会删除旧名称或身份。

`registered` 表示该群是否存在于 `groups`，`enabled` 原样显示已核对的布尔标记。角色和启用信息未知时不猜测。四种已知状态：

| collect_state | 判定 |
| --- | --- |
| `collecting` | 已登记且启用，近 7 天内有已归档消息，包含恰好 7 天边界 |
| `enrolled_silent` | 已登记且启用，有历史消息但近 7 天无消息 |
| `enrolled_no_messages` | 已登记且启用，消息归档中从未出现 |
| `not_enrolled` | 本次成功读取的群登记中没有该群；仍可能存在历史消息 |

采集配置/消息来源不可读、启用标记不明、明确关闭，或消息时间无法判定时，`collect_state=null` 并附中文原因。明确关闭的内部群也保持未知，不能仅靠允许字段确认独立工作入口权限。摘要在 `by_state.unknown` 单独计数，`registered` 总数仍包含这些已登记项。未来时间不计入近 7 天；若同时存在正常的近期消息，仍依据正常消息判定。

私聊与公众号可查看发现记录和名称，但本次获准读取的是群登记和群消息来源，不能核验它们的完整授权或独立消息来源。因此其 `registered/collect_state/n_messages/has_messages/last_message_at` 为 null，`message_scope` 和状态原因明确说明未核验；不会把未知误报成零条消息或未接入。

群消息数量按 `(app_id, group_id, message_id)` 去重；无效消息时间仍可计入归档条数。收件箱投影还校验消息内容和身份，因此归档有记录不保证已有有效会话。收件箱角色沿用 `message_scopes` 众数（平票按角色字典序），不会被目录的配置角色或名称猜测覆盖。

每行同时给出 `config_role`、`named_role`、`name_codes`、`counterpart_role`。每次总览在合并名称之后重跑纯命名规则，因此刷新后未接入群也会得到 `named_role_hint`，如“像是供应商群（按名称识别）”，摘要 `not_enrolled_suspected` 同步变化。命名类型包括 supplier/customer/forwarder/vendor；后两者在人话中显示“快递/物流/货代”“配套供应商”，汇总归入供应商。明确配置角色与名称识别的业务方向不符，或名称同时含 SUP/CUS 时标 `role_conflict=true`；未分类不是明确角色冲突。`role`/`by_role` 优先取配置角色，其次有效消息角色，最后命名推断；未登记疑似供应商/客户数量始终按名称推断单独统计，不构成采集授权。

`inbox_visible` 指默认客户视图，`inbox_visible_with_suppliers` 指 `role=all` 视图；按默认宿主的适配器角色集合判断。宿主若自定义该集合，应向服务传同样的 `include_roles`。个人和公众号不显示；无有效会话不显示；供应商需打开“同时显示供应商群”；学习/内部默认不进入适配器。每行有中文 `inbox_visibility_reason`。具体搜索词、待回复或休眠筛选不包含在此可见性承诺中。

## API 与宿主挂载

```python
from console import v3_directory_api

routes += v3_directory_api.build(app_module)  # 生产默认惰性构造只读适配器
# 测试或已有宿主可注入两方法只读对象：
# routes += v3_directory_api.build(app_module, readonly_client=fake_readonly_client)
```

默认 builder 传入惰性工厂，使用服务解析后的 `config_path`，只有刷新时才调用 `build_readonly_refresh_client()`。直接构造 `WechatDirectory` 仍须显式注入，不会意外访问远端。旧 `contact_fetcher` 注入继续兼容，但它单独只能刷新 ID，缺群名接口会在部分成功结果中说明。`scripts/rehearsal_server.py` 按现有动态挂载方式自动加入三个路由，缓存指向演练临时目录；假鉴权演练设置 `allow_refresh=False`，POST 返回 403，不创建真实客户端、不调用真实联系人接口。

接口沿用 roles 的 principal、旧 session、recheck、线程池、宿主 CSRF 与表单解析契约。GET 在读取/参数错误后都复核身份；POST 在任何调用前复核。必须挂在宿主 CSRF 中间件之后；路由本身不会把一个 confirm 表单当作 CSRF 防护。响应有 `Cache-Control: no-store` 和 `X-Content-Type-Options: nosniff`，错误不透传源文件异常。

- `GET /api/v1/directory/wechat?kind=group&state=&q=&page=1&page_size=50`：`kind` 默认 group，可为 private/official，空值或 all 表示全部；`state` 为空或四种已知状态；`q` 对名称/id 不区分大小写搜索，最多 200 字符；每页 1–100，按最近观察时间降序、id 稳定排序。返回 `items/total/page/page_size/ready/generated_at/sources/notes`。
- `GET /api/v1/directory/wechat/summary`：返回总数、`by_kind/by_state/by_role/conflicts`、`groups` 的发现/登记/并集/关闭统计、`not_enrolled_suspected` 的供应商与客户计数，以及 `conclusion`。摘要不受列表分页过滤影响。
- `POST /api/v1/directory/wechat/refresh`：URL 编码表单仅 `confirm=1`（CSRF 字段由宿主处理）。先读取联系人列表，再批量补名；返回 `refreshed_at/received/by_kind/message`、`groups_named`（本次取得有效名的群数）、`missing_group_ids`、`group_failures`（准确 ID 列表及固定中文原因）、`complete/notes`。联系人已缓存而补名失败时为 200 部分成功，`complete=false`，保留先前完成的批次；不会误称所有名称已更新。拒绝确认缺失、重复参数、附加业务参数和非表单媒体类型。

群摘要严格区分集合。以虚构测试的 49 个发现群、29 个登记群、交集 28 个为例：合并共 50 个群，通讯录内登记 28 个、未登记 21 个、通讯录外 1 个登记群；不会写出“49 = 29 + 21”。人话结论只称没有历史归档的未登记群“系统看不到它们的消息”，有历史消息的未登记群仍可查看旧会话。来源不可读时返回可用记录及明确缺失说明，不把全部群误报成未接入。

刷新使用独立 SQLite 的 `BEGIN IMMEDIATE` 先预约，跨实例/进程共用 600 秒额度，配置/凭据失败、远端失败也占额度；429 带 `retry_after` 和 `Retry-After`。客户端构造、联系人及所有群名批次共用单调时钟的 25 秒等待预算，不能每批重新获得 25 秒。联系人超时为中文 504，未知或格式错误为中文 502，无重试；群名批次异常/未知/超时则停止后续请求，返回部分成功和未取得的全部群 ID。正在运行的只读请求无法强制终止，但迟到结果不会落库，也不会触发下一批请求。SQLite 本地保存另有最多 5 秒锁等待。不会删除旧联系人，也不会将仍在通讯录里解释成“仍是群成员”。新缓存文件为 0600，新建私有目录为 0700；已有目录权限不修改。

## 注入与离线验证

`WechatDirectory` 可注入四个路径、`clock()`、`readonly_client` 或零参数 `readonly_client_factory()`、兼容的 `contact_fetcher()`、`refresh_timeout`、`include_roles`，以及读取函数 `discovery_reader(path)`、`config_reader(path)`、`state_reader(path, now)`、`conversation_reader(path, now)`。只读客户端只有零参数 `fetch_contacts_list()` 与 `get_brief_info(group_ids)`；读取函数返回允许字段投影，替代实现须遵守同样的只读与脱敏边界。非默认来源路径会将其他未显式提供的来源隔离到同目录 disabled 文件，避免测试意外读取生产。仅覆盖缓存路径时仍保留生产只读来源，供演练使用。

`tests/directory/` 全部使用虚构 SQLite、虚构敏感字段、假接口与时钟，禁止网络。覆盖四状态、七天边界、未来时间、角色冲突、名称优先级、分页过滤、集合算术、只读哈希/mtime、附件不生成密钥、刷新合并、跨进程节流、失败/超时/迟到结果、鉴权/CSRF/recheck 与演练保护。没有真实联系人接口调用或生产刷新验收。

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest -q tests/directory
PYTHONPATH=src /opt/imgw/venv/bin/python -m pytest -q tests/channels tests/inbox_api tests/roles
```

F2 原有验收为 directory 95 项、channels 190 项、inbox_api 37 项、roles 238 项。F2b 新增适配器、默认挂载及补名测试，覆盖只读属性白名单、凭据错误脱敏、101 群的 50/50/1 分批、旧缓存迁移、名称优先级、虚构 21 个未接入群的命名与疑似统计、跨批共享超时预算和迟到结果。还覆盖损坏的缓存时间不会导致整个总览失败。

2026-10-30 最终回归在四个独立 Python 进程运行；通过临时 `sitecustomize` 封锁 `connect/connect_ex/create_connection/sendto`，保护同样继承该环境的 Python 子进程。directory **189 passed，22 subtests passed**；channels **190 passed，14 subtests passed**；roles **238 passed**；inbox_api **37 passed，99 subtests passed**。合计 **654 项及额外 135 个子测试全部通过**，directory 比 F2 新增 94 项。没有真实 WTAPI 请求或生产刷新验收。

本任务不部署，不修改前端需求账本（用户明确要求不改 web），不 commit/push。真实账号的联系人读取权限和套餐可用性仍由后续宿主接入核验；这里的测试结果不是生产验收。

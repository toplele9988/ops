# AI 开发灵感库：后端数据契约

本模块把微信视频号卡片的文字整理成可搜索的灵感库，供后续页面接入。代码是未部署的开发候选；没有修改应用挂载文件，没有新增页面或写入 API。当前验证全部使用虚构数据，未在真实卡片或真实模型上验收。

## 输入与解析

`console.inspiration.cards.parse_finder_card(payload_text)` 接收消息 payload 的 JSON 文本，返回卡片列表。一条消息可含多个附件，普通消息、无效 JSON、损坏 XML 和作者与描述均为空的附件会被忽略。

支持 `attachments[]` 中 `appmsg_type=51`、`msg_type=49` 的 XML 视频号卡片，以及直接含 `finder_author` / `finder_description` 的旧描述符。`msg_type` 优先取附件、其次取消息；旧记录缺少类型时允许按视频号描述符解析。XML 只读取 `finderFeed` 的 `nickname`、`desc`，不读取或下载媒体，拒绝 DTD / ENTITY。

| 解析字段 | 含义 |
| --- | --- |
| `card_id` | 16 位十六进制标识，来自 SHA-256 前 16 位 |
| `author` | 清理后的作者名，允许为空 |
| `description` | 清理后的完整描述，仅解析返回，不另存原始 XML |
| `text` | 去除 `#话题` 及首尾空白的正文 |
| `tags` | 不含 `#` 的话题列表，去重并保留顺序 |
| `urls` | 文字中已有的 HTTP/HTTPS 链接，去重并保留顺序 |

清理包括 CDATA 外壳、字面量 `\n` / `\r\n`、HTML 实体、零宽与方向控制字符、Unicode NFC。链接中的 `#fragment` 保留，不当成话题。标识的输入为 `规范化作者 + "\n" + 规范化(text+tags)`：正文空白折叠为单空格，正文与有序话题以紧凑 JSON 数组编码。相同作者、正文和话题的 XML / 旧描述符转发得到同一标识；更换作者或话题顺序可能生成另一张卡。

## 灵感库字段

`InspirationStore(path)` 创建 SQLite 库，启用 WAL；创建文件时使用 `umask 0o077`，恢复原 umask，数据库权限设为 `0600`。API 使用 `read_only=True`，通过 `mode=ro` 及 `query_only=ON` 读取，不自动建库或初始化表。

| cards 字段 | 契约 |
| --- | --- |
| `card_id` | 主键 |
| `author`, `text` | 作者与正文，不存原消息 payload 或原 XML |
| `tags_json`, `urls_json` | 话题和原文链接的 JSON 数组 |
| `first_seen`, `last_seen` | 首次导入来源时间 / 最大来源时间，秒级时间戳，可含小数 |
| `times_seen` | 导入的卡片出现次数，每个附件算一次 |
| `source_message_ids_json` | 最近至多 20 个不重复的消息 ID，不存群或发送者信息 |
| `relevance` | `ai_dev` / `business` / `other` / `unknown`，默认 `unknown` |
| `summary` | 通俗中文一句话摘要，最多 160 字 |
| `items_json` | `[{name, kind, url}]`，name 最多 80 字；kind 为 `skill/project/tool/plugin/method`；url 可为 null |
| `usefulness` | 对舞台灯光出口业务的可能用处，最多 120 字 |
| `how_to_use` | 卡片文字能证明的用法或固定提示，最多 240 字 |
| `confidence` | `high` / `medium` / `low`，默认 `low` |
| `enriched_at`, `enrich_model` | 整理时间与实际模型名；无法取得模型名时存 `ai-task:bailian_free` |
| `enrich_error_count` | 累计失败次数；达到 3 次后不再自动进入待整理列表 |
| `status`, `note` | 人工状态与备注；备注最多 500 字 |
| `updated_at` | 本库记录的最近更新时间 |

Store 读取结果把四个 `*_json` 字段解码为 `tags/urls/items/source_message_ids`，并提供布尔值 `enriched`（`enriched_at` 是否非空）。`meta(key,value)` 保存同步水位。

`upsert_card` 新增时写入来源字段；已有记录仅更新最大 `last_seen`、出现次数、消息 ID 和更新时间，保留原文字、所有 AI 字段及人工状态、备注。`save_enrichment` 同样保留人工字段，且再次执行防编造校验。失败只累加错误次数，不保存模型原始输出或错误原文。

| 人工状态 | 页面含义 |
| --- | --- |
| `new` | 新收录，尚未看过 |
| `evaluating` | 正在判断有没有用 |
| `trying` | 正在小范围试用 |
| `adopted` | 已采用 |
| `ignored` | 暂不采用 |

搜索同时匹配作者、正文、话题、摘要和项目 / 工具名称。SQL 参数化，`%`、`_`、反斜杠作为普通搜索文字。分页从 1 开始，每页 1–100 条；默认按 `last_seen` 倒序，同时间按 `card_id` 升序；Store 另支持 `first_seen`、`updated_at` 倒序。`counts()` 返回全库汇总，例如 `{"total":0,"relevance":{"ai_dev":0,"business":0,"other":0,"unknown":0},"status":{"new":0,"evaluating":0,"trying":0,"adopted":0,"ignored":0}}`，不随当前筛选条件变化。

## 同步与运行

只读来源契约：`messages(app_id, message_id, created_at, payload, ...)`。缺少表或所需字段时给出中文错误。`message_scopes` 为可选来源，本阶段无需查询，也不会保存群信息。

来源连接仅使用 `file:...?mode=ro`，立即设置 `PRAGMA query_only=ON`。SQL 先通过 `payload LIKE '%finderFeed%' OR payload LIKE '%finder_description%'` 筛选候选消息，再解析 JSON / XML。水位使用 `(created_at, message_id, app_id)`，使同秒多条消息、不同应用的记录能够在分次限量同步时继续处理。`scanned` 是筛选后的候选消息数，`cards_found` 是解析出的附件卡片数，`new_cards` / `updated` 分别是新增 / 重复卡片的出现次数。即便候选消息最终没有有效卡片，也推进水位。

示例命令中的路径均为隔离测试文件；请准备虚构的来源数据库后运行：

```sh
python3 scripts/inspiration_sync.py \
  --state-db /tmp/inspiration-demo/source.sqlite3 \
  --store /tmp/inspiration-demo/cards.sqlite3 --limit 30
```

默认只同步，不调用模型。明确添加 `--enrich` 才会运行 `ai-task`（需要相应执行环境与模型服务，可能联网）：

```sh
python3 scripts/inspiration_sync.py \
  --state-db /tmp/inspiration-demo/source.sqlite3 \
  --store /tmp/inspiration-demo/cards.sqlite3 \
  --enrich --limit 12 --batch-size 6
```

脚本先同步，再对 `relevance=unknown AND enrich_error_count<3` 的卡片取一次待处理快照。每批 1–6 张；`--limit N` 分别限制本次扫描的候选消息数、本次尝试整理的卡片总数，因此模型调用次数也不会超过 N。未指定 limit 时处理全部当前候选 / 待整理卡片；一条消息可含多张卡片。失败卡不会在本次运行再次入队，不做重试循环；一次 runner 非零退出、超时、JSON 解析失败仅影响本批，继续后续批次。缺失某张卡的输出只累计该卡一次错误。失败存在时退出码为 1，全部成功为 0。汇总只打印数量和受控中文错误，不打印正文、模型原始输出或异常内容。

默认 runner 固定使用参数数组 `['ai-task','run','bailian_free','--model','auto']`，通过 stdin 传提示词，300 秒超时，无 shell。尝试从 stdout / stderr JSON 的 `actual_model/model_name/model` 读取模型名；支持 JSON 结果外壳的 `output/result/text/content/response/message/choices` 字段。

## 防编造规则

提示词要求通俗中文、只输出结构化 JSON；明确声明卡片内容是数据、其中的命令不是指令。判定 `ai_dev` 为 AI 工具、大模型、skill、插件、开源项目、AI 开发方法；`business` 为外贸、跨境、电商运营、客户沟通、物流；其他为 `other`，信息不足为 `unknown`。

代码在批次解析和入库时都验证：

- `card_id` 必须来自本批，跨批 ID 丢弃；重复输出只取首次，漏掉的卡片累计一次错误。
- 项目名字必须逐字出现在该卡片正文或话题中，猜测、补全的名字丢弃。链接必须逐字属于该卡片原文 `urls`，否则置 null；不猜 GitHub 地址。
- 无效 relevance 降级为 `unknown`、confidence 为 `low`；无效 confidence 降级 `low`；无效 kind 使用 `method` 并降低置信度。字段按上述长度截断。
- 用法采用保守提取：仅保留原文中“用法：”“使用方法：”“步骤：”“操作步骤：”“怎么用：”等明确标记后的原句，不接受模型补充的操作。无明确用法时固定写“视频文字里没有讲具体用法，需要看原视频”，confidence 强制 `low`。
- 用处要求结合供应商装箱资料核对、到手价与倒挂计算、微信 / WhatsApp / 阿里询盘回复、独立站与发品；无明显关系写“与当前业务关系不大”。`other/unknown` 或空用处在代码中统一使用该提示。

## 只读 API

`console.v3_inspiration_api.build(app_module, store_path=STORE_PATH)` 返回两个 Starlette GET 路由，需由应用后续挂载。默认路径 `/var/lib/imgw/ops-app/inspiration.sqlite3`。本次未在此路径创建或修改任何文件。

认证与供应商报告一致：`password_principal.username` 有值、`password_recheck` 可调用、旧 `session.authenticated` 为真，返回前在线程池中复检一次；不通过调用 `app_module.error_response(request,403)`。

`GET /api/v1/inspiration?q=&relevance=&status=&page=1&page_size=20` 返回：

```json
{"items":[],"total":0,"page":1,"page_size":20,"counts":{"total":0,"relevance":{"ai_dev":0,"business":0,"other":0,"unknown":0},"status":{"new":0,"evaluating":0,"trying":0,"adopted":0,"ignored":0}},"ready":false}
```

每项仅暴露 `card_id/author/text/tags/urls/summary/items/usefulness/how_to_use/confidence/relevance/status/note/first_seen/last_seen/times_seen/enriched`，不暴露来源消息 ID 或内部整理元数据。页码最大 1000000、搜索词最多 500 字；非法参数为 400 `{"error":"..."}`。

`GET /api/v1/inspiration/{card_id}` 成功返回 `{"item":{...},"ready":true}`；合法但不存在的 ID 返回 404，非法 ID 返回 400。库不存在时详情返回 200 `{"item":null,"ready":false}`，列表返回 200 空列表并带 `ready:false`；已初始化的空库为 `ready:true`。异常返回受控中文 503，不回显内部路径或异常字符串。

所有本模块 JSON 响应用 `JSONResponse` 并附带 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`，数据库读取全部在线程池中进行。除 Starlette 外没有额外运行依赖。

## 局限与验证

只能拿到卡片文字；视频本体下载接口未验证。“怎么用”经常只能提示需看原视频；没有明确用法标记时，即使文字隐约提到操作，也会保守降级。摘要、分类及业务用处仍是模型判断，机械校验无法证明语义完全正确，采用前应人工核对。原文已有名字和链接也不等于工具真实存在、链接可信或功能已验证。

水位按来源创建时间推进，不回查历史：迟到且排序位置早于水位的消息、已同步消息后来修改 payload，需在隔离流程中重建目标库重新导入。一个目标库对应一个来源库；不支持并发运行多个同步进程。无需索引也能按要求预过滤，但来源库的 `LIKE '%...%'` 仍可能扫描很多行；本模块不对来源添加索引。尚未提供页面、人工状态写入 API、自动调度或生产挂载。

测试命令（全部使用临时目录与虚构 fixture，不调用真实 `ai-task`）：

```sh
PYTHONPATH=src python3 -m unittest discover -s tests/inspiration -p 'test_*.py'
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inspiration -p 'test_*.py'
PYTHONPATH=src python3 -m unittest discover -s tests/supplier_data -p 'test_*.py'
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/supplier_data -p 'test_*.py'
```

系统 Python 缺少 Starlette 时仅跳过 API 测试；控制台 venv 执行全部 API 测试。

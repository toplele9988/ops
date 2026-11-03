# 多语言：阅读译文与母语草稿

对应 H6、H7。语言功能只生成和显示文字，不能发送消息。开发验收使用虚构会话、假 runner 和临时 SQLite；
测试通过不代表真实模型翻译准确、渠道数据完整或生产已可用。本次未部署，未连接真实渠道。

## 使用方法

在「语言设置」选择「我的语言」，默认为简体中文；默认行为决定会话页是否展开「按客户母语发送」面板。
术语表支持保持原样、指定译法及增删改。内置 DMX、RDM、Art-Net、sACN、beam、spot、wash、moving head、IP65；
品牌可加入保持原样术语。系统还保护字母数字型号、数字单位、金额和链接。

会话顶部可打开「显示译文」，也可对单条消息点击「译」，再切回原文。可临时选择阅读语言，
不修改全局设置。失败消息保留成功的分块，可再次点击只补失败块；缓存命中不再调用 runner。

发送区输入草稿后，生成客户母语版和回译核对稿。数字、型号、金额或链接缺失时显示红色核对提示；
请核对原文、译文和回译，再点击「采用」填入原发送框。这个按钮不会发送。
阿里仍须复制到 Win10 发送；WhatsApp 仍须走原有候选和人工确认流程，结果未知时不能自动重试。

## 联系人语言

联系人语言按渠道与会话键保存，来源为 `auto` 或 `manual`，附置信度和更新时间。
手动锁定优先于自动识别；解除锁定后可重新识别。文字脚本先区分中文、日文、韩文、西里尔文字、阿拉伯文、
泰文、天城文；拉丁文字先用停用词规则，不确定时才请求 AI。
界面提供 56 种目标语言。共享文字脚本不能直接确定语言，例如保加利亚语不能仅因使用西里尔文字就当成俄语，
不确定时交给 AI 或由人确认。
识别对多条客户消息投票，不使用我方消息，也不因一句短回复推翻已有语言。国家信息只给出提示，不确定母语。
母语未知时拒绝生成母语草稿，提示先识别或手动确认。

## 接口与宿主约束

所有接口前缀为 `/api/v1/lang`。所有 POST 使用 URL 编码表单，整体不超过 8KB，字段不得重复。
认证沿用收件箱的账号、旧会话与 `password_recheck`；POST 在读正文或写入前复查，GET 读后复查。
宿主必须保留现有全局 CSRF 中间件。响应附 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。
参数错误为 400，未找到会话为 404，额度不足为 429，暂时不可用为固定中文 503；不返回异常堆栈或模型原始输出。

| 方法与路径 | 用途 |
| --- | --- |
| GET `/settings` | 我的语言、默认行为及可用语言 |
| POST `/settings` | 保存我的语言与默认行为 |
| GET `/glossary` | 读取术语表 |
| POST `/glossary/save` | 新增或修改术语 |
| POST `/glossary/delete` | 删除术语 |
| GET `/contacts` | 已记录的联系人语言列表 |
| GET `/contact?conversation_key=…` | 当前会话语言与国家提示 |
| POST `/contact` | 手动确认、锁定或解除锁定 |
| POST `/detect` | 识别当前会话客户语言 |
| POST `/translate` | 翻译指定消息，保留每块结果 |
| POST `/compose` | 客户母语草稿、回译及程序校验；绝不发送 |

`settings` 表单为 `my_language`、`auto_native_send=0|1`；响应包含 `settings`、`languages` 和 `quota`。
`glossary/save` 表单为可选 `id`、`term`、`mode=keep|translate`、`translation`；删除使用 `id`。
`contact` 表单为 `conversation_key`、`native_language`、`locked=0|1`；解锁后恢复自动识别。
`detect` 只传 `conversation_key`。

`translate` 表单包含 `conversation_key`、可选 `target_language`、逗号分隔的 `message_indices`（从 0 开始，
对应详情页所显示的最近 300 条消息，每次最多 20 条）。返回 `target_language`、`items`；每项含 `index`、
`source_hash`、`source_text`、`source_characters`、`source_truncated`、`text`、`error`、`cached`、`complete`、`chunks`。
`source_text` 与哈希对应详情页可见的前 4000 字，用于防止新消息到达后把译文显示到错误消息上；
翻译处理完整原文，上限为 64000 字，超过明确拒绝，不静默截断。长文按约 1800 字分块，保留术语和链接边界，
每次 runner 最多八块。每块独立校验，坏块不会丢弃同批好块。
`compose` 表单包含 `conversation_key`、`text`；返回 `native_language`、`my_language`、`text`、
`back_translation`、`warnings`、`validation`、`back_validation`、`contact`。
`validation.ok`、`validation.missing`、`validation.added` 与核对稿校验共同决定界面红色提示。
新增数字、型号、金额和链接也会报出；正负号须保持。术语指定译法不能覆盖数字、型号、金额或链接的保护规则。
接口不接受客户端指定数据库路径、联系人任意地址或发送目标。

## 存储、额度与模型

独立库默认为 `/var/lib/imgw/ops-app/lang.sqlite3`，可通过构造参数 `path` 或环境变量 `IMGW_LANG_DB` 配置；
小时额度可由参数 `hourly_limit` 或 `IMGW_LANG_HOURLY_LIMIT` 配置（1 至 10000）；不升级 `archive.sqlite3`。
所有持久写入经过串行事务。库内保存语言设置、术语、联系人语言、翻译缓存、额度预约和不含正文的审计。
不把原始会话正文、模型请求或模型原始响应写进审计或应用日志。

翻译经 `inbox.ai.official_runner` 调用国产模型，提示词集中于 `src/console/lang/prompts.py`。
只提交完成当前识别或翻译所需的消息文字，不附客户名、公司、渠道配置、其他会话或知识库资料；
不得把与当前任务无关的信息发给模型。原文里的客户姓名、公司或价格可能作为待翻译正文出现，应由部署方确认模型使用规则。
业务内容始终作为资料，不能改变翻译规则或指示系统发送消息。

语言功能使用独立额度桶，默认每小时 120 次 runner 调用，缓存命中不消耗。
一次 runner 由网关内部最多尝试三次，额度统计单位是 runner 调用；识别、翻译和回译共用语言桶，不占岗位或抽取额度。
失败调用仍占额度。429 返回清楚的中文提示、`retry_after` 和 `Retry-After`；若此前已有成功块，随 `result` 一并返回。
缓存按原文哈希、目标语言、术语表指纹区分，术语变更后重新翻译，避免旧译法继续命中。
母语版新增事实或回译核对失败时返回红色警告，不把不安全译文写进缓存；占位符丢失等坏块要求重试。
不会因翻译失败自动重新调用模型；再次点击只补未完成块。

## 总指挥合并、部署与回滚

本快照没有完整生产宿主装配源码。需要总指挥在已有认证、CSRF 中间件后的 API 路由表加入：

```python
from console import v3_lang_api

routes += v3_lang_api.build(gate, lang_path=private_lang_path, inbox=shared_inbox)
```

`gate`、`private_lang_path`、`shared_inbox` 代表宿主中实际对象，须按真实装配源码核对。
演练必须显式指定临时库、假 inbox 和假 runner，不能回落到默认生产路径。
需求账本 H6、H7 状态由总指挥合并；README、master-plan 和账本未在本任务修改。

部署由总指挥审查后操作：

1. 审查差异并核对当前生产源文件基线，记录 SHA-256；本次未读取或改动生产文件。
2. 在隔离演练环境合并语言模块、API、前端与最少的路由导航行，构建前端；挂载临时语言库和假 runner。
3. 总指挥统一完成已授权的真实数据只读走查、全页面冒烟及老板翻译质量核对，全部通过后才允许装入生产。
   本轮开发不运行 `scripts/e2e_smoke.sh`。
4. 审查通过后，总指挥备份生产源文件、前端资源和已有语言库，再装入候选；配置语言库路径及目录权限，
   在批准的独立库主动初始化，核对路由表、重启、复查。档案库不执行迁移。
5. 回滚时移除语言路由和入口，恢复旧源文件及前端构建，重启核对；语言库可保留供复查或恢复备份。
   不回退档案库版本，不改原发送配置，不重放任何发送。

仓库快照改动前基线（不能替代生产 SHA-256）：

| 文件 | SHA-256 |
| --- | --- |
| `web/src/pages/Inbox.tsx` | `a31ac79a353866a0cfb06656ba83c11f717efc5dd177711bfd1a6cee680a1f7f` |
| `web/src/App.tsx` | `3ee7b3944df97cebf2c6e627e26f222200f3d7a94b77223dc707929249dfb5d0` |
| `web/src/nav.ts` | `dbcdc71996d81dd68ae18f3e2cd06174e2731c238bd6d01dbd3ca6d130fb4d47` |

本任务交付 18 个文件：

```text
src/console/lang/__init__.py
src/console/lang/detection.py
src/console/lang/prompts.py
src/console/lang/protection.py
src/console/lang/service.py
src/console/lang/storage.py
src/console/lang/translation.py
src/console/v3_lang_api.py
tests/lang/conftest.py
tests/lang/test_lang_api.py
tests/lang/test_lang_service.py
web/src/lang.ts
web/src/pages/LanguageSettings.tsx
web/src/ui/LanguagePanels.tsx
web/src/pages/Inbox.tsx
web/src/App.tsx
web/src/nav.ts
docs/lang.md
```

其中已有文件只修改收件箱页面和最少的路由、导航行：`web/src/App.tsx:11` 引入页面、`:57` 加入路由；
`web/src/nav.ts:2` 引入图标、`:61` 加入导航。其余为新增文件。需要总指挥合并的共享改动另有
生产宿主 API 挂载和需求账本 H6、H7 状态；本任务未改 README、master-plan 或需求账本。

## 安全自查

越权边界使用已有 principal、旧会话、recheck 和宿主 CSRF；部署必须正确挂载全局中间件。
会话键须合法解码并对应真实收件箱会话，客户端不能指定库路径，数据库拒绝符号链接。
SQL 使用参数化值；模型资料与规则分开，结果逐块校验；前端使用 React 文本展示，不执行模型文字或抓取消息链接。
异常返回固定中文，审计只留字段名和操作信息，测试只用虚构正文，未读取、打印或复制任何真实凭据。
语言模块没有发送适配器，「采用」只填入既有输入框；原发送确认和发送结果未知时不自动重试的约束保留。

## 验证与局限

隔离测试入口：

```sh
sudo -n -u imgw env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/lang" /opt/imgw/venv/bin/python -m pytest tests/lang -q -p no:cacheprovider
```

快照不允许 sudo 时，以当前 `imgw` 身份执行同样的 `env … python …`。前端使用 `npm run build`，不运行 `tsc -b`。
本次不运行 `scripts/e2e_smoke.sh`，不启动固定端口服务，不连接真实模型、渠道或收件人。

本快照缺少已有依赖 `src/console/media/`。无补充路径的标准 roles 命令在收集阶段失败，原文为：

```text
ModuleNotFoundError: No module named 'console.media'
```

该次退出码为 4，没有运行测试。为继续隔离验证，临时 `/tmp/ops-p1-test-support/sitecustomize.py`
只加载 `/srv/imgw-dev/ops/src/console/media/` 的现有源码，把媒体测试 key 路径指向临时目录，并阻断生产文件打开。
这是测试环境补缺，不是仓库交付依赖或生产修复；没有读取任何生产 key。
在当前 `imgw` 身份使用下表命令模式：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/<套件>:/tmp/ops-p1-test-support" /opt/imgw/venv/bin/python -m pytest tests/<套件> -q -p no:cacheprovider
```

| 套件（替换命令内两处 `<套件>`） | 结果 |
| --- | --- |
| `lang` | 70 通过，0 失败 |
| `inbox_api` | 37 通过，99 个子测试通过 |
| `extract` | 358 通过 |
| `outbox` | 42 通过，53 个子测试通过 |
| `roles` | 237 通过，1 失败 |
| `channels` | 192 通过，14 个子测试通过，1 失败 |

两项既有回归失败原文及原因：

```text
FAILED tests/roles/test_roles_safety_cli.py::test_cli_subprocess_defaults_to_preview_and_requires_apply
> assert result.returncode == 0 and json.loads(result.stdout)['dry_run']
E AssertionError: assert (1 == 0)
ModuleNotFoundError: No module named 'console.media'
1 failed, 237 passed in 31.35s
```

roles 子进程重设 `PYTHONPATH`，没有加载临时缺包补充。

```text
FAILED tests/channels/test_whatsapp_rehearsal.py::WhatsAppRehearsalTests::test_automounted_routes_keep_real_session_writes_disabled
E AssertionError: Items in the first set but not the second:
'/api/v1/listing/drafts/{id}'
'/api/v1/listing/drafts/generate'
'/api/v1/listing/products-ready'
'/api/v1/listing/drafts/{id}/update'
'/api/v1/listing/drafts'
'/api/v1/listing/drafts/{id}/export'
'/api/v1/listing/drafts/{id}/status'
1 failed, 192 passed, 14 subtests passed in 17.16s
```

channels 的旧演练路由断言没有隔离快照已有的发品路由。上述其他任务模块未修改，不能称全部回归通过。
前端命令 `npm run build --prefix web` 已通过（`tsc --noEmit && vite build`）；未运行 `tsc -b`。
sudo 在快照内被禁止，当前账号本来就是 `imgw`，未修改权限；拒绝原文：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
```

规则识别不能覆盖所有语言、方言、混合文字及只有型号的短句；使用某种语言也不能证明客户的母语。
品牌未登记且无法按格式辨认时可能误翻，应加入术语表。数字或链接原样出现只证明片段保留，不能证明语义、
数量关系、价格条件或整段翻译正确。回译也可能同时出错，发出前仍需人工核对。
术语表修改后使用新的缓存上下文；过去译文不代表当前术语规则已验证。真实数据可用性、模型质量与生产部署仍待总指挥验收。

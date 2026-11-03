# 统一知识中枢

供运营人员、系统问答/起草以及本机 Claude/Codex 会话共用。先读取公司背景，再按用途检索本机知识库，输出可核对的来源；不发送消息。本次只交付后端、命令行和隔离测试，不修改前端或部署生产。

需求与验收：内存只读索引、资料族与用途隔离、混合检索、公司背景及版本、统一 Hub、认证 API、起草来源持久化、外部 CLI；使用虚构库双环境测试，再对真实库执行只读数字汇总冒烟。按照本次明确限制，不修改 `web/` 中的需求账本。

```text
CLI / KB API / 收件箱起草
           ↓
      Hub + 公司背景（本地文件、20 版历史）
           ↓ purpose 策略
   BM25 中文二元组/西文词 + 本地 BGE → RRF → 族配额/去重
           ↓
   内存快照（float16 向量、1200 字文本、元数据/权限）
           ↓ mode=ro + query_only
          Catalog SQLite
```

## 真实契约与资料族

实现前只读核对了四表的实际列、权限枚举、源类型分布，以及生产 BGE 客户端和阿里归档 scope 构造源码。测试 fixture 复刻四表全部列，内容全为虚构。

`knowledge_chunks` 与 `multimedia_assets` 按 `(asset_id, owner, source_scope)` 关联。权限表的 `source_sha256` 对应 asset 的 `source_sha256`；标签中的 owner/scope 表示标签来源，同文件跨 scope 的副本仍须继承保密性，因此合并该哈希的所有标签，任一限制即生效。没有把某个文件的标签扩散到整个供应商 scope。

本次检查标签的 confidentiality 实际只有 `owner_only`；`is_cost_price` 有 0/1。生产分类源码表明它是内部价格敏感标记，不代表已经确认的采购成本。实现只认可显式 `public` 标签为公开，任何其他或未知值均保密；同一文件任一 cost 标记为真即保留。无标签的共享 supplier/technical 资料可供客户起草；这依赖上游标签完整性，不能替代人工核对。私有笔记/封存业务记录额外标为保密。买家会话即使没有标签，也先由资料族隔离。

群名和角色按注册表 `(owner, source_scope)` 关联，使用最新 `observed_at`。未知角色不猜成客户。以下规则从上往下首次命中生效，集中在 `kb/policy.py:FAMILY_RULES`：

| 资料族 | 中文名 | 命中依据（源类型或 scope 根或注册角色） |
| --- | --- | --- |
| customer_conversation | 买家会话 | icbu_customer_conversation、icbu_pwa_customer_conversation、icbu_conversation_analysis_candidate、icbu_official_inquiry、business_customer_inquiry/attachment、whatsapp_business_conversation；icbu/icbu-tm/icbu-analysis/icbu-inquiry；CHANNEL_ROLE_CUSTOMER |
| private_note | 私聊笔记/视频转写 | owner_ 开头源类型、legacy_sealed_business_record；private、business:private |
| supplier_chat | 与供应商的沟通 | business_supplier_message、supplier_chat_candidate；internal_supplier_chat；CHANNEL_ROLE_SUPPLIER |
| technical | 技术参考资料 | public_technical_reference；internal_technical_reference |
| supplier | 供应商资料与报价表 | supplier、supplier_image、business_supplier_attachment；internal_supplier |
| group_chat | 微信群 | learning_chat_snapshot、learning_qwen_digest_candidate；group |
| other | 其他资料 | 其余或来源关联损坏 |

scope 根匹配只接受完整名称或 `根:`，不接受相似名称前缀。客户与私有来源优先，避免因共享供应商范围而被错误公开。

| 用途 | 可见范围 | 限制 |
| --- | --- | --- |
| owner | 全部资料族 | 返回保密/成本敏感标记，限内部使用 |
| product_facts | supplier、technical、supplier_chat | 可含保密/成本敏感资料，不是对外发布许可 |
| customer_reply | supplier、technical | 排除 cost 与任何非公开标签；可额外加入 scope 精确相等的 customer_conversation，仍排除保密/成本敏感资料 |

## 索引与检索

构造 Hub/Index 不读取库。第一次检索加载索引；未加载时 stats 仅执行只读聚合，`loaded_at:null`。SQLite 始终 `mode=ro` 与 `query_only=ON`，一次加载内使用同一读事务。常驻向量为归一化 float16；每条向量单独从 float32 BLOB 转换，余弦计算时最多转换 512 行的临时 float32 块。numpy 缺失时仍可读取文本进行词法检索。

文本常驻最多 1200 字，`Index.full_text(chunk_id)` 提供内部按需全文读取（调用方须先执行用途策略，不对外开放直接读取 API）。词法索引采用中文连续文本二元组与西文/数字词，BM25 风格得分；查询向量使用固定 BGE 模型版本，余弦至少 0.35 才进入稠密候选。两路按 `1/(60+rank)` 的 RRF 合并。每轮各有命中的族取一条，空余配额继续分配；同 asset 最多两条，同 parent 的相邻 child 命中合并。只合并已经通过用途筛选且命中的相邻片段。

摘要围绕缓存文本中的命中位置截取，最多 300 字，不使用 HTML 高亮。标题仅显示文件 basename 或注册群名，显示层去除哈希及绝对/常见相对文件路径。没有可靠会话名时显示安全文件名或资料族名，不将 scope 哈希作为标题。source_id 是片段标识摘要的 base64url，不编码路径。

主库及 WAL 的 mtime、size、inode 参与变更检测；距离上次成功加载至少 120 秒才重载。新快照在锁内替换，已持有旧快照的检索继续执行，重载期间其他调用返回旧快照；首次加载调用需等待加载完成。每个 worker 保留自己的索引与限流状态。

BGE 客户端在调用时惰性导入 `video_knowledge.engine_client.engine_for`，传固定 embedding/reranker revision 和 `/run/imgw-bge/bge.sock`；使用 `encode([query])[0].validated().dense` 并检查 model_version。只访问本地 Unix 套接字，不加载模型、不访问互联网。依赖缺失、版本不符、无效向量或服务失败均退化成 `mode:lexical`。

## 公司背景与 Hub

默认背景只含任务规定的最小公司事实和回复规则，无真实聊天或报价。首次读取文件不存在时返回内置文本，不创建文件。通过 `POST /api/v1/kb/brief` 修改，或由可信本机调用 `Brief(...).save_brief(text, who)`；文本最长 8000 字，去除控制/格式字符（保留换行与制表）。文件 `/var/lib/imgw/ops-app/company-brief.md` 及历史库 `/var/lib/imgw/ops-app/kb.sqlite3` 权限均为 0600；文件通过临时文件原子替换，历史表 `brief_versions(id,saved_at,saved_by,text)` 保留最近 20 版。历史时间为 UTC ISO 字符串。

`Hub` 可注入 catalog_path、brief_path、history_path、index、brief、embedder；`default_hub()` 在进程内共享默认实例。默认 embedder 不可用时退化；测试使用 None 或虚构向量函数。

- `search(query, purpose='owner', top_k=8, families=None, conversation_scope=None)` 返回 `{ready,mode,items}`；query 1–500 字，top_k 1–20。
- `context(query,purpose,budget_chars=3000,k=5,conversation_scope=None)` 返回 `{text,sources}`。先公司背景，再编号资料与来源/类型/日期、片段。字符预算严格包含标题与分隔符，sources 只包含实际放进文本的资料。背景在有命中时最多占 1200 字及预算一半，编辑背景时将关键规则放在前面。无命中时加“知识库里没有找到相关资料”。极小预算允许截断提示文本。
- context sources 为 `{n,title,type,date,source_id}`。
- `ask(question,purpose='owner',runner=None)` 返回 `{answer,sources,mode}`。无命中直接返回固定中文提示，不调用模型。有命中以纯文本提示要求逐事实引用 `[n]`；剔除越界引用，模型失败固定返回“AI 暂时无法回答，请稍后再试”。默认复用 official_runner，可注入假 runner。

## API

`console.v3_kb_api.build(app_module, hub=..., runner=..., limiter=..., clock=...)` 返回五条 Starlette Route，由宿主挂载并沿用认证/CSRF 中间件，本次未部署。认证、GET 返回前 recheck、POST 读正文/调用 AI/写库前 recheck、`request.state.form` 优先与 parse_qs 回退，复用 inbox HTTP 辅助函数。JSON 响应带 `Cache-Control:no-store` 和 `X-Content-Type-Options:nosniff`；403 沿用宿主错误响应并补头。

| 接口 | 参数 | 返回 |
| --- | --- | --- |
| GET /api/v1/kb/search | q、purpose=owner/product_facts（默认 owner）、family（单个资料族）、top_k（默认 8） | `{ready,mode,items}` |
| POST /api/v1/kb/ask | URL 编码表单 q、purpose=owner/product_facts | `{ready,answer,sources,mode}` |
| GET /api/v1/kb/brief | 可选 version=N | 当前 `{text,versions:[{id,saved_at,saved_by}]}` 最近 5 版；指定版本返回 `{id,saved_at,saved_by,text}` |
| POST /api/v1/kb/brief | URL 编码表单 text | `{version}`，保存人为认证 principal.username |
| GET /api/v1/kb/stats | 无 | `{ready,chunks,with_vectors,loaded_at,mode,families:[{family,label,chunks}],assets_by_type:[{label,count}]}` |

search 每项字段：`rank,score,family,family_label,title,source_type_label,date,snippet,source_id,confidential,is_cost_price`。date 为 ISO 或 null；类型名为中文。stats 的 assets_by_type 统计有片段的 asset，合并中文类型后取前 10；mode 表示本进程最近一次检索模式，尚未检索时为 lexical，并非服务健康探测。

参数错误 400、版本不存在 404、AI 失败 502、读取/保存失败 503；固定中文错误不回显异常或路径。库不存在时 search/stats/ask 返回 200 与 `ready:false`，不创建库。ask 在进程内原子计数，每分钟最多 6 次、每小时最多 60 次（失败尝试也计数），超限 429 中文并带 retry_after/Retry-After；多个 build 共享默认限流器，多个进程不共享。重复已知查询参数与重复表单值拒绝。

## 起草与外部调用

收件箱 builder 增加可注入 hub、knowledge_timeout（默认 3 秒）。查询取最近两条 buyer 消息，去图片/视频/语音等已知占位符后限 300 字；供应商会话用 product_facts，其余用 customer_reply。空文本不查库。

阿里 scope 按已核实源码规则计算：`icbu-tm:<binding_id>:sha256(canonical_json([account,conversation_id]))`，canonical JSON 为 UTF-8、ensure_ascii=False、sort_keys=True、separators=(',',':')，且 conversation_id 须匹配 tm: 格式。binding_id 从已加载 scopes 中以哈希后缀唯一确认，不读私有配置、不猜名称；多个匹配、其他阿里标识、微信/WhatsApp 无法证明绑定时传空 scope。这些渠道仍可使用公开 supplier/technical 资料，但不额外纳入知识库会话历史；当前收件箱会话本身仍传给起草。

知识检索使用最多两个守护线程槽，超时/不可用只带公司背景继续起草；晚到结果不写入草稿，不调用 AI。首次冷加载超过 3 秒时首份草稿可能仅使用背景。`make_prompt(...,knowledge=None)` 保持“语言：”与 `=====待确认=====` 的纯文本输出格式，绝不要求 JSON。事实仅来自对话与资料，使用资料数字需核对来源；草稿正文禁止内部资料编号，保存前再次清除“资料 N”/`[N]`。有来源且正文含数字时，额外加入来源核对提示。

生成响应和 GET 详情中的 draft 均添加 `sources:[{title,type,date}]`。首次写入旧草稿库时 `ALTER TABLE drafts ADD COLUMN sources_json TEXT DEFAULT '[]'`；读取旧库无此列时返回空数组，不做写迁移。

CLI 为 `python3 scripts/kb_search.py "问题" [--purpose owner|product_facts] [--top 8] [--json]`；包含公司背景与来源日期，不调用 AI、不写背景或历史。numpy 缺失时 stderr 说明词法退化，仍返回可用结果。参数错误退出 2，缺库/其他不可读退出 1。本机已为 imgw 用户，已验证直接命令：

```sh
env PYTHONPATH=/opt/imgw/wechat:/srv/imgw-dev/ops/src /opt/imgw/venv/bin/python scripts/kb_search.py "CE 认证" --json
```

sudo 包装在本容器因 no-new-privileges 被拒绝，未验证成功；不要据此调整生产权限。CLI 把仓库 src 放在最前，`src/console/__init__.py` 防止生产同名包遮蔽开发包。网页外部 AI 没有本机访问能力时不能自动运行脚本。

## 局限与实测

- 中文二元组是近似分词；只索引前 1200 字，尾部事实可能漏检。向量召回依赖本地 BGE，0.35 门槛及族配额是确定性启发式，不能保证语义相关性。
- 索引可能滞后约 2 分钟；持续变更、加载期间与重载失败可能延长可用结果等待。reload 期间新旧矩阵共存，峰值内存高于稳定状态。
- float16 带来余弦精度损失。标注及 scope 映射依赖上游完整性，产品规格/来源日期不等于已核实的业务事实；候选分析亦可能有误。
- 引用范围校验只检查编号存在，不证明 AI 的事实与引用一致。提示词与编号清理不能替代人工审核，也不是防提示注入的形式化保证。
- 公司背景文件与 SQLite 是两个存储介质，断电时不能保证两者作为一个事务提交；文件原子替换避免半写，历史可供人工恢复。直接编辑文件不会新增版本，应优先用保存接口。
- 后端路由未挂载到生产、未增加前端，未运行真实 AI、未测试对外发送。本次任务没有界面变更，因此未做浏览器验收。

最终只读本机实测（只保留数字与类型，不保存真实正文）：40,629 个片段均有匹配模型的向量。冷加载 7.3107 秒，加载后 RSS 增量 191.69 MiB；矩阵 79.35 MiB，另外内存用于文本、词法倒排、元数据及 Python/numpy。三次热检索后的 RSS 增量 194.31 MiB。

首轮实测为 40,622 个片段，加载与查询前后整库 SHA-256 对比相同（不记录摘要值），mtime 和 size 均不变。最终一轮期间主库 SHA-256 与 mtime 变化、size 未变，不能宣称现场库在整个任务期间字节未变化；未核实写入来源。所有本任务数据库连接均只读，未执行原库写入；隔离测试中的源库前后字节不变断言通过。未停止现场服务或复制真实数据来制造静态验收环境。

| 问题 | 命中 | 模式 | 同进程检索耗时（不含加载） | 独立 CLI 总耗时（含冷加载/启动） |
| --- | --- | --- | --- | --- |
| 200W 频闪灯 外箱尺寸 | 8 | hybrid | 0.3452 秒 | 8.6949 秒 |
| LED 面光灯 毛重 | 8 | hybrid | 0.2974 秒 | 10.4715 秒 |
| CE 认证 | 8 | hybrid | 0.2673 秒 | 8.8776 秒 |

最终三次命中的来源类型包括供应商资料/消息/沟通整理、公开技术参考、阿里会话分析、私有笔记与转写、学习群记录、其他资料。此表使用 owner 用途，不代表这些来源允许用于客户回复。每次 CLI 返回码均为 0；真实查询未调用问答 runner。

隔离验收命令（2 万行性能断言包含在 tests/kb 内）：

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/kb -p 'test_*.py'
PYTHONPATH=src python3 -m unittest discover -s tests/kb -p 'test_*.py'
```

同样双环境回归 inbox_api、channels、outbox、media、packing_api、inspiration、web_app、supplier_data。系统 Python 缺 numpy/Starlette 时按功能跳过测试，其余纯函数、数据库、CLI、性能测试仍执行。实际测试末尾原文见文末验收记录。

## 本次文件

新增：

- `src/console/__init__.py`：避免开发 CLI 被生产同名包遮蔽。
- `src/console/kb/__init__.py`、`index.py`、`policy.py`、`search.py`、`text.py`、`brief.py`、`hub.py`、`drafting.py`。
- `src/console/v3_kb_api.py`、`scripts/kb_search.py`、`docs/knowledge-hub.md`。
- `tests/kb/kb_fixtures.py`、`test_core.py`、`test_api.py`、`test_cli.py`。

修改：`src/console/inbox/ai.py`、`src/console/inbox/drafts.py`、`src/console/v3_inbox_api.py`、`tests/inbox_api/test_model.py`、`CLAUDE.md`、`docs/inbox-api.md`。

未 commit/push，未部署；未修改 web/ 或本任务禁止的业务模块源码，保留工作区原有修改。验收不包括真实模型回答质量、浏览器界面或对外发送。

## 双环境验收记录

以下为最终一次隔离运行的各套件末尾原文。所有命令退出码为 0；测试内容均为虚构。

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/kb -p test_*.py
----------------------------------------------------------------------
Ran 31 tests in 9.254s

OK

PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inbox_api -p test_*.py
----------------------------------------------------------------------
Ran 37 tests in 2.205s

OK

PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/channels -p test_*.py
----------------------------------------------------------------------
Ran 44 tests in 2.384s

OK

PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/outbox -p test_*.py
----------------------------------------------------------------------
Ran 42 tests in 11.278s

OK

PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/media -p test_*.py
----------------------------------------------------------------------
Ran 25 tests in 0.357s

OK

PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/packing_api -p test_*.py
----------------------------------------------------------------------
Ran 24 tests in 0.410s

OK

PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inspiration -p test_*.py
----------------------------------------------------------------------
Ran 56 tests in 2.151s

OK

PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/web_app -p test_*.py
----------------------------------------------------------------------
Ran 6 tests in 0.050s

OK

PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/supplier_data -p test_*.py
----------------------------------------------------------------------
Ran 110 tests in 6.099s

OK

PYTHONPATH=src python3 -m unittest discover -s tests/kb -p test_*.py
----------------------------------------------------------------------
Ran 31 tests in 7.859s

OK (skipped=10)

PYTHONPATH=src python3 -m unittest discover -s tests/inbox_api -p test_*.py
----------------------------------------------------------------------
Ran 37 tests in 0.219s

OK (skipped=19)

PYTHONPATH=src python3 -m unittest discover -s tests/channels -p test_*.py
----------------------------------------------------------------------
Ran 44 tests in 1.319s

OK (skipped=15)

PYTHONPATH=src python3 -m unittest discover -s tests/outbox -p test_*.py
----------------------------------------------------------------------
Ran 42 tests in 4.188s

OK (skipped=12)

PYTHONPATH=src python3 -m unittest discover -s tests/media -p test_*.py
----------------------------------------------------------------------
Ran 25 tests in 0.081s

OK (skipped=9)

PYTHONPATH=src python3 -m unittest discover -s tests/packing_api -p test_*.py
----------------------------------------------------------------------
Ran 24 tests in 0.000s

OK (skipped=24)

PYTHONPATH=src python3 -m unittest discover -s tests/inspiration -p test_*.py
----------------------------------------------------------------------
Ran 56 tests in 1.561s

OK (skipped=11)

PYTHONPATH=src python3 -m unittest discover -s tests/web_app -p test_*.py
----------------------------------------------------------------------
Ran 6 tests in 0.000s

OK (skipped=6)

PYTHONPATH=src python3 -m unittest discover -s tests/supplier_data -p test_*.py
----------------------------------------------------------------------
Ran 110 tests in 5.702s

OK (skipped=6)
```

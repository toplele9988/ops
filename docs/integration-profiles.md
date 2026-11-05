# 会话画像交付说明

已完成完整历史分段读取、分次补齐、增量更新、画像查询和分拣接入。真实聊天演练没有运行，须由总指挥在宿主完成。没有部署、建立服务、发送消息或修改网络设置。

## 改动文件

- `src/console/profiles/{__init__,__main__,schema,store,windows,model,worker,sources,schedule}.py`：画像处理、侧库、预算、断点、只读来源和独立进程调度。
- `src/console/v3_profiles_api.py`：两个需要登录的只读接口。
- `src/console/v3.py`：接口注册。快照原来没有此文件；从只读安装目录的实际注册文件复制，仅增加画像接口的导入和注册。整合时把这两行合入总指挥手上的最新版注册文件，避免覆盖其他任务的接线。
- `src/console/roles/{triage,service,automatic}.py`：档案规则优先，随后采用可信画像；自动扫描仍不现场调用分拣模型。
- `tests/profiles/{conftest,test_worker,test_model_sources,test_api_schedule}.py`、`tests/roles/test_profiles.py`：全部使用虚构消息、临时库和假模型。
- `docs/profiles.md`、本说明。

没有修改 `operator/worker.py`、`operator/host.py` 或前端。不需要前端构建。

## 新表和接口

侧库：`/var/lib/imgw/ops-app/profiles/profiles.sqlite3`。真实运行首次创建；查询和只读演练不会创建。库文件 0600，目录新建时 0700。所有新增表使用 `cp_` 前缀。

画像表字段固定，未改名：

```text
cp_profile(conversation_key TEXT PK, channel TEXT, title TEXT, kind TEXT,
 customer_stage TEXT, org_name TEXT, people_json TEXT, country TEXT,
 country_name TEXT, language TEXT, products_json TEXT, summary TEXT,
 evidence_json TEXT, confidence REAL, messages_read INT, messages_total INT,
 first_at REAL, last_at REAL, last_inbound_at REAL, state TEXT, error_code TEXT,
 model TEXT, updated_at REAL)
```

辅助表：`cp_window` 保存待读窗口、结果和失败码；`cp_cursor` 保存纳入范围、历史指纹和检查时间；`cp_reduce` 缓存分层汇总；`cp_run` 保存运行数字；`cp_call` 保存调用预留和用量。窗口和游标每步提交，断点后继续；坏答案不覆盖旧画像。

接口为 `GET /api/v1/profiles?kind=&stage=&channel=&q=` 和 `GET /api/v1/profiles/{conversation_key}`。返回画像原字段，三个 JSON 字段解析成数组。列表带全库分类计数和最新更新时间。登录与登录复核沿用现有接口。

旧分拣表的来源字段有固定的“规则/AI”约束。这次不改旧表结构：画像来源保存在 `role_runs.output_json.profile_source` 和 `triage_suggestions.summary_json.profile_source`。服务返回的运行记录、扫描结果、分拣列表和岗位最近来源统一还原成 `source='ai_profile'`。直接查旧表的 SQL 来源仍为 `ai`，需要精确区分时读取这两个 JSON 标记。档案规则 0.95/0.9 仍优先。

## 总指挥接线

接口注册已提供在 `v3.py`，合并到最终注册处：

```python
from . import v3_profiles_api
routes += v3_profiles_api.build(app_module)
```

本任务没有修改运营员进程。请由总指挥在现有工作循环接入：

```python
from console.profiles.schedule import ProfilesSchedule

# 初始化一次。store 为现有运营员 Store，clock 为现有时钟。
self.profiles_schedule = ProfilesSchedule(
    self.store, self.clock, source_root='/opt/imgw/wechat',
    daily_budget_tokens=1000000,
)

# 在现有 poll 循环调用，并沿用现有异常/健康记录处理。
self.profiles_schedule.poll()
```

`ProfilesSchedule` 在 `operator_cursors` 用 `profiles` 游标记下准入时间，不改表结构。先占时间再启动；重启或启动失败也不会一小时内反复启动。子进程每次最多处理 20 个会话。上一轮未结束时不再启动。画像侧库另有跨进程锁，保护预算和游标。

如果整合后源码不在上述路径，把 `source_root` 换成最终完整源码根目录。调度使用当前 Python 解释器，也可显式传 `python='/opt/imgw/venv/bin/python'`。不要在公共快照上直接接生产调度。

## 测试

按目录分别执行，不把多个目录放进同一 pytest 进程。约定命令是：

```bash
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/profiles
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/roles
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/operator
```

快照缺少 `console.media` 等旧模块，Python 的普通 `PYTHONPATH` 不会合并两个同名包。未加临时加载设置时，现有运营员测试收集就报 `ModuleNotFoundError: console.media`。这是快照加载限制，本次未更改公共包入口或复制旧模块进仓库。实际完整测试通过临时 `sitecustomize.py` 追加只读旧代码路径；子进程也继承，保持候选源码优先：

```bash
profiles_bootstrap=$(mktemp -d /tmp/profiles-test-bootstrap.XXXXXX)
cat > "$profiles_bootstrap/sitecustomize.py" <<'PY'
import console
console.__path__.append('/opt/imgw/wechat/console')
PY
env PYTHONPATH="$profiles_bootstrap:src:/opt/imgw/wechat" PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/profiles
env PYTHONPATH="$profiles_bootstrap:src:/opt/imgw/wechat" PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/roles
env PYTHONPATH="$profiles_bootstrap:src:/opt/imgw/wechat" PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/operator
```

最终结果：画像 **38 passed**；分拣 **288 passed**；运营员 **111 passed**。三个目录分别运行，全部通过。覆盖窗口顺序、超长消息、大群分次补齐、只处理新增、七天触发、规则跳过、坏答案/超时、付款与报价阶段、跨窗成交证据保留、分层汇总缓存、预算、只读演练不写库、登录和输出契约、每小时调度、画像分拣及档案优先。

路由检查在 `/tmp` 组装候选目录，`console` 指向本次源码，遗漏的 `app.py` 只读指向安装版本，其他旧模块用检查器自带的 `--fallback`。执行：

```bash
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/operator/check_routes.py /tmp/profiles-routes-kdozu42r --web-root web
```

已通过：236 条路由、352 个路径方法组合，运营员生命周期正常，网络和数据库调用均为 0。该临时路径只用于本次检查；其他环境需重新组装候选目录后传入检查器。

## 总指挥运行真实只读演练

本任务没有尝试连接模型网关，没有读取真实聊天，也没有向指定私有报告目录写文件。以下命令须由总指挥以 `imgw` 身份在宿主运行。报告只写指定私有目录，不能提交仓库。

已知道会话标识时，完整命令为以下格式。把标识放在宿主环境变量，不能填入被跟踪文件：

```bash
cd /srv/imgw-dev/ops
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 \
  /opt/imgw/venv/bin/python -m console.profiles run --once \
  --dry-run --max-conversations 8 --max-windows 6 \
  --report /srv/imgw-dev/ops/private_out/profiles/dry1 \
  --only "$PROFILES_LARGEST_WECHAT" \
  --only "$PROFILES_SUPPLIER_1" --only "$PROFILES_SUPPLIER_2" \
  --only "$PROFILES_ALIBABA_1" --only "$PROFILES_ALIBABA_2" --only "$PROFILES_ALIBABA_3" \
  --only "$PROFILES_EXTRA_1" --only "$PROFILES_EXTRA_2"
```

若没有预先选好的标识，用下面整段命令自动选样，不打印真实名称或群标识：最大的微信群、另两个档案或渠道已认定的供应商群、三个阿里会话，再补两个其他微信或 WhatsApp 会话。若来源不足就停止，不冒充满足选样要求。任务指定的 `/srv/imgw-dev/ops/private_out/profiles` 已由现有 `private_out/` 忽略规则排除，程序明确允许这个指定目录。其他报告仍须在仓库外。

```bash
cd /srv/imgw-dev/ops
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python - <<'PY'
import time
import console
# 公共快照缺失的旧模块只从安装目录读取；不写 /opt/imgw。
console.__path__.append('/opt/imgw/wechat/console')
from console.profiles.sources import build_inbox
from console.profiles.__main__ import main
from console.roles import dossier_links
from console.archive.store import ARCHIVE_PATH

inbox = build_inbox()
rows = list(inbox.read(time.time())['conversations'].values())
groups = [c for c in rows if c['channel'] == 'wechat' and c.get('is_group')]
if not groups:
    raise SystemExit('没有可读取的微信群，停止演练')
largest = max(groups, key=lambda c: c['n_messages'])
selected = [largest['key']]
known = {r['conversation_key'] for r in dossier_links.load(archive_path=ARCHIVE_PATH) if r['kind'] == 'supplier'}
suppliers = sorted((c for c in groups if c['key'] not in selected and
                    (c['key'] in known or c.get('counterpart_role') == 'supplier')),
                   key=lambda c: c['n_messages'], reverse=True)[:2]
if len(suppliers) != 2:
    raise SystemExit('不足两个已确认的供应商群，停止演练')
selected.extend(c['key'] for c in suppliers)
alibaba = sorted((c for c in rows if c['channel'] == 'alibaba'),
                 key=lambda c: c['last_seen'], reverse=True)[:3]
if len(alibaba) != 3:
    raise SystemExit('不足三个阿里会话，停止演练')
selected.extend(c['key'] for c in alibaba)
extra = sorted((c for c in rows if c['channel'] in ('wechat', 'whatsapp') and c['key'] not in selected),
               key=lambda c: c['last_seen'], reverse=True)[:2]
if len(extra) != 2:
    raise SystemExit('不足八个不同会话，停止演练')
selected.extend(c['key'] for c in extra)
args = ['run', '--once', '--dry-run', '--max-conversations', '8', '--max-windows', '6',
        '--report', '/srv/imgw-dev/ops/private_out/profiles/dry1']
for key in selected:
    args.extend(['--only', key])
# 调用同一 CLI 入口，不输出命令参数或来源内容，只输出数字摘要。
raise SystemExit(main(args))
PY
```

演练后检查报告中的 `messages_read/messages_total/state`、报价/付款证据、档案供应商与画像判断是否一致。真实模型的准确率、时长和用量要等这次宿主演练后才能验收，当前通过的是虚构数据和假模型验证。

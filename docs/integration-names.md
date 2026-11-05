# 会话名字交付

已完成统一找名、群成员缓存、自动刷新类、国家推断、微信及 WhatsApp 显示、通讯录缺名提示、只读数量报告。没有部署。没有修改宿主或工作循环。没有调用真实渠道接口或模型，没有发送消息。

## 改动文件

| 文件 | 用途 |
| --- | --- |
| `src/console/names/__init__.py`、`resolver.py`、`clean.py`、`sources.py` | 统一找名、清洗、只读来源和缓存变化检查 |
| `src/console/names/store.py`、`refresh.py` | 群成员缓存、持久化节流及失败健康记录 |
| `src/console/names/country.py` | 本地国际电话区号表 |
| `src/console/names/report.py`、`__main__.py` | 只输出数字的只读核对命令 |
| `src/console/directory/wtapi_source.py` | 新增只读成员列表入口，原有通讯录客户端仍只有两个方法 |
| `src/console/directory/refresh.py`、`service.py` | 备注优先、补齐仅消息库存在的群、缺名计数 |
| `src/console/channels/wechat.py`、`wechat_private.py`、`whatsapp.py` | 新名字及国家显示；微信缓存也检查名字侧库的变化 |
| `src/console/ops_queue/inbox.py` | 待办里的微信名字与会话显示共用解析 |
| `web/src/pages/Directory.tsx` | 增加一行中文缺名提示 |
| `tests/names/**`、`tests/directory/test_wtapi_members.py` | 全部使用虚构资料、假接口及临时数据库 |
| `tests/channels/test_adapters.py`、`test_whatsapp_rehearsal.py` | 更新号码回退断言；演练断言限定到本功能路由 |
| `tests/directory/test_refresh_names.py`、`test_api.py` | 更新备注优先断言；演练断言限定到通讯录路由 |
| `web/e2e/fixtures.base.mjs`、`names.mjs` | 虚构缺名计数和桌面、手机检查 |
| `docs/names.md`、本文件 | 使用说明及接线说明 |

文件清单以任务范围为准。前端夹具和脚本、此交付文件虽然不在最初允许清单的枚举中，但属于任务书明确要求的同步交付。没有修改其他业务文件。

## 表和接口

新增侧库 `/var/lib/imgw/ops-app/names/names.sqlite3`。只有刷新成功后才创建。新文件权限为 `0600`，新目录为 `0700`。已有来源始终只读。

```sql
CREATE TABLE nm_members (
  group_id TEXT NOT NULL,
  wxid TEXT NOT NULL,
  display_name TEXT,
  nick_name TEXT,
  fetched_at REAL NOT NULL,
  PRIMARY KEY (group_id, wxid)
);
```

不增加运营库的表。使用已有 `operator_cursors` 的这些条目：

- `names:directory`：六小时自动刷新节流。
- `names:member:<群号的 SHA256>`：单群滚动 24 小时节流。
- `names:members-backoff`：成员请求或来源读取失败后的十分钟暂停。

请求前先提交节流记录。失败也消耗本次额度。健康写入已有 `operator_health`，部件名为 `names-directory` 和 `names-members`；只保存固定异常类型，不保存原始错误或接口回复。

新增 Python 入口：`NamesResolver`、`group_name(group_id)`、`member_name(group_id, sender_id)`、`contact_name(identity, ...)`、`country_from_phone(number)`、`NamesRefresh(store, clock, ...)`。需要指定虚构来源时，使用 `NamesResolver(state_path, directory_path=..., members_path=..., dossiers_path=..., contacts_path=...)`。自定义消息库会自动隔离未指定的其他来源。

组名优先取通讯录备注和昵称，再取消息库各表中最新的有效名称，最后取 `sd_supplier.contacts_json` 中准确群号对应的 `group_name` 或 `name`。成员名先取通讯录，再取准确群号及成员号对应的缓存，随后兼容原有消息库联系人表。私聊保留原有身份分类，并使用同一解析器显示名称。私聊联系人库仍按账号隔离。

通讯录页已有的配置名展示和角色核对规则继续使用。新增的会话缺名统计按统一解析结果判断。没有新增网页接口；现有通讯录列表及汇总响应增加可选 `name_health: {total, named, missing, reasons}`。原始会话增加可选 `name_missing`、`name_missing_reason`。原本已有字符串字段 `country`，现在填写推断出的国家两位码，不改字段类型。

成员入口 `build_readonly_members_client(config_path)` 只有 `get_chatroom_member_list(group_id)`。它复用 `_credentials` 的凭据读取方式，固定调用成员列表读取地址，拒绝跳转及环境代理。只导入生产客户端的 `_DeadlineHTTPSHandler`、`_read_before_deadline` 两个读取辅助能力，不实例化或暴露完整客户端。沿用 20 秒网络总时限、1 MiB 回复限制及目录刷新外层默认 25 秒时限。缺少辅助能力时返回固定的不可用错误。原有通讯录两方法客户端不变。

空或错误回复不删除旧成员名。迟到结果也不会入库。成员显示名及昵称之外的字段不会写入新侧库。

## 总指挥接线

在已有宿主中创建一次，并在现有维护循环调用。此任务没有改动 `operator/worker.py` 或 `operator/host.py`。

```python
from console.names import NamesRefresh

# operator 是宿主已有实例；不重新创建运营库。
names_refresh = NamesRefresh(operator.store, operator.clock)

# 放在宿主已有的维护循环中；无需新增系统服务或定时器。
# 若结合现有 Worker，可新增 names_refresh 参数保存此实例，
# 再在维护任务处调用下面这一行。
names_result = names_refresh.poll()
```

每轮最多包含一次通讯录刷新和三个群读取；最坏等待约 100 秒。请放在宿主已有的维护执行通道，避免阻塞网页请求或消息处理。`poll()` 内部已分别记录两个部件的健康，不要再用外层无条件“成功”覆盖它们。多个进程或重启通过运营库事务共同节流。

生产只读核对命令由总指挥执行：

```bash
cd /srv/imgw-dev/ops
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 \
  /opt/imgw/venv/bin/python -m console.names report
```

输出含微信、阿里国际站、WhatsApp 的会话总数、有名字数、没名字数、原因计数，以及数字形式的来源可读、统计完整标志。不可读不当作已查明零会话。WhatsApp 达到 500 条上限时标记不完整。退出码 `0` 表示来源均可读且范围完整，`1` 表示至少一项不完整。此命令不触发名字刷新、附件登记或媒体密钥写入。

## 验证

当前快照缺少原有 `src/console/media` 包，直接运行渠道测试会在收集阶段报 `ModuleNotFoundError`。没有越界补进仓库。验证使用 `/tmp/names-validation`：业务源码链接到当前工作区，测试和脚本复制到临时目录，仅缺失的媒体包链接到 `/srv/imgw-dev/ops/src/console/media`。脚本使用真实临时路径，保证岗位测试的子进程也加载相同源码。

复建本次临时验证目录的方法如下。在当前工作区执行。补齐的都是已有源码，不复制真实数据库或配置文件。

```bash
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python - <<'PY'
from pathlib import Path
import shutil
root = Path.cwd()
stage = Path('/tmp/names-validation')
(stage / 'src/console').mkdir(parents=True, exist_ok=True)
for entry in (root / 'src/console').iterdir():
    target = stage / 'src/console' / entry.name
    if not target.exists():
        target.symlink_to(entry, target_is_directory=entry.is_dir())
media = stage / 'src/console/media'
if not media.exists():
    media.symlink_to('/srv/imgw-dev/ops/src/console/media', target_is_directory=True)
for name in ('tests', 'scripts'):
    target = stage / name
    if target.is_symlink():
        target.unlink()
    shutil.copytree(root / name, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
for name in ('deploy', 'config', 'docs', 'web', 'patches', 'examples', 'data', 'tasks', 'tools'):
    target = stage / name
    if not target.exists():
        target.symlink_to(root / name, target_is_directory=True)
PY
cd /tmp/names-validation
```

每个测试目录单独执行，命令格式如下：

```bash
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 \
  /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/names
```

将末尾目录分别换为下表各项。源码完整的整合仓库可直接在仓库执行，不需要临时依赖补齐。

| 目录 | 结果 |
| --- | --- |
| `tests/names` | 47 通过 |
| `tests/directory` | 201 通过，22 个子断言通过 |
| `tests/channels` | 193 通过，14 个子断言通过 |
| `tests/ops_queue` | 27 通过 |
| `tests/operator` | 111 通过 |
| `tests/roles` | 274 通过 |

额外扩展检查有两项原有失败，均已用未修改的 `/srv/imgw-dev/ops/src` 单独复现：

- `tests/audit`：203 通过、1 失败。演练测试未隔离后加入的其他构建入口，因临时目录多出其他功能的数据库而失败。
- `tests/wechat_private`：34 通过、1 失败。`Search` 原有构造函数没有该测试要求的 `private_path` 参数。初次运行还被生产代码版本与旧发布校验不一致挡住；补测用 `/tmp/names-private-baseline`，其中 20 个源码文件均与发布清单的校验值完全一致，并通过 `WECHAT_PRIVATE_BASELINE` 指定。没有修改生产源码或发布清单。

私聊补测在上面的测试命令前另加 `WECHAT_PRIVATE_BASELINE=/tmp/names-private-baseline`，末尾目录用 `tests/wechat_private`。旧源码核验失败时不能跳过校验冒充通过。

以上两项涉及本任务未允许修改的知识检索或演练隔离内容，留给总指挥统一处理；不能宣称整仓全部通过。私聊新名称和国家显示另有本任务的端到端虚构测试覆盖。

前端已通过：

```bash
cd web
npm ci --ignore-scripts --no-audit --no-fund --cache /tmp/names-npm-cache
npm run build && npx tsc --noEmit
env CHROME=/tmp/ops-deals-browsers/chromium-1248/chrome-linux64/chrome \
  XDG_CONFIG_HOME=/tmp/names-chrome-config XDG_CACHE_HOME=/tmp/names-chrome-cache \
  node e2e/names.mjs
```

浏览器检查只访问本工作区构建和虚构接口。桌面 1440、手机 400 宽度均通过，提示文字及页面宽度正常。其他环境可用 `CHROME` 指定已安装的浏览器。

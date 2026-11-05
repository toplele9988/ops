# OP5 集成说明

## 改动文件

- `src/console/roles/dossier_links.py`（新增）：复用档案只读连接；提取微信群链接、映射类型、优先 legacy_map、检测群号/单位冲突。
- `src/console/roles/automatic.py`（新增）：规则扫描适配器；用 operator 时钟与既有游标表持久化 600 秒准入。
- `src/console/roles/triage.py`：档案规则优先、180 天群资格、冲突解释、避免买家合并覆盖档案归属。
- `src/console/roles/service.py`：注入档案路径、跳过已有身份的档案群、`use_ai=False` 入口、证据与指纹（区分人工模型/自动规则运行）。采用仍复用现有 decide。
- `src/console/operator/host.py`、`worker.py`：同 archive、shared_inbox 的 Roles 构造；可选 triage poll 与健康记录。
- `web/src/pages/Triage.tsx`：档案匹配计数、明确确认清单、串行采用、逐条进度与失败原因；替换原来不区分来源的一键确认。
- `web/e2e/fixtures.triage.mjs`、`web/e2e/ops-triage.mjs`（新增）：完全虚构的桌面/手机实点验证；无新增 API 请求字段。
- `web/dist/index.html`、`web/dist/assets/index-BKH6TQ4B.css`、`web/dist/assets/index-CCSF4Oop.js`：本次完整生产构建。
- `tests/roles/conftest.py`：角色测试默认档案路径指向各自 tmp_path 中的不存在文件，避免新增读取真实档案。
- `tests/roles/test_dossier_links.py`（新增）：档案优先、已有/新建单位、类型映射、legacy/name/alias、冲突、已有身份、资格边界、纯规则、持久节流、缺库/缺表等。
- `tests/operator/test_triage_worker.py`（新增）：组件可选性、成功/异常/恢复健康、缺库正常、重启节流、宿主无初始化副作用。
- `docs/ownership-suggestions.md`（新增）：中文规则、频率、生产文件清单及备份回滚。
- `INTEGRATION.md`（本文件）。

未更改路由、`src/console/v3.py`、`app.py`、`src/console/operator/engine.py` 或 `web/src/pages/Operator.tsx`。未读取或复制真实业务数据库，未调用真实模型、发送消息、改变网络配置或部署生产。

## 测试环境和命令

四个 Python 目录各自独立运行，未把有冲突的 conftest 合并。使用现有 `/tmp/ops-deals-venv/bin/python`。当前检出不含依赖模块 `console.media`，测试用 `/tmp/op5-test-support/sitecustomize.py` 给 `console.__path__` 追加 `/tmp/r7f-test-deps/console` 的现有 Python 代码依赖，不引入业务数据。辅助文件内容为：

```python
import console
console.__path__.append('/tmp/r7f-test-deps/console')
```

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:/tmp/op5-test-support /tmp/ops-deals-venv/bin/python -m pytest tests/roles -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:/tmp/op5-test-support /tmp/ops-deals-venv/bin/python -m pytest tests/operator -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:/tmp/op5-test-support /tmp/ops-deals-venv/bin/python -m pytest tests/dossiers -q
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:/tmp/op5-test-support /tmp/ops-deals-venv/bin/python -m pytest tests/ops_queue -q
```

| 检查 | 结果 |
| --- | --- |
| `tests/roles` | 274 passed（原有 243 项 + 新增 31 项） |
| `tests/operator` | 103 passed（含新增 4 项） |
| `tests/ops_queue` | 27 passed |
| `tests/dossiers`，直接使用当前 /opt 代码作为旧发布夹具 | 18 passed、2 failed；见下方既有基线漂移 |
| `tests/dossiers`，使用哈希校验过的隔离旧发布代码夹具 | 20 passed |
| 前端 `npm run build` | TypeScript 检查及 Vite 生产构建通过 |
| 前端 `node_modules/.bin/vitest run` | 53 passed |
| `web/e2e/ops-triage.mjs` | 1440px、400px 均通过；各串行请求 4 次（包含失败后重试） |

前端依赖复用现有 `/tmp/r7f-web-deps/node_modules`（工作区 `web/node_modules` 为指向它的忽略链接，无下载）。从 `web/` 执行：

```bash
npm run build
node_modules/.bin/vitest run
XDG_CONFIG_HOME=/tmp/op5-chrome-config XDG_CACHE_HOME=/tmp/op5-chrome-cache CHROME=/tmp/ops-deals-browsers/chromium-1248/chrome-linux64/chrome node e2e/ops-triage.mjs
```

两个 XDG 目录需预先 `mkdir -p`；只用于隔离浏览器的本地缓存。浏览器只连回环测试静态服务，所有 API 都由虚构 fixtures 拦截。覆盖取消确认不提交、精确筛选 0.9 边界、确认群→单位清单、正在处理时禁用重复操作、串行提交及 CSRF、单条失败继续后续条目、失败项保留和重试。

## dossiers 旧发布基线漂移的区分

`tests/dossiers/test_release.py::test_install_replay_and_rollback[original/production]` 从 `/opt/imgw/wechat` 复制旧版代码，再检查 `deploy/dossiers/*.baseline.sha256`。当前 `/opt` 的代码已变化；失败发生在第 33 行旧 ops_queue 哈希断言、进入发布前，与 OP5 运行逻辑无关。切回改动前保存的角色代码、使用同一环境重跑该文件，同样得到 **2 failed、1 passed**。

本次没有修改那些基线清单、旧发布测试或生产代码来绕过检查。额外使用已存在的 `/tmp/dossiers-wechat-baseline` 中两份**代码**作为夹具，先验证与包中 production 基线完全一致：

| 夹具代码 | SHA-256 |
| --- | --- |
| `console/v3.py` | `77db79b805ffc5619a0c5655fbda855b52b3241469ff7e50fcef2bbc91251b5b` |
| `console/ops_queue/service.py` | `7d2bdd54fde2633d43e3c75324bdce1e38a62ca79503847b1fc42dcad332d4fb` |

临时 pytest 插件 `/tmp/op5-dossier-test-support/op5_release_baseline.py` 只把旧发布测试从 `/opt` 复制这两份代码的来源换为该隔离目录，所有业务 SQLite 仍由原测试在 tmp_path 中生成。没有修改 `/opt`、仓库的 v3.py 或 ops_queue 源文件。旧测试自己仍执行原来的 companion 逆补丁、原版/生产版哈希断言、安装、重装和回滚。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:/tmp/op5-test-support:/tmp/op5-dossier-test-support /tmp/ops-deals-venv/bin/python -m pytest tests/dossiers -p op5_release_baseline -q
```

结果 **20 passed**。因此功能回归及两种发布重放均在正确隔离基线上通过；直接依赖当前 `/opt` 的旧包基线测试仍有上述环境失败，不宣称其原始命令已全部通过。临时插件和夹具只用于验证，不属于生产替换文件。

## 发布交接

生产文件清单、备份、频率和回滚步骤见 [docs/ownership-suggestions.md](docs/ownership-suggestions.md)。合并并行的总指挥改动后统一重建 dist；本次未覆盖其禁止修改的源码。自动建议没有直接采用路径，所有身份/单位写入必须经过现有人点击接受。

# 翻译任务交付说明

## 老板能看到的变化

打开会话就能看到中文译文。可以只看中文，刷新后保留选择。
列表已有中文译文就显示中文。额度用完只提示等待时间，已经译好的内容继续可看。

回复先生成英文。客户母语已确认且不是英语时，可以再附当地语言。
两段都有回译核对，数字、型号、金额有变化会标红。
采用只填进回复框，发送继续由人确认。

本次没有部署，没有调用真实模型，没有发送客户或供应商消息。
后台预翻译已提供，等待总指挥接入原有轮询。

## 改动文件

| 文件 | 用途 |
| --- | --- |
| `src/console/lang/service.py` | 自动翻译、只读预览、最近两天预翻译、双语起草和逐段核对 |
| `src/console/lang/storage.py` | 收信开关迁移、后台节流时间、中文额度提示 |
| `src/console/lang/translation.py` | 缓存只读模式、解析网关字符串内的 JSON 结果，兼容旧编号格式 |
| `src/console/lang/detection.py` | 跳过中文、我方文字、空消息和无须翻译的符号 |
| `src/console/lang/prompts.py` | 明确字符串回答内的翻译及识别格式 |
| `src/console/lang/schedule.py` | 五分钟一次、每次最多二十条的后台入口 |
| `src/console/lang/__init__.py` | 导出后台入口 |
| `src/console/inbox/ai.py` | 官方模型统一改经公共网关入口，核对返回来源；原有三次尝试上限保留 |
| `web/src/lang.ts` | 新字段、自动翻译请求和缓存预览请求 |
| `web/src/pages/Inbox.tsx` | 自动翻译展示、原文折叠、列表中文预览 |
| `web/src/pages/inbox/LanguagePanels.tsx` | 本页翻译状态、等待提示、双语草稿与回译核对面板 |
| `web/src/pages/inbox/Operations.tsx` | 在回复框接入草稿面板，保留原确认流程 |
| `web/src/pages/inbox/inbox.css` | 灰色小字译文和草稿面板样式 |
| `web/src/pages/LanguageSettings.tsx` | 两个设置开关及中文说明 |
| `tests/lang/test_lang_service.py` | 更新旧的母语未知及单语预期 |
| `tests/lang/test_lang_api.py` | 自动额度正常返回、缓存预览、未知母语英文稿的接口验收 |
| `tests/lang/test_translate_workflow.py` | 新增自动翻译、缓存、节流、双语及逐段保护检查 |
| `web/e2e/fixtures.inbox.mjs` | 仅虚构会话、内存缓存、双语草稿和额度状态 |
| `web/e2e/ops-inbox.mjs` | 电脑和手机浏览器验收 |
| `docs/lang.md` | 更新使用方法及接口说明 |
| `docs/integration-translate.md` | 本交付说明，按任务要求新增 |

所有源码改动均在允许范围内。原共享界面文件 `web/src/ui/LanguagePanels.tsx` 不在本次允许范围，
因此本次在允许的收件箱目录内实现并接入新面板；原文件无调用点，也未修改。
没有修改 `operator/worker.py`、`host.py`、根目录文档、路由挂载或其他任务源码。

## 新表与接口

沿用独立语言库，不改档案库。

- 原 `translation_cache` 直接复用，没有另建消息译文表。相同原文、语言和术语规则共用缓存。
- `settings` 增加 `auto_translate_incoming INTEGER NOT NULL DEFAULT 1`，首次写入自动迁移。
  旧库原有阅读语言和回复偏好继续保留；新库两个开关均开启。
- 新表 `pretranslate_schedule(id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL)`。
  只保存后台扫描时间，不存客户正文。
- 既有 `POST /api/v1/lang/translate` 增加 `mode=auto|cached|preview`，没有新增路由。
  自动模式一次处理当前会话最近 300 条消息中的对方外文，模型继续每次最多八块。
  缓存预览模式每次最多读取 40 个会话，只读已有缓存。
- 自动模式额度不足返回正常状态和中文提示：HTTP 200、`quota_exhausted`、`retry_after`、`hint`。
  人工翻译、识别和起草仍使用原有 429。
- `compose` 返回英文及可选当地语言，`versions` 内保存每段的回译和校验；
  `text` 是可采用正文，英文在上，中间空一行。未知母语只出英文。
- `Lang.pretranslate(limit)` 提供后台预翻译；`PretranslateSchedule.poll()` 提供持久节流。

完整表单及响应字段见 [语言说明](lang.md)。

## 总指挥接线

现有语言路由不需新增挂载。前后端应一并合并，避免旧页面继续按单语响应解释草稿。
设置变化在旧库首次写入时迁移，仅作用于语言库；先按原流程备份语言库即可。

后台入口没有自动启动。请在已有宿主装配和轮询处接入下面片段，放在现有线程池或后台执行位置，
不要阻塞页面请求。`shared_inbox`、`private_lang_path` 代表宿主中已经存在并核对过的对象与路径。
本次未修改宿主、操作员循环，也未创建任何定时服务。

```python
from console.lang import Lang, PretranslateSchedule

# 装配时创建一次；同前端使用相同语言库和收件箱。
translation = Lang(private_lang_path, inbox=shared_inbox)
translation_schedule = PretranslateSchedule(translation)

# 原有后台循环中调用；函数内部保证五分钟一次，每次最多二十条。
# 宿主沿用现有后台异常处理，不记录消息正文或模型输出。
translation_schedule.poll()
```

自动翻译关闭时，后台也不调用模型。多个宿主实例共用同一语言库时，共用扫描时间和翻译锁。
预翻译只读取收件箱已有快照，不专门拉取未打开会话的完整历史。

## 测试命令与结果

要求的原始命令已分别运行：

```sh
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/lang
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/operator
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/roles
```

三个目录均遇到快照原有缺包：`ModuleNotFoundError: No module named 'console.media'`，
在收集阶段停止。未改允许范围之外的源码来掩盖缺包。

为继续验证，在 `/tmp/translate-test-support/sitecustomize.py` 临时补入已存在的媒体源码依赖目录，
将媒体密钥位置指向临时目录，并禁止真实网络、非临时数据库及生产私有文件访问。
该辅助文件不属于仓库交付，不使用真实客户数据。每个测试目录单独执行：

```sh
env PYTHONPATH=src:/opt/imgw/wechat:/tmp/translate-test-support PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/lang
env PYTHONPATH=src:/opt/imgw/wechat:/tmp/translate-test-support PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/operator
env PYTHONPATH=src:/opt/imgw/wechat:/tmp/translate-test-support PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/roles
env PYTHONPATH=src:/opt/imgw/wechat:/tmp/translate-test-support PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/inbox_api
```

| 目录 | 结果 |
| --- | --- |
| 翻译 `tests/lang` | 135 通过 |
| 操作员 `tests/operator` | 111 通过 |
| 岗位 `tests/roles` | 274 通过 |
| 收件箱 `tests/inbox_api` | 37 通过，另有 111 个子测试通过 |

在完整仓库合并后，请去掉临时依赖路径，重新运行上面的原始命令。
本次测试中的模型均为注入的假模型；官方入口测试也替换了网关，不会扣真实余额。

前端使用已有临时依赖，构建产物和依赖目录均在忽略范围内，没有改包清单或锁文件。

```sh
cd web
npm run build && npx tsc --noEmit
```

结果：构建通过，单独类型检查通过。

浏览器脚本只启动随机本地端口的静态服务器，所有接口由虚构夹具拦截，外部请求直接拒绝。
电脑和手机各覆盖原收件箱流程、失败处理、自动翻译及双语草稿、额度与设置四组流程。
包括打开即见译文、仅看中文并记住选择、刷新不多扣额度、保留附件、中文不翻、列表中文预览、
英文母语与未知母语只出英文、其他母语出两段、检查失败不能采用、生成和采用都不会发送。
本机浏览器需要可写的临时配置目录：

```sh
XDG_CONFIG_HOME=/tmp/translate-browser-config \
XDG_CACHE_HOME=/tmp/translate-browser-cache \
CHROME=/tmp/ops-deals-browsers/chromium-1248/chrome-linux64/chrome \
node web/e2e/ops-inbox.mjs
```

其他机器把 `CHROME` 改为本机测试浏览器路径。
结果：电脑四组、手机四组，共八组通过；没有页面脚本错误或外部请求。
结果与截图在忽略目录 `tests/inbox/artifacts/`。

## 仍需人工核对

规则无法可靠分辨所有混合语言或中日共用汉字。国家信息也不能证明母语。
数字、型号、金额和回译检查可以发现片段变化，不能证明整段含义一定正确。
上线前仍需老板核对真实翻译质量。此次没有做真实模型质量验收，也没有部署。

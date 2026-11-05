# customers 交付记录

## 完成内容

客户页增加五个分段、数量、聊天发现的客户、国家和渠道、产品、北京时间、AI 总结及可点击原话；支持单个和批量建档。未部署画像接口时正常显示等待提示。原有客户资料、手工新建和合并入口保留。

## 文件

| 文件 | 内容 |
| --- | --- |
| `web/src/pages/Customers.tsx` | 分层、档案归属、完整证据、建档和失败恢复；保留原资料页 |
| `web/src/pages/Customers.css` | 沿用现有设计变量，桌面和手机布局 |
| `web/src/profiles.ts` | 画像契约类型和只读接口；未部署处理 |
| `web/e2e/fixtures.profiles.mjs` | 全虚构画像、故障开关和只读会话夹具 |
| `web/e2e/customers.mjs` | 真实浏览器点击、请求核对和截图 |
| `web/e2e/fixtures.parties.mjs` | 补齐真实建档接口的同名/归属约束；可模拟保存成功后响应中断 |
| `web/e2e/ops-parties.mjs` | 原客户工作台验收从 `?view=records` 进入，其他流程保留 |
| `web/e2e/fixtures.mjs` | 只增加画像夹具注册 |
| `docs/customers-page.md` | 页面规则、接口和验收方式 |
| `docs/integration-customers.md` | 本交付记录，按通用交付要求新增 |

未修改 `nav.ts`、`App.tsx`、共享客户/供应商组件或后端源码。没有新增表或后端接口，没有部署或发送消息，没有调用模型。所有截图和数据都来自虚构夹具。

## 接线

画像任务按既定契约提供 `GET /api/v1/profiles?kind=customer` 与 `GET /api/v1/profiles/<conversation_key>` 即可。列表不依赖任何额外的 `party_id` 或档案字段；档案归属读取现有往来单位接口。`conversation_key` 必须与现有收件箱及档案接口一致。列表支持完整无分页契约；若将来画像接口分页，需同步扩展封装。

真实建档已确认：`POST /api/v1/parties-ops` 的 `conversation_key` 会在同一事务里调用 `_link`。本页不额外调用第二次 link。已有同名资料须核对后在保留的资料页关联，不能静默合并。旧 `conversation_ref` 由资料接口投影到 `identities`；若将来会话列表能直接返回这类归属，可移除本页按需补读，减少请求。

分段数量使用合并后客户行口径，已建档客户按归属去重；画像接口的 `counts` 是会话数量。本页原话跳转到正确会话，现有收件箱未提供按消息索引定位的入口；无须改导航或路由。

## 验证

前端命令：

```sh
cd web
npm run build
npx tsc --noEmit
PLAYWRIGHT_BROWSERS_PATH=/tmp/inbox-browsers node e2e/customers.mjs
PLAYWRIGHT_BROWSERS_PATH=/tmp/inbox-browsers node e2e/ops-parties.mjs
```

| 检查 | 实际结果 |
| --- | --- |
| `npm run build` | 通过 |
| `npx tsc --noEmit` | 通过 |
| `e2e/customers.mjs` | 19 个场景通过，桌面 1440、手机 390，无脚本异常或整页溢出 |
| `e2e/ops-parties.mjs` | 桌面、手机及空态通过；各 18 次写请求、6 次确认 |

浏览器结果在 `artifacts/customers/browser-results.json` 和 `artifacts/parties/browser-results.json`；两种尺寸的截图在 `artifacts/customers/`，包括 `desktop-potential.png`、`mobile-potential.png`、`desktop-evidence.png`、`mobile-evidence.png`、`desktop-404.png`、`mobile-404.png`。浏览器使用严格 CSP、本地静态服务和虚构请求，无真实业务后端。截图容器缺少 emoji 字体，部分国旗显示为方框；页面文本和浏览器断言包含正确的国旗 emoji，中文国名正常显示。

按任务要求分别执行后端原命令：

```sh
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/operator
env PYTHONPATH=src:/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/roles
```

两条原命令均在收集时被公开快照缺少 `console.media` 阻断。没有修改后端来掩盖这一问题。

补充验证复用了 `docs/integration-ownership.md` 已记录的本机临时源码依赖：`/tmp/op5-test-support/sitecustomize.py` 只给 `console.__path__` 追加 `/tmp/r7f-test-deps/console`；其中仅包含四份媒体 Python 源码，不引入客户数据。媒体密钥固定到临时测试路径。分别执行：

```sh
env PYTHONPATH=/tmp/op5-test-support:src:/opt/imgw/wechat OPS_MEDIA_KEY_PATH=/tmp/customers-test-media.key PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/operator
env PYTHONPATH=/tmp/op5-test-support:src:/opt/imgw/wechat OPS_MEDIA_KEY_PATH=/tmp/customers-test-media.key PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest -q -p no:cacheprovider tests/roles
```

结果：`operator` **111 passed in 21.29s**；`roles` **274 passed in 28.78s**。这是补充依赖环境的结果，不代表干净快照已补齐依赖。临时目录不保证跨会话保留；总指挥需在完整依赖环境再次跑原命令。

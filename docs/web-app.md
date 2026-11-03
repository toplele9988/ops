# 运营台前端（web/）

前后端分离的新版运营台。当前真实源码是 React + React Router + TanStack Query，使用本地 CSS 与 Lucide 图标；后端通过 `/api/v1/*` 的 JSON 接口提供数据。应用挂在现有控制台的登录和密码复核之后，不单独开生产端口。

## 导航分区

导航的唯一来源是 `web/src/nav.ts` 中的 `NAV`，路由元数据另外保留隐藏页面：

- **运营台（front）**：固定九项，依次为总览 `/`、商机沟通、案子、客户、供应商、产品、备货与发货、知识检索、AI 员工。
- **后台设置（back）**：渠道与数据（渠道接入、数据管道健康、阿里订单接入、知识中枢）、设置（语言设置、账号与权限）、整理工具（待整理会话、待分类、微信通讯录、数据体检、货代运价、价格建议、供应商包装核对、需求与验收）。顶部切换进入 `/system` 分组目录，不占侧栏入口。

`kind` 含义：`live` 有实现页面（是否可读取生产数据以接口状态为准）；`plan` 是保留 URL 的规划占位页，不进导航；`external` 只有账号与权限 `/v3/admin`，明确标记「旧后台」。其他旧版工具、旧后台外链、怎么用、灵感库、工作台和规划占位均摘除导航。页面代码和 URL 保留，工作台改为 `/workbench`；其独有运营内容并入总览「今日运营」。`/overview` 为总览兼容别名。完整映射见 `docs/web-entrypoints.md`。

后台归属优先读取页面元数据，因此 `/triage`、`/classify` 等原 URL 打开后会正确显示后台设置导航，不依赖 `/system` 前缀。共享收件箱保持会话、对话、客户资料三栏，小屏可展开资料；案子按阶段显示卡片；客户/供应商展示联系人资料与已有会话/报价时间线。

## 严格 CSP 与样式

控制台的全局响应头强制 `script-src 'self'; style-src 'self'`。当前构建直接由 Vite 导出静态 CSS，无运行时样式注入；基础样式在 `web/src/styles/`，总览和管道另有页面 CSS。未使用外部字体或第三方在线脚本。

`style={{...}}` 属性写法由 React 通过脚本设置，不受该限制。

## 构建与部署

```
cd web && npm install && npm run build   # 产物在 web/dist
```

生产现有挂载：`/var/lib/imgw/ops-app/dist` 由 `console/v3_app.py` 在 `/app/` 下提供（需登录）。写入类请求带 `X-CSRF-Token`，令牌由服务端在发 index.html 时写进 `<meta name="csrf-token">`。本 UX 任务仅构建 workspace 的 `web/dist`，不复制到生产、不部署。登录落点候选补丁及回滚说明见 `patches/ux-root/README.md`。

## 本地验收

在 `web/` 运行：

```sh
npm run build
npx tsc --noEmit
PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" node e2e/smoke.mjs
```

缺少浏览器时先运行 `PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" npm_config_cache="$PWD/.npm-cache" npx --no-install playwright-core install chromium`。也可设置 `CHROME` 指向已有 Chromium。不要运行 `tsc -b`。

冒烟脚本自动创建 loopback 静态服务，仅服务当前 workspace 的构建，响应严格 CSP。每个 API 都被纯虚构夹具拦截，未登记 API 会使检查失败；不接受 BASE 指向其他环境，不访问真实后端。桌面 1440×900、手机 400×860，检查全部页面和保留 URL、导航数量顺序与后台分组、总览故障场景、三栏/小屏资料展开、看板和联系人时间线。浏览器脚本异常、CSP 错误或整页溢出均失败。结果、日志与截图在 `artifacts/ux-smoke/`；截图路径清单见 `docs/ux-acceptance.md`。

不要用 `scripts/e2e_smoke.sh` 完成本任务的本地验收：该历史脚本会写生产静态目录。纯夹具验收证明前端行为，不代表生产数据和渠道已接通。

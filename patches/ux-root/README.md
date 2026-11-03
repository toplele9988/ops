# UX1：根入口与登录落点候选补丁

2026-11-02，以 imgw 只读核对 `/opt/imgw/wechat/console`。**未应用生产、未部署、未重启服务。** 不涉及后端业务逻辑、数据库迁移或网络配置。

## 真实原因与挂载位置

- `console/app.py:881–885` 的 `_to_new_app` 已经返回 **302 `/app/`**，基础 routes 在 892 行将它挂到 `/`。现有旧首页函数 `overview` 还在 264 行，但未挂到根入口。
- `console/password_candidate/access_app.py:103` 的密码登录成功分支直接 **303 `/v3/workbench`**，所以新登录绕过了根入口。这是当前源码中落到旧工作台的路径。
- `console/v3.py:12–13` 的 redirect 负责 `/v3`、`/v3/`，也已 302 `/app/`；本补丁不改它。
- `console/v3.py` 在各 API 后、admin 前调用 `v3_app.build(app_module)`；`console/v3_app.py:72–74` 依次挂 `/app`、`/app/`、`/app/{path:path}`，构建目录是 `/var/lib/imgw/ops-app/dist`，页面响应注入当前 CSRF token 并执行 `password_recheck`。
- `console/app.py:910–911` 将 `v3.build()` 加在基础 routes 后，之后还有旧图片/知识路由；repair 与 CPQ 路由插入列表首部。已核对 CPQ 的显式前置路由没有根路径通配处理，不覆盖 `/`。
- 最终先建 `Starlette(routes=routes)`，再套 `SecurityMiddleware`，再通过 `password_candidate.runtime.build` 建 `PasswordMux`。调用方向为 **PasswordMux → LegacyGate → SecurityMiddleware → 路由**。`/access/*` 由 mux 交给 Access，LAN 用同一 mux 的 LAN 模式。无密码会话的 GET 先由 LegacyGate 303 到 `/access/login`。

补丁 `production-entrypoints.patch` 仅改两处：

1. `console/app.py` 基础 routes 的 `/` 后新增 `Route('/legacy', overview, methods=['GET'], name='legacy_overview')`，让原旧首页拥有明确 URL，继续经过原认证门。
2. `console/password_candidate/access_app.py` 把**成功验证之后**的 RedirectResponse 目标从 `/v3/workbench` 改成 `/`，保留 303、cookie 和其余语句。正常链为 `POST /access/login → 303 / → 302 /app/ → 200 新版总览`。

已登录 GET `/` 继续 302 `/app/`；未登录仍走原登录。没有新建公开路由、改 middleware 顺序或放宽认证；原表单/JSON CSRF 检查、同源检查、cookie 属性、密码限速、密码复核、注销及会话撤销代码均保留。`/v3/workbench` 等旧业务 URL 保留。

## SHA256 基线

`baseline.sha256` 使用相对 `/opt/imgw/wechat` 的路径。包含两个待改文件及所核对的 v3、静态入口与认证适配文件；没有生产配置、账号、令牌或真实业务数据。

| 待改文件 | 基线 SHA256 |
| --- | --- |
| `console/app.py` | `feecec2ee2b714b13b4ca6081a47db1f7104a6f029c461a0fa3dd78755ef46c6` |
| `console/password_candidate/access_app.py` | `eddb066c9e35300a322c96cb12a5002e0e14a20669001be3a1144fe09e1f9b08` |

未来另行批准发布时，应先用 `sha256sum -c /absolute/path/to/baseline.sha256` 在 `/opt/imgw/wechat` 核验全部基线；不匹配就停止，重新只读审查并生成补丁，禁止强套或忽略 fuzz。再将代码复制到发布暂存目录，执行 `patch --batch --fuzz=0 -p1 --dry-run -i /absolute/path/to/production-entrypoints.patch`。本任务只在 workspace 副本执行这些检查。

## 本地测试（本次已执行）

在任务 workspace 根目录：

```sh
/opt/imgw/venv/bin/python patches/ux-root/test_entrypoints.py
cd web
npm run build
npx tsc --noEmit
PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" node e2e/smoke.mjs
```

若缺浏览器，可先在 `web/` 执行 `PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" npm_config_cache="$PWD/.npm-cache" npx --no-install playwright-core install chromium`。浏览器安装、静态服务、数据库和截图都只写 workspace。没有运行 `tsc -b`，没有调用会复制产物到生产的 `scripts/e2e_smoke.sh`。

Python 测试先核对生产源码 SHA256，只复制源码到 `artifacts/ux-root/` 临时目录，零 fuzz 应用补丁并编译，再用真实 PasswordMux、Access、LegacyGate、PasswordStore 与抽出的原 SecurityMiddleware/根路由代码搭建隔离应用。settings、旧会话库和账号都是临时虚构对象；不 import 生产 app、不读取生产认证配置或业务库。旧首页的业务渲染在测试中用哨兵替代，只证明路由与认证保留。

九项测试覆盖：HTTPS 登录到新页面、LAN IP:端口同一跳转链、未登录的 `/`/`/app/`/`/legacy`/旧工作台门禁、登录 CSRF/Origin/错误密码、业务表单与 JSON CSRF、密码重置后会话撤销、`/app/` 响应前密码复核、旧首页和旧工作台可达、`/v3`/`/v3/` 跳转及反向补丁恢复全部基线。日志：`artifacts/ux-root/test.log`。

`web/e2e/smoke.mjs` 自建 loopback 静态服务，只读取本 workspace 的 `web/dist`，所有 API 由虚构夹具拦截；不接受 BASE 覆盖。覆盖桌面/手机全部新版路由、保留页、规划占位 URL、详情页以及总览故障场景，同时断言九项运营导航顺序、十四项后台导航分组、唯一账号旧后台外链和旧总览别名高亮。报告和截图：`artifacts/ux-smoke/`。

这些结果不代表生产已上线或已用真实账号登录验收。将来发布后应由现场操作人员用正常授权会话确认输入 IP:端口、登录、302 链、总览、旧首页、账号页、合法写入与错误 CSRF 拒绝；不要用冒烟脚本连接生产或发送真实测试消息。

## 回滚方法

本任务未改生产，因此当前没有生产回滚操作。未来若批准上线：

1. 发布前备份上述两个待改源码文件，记录哈希；前端静态产物单独备份，根补丁与前端总览要作为配套版本审查。
2. 先在暂存副本执行 `patch --batch --fuzz=0 -R -p1 --dry-run -i /absolute/path/to/production-entrypoints.patch`，再反向应用，检查 `baseline.sha256` 全部通过。自动测试已验证该反向操作。
3. 通过既有发布流程恢复这两个源码文件和需要回退的前端产物，再按现场受控流程重新加载对应服务；本 README 不执行这些动作。
4. 回退后确认原登录成功 303 `/v3/workbench`，已有 `/` 和 `/v3/` 仍跳 `/app/`；确认登录、账号、CSRF、会话撤销和旧链接。只回退代码及静态产物，不覆盖任何业务或认证数据库。若回滚前又有源码改动，先重新审查差异，不强行反向套补丁。

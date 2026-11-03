# UX1 / UX2 / UX3 本地验收与截图

2026-11-02，开发候选，未部署。入口映射见 [web-entrypoints.md](web-entrypoints.md)，根入口补丁与回滚见 [ux-root README](../patches/ux-root/README.md)。

## 验收范围

| 检查 | 结果 / 证据 |
| --- | --- |
| `cd web && npm run build` | 通过；`artifacts/ux-smoke/build.log` |
| `cd web && npx tsc --noEmit` | 通过；未运行 `tsc -b`；`artifacts/ux-smoke/typecheck.log` |
| `web/e2e/smoke.mjs` | 108 个视图通过：43 个页面、筛选、详情和保留 URL × 桌面/手机，另加 22 个总览故障边界场景 |
| 运营台入口 | 精确九项，顺序为总览、商机沟通、案子、客户、供应商、产品、备货与发货、知识检索、AI 员工；每页都有 DOM 数量、文字与 URL 顺序断言 |
| 后台设置 | 十四项，按渠道与数据、设置、整理工具分组；整理工具旧 URL 能切换到正确后台导航；唯一 `/v3/` 导航链接为注明旧后台的账号与权限 |
| 首页及旧 URL | `/app/` 为总览，`/app/overview` 同页并高亮总览；`/app/workbench`、guide、inspiration、legacy 和四个规划占位 URL 保留并纳入冒烟 |
| 业务页面 | 桌面收件箱三栏顺序、手机资料展开/收起、案子阶段看板、客户与供应商时间线均有断言；检查脚本异常、CSP 错误与整页横向溢出 |
| 根入口补丁 | 九项 Python 隔离测试通过，含 HTTPS/LAN IP:端口登录链、未登录门禁、旧首页/旧工作台、CSRF、密码重置/复核、v3 跳转与反向补丁哈希恢复；`artifacts/ux-root/test.log` |
| 生产文件 | 只读；`patches/ux-root/baseline.sha256` 核对通过；补丁未应用生产 |

浏览器数据由 `web/e2e/fixtures.mjs` 和 `web/src/fixtures/overview.demo.ts` 虚构。只服务 workspace 构建产物的 loopback 静态服务带严格 CSP，所有 API 请求均在浏览器内被夹具响应，未登记 API 使验收失败。不连接真实业务接口，不创建真实订单或发送消息。旧首页业务渲染在 Python 测试中使用哨兵，仅验证路由与认证链。

生产实际 IP:端口登录、真实数据和渠道状态仍待另行发布后核验，因此需求账本 UX1 保持 partial，明确记录候选补丁已完成但未部署；UX2、UX3 为已完成的前端候选。

## 截图路径

下列路径均相对任务 workspace，桌面为 1440×900，手机为 400×860。总览、看板和联系人详情采用整页截图；手机导航截图等待抽屉完全展开，其余截图等待抽屉完全收回。图中名称、消息和数字均来自虚构夹具。

| 页面 | 桌面 | 手机 |
| --- | --- | --- |
| 首页总览与今日运营 | `artifacts/ux-smoke/桌面-全局总览.png` | `artifacts/ux-smoke/手机-全局总览.png` |
| 商机沟通三栏/对话 | `artifacts/ux-smoke/桌面-会话-客户.png` | `artifacts/ux-smoke/手机-会话-客户.png` |
| 案子管道看板 | `artifacts/ux-smoke/桌面-案子列表.png` | `artifacts/ux-smoke/手机-案子列表.png` |
| 客户资料与时间线 | `artifacts/ux-smoke/桌面-客户资料.png` | `artifacts/ux-smoke/手机-客户资料.png` |
| 供应商资料与时间线 | `artifacts/ux-smoke/桌面-供应商资料.png` | `artifacts/ux-smoke/手机-供应商资料.png` |
| 后台设置目录 | `artifacts/ux-smoke/桌面-系统总览.png` | `artifacts/ux-smoke/手机-系统总览.png` |
| 手机九项运营导航 | 首页桌面截图中可见完整侧栏 | `artifacts/ux-smoke/手机-运营台导航.png` |
| 手机后台分组导航 | 后台设置桌面截图中可见完整侧栏 | `artifacts/ux-smoke/手机-后台设置导航.png` |

所有页面截图的完整文件清单在 `artifacts/ux-smoke/screenshots.md`；机器报告为 `artifacts/ux-smoke/result.json`，逐页结果为 `artifacts/ux-smoke/run.log`。`artifacts/` 是本地验收产物，不进入仓库。

复现命令见 [web-app.md](web-app.md) 的「本地验收」。根入口测试独立执行：

```sh
/opt/imgw/venv/bin/python patches/ux-root/test_entrypoints.py
```

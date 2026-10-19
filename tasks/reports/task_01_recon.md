# TASK-01 Step 0 勘察报告(2026-10-19,现场总工头)

## 结论
工单 §3.3 的目录与前提按"通用 app/ 包"假设编写,和现网不符。**不能原样派给 Codex**,需先确认下列偏差的处理方式。

## 现网事实
| 项 | 工单假设 | 现网 |
|---|---|---|
| 应用根 | `app/main.py:create_app` | `/opt/imgw/wechat/console/`(app.py 857 行、data.py、v3*.py;Starlette 路由表,无 create_app 工厂) |
| 模板/静态 | `app/templates`, `app/static` | `console/templates`(含 v3/)、`console/static`(app.css、app.js、v3、vendor) |
| 鉴权 | Relay 注入头 + 角色 | 控制台自带登录会话(auth.py,sessions.sqlite3),**没有角色体系**;`cpq.*` 权限需新建 roles 机制 |
| CSP | 待查 | `default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'`(auth.py:28);`style-src 'self'` 意味着 JS 设置 `el.style` 在 CSP 下可用,但 `style=` 属性不可用 |
| 生产库 | `XL_DB_PATH` | `/var/lib/imgw/video_knowledge_auto/equipment/catalog.sqlite3`(属主 imgw,cz 需 sudo 读) |
| 频闪灯数 | 37 | `equipment_specs.fixture_type='strobe'` 有 931 行;凤舞供应商 S2 的有价灯具 37 款(`cat_supplier_offer` 共 39 条,0 条无有效期)。"37"必须以 S2 报价集合定义,不能用 fixture_type |
| 图片 | multimedia_assets | 43 张 `supplier_image` 已登记(上一期),经 `/media/fengwu/<型号>.png` 提供 |
| 运价/汇率/包装 | 新建 cpq_* 表 | 现网已有 **空表** `cat_freight_rate`、`cat_fx_rate`、`cat_packing`(CPQ 核心运行时);工单新建 `cpq_freight_lane/cpq_fx_rate/cpq_packing_profile` 与之重复,需决定复用或并存 |
| 成本字段 | cost_basis_cny | `cat_supplier_offer.raw_cost_rmb_fen`(单位 **分**),经 CPQ 核心 IPC 访问;控制台进程不应直读成本 |
| 依赖 | weasyprint/pillow/pypdf/yaml | **weasyprint 未装**(venv 是 imgw 属主,可装;pango 系统库已在);node v22 已有 |
| 派单 | Codex 读仓库全文 | swarm 的 Codex **没有任何工具**,只看 `--repo-file` 列出的文件和任务文本,返回完整新文件内容与 diff,不会写盘;工单正文与 recon 必须内联进任务体,且单批产出过大会超时(上期单任务约 22 分钟) |

## 必须先定的决策
1. **目录映射**:`app/` → `console/`(新代码放 `console/strobe/`、`console/cpq/`),`tests/cpq` 放镜像测试目录。建议采用。
2. **角色**:控制台登录用户目前等同"业主"。建议先把唯一登录者映射为全部权限,`roles.yaml` 留给将来;但 cpq.view_cost 对销售员隔离的测试仍按工单实现。
3. **运价/包装/汇率**:现网全空。工单规定缺数据时引擎阻断(`LANE_UNPRICED`/`PACKING_COST_MISSING`/`FX_EXPIRED`)。结果:**上线后所有报价都会被阻断,直到业主填入真实货代报价、汇率、航空箱数据**(§5.7 清单)。我不能编造这些数。
4. **复用 vs 新建表**:建议复用 `cat_fx_rate`(cny_per_usd_micro)、`cat_freight_rate`、`cat_packing`,只新建工单特有的表。
5. **weasyprint**:需要 `sudo -u imgw /opt/imgw/venv/bin/pip install weasyprint`。属于新增依赖,需业主批准。
6. **规模**:三批合计约 40 个文件,真实工期以天计,不是一次会话。建议拆为 A(引擎+数据+API)→ B(前端)→ C(导出+函件)三个独立验收节点。

## 已做 / 未做
- 已做:拉取工单、移入 `tasks/pending/`、勘察。
- 未做:生产库备份(仅在派单前执行)、任何 Codex 派单、任何部署。

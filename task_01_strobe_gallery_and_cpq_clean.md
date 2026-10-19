# TASK-01 · 频闪灯产品画廊 + Freight-First CPQ 报价中枢

| 字段 | 值 |
|---|---|
| 工单编号 | `TASK-01` |
| 归档路径 | `tasks/pending/task_01_strobe_gallery_and_cpq.md` |
| 签发 | 项目负责人 / 总架构师 |
| 执行 | 执行协调器（以下简称 **OC**）负责调度；主力施工 Codex（Astra） |
| 调度命令 | `/usr/local/bin/swarm run codex --class code --tier deep --effort high` |
| 范围 | 已入库 37 款频闪灯 → 画廊（卡片/表格双模）+ 全屏灯箱 + 右侧 CPQ 抽屉 + 对客报价导出（PDF / 自包含 HTML / WhatsApp 文案 / 图片包）+ Sol 英文函件 |
| 交付模式 | 交钥匙：全部测试通过 + 验收报告提交后，工单移入 `tasks/done/` |

> **本工单是唯一权威规格。** Codex 必须完整阅读本文件全文（§1–§6），其中所有“必须 / 严禁”均为强制条款。规格与现有代码冲突时，以“不破坏现有页面”为前提遵从本工单；无法调和的冲突写入验收报告的「阻塞项」，不得擅自降级规格。

---

## 1. 【业务目标与交付定义】

### 1.1 要解决的痛点

1. **报价是“裸数字”**：业务员给海外买家只发价格，买家还要反复追问外观、灯珠、通道、防水、包装体积，成交周期被拉长。
2. **运费算不准、算得晚**：频闪/帕灯货值低、航空箱体积大，DDP 运费常占货值 30%–70%，甚至高于出厂价。先报货价再补运费，常常一单做亏。
3. **出厂底价有泄露风险**：手工拼报价单时，供应商名称、工厂型号、人民币底价容易被复制进对客文件，买家可能直接绕过我们找工厂。

### 1.2 本期交付物（全部必须交付）

| # | 交付物 | 说明 |
|---|---|---|
| D1 | 频闪灯画廊页 `/strobes` | 37 款真实频闪灯；卡片画廊 ↔ 高密表格一键切换；筛选、搜索、排序 |
| D2 | 全屏灯箱 | 点击任一产品图打开；缩放、平移、键盘与手势切换、缩略图条、参数浮层 |
| D3 | CPQ 算价抽屉 | 右侧滑出；多行报价篮；纸箱/航空箱换算与对比；海运 LCL/FCL、快件测算；汇率缓冲；毛利滑块与反算；告警与闸门 |
| D4 | 服务端权威算价引擎 | Python 与 JS 同一算法，黄金向量对齐（误差 ≤ 0.01 USD） |
| D5 | 报价冻结与对客导出 | 冻结报价 → 对客 PDF、自包含 HTML、WhatsApp 文案、图片 ZIP；“图、文、参、价、运”五位一体 |
| D6 | Sol 英文商务函件 | 只喂白名单对客数据；输出过泄露扫描与数字一致性校验；人工确认后才可复制，**永不自动发送** |
| D7 | 底价防泄露断路器 | 白名单 DTO + 渲染后全文扫描；命中即拒绝导出并写审计日志 |
| D8 | 安全媒体路由 | 不透明资产 ID、缩略图、路径穿越防护、鉴权 |
| D9 | 测试套件 + 验收报告 | §5 全部测试通过；`tasks/reports/task_01_acceptance.md` |

### 1.3 交钥匙验收标准（Definition of Done）

全部满足才算完成，任何一项不满足 = 未完成：

1. `pytest tests/cpq -q` 全绿，**零 skip、零 xfail**（§5.6 有防篡改检查）。
2. `pytest tests/live -m live -q` 全绿：生产库快照中频闪灯数量 = 37，每款至少 1 张可对客的有效图片，或在验收报告中逐款列明缺图原因并由 `IMAGE_MISSING` 闸门正确拦截。
3. 对 37 款逐一生成对客导出，泄露扫描零命中。
4. 页面在 1440px 桌面与 390px 手机宽度下无横向滚动，无控制台报错，无 CSP 违规（OC 用无头浏览器截图并检查 console，截图附在验收报告）。
5. 数据库迁移只做增量（只有 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`），迁移前已做 SQLite 在线备份。
6. 现有页面与路由行为不变（含旧 `/media/<supplier>/<model>.png`）。
7. 验收报告包含：测试输出摘要、规格数据审计表（§4.6）、截图、阻塞项（无则写“无”）。

### 1.4 明确不在本期范围

- 询盘意图识别（DeepSeek/Qwen 并发池）、供应商资料清洗：后续工单。
- 对外公开分享报价链接：本期导出文件由内部人员下载后自行发送。
- 运费单独列行的报价呈现：本期只做“含运费单价”呈现，并在条款中写明包含项。
- 运价、汇率的自动抓取：本期为人工维护 + 过期告警。

---

## 2. 【UI/UX 交互与视觉规格】

### 2.1 全局视觉规范

- 框架：Tabler（Bootstrap 5），**仅使用仓库内本地静态文件**；图表 ECharts 5.5 本地文件。不新增任何第三方前端库（灯箱、抽屉、滑块均自研或用 Bootstrap 原生组件）。
- 色彩：沿用 Tabler 主题变量；状态色统一：
  - `ok` 绿 `--tblr-success`；`warn` 琥珀 `--tblr-warning`；`heavy` 橙 `--tblr-orange`；`fuse` 红 `--tblr-danger`；`info` 蓝 `--tblr-info`。
- 支持浅色/深色（跟随 Tabler 的 `data-bs-theme`），所有自定义色走 CSS 变量。
- 数字使用 `font-variant-numeric: tabular-nums`；金额右对齐。
- **CSP 兼容硬规则**：模板内禁止 `style="..."` 属性、禁止内联 `<script>` 代码、禁止 `onclick=` 等内联事件。页面初始数据用 `<script type="application/json" id="...">` 承载（不执行，不受 script-src 限制）；动态样式只能用 JS 的 CSSOM（`el.style.x = ...` 或切换 class）。

### 2.2 页面布局 `/strobes`

```
┌──────────────────────────────────────────────────────────────────────┐
│ 顶栏：标题「频闪灯 Strobe · 37」  [搜索框]  [视图切换 ▦ ☰]  [报价篮 (n)] │
├──────────────────────────────────────────────────────────────────────┤
│ 筛选条（chips，可多选）：IP 等级 | 光源（RGB / W / RGB+W 双光源）      │
│   | 像素分段 | 功率区间 | DMX 最大通道 | 有航空箱方案 | 规格完整度      │
│ 排序：默认 / 功率 / 灯珠总数 / 分段数 / 重量                            │
├──────────────────────────────────────────────────────────────────────┤
│ 卡片网格（≥1400px 4 列，≥992px 3 列，≥576px 2 列，手机 1 列）          │
│ 或 高密表格                                                            │
└──────────────────────────────────────────────────────────────────────┘
                                              ┌────────── CPQ 抽屉 ─────┐
                                              │ 从右侧滑出，见 §2.6       │
                                              └──────────────────────────┘
```

- 首屏由服务端 Jinja 直接渲染卡片与表格（无 JS 也能看到内容），JS 只负责筛选、排序、视图切换、灯箱与抽屉。
- 视图模式、筛选状态存 `localStorage`（读写全部包 try/catch；读不到时回落默认值）。URL 同步查询参数 `?view=table&ip=IP65,IP66`，可复制链接给同事。

### 2.3 卡片（Card）规格

```
┌────────────────────────────┐
│ [IP66]            [RGB+W]  │ ← 左上 IP Badge；右上光源 Badge
│                            │
│      产品实物图 4:3        │ ← object-fit: contain，浅中性底
│   （hover 时淡入第 2 张图）  │
│                            │
├────────────────────────────┤
│ STB-012  1000W LED Strobe  │ ← 对内显示内部型号 + 产品名
│ ┌──────────┐┌────────┐     │
│ │768 RGB + │ │48 段像素│    │ ← 参数标签 chips
│ │288 W     │ └────────┘     │
│ └──────────┘┌──────────┐    │
│ │DMX 4/8/16/30ch│ │1000 W│  │
│ 净重 9.8 kg · 纸箱 2 台/箱   │
│ [🔍 大图]   [＋ 加入报价]    │ ← hover 时浮现（触屏常驻）
└────────────────────────────┘
```

- **IP Badge 配色**：IP20/IP2x → 灰；IP54–IP65 → 蓝；IP66/IP67 → 青；IP68 → 深青并带水滴图标。未知 → 不显示 Badge（不得猜测）。
- **光源 Badge**：RGB 与白光颗数都 > 0 → `RGB+W 双光源`；只有 RGB → `RGB`；只有白光 → `W`；未知 → 不显示。
- **参数标签**：最多 4 个，优先级：双光源灯珠组合 > 像素分段 > DMX 通道模式 > 功率。缺失字段不显示标签，不显示“0”。
- **hover**：卡片上浮 2px + 阴影加深（`transition: 150ms`）；若有第 2 张图，交叉淡入；操作按钮从底部淡入。尊重 `prefers-reduced-motion`（关闭动画）。
- **图片**：卡片用 640px 缩略图（§4.2），`loading="lazy"`、`decoding="async"`；加载前显示骨架屏；加载失败显示“暂无图片”占位并在卡片角标显示 ⚠。
- **规格完整度**：关键字段（见 §4.6）缺失时卡片右下角显示小圆点 ⚠，hover 提示缺失项。

### 2.4 高密表格（Table）规格

- 列：缩略图 48px | 内部型号 | 产品名 | IP | RGB 颗数 | 白光颗数 | 分段 | DMX 模式 | 功率 W | 净重 kg | 纸箱规格（台/箱 · 尺寸 · 毛重）| 航空箱方案（有/无/估算）| 完整度 | 操作（大图 / 加入报价）。
- 表头吸顶；点击列头排序（再次点击切换升降序）；列显示开关（下拉勾选，存 localStorage）。
- 行高紧凑（`table-sm`）；斑马纹；hover 高亮整行；点击缩略图打开灯箱。
- 手机宽度：表格容器内部横向滚动（页面本身不得横向滚动），首列吸左。

### 2.5 全屏灯箱（Lightbox Modal）

- 基于 Bootstrap `modal-fullscreen`，深色半透明背景（`rgba(0,0,0,.92)`），自研交互：
  - 打开：点击卡片图片 / 表格缩略图 / 「🔍 大图」。默认加载 1280px 版本，点击「原图」按钮加载原图。
  - 缩放：滚轮缩放（1×–5×，以光标为中心）；双击在 1× 与 2.5× 之间切换；触屏双指捏合缩放、单指拖动平移；放大时拖动平移不越界。
  - 切换：← → 键、左右箭头按钮、触屏左右滑（1× 时）；底部缩略图条，当前项高亮。
  - 关闭：Esc、右上 ×、点击背景空白处（放大状态下点击背景不关闭）。
  - 参数浮层：按 `I` 键或「参数」按钮切换，左下半透明面板显示该型号关键参数（与卡片同源数据）。
  - 计数：右上 `3 / 7`。
  - 操作：「＋ 加入报价」直接加入报价篮；内部「标记为不可对客」（需 `cpq.admin` 权限，用于有工厂 Logo/水印的图，见 §4.2.4）。
- 无障碍：`role="dialog"`、`aria-modal`，焦点锁定在灯箱内，关闭后焦点回到触发元素。
- 预加载前后各 1 张。

### 2.6 右侧 CPQ 算价抽屉

基于 Bootstrap `offcanvas-end`；宽度：桌面 560px，≤768px 时 100%。整体纵向分区，可折叠：

```
┌─ 报价篮 · 3 行 ─────────────────────────────── [×] ┐
│ ① 产品与数量                                        │
│   [图] STB-012 1000W Strobe   数量 [ 40 ]  [删除]   │
│   [图] STB-007 Pixel Strobe   数量 [ 24 ]  [删除]   │
│   配件（每台）：□ 快装灯钩 ×2  □ 保险绳 ×1  □ 手拉手线材 ×1│
│ ② 包装方案   ( 纸箱 | 航空箱 )   ← 分段按钮，按行设置  │
│   对比表：            纸箱        航空箱             │
│     件数              20          10                │
│     体积 CBM          2.16        3.84              │
│     毛重 kg           432         780               │
│     到岸总成本 USD    xxxx        xxxx   (Δ +xx%)   │
│   航空箱尺寸来源：[档案] / [估算 ⚠] / [手工录入]     │
│ ③ 运输与贸易术语                                    │
│   方式 ( 海运拼箱 LCL | 海运整柜 FCL | 快件 Express ) │
│   术语 ( FOB | CIF | DDP )   快件时为 ( DAP | DDP )  │
│   航线 [ 下拉：目的国 / 目的港，显示运价有效期 ]      │
│   FCL：系统推荐柜型组合 1×40HQ（利用率 82%）          │
│ ④ 汇率   USD/CNY 7.12（更新于 3 天前）  缓冲 1.5%    │
│ ⑤ 毛利   ───────●──────────── 18.0%   [反算：输入单价]│
│          红区 ≤-2.1% | 琥珀区 <15% | 绿区 ≥15%       │
│ ⑥ 结果                                              │
│   单价（含运费，按行） / 总额 USD                    │
│   件数 · 总 CBM · 总毛重 · 计费重（快件）             │
│   成本构成堆叠条（ECharts）：货值|包装|配件|运费|关税增值税|毛利│
│   运费/货值比 ███████░░ 64%  [运费敏感]               │
│   成本基数 ●●●●● [👁 显示 30 秒]（需 cpq.view_cost）  │
│   告警区（按严重度排序，见 §4.4）                     │
│   ✓ 已通过服务端校验（或 ✗ 与服务端不一致，显示差额）  │
│ ⑦ 操作                                              │
│   [保存报价]  [导出 PDF]  [复制 WhatsApp 文案]        │
│   [下载图片包]  [生成英文函件 (Sol)]                  │
└─────────────────────────────────────────────────────┘
```

交互细则：

1. **秒级算价**：任一输入变化 → 本地 JS 引擎立即重算（目标 < 30ms，数量输入 120ms 去抖）；停止输入 400ms 后调用 `/api/cpq/calculate` 做服务端校验，对比差额 > 0.01 USD 显示红色 ✗ 并禁止保存。
2. **毛利滑块**：范围 −10% 至 +60%，步长 0.5%；轨道三段着色（≤ −2.1% 红、< 15% 琥珀、≥ 15% 绿），在 −2.1% 与 15% 处画刻度线。拖动时实时显示目标毛利与“取整后实际毛利”。
3. **反算模式**：切换后每行出现单价输入框，输入单价 → 引擎算出实际毛利，滑块跟随移动且不可拖。
4. **成本隔离**：成本基数默认打码 `●●●●●`；具备 `cpq.view_cost` 的用户点击「👁」后显示 30 秒自动恢复打码；成本数字只存在 JS 内存，未点开前不写入 DOM。供应商名称、工厂型号永不出现在抽屉中（`cpq.view_supplier` 管理员除外，见 §4.3）。
5. **闸门与按钮状态**：
   - 存在 `fuse` 级告警 → 「保存/导出/函件」全部禁用，按钮旁显示原因。
   - 存在需确认的告警（如运费倒挂、包装估算）→ 告警旁出现确认勾选框，勾选后才可导出。
   - 毛利低于 15% → 需 `cpq.override_margin` 权限的用户填写 ≥ 10 字原因才可导出；无权限用户只能保存草稿。
6. **报价篮**：跨页面保留（localStorage，try/catch）；卡片、表格、灯箱都可加入；同一型号再次加入时数量累加并提示。
7. **成本构成图**：ECharts 横向堆叠单条图，`renderer: 'canvas'`，主题跟随深浅色；悬停显示金额与占比。无权限看成本的用户，货值/包装/配件三段合并为“产品成本”，且不显示金额只显示占比。

### 2.7 对客报价单视觉（PDF / 自包含 HTML）

A4 纵向，英文，品牌化排版：

1. 页眉：我方 Logo（本地文件）、公司名、联系方式（WhatsApp / Email），报价号 `Q261019-001`、日期、有效期。
2. 买家信息：公司、联系人、国家。
3. 每个产品一个区块：左侧主图（约 70mm 宽）+ 最多 2 张细节图；右侧 **对客 SKU**、产品名、亮点（如 `768 RGB + 288 White dual light source`、`48-segment pixel control`、`IP66`）、参数表（Light source / Pixel segments / DMX modes / Control protocols / Power / Voltage / IP rating / Dimensions / Net weight / Certifications）、包装（Packing type / Units per package / Package size / G.W. / Packages / CBM）、随附配件、数量、单价、金额、交期。
4. 汇总：Subtotal、Total amount（USD）、Incoterm + 指定地点（如 `DDP Hamburg, Germany`）、总件数 / 总 CBM / 总毛重、运输方式。
5. 条款：付款条款、交期、质保、报价有效期、价格包含项说明（例：price includes sea freight, destination charges, import duty and VAT to the named place）。
6. 页脚：页码 `Page 1 / 3`。
7. **对客文件中只允许出现 USD 金额**，不得出现任何人民币符号或金额（§6 红线）。

### 2.8 WhatsApp 文案格式

纯文本，使用 WhatsApp 标记（`*粗体*`、`_斜体_`），每个产品 6–10 行，示例结构：

```
*Quotation Q261019-001* · valid until 03 Nov 2026
*1) STB-012 · 1000W LED Strobe*
• 768 RGB + 288 White LEDs, 48 pixel segments
• DMX 4/8/16/30 ch · IP66
• Packing: flight case, 2 pcs/case, 0.384 CBM/case
• Qty 40 · *USD 186.50/pc* · USD 7,460.00
*Total: USD 12,940.00 · DDP Hamburg*
Shipment: sea freight, 10 cases, 3.84 CBM, 780 kg
Payment: 30% T/T deposit, 70% before shipment
Lead time: 15–20 days after deposit
(Product photos sent separately)
```

图片无法嵌入文本，因此配套「下载图片包」：ZIP 内文件命名为 `01_STB-012_main.jpg`、`01_STB-012_detail-1.jpg`。

---

## 3. 【Codex 专属派单任务体（Prompt Payload）】

### 3.1 OC 调度流程（OC 严格按此执行，不手写业务代码）

```
Step 0  环境勘察（OC 自己执行，只读，不改代码）→ 产出 tasks/reports/task_01_recon.md
Step 1  生产库在线备份
Step 2  派单 Batch A（后端引擎 + 数据层 + 迁移 + 测试） → 跑 §5 中 A 组测试
Step 3  派单 Batch B（前端画廊 + 灯箱 + 抽屉 + 本地 JS 引擎） → 跑 B 组测试 + 无头浏览器截图
Step 4  派单 Batch C（冻结、导出、泄露断路器、Sol 函件） → 跑全部测试
Step 5  防篡改检查（§5.6）+ 生成验收报告 → 工单移入 tasks/done/
任何一步测试失败：OC 把失败输出（只截取失败断言与报错栈的最后 60 行）回传给 Codex 修复，同一 Batch 最多 3 轮；3 轮仍失败 → 停止，写入阻塞项。
```

**Step 0 勘察清单（OC 执行并记录到 recon 报告）：**

1. 应用仓库路径、Python 包根目录名、Starlette app 工厂函数位置（如 `app/main.py:create_app`）、路由注册方式、Jinja2 模板目录、静态目录、Tabler 与 ECharts 本地文件路径。
2. `equipment_specs`、`cat_supplier_offer`、`multimedia_assets`、`intake_file` 的完整表结构（`PRAGMA table_info`）及每表 3 行样例（**样例中的价格字段在报告里打码**）。
3. 识别频闪灯的过滤条件（品类字段/关键词），确认结果数为 37；不是 37 时记录实际数和差异原因。
4. 现有鉴权方式：Relay-Server 注入的身份头名称（如 `X-Auth-User`）、可信来源 IP；应用内是否已有角色体系。
5. 线上实际 CSP 响应头（`curl -sI https://<relay>/` 取 `Content-Security-Policy`）。
6. 依赖：`python -c "import weasyprint, PIL, pypdf, yaml"`；`node --version`（≥18）；缺失项记录，并在 Step 2 前安装（`pip install weasyprint pillow pypdf pyyaml`；WeasyPrint 系统依赖 `libpango-1.0-0 libpangoft2-1.0-0`；测试用 `nodejs`）。无安装权限 → 阻塞项，不得跳过。
7. `swarm run --help` 确认 prompt 传参方式（stdin 或 `--prompt-file`），以及 Sol 的调用方式（供 §4.5 的 `SOL_LETTER_CMD` 使用）。

**Step 1 备份：**

```bash
sqlite3 "$XL_DB_PATH" ".backup '/var/backups/xl/pre_task01_$(date +%Y%m%d_%H%M%S).db'"
```

**派单组装：** OC 依次提取下方 `CODEX_PAYLOAD_COMMON` 与对应 Batch 的 payload，拼接后连同 recon 报告路径交给 swarm，例如：

```bash
cat payload_common.md payload_batch_a.md > /tmp/task01_a.md
/usr/local/bin/swarm run codex --class code --tier deep --effort high < /tmp/task01_a.md
# 若 swarm 使用其它传参方式，以 Step 0 记录的 `swarm run --help` 为准
```

---

### 3.2 Payload · 公共上下文

<!-- CODEX_PAYLOAD_COMMON_BEGIN -->
~~~~markdown
# 你的身份
你是本项目的主力施工工程师。你在 App-Server 的应用仓库中工作，Python 3.11 + Starlette + Jinja2 + SQLite3（WAL），前端 Tabler（Bootstrap 5）+ ECharts 5.5，全部为本地静态资源。

# 权威规格
完整阅读规格文件：{OPS_REPO}/tasks/pending/task_01_strobe_gallery_and_cpq.md
其中 §2（UI/UX）、§3.3（文件清单）、§3.4（JSON 与 DOM 契约）、§4（算法与路由规则）、§5（测试）、§6（红线）全部是强制要求。
环境勘察结果：{OPS_REPO}/tasks/reports/task_01_recon.md —— 表结构、过滤条件、鉴权头、app 工厂、静态目录以它为准。

# 通用硬规则
1. 只做增量：不修改现有表结构，不删除、不重命名现有路由与模板；旧 /media/<supplier>/<model>.png 行为保持不变。
2. 所有新代码放在 §3.3 列出的路径。若仓库实际包根目录不叫 app/，保持相对结构不变放到实际包根下，并在交付说明中写明映射。
3. 严禁引入任何外部 CDN、外部字体、外部图片 URL；严禁新增第三方前端库。
4. 模板中禁止 style="..."、内联 <script> 代码、内联事件属性；初始数据用 <script type="application/json">。
5. 供应商出厂价、供应商名称、工厂型号只允许存在于：数据库、内部 repository 层、具备权限的内部 API 响应。对客 DTO 用白名单显式构造，禁止 dict 透传、禁止 **kwargs 展开 ORM/行对象。
6. 算法以 §4.1 伪代码为准，Python（app/cpq/engine.py）与 JS（app/static/js/cpq/cpq-engine.js）逐步对应，变量命名保持一致，便于对照。
7. 所有阈值从 config/cpq.yaml 读取，代码中不得硬编码 0.15、-0.021、260 等业务数字（测试 fixture 除外）。
8. 不得为了让测试通过而修改 §5 规定的断言、阈值或测试名；不得添加 skip/xfail。
9. 代码风格：Python 类型注解完整；函数单一职责；JS 使用 ES Module，无全局变量污染（命名空间 window.XL 仅在入口文件挂载一次）。
10. 每个 Batch 结束时输出：新增/修改文件列表、每个文件一句话职责、你自己运行的测试命令及结果。
~~~~
<!-- CODEX_PAYLOAD_COMMON_END -->

### 3.3 文件清单（创建 = C，修改 = M）

| 路径 | C/M | Batch | 职责 |
|---|---|---|---|
| `migrations/20261019_task01_cpq.sql` | C | A | 新表（§4.7），全部 `IF NOT EXISTS` |
| `config/cpq.yaml` | C | A | 阈值、定价参数、柜型、估算参数、对客 SKU 规则 |
| `config/company_profile.yaml` | C | A | 我方公司对客信息、默认付款/质保条款、Logo 本地路径 |
| `config/roles.yaml` | C | A | 用户名 → 角色映射（仅当现有系统无角色体系时启用） |
| `data/cpq/freight_lanes.yaml` | C | A | 航线运价表模板（结构完整，数值由业务方填写） |
| `data/cpq/accessories.yaml` | C | A | 配件目录（灯钩、保险绳、手拉手线材等）模板 |
| `scripts/cpq_seed.py` | C | A | 将 YAML 运价、配件、汇率导入数据库；幂等 |
| `scripts/strobe_spec_audit.py` | C | A | 输出 37 款规格完整度审计表（Markdown） |
| `app/security/roles.py` | C | A | 身份解析（Relay 注入头 + 可信 IP）与权限判断 |
| `app/security/media.py` | C | A | 安全媒体服务：不透明资产 ID、缩略图、穿越防护 |
| `app/strobe/repository.py` | C | A | 读取频闪灯规格/图片/报价；字段映射常量集中于此 |
| `app/strobe/spec_parser.py` | C | A | 规格文本 → 规范化字段（§4.6） |
| `app/cpq/config.py` | C | A | 加载并校验 `config/cpq.yaml` |
| `app/cpq/models.py` | C | A | 数据类：输入、结果、告警、内部快照、对客 DTO（白名单） |
| `app/cpq/engine.py` | C | A | 纯函数算价引擎（§4.1） |
| `app/cpq/gates.py` | C | A | 告警与闸门判定（§4.4） |
| `app/cpq/store.py` | C | A | 新表读写（报价、包装档案、航线、汇率、审计） |
| `app/routes/strobe_routes.py` | C | A/B | `/strobes` 页面、`/api/strobes*` |
| `app/routes/cpq_routes.py` | C | A/C | `/api/cpq/*`、`/quotes/*` 导出 |
| `app/main.py`（或 recon 中的 app 工厂文件） | M | A | 注册新路由（仅追加） |
| `app/templates/strobe/gallery.html` | C | B | 页面骨架，继承现有 base 模板 |
| `app/templates/strobe/_card.html` | C | B | 卡片宏 |
| `app/templates/strobe/_table.html` | C | B | 表格 |
| `app/templates/strobe/_lightbox.html` | C | B | 灯箱结构 |
| `app/templates/strobe/_cpq_drawer.html` | C | B | 抽屉结构 |
| `app/static/css/strobe.css` | C | B | 画廊/灯箱/抽屉样式（CSS 变量 + 深浅色） |
| `app/static/js/strobe/gallery.js` | C | B | 筛选、排序、视图切换、URL 同步 |
| `app/static/js/strobe/lightbox.js` | C | B | 灯箱交互 |
| `app/static/js/cpq/cpq-engine.js` | C | B | 纯函数 JS 引擎，与 engine.py 同算法，**不得访问 DOM** |
| `app/static/js/cpq/cpq-drawer.js` | C | B | 抽屉交互、服务端校验、闸门 UI、ECharts 图 |
| `app/static/js/cpq/basket.js` | C | B | 报价篮状态 |
| `app/cpq/leak_guard.py` | C | C | 泄露断路器（§4.3.3） |
| `app/cpq/export.py` | C | C | 对客 HTML/PDF/WhatsApp/ZIP 渲染 |
| `app/cpq/letter.py` | C | C | Sol 函件适配器与校验（§4.5） |
| `app/templates/quote/customer_quote.html` | C | C | 对客报价单（打印/PDF 共用） |
| `app/templates/quote/whatsapp.txt.j2` | C | C | WhatsApp 文案 |
| `app/static/fonts/`（本地字体文件） | C | C | PDF 用本地字体（如 Inter / Noto Sans，OFL 授权） |
| `tests/cpq/conftest.py` 及 §5 所列测试文件 | C | A/B/C | 见 §5 |
| `tests/cpq/fixtures/cpq_vectors.json` | C | A | 黄金向量（≥ 12 组，覆盖全部运输方式与术语） |
| `tests/cpq/js_parity_runner.mjs` | C | B | Node 端跑黄金向量 |
| `tests/live/test_live_strobe_catalog.py` | C | A | 生产库快照的真实数据测试 |
| `scripts/smoke_task01.py` | C | C | 冒烟脚本：依次请求全部新端点并打印状态 |

### 3.4 JSON 与 DOM 契约

#### 3.4.1 通用

- 全部 JSON 使用 `snake_case`；金额字段后缀 `_usd` / `_cny`；体积 `_cbm`；重量 `_kg`；尺寸 `_cm`。
- 错误统一格式（HTTP 4xx/5xx）：

```json
{ "error": { "code": "MARGIN_FUSE", "message": "Actual margin -3.20% is at or below fuse threshold -2.10%", "details": {} } }
```

- 鉴权：未登录 `401 {"error":{"code":"UNAUTHENTICATED"}}`；无权限 `403 {"error":{"code":"FORBIDDEN","details":{"required":"cpq.view_cost"}}}`。

#### 3.4.2 权限

| 权限 | 能力 |
|---|---|
| `strobe.view` | 看画廊、规格、图片（所有内部用户） |
| `cpq.quote` | 使用抽屉、保存报价、导出（业务员） |
| `cpq.view_cost` | 看混合成本基数（不含供应商身份） |
| `cpq.override_margin` | 毛利低于最低线时填写原因放行（主管） |
| `cpq.view_supplier` | 看供应商名称、各家报价明细（管理员） |
| `cpq.admin` | 维护包装档案、对客 SKU、图片可对客标记 |

#### 3.4.3 `GET /api/strobes` → 列表（无任何成本字段）

```json
{
  "count": 37,
  "items": [
    {
      "model_id": 1203,
      "internal_sku": "STB-012",
      "product_name": "1000W LED Strobe",
      "ip_rating": "IP66",
      "light_source": { "rgb_count": 768, "white_count": 288, "led_type": "5050 SMD", "dual": true, "label": "768 RGB + 288 W" },
      "pixel_segments": 48,
      "dmx_modes": [4, 8, 16, 30],
      "control_protocols": ["DMX512", "RDM"],
      "power_w": 1000,
      "voltage": "AC100-240V 50/60Hz",
      "net_weight_kg": 9.8,
      "dimensions_cm": { "l": 52.0, "w": 22.0, "h": 15.0 },
      "carton": { "units_per_pkg": 2, "l_cm": 60, "w_cm": 50, "h_cm": 40, "gw_kg": 22.0 },
      "flight_case": { "source": "profile", "units_per_pkg": 2, "l_cm": 80, "w_cm": 60, "h_cm": 80, "gw_kg": 39.0 },
      "certifications": ["CE", "RoHS"],
      "images": [
        { "asset_uid": "a_3f9c1e7b2d4a6c8e", "role": "main", "customer_safe": true,
          "thumb_url": "/media/a/a_3f9c1e7b2d4a6c8e?w=640", "large_url": "/media/a/a_3f9c1e7b2d4a6c8e?w=1280",
          "orig_url": "/media/a/a_3f9c1e7b2d4a6c8e" }
      ],
      "spec_completeness": { "score": 0.9, "missing": ["certifications"] },
      "has_cost_basis": true
    }
  ]
}
```

- `flight_case.source` ∈ `profile | estimate | none`；`none` 时 `flight_case` 其余字段为 `null`。
- 未知字段值一律 `null`，**不得填 0 或猜测**。
- `GET /api/strobes/{model_id}` 返回单个 item，额外附 `specs_full: [{ "label": "...", "value": "..." }]`（对内完整规格表）。

#### 3.4.4 `GET /api/cpq/context?model_ids=1203,1207`（需 `cpq.quote`）

```json
{
  "fx": { "usd_cny": 7.12, "updated_at": "2026-10-16T02:00:00Z", "buffer_pct": 0.015, "age_days": 3 },
  "thresholds": { "min_margin": 0.15, "fuse_margin": -0.021,
                  "price_floor_cny_per_unit": 260,
                  "freight_ratio": { "sensitive": 0.30, "heavy": 0.70, "inverted": 1.00 } },
  "pricing": { "unit_price_step_usd": 0.5, "insurance_rate": 0.003, "insured_markup": 1.10, "payment_fee_pct": 0.0 },
  "containers": { "20GP": { "usable_cbm": 28, "max_kg": 20000 }, "40GP": { "usable_cbm": 58, "max_kg": 26000 }, "40HQ": { "usable_cbm": 68, "max_kg": 26000 } },
  "lanes": [ { "lane_id": "SEA-DEHAM", "...": "结构见 §4.1.2，全量字段" } ],
  "accessories": [ { "accessory_id": "HOOK-QR", "name_en": "Quick-release clamp", "cost_cny": 6.5 } ],
  "models": [
    {
      "model_id": 1203,
      "cost_basis_cny": 412.00,
      "cost_basis_mode": "lowest_valid_offer",
      "offer_valid_until": "2026-12-31",
      "packing": { "carton": { "...": "同 3.4.3" }, "flight_case": { "...": "...", "case_cost_cny": 180.0 } }
    }
  ]
}
```

- 无 `cpq.view_cost` 权限：`cost_basis_cny` 与 `accessories[].cost_cny` 字段**整体不出现**（不是 null，是不存在），同时返回 `"cost_masked": true`，前端改走纯服务端计算（每次变更调用 `/api/cpq/calculate`，结果中成本类字段同样被剔除）。
- 仅 `cpq.view_supplier`：每个 model 额外出现 `offers: [{ "supplier_id": 17, "supplier_name": "...", "exw_cny": 412.0, "valid_until": "..." }]`。

#### 3.4.5 `POST /api/cpq/calculate`（需 `cpq.quote`）

请求：

```json
{
  "lines": [
    { "model_id": 1203, "qty": 40, "packing_mode": "flight_case",
      "packing_override": null,
      "accessories": [ { "accessory_id": "HOOK-QR", "qty_per_unit": 2 } ],
      "unit_price_usd": null }
  ],
  "shipping": { "mode": "sea_lcl", "incoterm": "DDP", "lane_id": "SEA-DEHAM" },
  "target_margin": 0.18,
  "pricing_mode": "margin"
}
```

- `pricing_mode` ∈ `margin | price`；`price` 模式下每行必须给 `unit_price_usd`，`target_margin` 忽略。
- `packing_override`（手工录入包装）：`{ "units_per_pkg": 2, "l_cm": 80, "w_cm": 60, "h_cm": 80, "gw_kg": 39.0, "case_cost_cny": 180.0 }`；仅 `cpq.view_cost` 可传 `case_cost_cny`。
- **请求中出现任何成本类键（`cost_basis_cny`、`exw_cny`、`cost_*`、`supplier_*`、`offer_*`）→ `400 CLIENT_COST_REJECTED`**。成本永远由服务端从数据库取。

响应：

```json
{
  "engine_version": "cpq-2026.10.1",
  "lines": [
    { "model_id": 1203, "qty": 40, "packages": 20, "cbm": 7.68, "gw_kg": 780.0, "chargeable_kg": 0,
      "unit_price_usd": 186.5, "amount_usd": 7460.0,
      "logistics_alloc_usd": 2410.33, "unit_goods_value_cny": 899.12,
      "cost_usd": 6112.40 }
  ],
  "totals": { "packages": 20, "cbm": 7.68, "gw_kg": 780.0, "chargeable_kg": 0,
              "price_usd": 12940.0, "cost_usd": 10571.2, "actual_margin": 0.1830,
              "goods_cost_usd": 3800.1, "packaging_cost_usd": 500.0, "accessory_cost_usd": 70.0,
              "freight_usd": 2650.0, "duty_vat_usd": 1300.0, "payment_fee_usd": 0.0,
              "freight_ratio": 0.6974 },
  "breakdown": { "inland_usd": 120.0, "origin_usd": 180.0, "main_carriage_usd": 1650.0,
                 "insurance_usd": 42.7, "dest_usd": 657.3, "duty_usd": 520.0, "vat_usd": 780.0 },
  "fcl_plan": null,
  "packing_compare": { "carton": { "packages": 20, "cbm": 2.4, "gw_kg": 440.0, "total_cost_usd": 9020.5 },
                       "flight_case": { "packages": 20, "cbm": 7.68, "gw_kg": 780.0, "total_cost_usd": 10571.2 } },
  "alerts": [ { "code": "FREIGHT_HEAVY", "severity": "heavy", "requires": null, "message": "Freight is 69.7% of goods value" } ],
  "gate": { "can_save": true, "can_export": true, "requires_ack": [], "requires_override": false, "blocked_by": [] }
}
```

- 无 `cpq.view_cost` 时剔除：`lines[].cost_usd`、`lines[].unit_goods_value_cny`、`totals.cost_usd`、`totals.goods_cost_usd`、`totals.packaging_cost_usd`、`totals.accessory_cost_usd`、`packing_compare.*.total_cost_usd`（改为 `delta_pct`）；保留 `actual_margin`、`freight_ratio` 与告警。
- `fcl_plan`（仅 `sea_fcl` 或 LCL 建议转 FCL 时）：`{ "containers": {"20GP":0,"40GP":0,"40HQ":1}, "utilization_cbm": 0.82, "cost_usd": 3100.0 }`。

#### 3.4.6 `POST /api/cpq/quotes` → 冻结报价（需 `cpq.quote`）

请求 = calculate 请求 + ：

```json
{
  "buyer": { "company": "Stagecraft GmbH", "contact": "Jonas M.", "country": "DE" },
  "payment_terms_id": "TT_30_70",
  "lead_time_days": { "1203": "15-20" },
  "acks": ["FREIGHT_EXCEEDS_GOODS", "PACKING_ESTIMATED"],
  "override": { "reason": "Strategic first order, trial container" }
}
```

响应 `201`：

```json
{ "quote_token": "q_7c2f...（32 位十六进制）", "quote_no": "Q261019-001", "valid_until": "2026-11-03",
  "gate": { "...": "同上" },
  "exports": { "pdf": "/quotes/q_7c2f.../customer.pdf", "html": "/quotes/q_7c2f.../customer.html",
               "whatsapp": "/quotes/q_7c2f.../whatsapp.txt", "images_zip": "/quotes/q_7c2f.../images.zip" } }
```

- 服务端**必须重算**（不信任前端结果），冻结时写入 `cpq_quote`：输入、计算结果、内部成本快照、对客 DTO JSON、引擎版本、操作人、确认项、放行原因。
- 闸门不通过：`409`，`code` 为首个阻断原因（如 `MARGIN_FUSE`、`ACK_REQUIRED`、`OVERRIDE_REQUIRED`），`details.blocked_by` 列全部原因。
- 草稿：`"draft": true` 时允许在 `requires_ack` / `requires_override` 未满足下保存，但 `exports` 返回 `null`，`fuse` 级仍拒绝保存。

#### 3.4.7 对客 DTO（`CustomerQuoteDTO`，白名单，字段集合固定，测试会逐字段比对）

```json
{
  "quote_no": "Q261019-001", "issue_date": "2026-10-19", "valid_until": "2026-11-03", "currency": "USD",
  "seller": { "company": "", "address": "", "contact": "", "email": "", "whatsapp": "", "website": "" },
  "buyer": { "company": "", "contact": "", "country": "" },
  "incoterm": "DDP", "named_place": "Hamburg, Germany", "shipping_mode_label": "Sea freight (LCL)",
  "lines": [
    { "line_no": 1, "sku": "STB-012", "product_name": "1000W LED Strobe",
      "highlights": ["768 RGB + 288 White dual light source", "48-segment pixel control", "IP66"],
      "specs": [ { "label": "DMX modes", "value": "4 / 8 / 16 / 30 ch" } ],
      "packing": { "type": "Flight case", "units_per_pkg": 2, "pkg_size_cm": "80 × 60 × 80", "pkg_gw_kg": 39.0,
                   "packages": 20, "cbm": 7.68, "gw_kg": 780.0 },
      "accessories_included": ["2 × Quick-release clamp", "1 × Safety cable"],
      "image_refs": ["a_3f9c1e7b2d4a6c8e"],
      "qty": 40, "unit_price": 186.5, "amount": 7460.0, "lead_time": "15–20 days" }
  ],
  "totals": { "total_amount": 12940.0, "packages": 20, "cbm": 7.68, "gw_kg": 780.0 },
  "terms": { "payment": "", "lead_time": "", "warranty": "", "price_includes": "", "validity": "" }
}
```

- `sku` 是**对客 SKU**（§4.3.2），绝不是工厂型号。`image_refs` 只允许 `customer_safe = true` 的资产。
- 构造函数 `build_customer_dto(quote_snapshot) -> CustomerQuoteDTO`，用 `@dataclass(frozen=True, slots=True)` 显式字段，禁止从行对象拷贝。

#### 3.4.8 导出端点（需 `cpq.quote`，且报价 `gate.can_export = true`）

| 端点 | 内容 | 头 |
|---|---|---|
| `GET /quotes/{token}/customer.pdf` | WeasyPrint 渲染 `customer_quote.html` | `Content-Disposition: attachment; filename="Q261019-001.pdf"` |
| `GET /quotes/{token}/customer.html` | 自包含 HTML（图片 base64 内联、CSS 内联、无 JS） | 同上 `.html` |
| `GET /quotes/{token}/whatsapp.txt` | 文案 | `text/plain; charset=utf-8` |
| `GET /quotes/{token}/images.zip` | 重新编码后的图片 | `application/zip` |
| `POST /api/quotes/{token}/letter` | Sol 函件，见 §4.5 | JSON |

- 所有导出在返回前必须经过 `leak_guard.scan()`；命中 → `422 LEAK_BLOCKED`，写 `cpq_audit_log`，响应中**不得回显命中内容**（只回显命中类别，如 `supplier_name`）。

#### 3.4.9 媒体端点

| 端点 | 规则 |
|---|---|
| `GET /media/a/{asset_uid}` | 需 `strobe.view`；原图（内部） |
| `GET /media/a/{asset_uid}?w=320\|640\|1280` | 缩略图，WebP，宽度只允许白名单，其他值 `400 BAD_WIDTH` |
| `GET /media/<supplier>/<model>.png`（旧） | 行为不变，本期不改；新页面不使用 |

#### 3.4.10 DOM 契约（前端与测试共用的选择器，不得改名）

| 选择器 | 说明 |
|---|---|
| `#sg-root` | 页面根，`data-view="grid|table"` |
| `#sg-data` | `<script type="application/json">`，内容为 3.4.3 的 `items` |
| `#sg-search` | 搜索输入框 |
| `#sg-filters [data-filter][data-value]` | 筛选 chip，激活时 `aria-pressed="true"` |
| `#sg-sort` | 排序下拉 |
| `#sg-view-toggle [data-view]` | 视图切换按钮 |
| `#sg-grid .sg-card[data-model-id]` | 卡片；另带 `data-ip` `data-dual` `data-segments` `data-power` `data-dmx-max` `data-fc` |
| `.sg-card .sg-img[data-asset-uid]` | 卡片图片，点击开灯箱 |
| `.sg-add[data-model-id]` | 加入报价按钮 |
| `#sg-table tbody tr[data-model-id]` | 表格行 |
| `#sg-lightbox`、`#sg-lb-stage img`、`#sg-lb-thumbs`、`#sg-lb-counter`、`#sg-lb-specs` | 灯箱 |
| `#cpq-drawer` | 抽屉（offcanvas） |
| `#cpq-lines [data-line-model-id]`、`.cpq-qty`、`.cpq-pack [data-mode]` | 行、数量、包装切换 |
| `#cpq-mode [data-mode]`、`#cpq-incoterm [data-incoterm]`、`#cpq-lane` | 运输、术语、航线 |
| `#cpq-margin`（`input[type=range]`）、`#cpq-margin-value`、`#cpq-pricing-mode` | 毛利 |
| `#cpq-result`、`#cpq-cost-chart`、`#cpq-freight-ratio`、`#cpq-verify` | 结果、图、运费比、服务端校验状态 |
| `#cpq-alerts .cpq-alert[data-code][data-severity]` | 告警 |
| `#cpq-ack input[data-ack-code]`、`#cpq-override-reason` | 确认与放行原因 |
| `#cpq-cost-basis[data-masked]` | 成本基数显示位 |
| `#cpq-actions [data-action="save|pdf|whatsapp|zip|letter"]` | 操作按钮，禁用时 `disabled` + `title` 写原因 |

---

### 3.5 Payload · Batch A（后端引擎、数据层、迁移、测试）

<!-- CODEX_PAYLOAD_BATCH_A_BEGIN -->
~~~~markdown
# Batch A 目标
实现后端基础：迁移、配置、身份与权限、安全媒体、频闪灯数据仓库与规格解析、算价引擎、告警闸门、存储层、/api/strobes 与 /api/cpq/context、/api/cpq/calculate 路由，以及 A 组测试。

# 必须交付的文件
§3.3 中 Batch 列标为 A 或 A/B、A/C 的全部后端文件（A/B、A/C 的路由文件本批只实现本批端点）。

# 关键要求
1. migrations/20261019_task01_cpq.sql 按 §4.7 建表；应用启动时若表不存在自动执行该脚本（幂等）。
2. app/strobe/repository.py：所有与现有表的字段映射写成模块顶部常量 FIELD_MAP（依据 recon 报告），其余代码不得出现原始列名。频闪灯过滤条件同样集中为常量 STROBE_FILTER_SQL。
3. app/strobe/spec_parser.py：按 §4.6 规则解析；解析不到返回 None，绝不臆造；每个字段附带 source（"column" | "parsed" | "override"）。
4. app/cpq/engine.py：严格实现 §4.1 伪代码，纯函数，无 I/O；入口 compute(inputs: CalcInputs) -> CalcResult。暴露 ENGINE_VERSION = "cpq-2026.10.1"。
5. app/cpq/gates.py：按 §4.4 告警码表实现 evaluate(result, inputs, ctx) -> (alerts, gate)。
6. app/security/media.py：按 §4.2 实现；asset_uid = "a_" + HMAC-SHA256(MEDIA_UID_SECRET, str(asset_id)) 前 16 位十六进制；MEDIA_UID_SECRET 从环境变量读取，缺失时启动失败（不得使用默认值）。
7. app/security/roles.py：按 recon 中的身份头实现；只信任来自可信 IP（环境变量 TRUSTED_PROXY_IPS）的身份头；本地测试通过依赖注入覆盖。
8. /api/cpq/calculate 与 /api/cpq/context 的字段剔除规则严格按 §3.4.4 / §3.4.5。
9. 黄金向量 tests/cpq/fixtures/cpq_vectors.json：≥12 组，覆盖 sea_lcl×{FOB,CIF,DDP}、sea_fcl×{FOB,CIF,DDP}、express×{DAP,DDP}、纸箱/航空箱、多行、price 模式、LCL 最低计费、快件分段费率边界、FCL 多柜组合；每组含完整输入和期望输出（期望值用 engine.py 计算后人工复核合理性，写入 JSON）。
10. 实现 §5 中标记为 [A] 的全部测试文件。

# 完成后自测
python -m pytest tests/cpq -q -m "not js and not export"
python -m pytest tests/live -m live -q
python scripts/strobe_spec_audit.py > /tmp/strobe_spec_audit.md
~~~~
<!-- CODEX_PAYLOAD_BATCH_A_END -->

### 3.6 Payload · Batch B（前端画廊、灯箱、抽屉、JS 引擎）

<!-- CODEX_PAYLOAD_BATCH_B_BEGIN -->
~~~~markdown
# Batch B 目标
实现 /strobes 页面：卡片画廊与高密表格双模、筛选排序、全屏灯箱、右侧 CPQ 抽屉、报价篮、本地 JS 算价引擎与服务端校验，以及 B 组测试。

# 必须交付的文件
§3.3 中 Batch 列标为 B 的全部文件；strobe_routes.py 补上 GET /strobes 页面路由。

# 关键要求
1. 视觉与交互严格按 §2.1–§2.6；DOM 选择器严格按 §3.4.10，不得改名。
2. 模板继承现有 base 模板；Tabler、ECharts 使用 recon 报告记录的本地路径；JS 使用 <script type="module" src="...">。
3. 首屏服务端渲染卡片与表格；#sg-data 承载数据；JS 只增强交互。
4. cpq-engine.js 必须是 engine.py 的逐步对照实现：同名函数、同运算顺序、同取整函数 ceil_to（§4.1.4）。不得访问 DOM、window、fetch；只 export 纯函数。
5. 抽屉：本地重算 < 30ms；400ms 去抖调用 /api/cpq/calculate 校验，差额 > 0.01 USD 显示 ✗ 并禁用保存；cost_masked 时完全走服务端计算。
6. 成本基数只在用户点击「👁」后写入 DOM，30 秒后清空并恢复 data-masked="true"。
7. ECharts 成本构成图：canvas 渲染，监听主题切换与容器尺寸变化（ResizeObserver）。
8. localStorage 读写全部 try/catch，失败时页面功能完整。
9. 响应式：1440px 与 390px 宽度无页面级横向滚动；抽屉 ≤768px 全宽。
10. 实现 §5 中标记为 [B] 的测试，包括 tests/cpq/js_parity_runner.mjs。

# 完成后自测
python -m pytest tests/cpq -q -m "not export"
node tests/cpq/js_parity_runner.mjs tests/cpq/fixtures/cpq_vectors.json
~~~~
<!-- CODEX_PAYLOAD_BATCH_B_END -->

### 3.7 Payload · Batch C（冻结、导出、泄露断路器、Sol 函件）

<!-- CODEX_PAYLOAD_BATCH_C_BEGIN -->
~~~~markdown
# Batch C 目标
实现报价冻结、对客 DTO、四种导出、泄露断路器、Sol 英文函件，以及 C 组测试与冒烟脚本。

# 必须交付的文件
§3.3 中 Batch 列标为 C 或 A/C 的全部文件。

# 关键要求
1. POST /api/cpq/quotes 服务端重算并冻结，规则见 §3.4.6；quote_no 每日序号在事务内生成（BEGIN IMMEDIATE），避免并发重号。
2. build_customer_dto() 白名单显式构造，字段集合严格等于 §3.4.7。
3. 导出按 §2.7 / §2.8 / §4.3；PDF 用 WeasyPrint，字体来自 app/static/fonts，PDF 元数据 Title=报价号、Author=我方公司名、Producer 固定为我方公司名（覆盖默认值）。
4. 图片进入任何对客导出前：Pillow 打开 → 转 RGB → 长边限制 1600px → 重新编码 JPEG（quality 85）→ 不保留 EXIF/ICC 以外元数据；文件名只用对客 SKU。
5. leak_guard 按 §4.3.3 实现，所有导出与函件必须经过它；命中返回 422 并写审计日志。
6. letter.py 按 §4.5：只发送 CustomerQuoteDTO（不含 image_refs）；外部命令从环境变量 SOL_LETTER_CMD 读取（按 recon 中的 swarm 调用方式配置）；超时 90 秒；返回后依次过 leak_guard 与数字一致性校验；结果存 cpq_quote_letter 表，状态 draft，前端展示供人工编辑复制，系统不提供任何发送功能。
7. 实现 §5 中标记为 [C] 的全部测试与 scripts/smoke_task01.py。

# 完成后自测
python -m pytest tests/cpq -q
python -m pytest tests/live -m live -q
python scripts/smoke_task01.py --base http://127.0.0.1:<port>
~~~~
<!-- CODEX_PAYLOAD_BATCH_C_END -->

---

## 4. 【数据与模型路由规则】

### 4.1 算价引擎（Python 与 JS 同一算法）

#### 4.1.1 输入

- 每行：`model_id`、`qty`（正整数）、`packing_mode`（`carton | flight_case`）、包装档案（`units_per_pkg, l_cm, w_cm, h_cm, gw_kg, case_cost_cny`）、配件 `[(cost_cny, qty_per_unit)]`、`cost_basis_cny`（服务端注入）、`unit_price_usd`（price 模式）。
- 运输：`mode`（`sea_lcl | sea_fcl | express`）、`incoterm`（海运 `FOB | CIF | DDP`；快件 `DAP | DDP`；组合不合法 → `400 BAD_INCOTERM`）、航线 lane。
- 汇率 `fx_rate`（CNY per USD）、`fx_buffer`；定价参数与阈值来自 `config/cpq.yaml`。

#### 4.1.2 航线 lane 字段（`data/cpq/freight_lanes.yaml` → `cpq_freight_lane`）

```yaml
- lane_id: SEA-DEHAM
  mode: sea            # sea | express
  dest_country: DE
  named_place: "Hamburg, Germany"
  valid_until: 2026-11-30
  currency: USD
  inland_cny_per_cbm: 0      # 工厂到起运港拖车，CNY
  inland_min_cny: 0
  origin_fixed_usd: 0        # LCL 起运港杂费、报关、文件（每票）
  origin_per_cbm_usd: 0
  lcl_rate_usd_per_wm: 0     # LCL 海运费，按 W/M（1 CBM 或 1000 kg 取大）
  lcl_min_wm: 1.0
  fcl_rate_usd: { 20GP: 0, 40GP: 0, 40HQ: 0 }
  fcl_origin_fixed_usd_per_container: 0
  fcl_dest_fixed_usd_per_container: 0
  dest_fixed_usd: 0          # LCL 目的港费用（DDP 才计入）
  dest_per_cbm_usd: 0
  last_mile_usd: 0           # 目的港到门
  duty_rate: 0.0             # 进口关税率
  duty_basis: CIF            # CIF | FOB（如美国按 FOB 申报）
  import_vat_rate: 0.0
  include_import_vat: true
- lane_id: EXP-DHL-DE
  mode: express
  dest_country: DE
  named_place: "Door, Germany"
  valid_until: 2026-11-30
  volumetric_divisor: 5000
  increment_kg: 0.5
  bands: [ { upto_kg: 21, usd_per_kg: 0 }, { upto_kg: 45, usd_per_kg: 0 }, { upto_kg: 100, usd_per_kg: 0 }, { upto_kg: 99999, usd_per_kg: 0 } ]
  min_charge_usd: 0
  fuel_surcharge_pct: 0.0
  ddp_fee_usd: 0
  duty_rate: 0.0
  duty_basis: CIF
  import_vat_rate: 0.0
  include_import_vat: true
```

> 模板中的 `0` 是结构占位，**上线前必须由业务方填入真实货代报价**（§5.7 验收清单第 1 项）。`scripts/cpq_seed.py` 遇到所有费率全为 0 的航线时打印警告，引擎对这类航线产生 `LANE_UNPRICED` 阻断告警。

#### 4.1.3 计算步骤（伪代码，Python/JS 必须逐步一致）

```text
# ---------- 1. 包装与体积 ----------
for each line i:
    p        = packing profile of line i (by packing_mode, or override)
    pkgs_i   = ceil(qty_i / p.units_per_pkg)
    pkg_cbm  = p.l_cm * p.w_cm * p.h_cm / 1_000_000
    cbm_i    = pkgs_i * pkg_cbm
    gw_i     = pkgs_i * p.gw_kg
    if mode == express:
        vol_kg_pkg  = p.l_cm * p.w_cm * p.h_cm / lane.volumetric_divisor
        charge_pkg  = ceil_to(max(p.gw_kg, vol_kg_pkg), lane.increment_kg)
        chg_i       = pkgs_i * charge_pkg
    else: chg_i = 0
    exw_cny_i  = cost_basis_cny_i * qty_i
    pack_cny_i = (packing_mode == flight_case) ? pkgs_i * p.case_cost_cny : 0     # 纸箱成本视为含在出厂价内
    acc_cny_i  = qty_i * Σ(acc.cost_cny * acc.qty_per_unit)
CBM = Σcbm_i ; GW = Σgw_i ; CHG = Σchg_i
fx_eff = fx_rate * (1 - fx_buffer)                       # 保守汇率：每美元换更少人民币
goods_usd_i = (exw_cny_i + pack_cny_i + acc_cny_i) / fx_eff
GOODS = Σgoods_usd_i ; EXW_USD = Σexw_cny_i / fx_eff

# ---------- 2. 不依赖售价的物流成本 ----------
inland = (mode in sea) ? max(lane.inland_min_cny, lane.inland_cny_per_cbm * CBM) / fx_eff : 0
if mode == sea_lcl:
    wm     = max(CBM, GW / 1000, lane.lcl_min_wm)
    origin = lane.origin_fixed_usd + lane.origin_per_cbm_usd * CBM
    main   = wm * lane.lcl_rate_usd_per_wm
    dest   = lane.dest_fixed_usd + lane.dest_per_cbm_usd * CBM + lane.last_mile_usd
if mode == sea_fcl:
    plan   = best_fcl_plan(CBM, GW, containers, lane.fcl_rate_usd)   # 见 4.1.5
    n      = plan.total_containers
    origin = lane.fcl_origin_fixed_usd_per_container * n
    main   = plan.ocean_usd
    dest   = lane.fcl_dest_fixed_usd_per_container * n + lane.last_mile_usd
if mode == express:
    rate   = first band where CHG <= band.upto_kg  (超出最后一档用最后一档)
    main   = max(lane.min_charge_usd, CHG * rate) * (1 + lane.fuel_surcharge_pct)
    inland = origin = 0
    dest   = (incoterm == DDP) ? lane.ddp_fee_usd : 0

按术语保留的分项：
    FOB : inland + origin
    CIF : inland + origin + main            (+ insurance)
    DDP : inland + origin + main + dest     (+ insurance + duty + vat)    # 海运
    DAP : main                                                          # 快件
    DDP : main + dest                       (+ duty + vat)              # 快件，dest 即 ddp_fee
FIXED = 上述保留分项之和（不含 insurance / duty / vat / fee）

# ---------- 3. 依赖售价的分项：不动点迭代 ----------
function price_dependent(P):
    ins  = (incoterm in {CIF, DDP} and mode in sea) ? P * insured_markup * insurance_rate : 0
    fee  = P * payment_fee_pct
    if incoterm == DDP:
        post = dest_kept                                  # 目的港/末端/快件 DDP 费
        # 先假设 duty, vat 未知，用闭式：P = cif + post + duty + vat
        # cif_value = P - post - duty - vat
        # duty = r_d * customs_value ; vat = r_v * (cif_value + duty)
        # customs_value = (duty_basis == CIF) ? cif_value : cif_value - main - ins
        → 以迭代求解（下方 solve_ddp），最多 50 次，|Δ| < 1e-6 停止
    else: duty = vat = 0
    return ins, fee, duty, vat

function total_cost(P):
    ins, fee, duty, vat = price_dependent(P)
    return GOODS + FIXED + ins + fee + duty + vat

solve_ddp(P):   # 内层迭代
    duty = vat = 0
    repeat 50:
        cif  = P - post - duty - vat
        cv   = (duty_basis == CIF) ? cif : cif - main - ins
        duty2 = lane.duty_rate * cv
        vat2  = include_import_vat ? lane.import_vat_rate * (cif + duty2) : 0
        if |duty2-duty| + |vat2-vat| < 1e-6: break
        duty, vat = duty2, vat2
    return duty2, vat2

margin 模式：
    P = (GOODS + FIXED) / (1 - m)
    repeat 50:
        P_new = total_cost(P) / (1 - m)
        if |P_new - P| < 1e-6: break
        P = P_new
    LOG = total_cost(P) - GOODS                           # 全部非货值成本（含 fee、duty、vat）

# ---------- 4. 分摊与取整 ----------
share_i = (mode == express) ? chg_i / CHG : cbm_i / CBM
cost_line_i  = goods_usd_i + LOG * share_i
unit_raw_i   = cost_line_i / (1 - m) / qty_i
unit_i       = ceil_to(unit_raw_i, unit_price_step_usd)   # 只向上取整，保护毛利
price 模式：unit_i = 输入单价（不取整）

# ---------- 5. 最终实际值（用最终售价一次性重算，不再迭代） ----------
P_final  = Σ unit_i * qty_i
C_final  = total_cost(P_final)
LOG_f    = C_final - GOODS
actual_margin = (P_final - C_final) / P_final
logistics_alloc_i = LOG_f * share_i
unit_goods_value_cny_i = (unit_i - logistics_alloc_i / qty_i) * fx_rate     # 用原始汇率
freight_usd = inland + origin + main + dest + ins          # 运费口径：不含关税、增值税、支付费
freight_ratio = freight_usd / EXW_USD                     # 运费 / 灯具出厂货值
```

#### 4.1.4 取整函数（两端必须完全一致）

```text
ceil_to(x, step) = ceil((x - 1e-9) / step) * step
round2(x)        = 显示与 DTO 输出时：Python Decimal(str(x)).quantize(0.01, ROUND_HALF_UP)；JS 用同等实现（先 toFixed(10) 再按字符串半入）
```

内部计算一律用 float，**只在输出层 round2**；黄金向量比较容差 0.01 USD。

#### 4.1.5 FCL 柜型组合

```text
best_fcl_plan(CBM, GW, containers, rates):
    k = ceil(CBM / containers["20GP"].usable_cbm) + 1, 上限 10
    枚举 n20, n40, n40hq ∈ [0..k]，排除全 0
    可行：Σn*usable_cbm ≥ CBM 且 Σn*max_kg ≥ GW
    目标：最小化 (ocean_usd, total_containers, n20)    # 依次比较，保证确定性
    utilization_cbm = CBM / Σn*usable_cbm
```

当 `mode = sea_lcl` 且同航线 FCL 方案的 `main + origin` < LCL 的 `main + origin` → 产生 `LCL_FCL_SUGGEST`（info）。

#### 4.1.6 包装对比与估算

- 每次计算同时用“全部行纸箱”与“全部行航空箱”各算一遍 `total_cost`，写入 `packing_compare`。
- 某型号无航空箱档案时估算：`l/w/h = 纸箱尺寸 + fc_pad_cm`，`gw = units_per_pkg × net_weight_kg + fc_tare_kg`，`case_cost_cny` 必须人工录入（无法估算，未录入 → `PACKING_COST_MISSING` 阻断）。估算或手工录入的包装 → `PACKING_ESTIMATED`（需确认）。`cpq.admin` 可在抽屉中「保存为该型号默认航空箱方案」写入 `cpq_packing_profile`。

#### 4.1.7 成本基数

`cost_basis_cny` = 该型号在 `cat_supplier_offer` 中**有效期内（valid_until ≥ 今天或为空）的最低出厂价**；无有效报价 → `NO_COST_BASIS` 阻断。冻结报价时把所用 offer id 写入内部快照，供日后追溯。

### 4.2 图片安全加载逻辑

1. **只从数据库登记的资产出发**：`asset_uid` → 查 `multimedia_assets` 得到文件相对路径；数据库查不到 → 404。永不根据 URL 拼文件路径。
2. **路径校验**：`resolved = (MEDIA_ROOT / rel).resolve()`；必须 `resolved.is_relative_to(MEDIA_ROOT.resolve())`；拒绝含 `..`、绝对路径、NUL、反斜杠的记录；符号链接解析后越界 → 404；扩展名白名单 `.png .jpg .jpeg .webp`；读取前校验文件头魔数与扩展名一致。
3. **缩略图**：`w ∈ {320, 640, 1280}`，Pillow 生成 WebP（quality 82），缓存在 `MEDIA_CACHE/thumbs/{asset_uid}_{w}.webp`；源文件 mtime 变化则重建。
4. **可对客标记**：`cpq_asset_flag(asset_id, customer_safe, reason, updated_by, updated_at)`；默认可对客；带工厂 Logo/水印的图由 `cpq.admin` 在灯箱中标记为不可对客，此类图仅内部可见，导出时自动排除。
5. **响应头**：`Cache-Control: private, max-age=86400`、`X-Content-Type-Options: nosniff`、`ETag`；需要鉴权（`strobe.view`），未登录 401。
6. **对客文件不含任何媒体 URL**：PDF/HTML 中的图片一律内联（base64）；ZIP 内为重新编码的文件。旧路由 `/media/<supplier>/<model>.png` 因路径中含供应商名，**绝不得出现在任何对客输出中**（泄露扫描覆盖 `/media/` 字样）。

### 4.3 出厂价隔离与对客输出

#### 4.3.1 三层隔离

| 层 | 可见内容 |
|---|---|
| 数据层（DB、repository） | 全部 |
| 内部 API | 按权限剔除字段（§3.4.4、§3.4.5）；剔除是“键不存在”，不是置 null |
| 对客输出（PDF/HTML/WhatsApp/ZIP/函件） | 只来自 `CustomerQuoteDTO`；渲染后再过泄露断路器 |

#### 4.3.2 对客 SKU

工厂型号本身就会暴露供应商，因此对客一律使用我方 SKU：`cpq_public_sku(model_id, public_sku, created_at)`。首次需要时按 `config/cpq.yaml` 的 `public_sku.pattern`（默认 `"{brand}-STB-{seq:03d}"`，`brand` 取自 `company_profile.yaml`）生成并持久化，此后不变；`cpq.admin` 可改。卡片/表格上的 `internal_sku` 显示的就是它。

#### 4.3.3 泄露断路器 `leak_guard.scan(text, ctx) -> list[Hit]`

对每个导出物先抽取全文（HTML 去标签；PDF 用 pypdf 抽文本并读取元数据；ZIP 检查文件名与图片 EXIF；函件原文），然后扫描：

| 类别 | 规则 |
|---|---|
| `supplier_name` | 全部供应商名称、简称、别名（来自供应商表，不区分大小写，含去空格变体） |
| `factory_model` | 全部工厂原型号（来自 `equipment_specs` 与 `cat_supplier_offer`），长度 ≥ 4 的才参与，避免误杀 |
| `cny_marker` | `¥`、`￥`、`CNY`、`RMB`、`元`、`人民币` —— 对客文件不得出现任何人民币标记 |
| `cost_value` | 本报价涉及型号的每个出厂价、航空箱成本、配件成本（整数与两位小数两种写法，含千分位），紧邻（±12 字符内）任何货币标记时命中 |
| `internal_key` | `exw`、`cost_basis`、`supplier_id`、`offer_id`、`margin`、`底价`、`出厂`、`成本`、`供应商`、`毛利`、`/media/` |
| `pdf_meta` | PDF 的 Author/Creator/Producer/Title 不得含上述任何内容 |

命中任一 → 拒绝输出，`422 LEAK_BLOCKED`，审计日志记录 `quote_token`、导出类型、命中类别、操作人、时间（**日志中也不写命中原文**，只写类别和位置偏移）。断路器没有放行开关，任何权限都不能绕过。

### 4.4 告警码与闸门

| 代码 | 条件（阈值来自配置） | 级别 | 对导出的影响 |
|---|---|---|---|
| `NO_COST_BASIS` | 任一行无有效出厂价 | fuse | 阻断保存与导出 |
| `LANE_UNPRICED` | 航线费率全为 0 | fuse | 阻断 |
| `LANE_EXPIRED` | `valid_until < 今天` | fuse | 阻断 |
| `FX_EXPIRED` | 汇率超过 `fx_expired_days`（默认 14） | fuse | 阻断 |
| `MARGIN_FUSE` | `actual_margin ≤ fuse_margin`（−2.1%） | fuse | 阻断，任何权限不可放行 |
| `PRICE_FLOOR_FUSE` | 任一行 `unit_goods_value_cny < price_floor_cny_per_unit`（频闪 ¥260） | fuse | 阻断 |
| `PACKING_COST_MISSING` | 航空箱成本未录入 | fuse | 阻断 |
| `IMAGE_MISSING` | 任一行无可对客图片 | fuse | 阻断导出（可存草稿） |
| `MARGIN_BELOW_MIN` | `fuse_margin < actual_margin < min_margin`（15%） | warn | 需 `cpq.override_margin` + 原因 ≥10 字 |
| `FREIGHT_EXCEEDS_GOODS` | `freight_ratio > 1.00` | fuse 色 | 需勾选确认，并强制显示改进建议 |
| `FREIGHT_HEAVY` | `0.70 ≤ freight_ratio ≤ 1.00` | heavy | 仅提示 |
| `FREIGHT_SENSITIVE` | `0.30 ≤ freight_ratio < 0.70` | warn | 仅提示 |
| `PACKING_ESTIMATED` | 使用估算或手工包装 | warn | 需勾选确认 |
| `FX_STALE` | 汇率超过 `fx_stale_days`（默认 7） | warn | 仅提示 |
| `SPEC_INCOMPLETE` | 关键规格缺失 | warn | 仅提示 |
| `LCL_FCL_SUGGEST` | §4.1.5 | info | 仅提示 |

`FREIGHT_EXCEEDS_GOODS` 的改进建议由引擎给出（不调用 LLM）：①纸箱与航空箱对比差额；②LCL 改 FCL 的差额（若适用）；③改报 FOB/CIF 让买家自行清关的价格；④建议提高起订量以摊薄起运港固定费。

报价有效期 = `min(今天 + quote_validity_days, 航线 valid_until, 最低价 offer valid_until)`。

### 4.5 Sol 英文商务函件边界

1. **输入**：`CustomerQuoteDTO`（去掉 `image_refs`）+ `{ "tone": "formal|friendly", "purpose": "first_quote|follow_up|revised_quote", "extra_notes": "业务员补充（≤500 字）" }`。`extra_notes` 也要先过 `leak_guard`，命中则拒绝调用。
2. **系统提示词要点**（写在 `letter.py` 常量中）：资深舞台灯光外贸业务员口吻；只使用给定数据；不得新增或改动任何数字、型号、条款；不得承诺未给出的认证、交期或质保；输出纯文本邮件（Subject + Body），≤ 250 词。
3. **输出校验**：
   - `leak_guard.scan` 必须零命中；
   - 数字一致性：从函件抽取全部数字（含小数、千分位），每个数字必须出现在由 DTO 生成的“允许数字集合”中（单价、金额、数量、CBM、毛重、件数、交期区间、日期组成部分、通道数、灯珠数、分段数、功率、IP 等级数字）；出现集合外数字 → 判定失败，返回 `422 LETTER_NUMBER_MISMATCH` 并列出异常数字；
   - 失败时允许重试 1 次，仍失败则返回错误，不给出半成品。
4. **存储与使用**：存 `cpq_quote_letter(quote_token, version, subject, body, status='draft', created_by, created_at)`；前端显示在可编辑文本框，提供「复制」按钮；**系统不提供任何发送功能**。
5. **调用方式**：`subprocess.run(shlex.split(os.environ["SOL_LETTER_CMD"]), input=json_payload, timeout=90, capture_output=True)`；命令由 OC 依据 recon 结果配置；测试中 monkeypatch 替换。
6. **路由**：本工单只调用 Sol；DeepSeek/Qwen 不参与本工单任何环节。

### 4.6 规格规范化规则（`spec_parser.py`）

关键字段（参与完整度评分）：`rgb_count`、`white_count`、`pixel_segments`、`dmx_modes`、`power_w`、`ip_rating`、`net_weight_kg`、`dimensions_cm`、`carton`、`certifications`。

| 字段 | 优先来源 | 文本解析规则（不区分大小写，中英文） |
|---|---|---|
| `rgb_count` | 专用列 | `(\d+)\s*(pcs\|颗\|x)?\s*(rgb\|3合1\|3in1)` |
| `white_count` | 专用列 | `(\d+)\s*(pcs\|颗\|x)?\s*(白光\|white\|cw\|ww)` |
| `led_type` | 专用列 | `(5050\|3535\|2835\|smd\s*\d{4})` |
| `pixel_segments` | 专用列 | `(\d+)\s*(段\|segments?\|zones?\|sections?)` |
| `dmx_modes` | 专用列 | 抓取所有 `(\d+)\s*(ch\|通道\|channels?)`，去重升序 |
| `control_protocols` | 专用列 | 关键词集合：DMX512、RDM、Art-Net、sACN、Sound、Auto、Master/Slave |
| `power_w` | 专用列 | `(\d{2,4})\s*w\b`，多个时取标称最大值 |
| `ip_rating` | 专用列 | `ip\s?([0-6][0-9])`，规范为 `IP66` |
| 尺寸/重量 | 专用列 | `(\d+(\.\d+)?)\s*[x×*]\s*(\d+(\.\d+)?)\s*[x×*]\s*(\d+(\.\d+)?)\s*(mm\|cm)`，统一换算 cm |

- 有专用列且非空 → 用列值（`source=column`）；否则解析文本（`source=parsed`）；`cpq_spec_override` 表中有覆盖值 → 用覆盖值（`source=override`）。
- 解析出多个互相矛盾的值 → 返回 `None` 并在审计中标记 `conflict`。
- `scripts/strobe_spec_audit.py` 输出 37 行审计表：型号 | 完整度 | 缺失字段 | 冲突字段 | 图片数（可对客/总数）| 有效报价数 | 航空箱方案来源。该表必须附在验收报告中。

### 4.7 新增数据表

```sql
CREATE TABLE IF NOT EXISTS cpq_packing_profile (
  model_id INTEGER NOT NULL, packing_mode TEXT NOT NULL CHECK (packing_mode IN ('carton','flight_case')),
  units_per_pkg INTEGER NOT NULL CHECK (units_per_pkg > 0),
  l_cm REAL NOT NULL, w_cm REAL NOT NULL, h_cm REAL NOT NULL, gw_kg REAL NOT NULL,
  case_cost_cny REAL, source TEXT NOT NULL DEFAULT 'manual',
  updated_by TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (model_id, packing_mode));
CREATE TABLE IF NOT EXISTS cpq_freight_lane (lane_id TEXT PRIMARY KEY, mode TEXT NOT NULL, payload_json TEXT NOT NULL, valid_until TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cpq_fx_rate (id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT NOT NULL DEFAULT 'USDCNY', rate REAL NOT NULL, effective_at TEXT NOT NULL, entered_by TEXT);
CREATE TABLE IF NOT EXISTS cpq_accessory (accessory_id TEXT PRIMARY KEY, name_en TEXT NOT NULL, name_zh TEXT, cost_cny REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS cpq_public_sku (model_id INTEGER PRIMARY KEY, public_sku TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cpq_asset_flag (asset_id INTEGER PRIMARY KEY, customer_safe INTEGER NOT NULL DEFAULT 1, reason TEXT, updated_by TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS cpq_spec_override (model_id INTEGER NOT NULL, field TEXT NOT NULL, value_json TEXT NOT NULL, updated_by TEXT, updated_at TEXT, PRIMARY KEY (model_id, field));
CREATE TABLE IF NOT EXISTS cpq_quote (
  quote_token TEXT PRIMARY KEY, quote_no TEXT NOT NULL UNIQUE, status TEXT NOT NULL CHECK (status IN ('draft','final')),
  engine_version TEXT NOT NULL, inputs_json TEXT NOT NULL, result_json TEXT NOT NULL,
  internal_snapshot_json TEXT NOT NULL, customer_dto_json TEXT,
  acks_json TEXT, override_reason TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, valid_until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cpq_quote_letter (id INTEGER PRIMARY KEY AUTOINCREMENT, quote_token TEXT NOT NULL, version INTEGER NOT NULL, subject TEXT, body TEXT, status TEXT NOT NULL DEFAULT 'draft', created_by TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cpq_audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT, action TEXT NOT NULL, quote_token TEXT, detail_json TEXT);
CREATE INDEX IF NOT EXISTS idx_cpq_quote_created ON cpq_quote(created_at);
CREATE INDEX IF NOT EXISTS idx_cpq_audit_at ON cpq_audit_log(at);
```

`internal_snapshot_json`（含成本、供应商、offer id）只在内部审计接口可读，任何导出代码路径不得读取该列（§5 有静态检查）。

### 4.8 `config/cpq.yaml` 默认值

```yaml
thresholds:
  min_margin: 0.15
  fuse_margin: -0.021
  price_floor_cny_per_unit: { strobe: 260 }
  freight_ratio: { sensitive: 0.30, heavy: 0.70, inverted: 1.00 }
  fx_stale_days: 7
  fx_expired_days: 14
pricing:
  unit_price_step_usd: 0.5
  fx_buffer_pct: 0.015
  insurance_rate: 0.003
  insured_markup: 1.10
  payment_fee_pct: 0.0
  quote_validity_days: 15
containers:
  20GP: { usable_cbm: 28, max_kg: 20000 }
  40GP: { usable_cbm: 58, max_kg: 26000 }
  40HQ: { usable_cbm: 68, max_kg: 26000 }
packing_estimate: { fc_pad_cm: 10, fc_tare_kg: 12 }
public_sku: { pattern: "{brand}-STB-{seq:03d}" }
payment_terms:
  - { id: TT_30_70, en: "30% T/T deposit, 70% balance before shipment" }
  - { id: TT_100, en: "100% T/T before shipment" }
  - { id: ALI_TA, en: "Alibaba Trade Assurance" }
```

`app/cpq/config.py` 启动时校验：`fuse_margin < min_margin < 1`、所有比率在合理区间、柜型齐全；校验失败 → 应用启动失败并打印具体字段。

---

## 5. 【自动化测试与验收标准】

### 5.1 测试布局

```
tests/
  cpq/
    conftest.py                 [A] 生产库快照、app 工厂、身份注入、测试配置
    fixtures/cpq_vectors.json   [A]
    test_engine.py              [A]
    test_gates.py               [A]
    test_freight_alert.py       [A]
    test_api_contract.py        [A]
    test_media_security.py      [A]
    test_csp_static.py          [B]
    test_dom_contract.py        [B]
    test_js_parity.py           [B]  marker: js
    js_parity_runner.mjs        [B]
    test_leak_breaker.py        [C]  marker: export
    test_exports.py             [C]  marker: export
    test_letter.py              [C]  marker: export
  live/
    test_live_strobe_catalog.py [A]  marker: live
```

`pytest.ini`（或 `pyproject.toml`）注册 `live`、`js`、`export` 三个 marker，并设置 `--strict-markers`。

### 5.2 `conftest.py` 要点

```python
import os, shutil, sqlite3, pytest
from starlette.testclient import TestClient

PROD_DB = os.environ["XL_DB_PATH"]

@pytest.fixture(scope="session")
def snapshot_db(tmp_path_factory):
    """生产库在线备份到临时文件：真实 37 款数据，只读来源，绝不写生产库。"""
    dst = tmp_path_factory.mktemp("db") / "snapshot.db"
    src = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    src.backup(out); src.close(); out.close()
    return str(dst)

@pytest.fixture(scope="session")
def app(snapshot_db, tmp_path_factory):
    os.environ.setdefault("MEDIA_UID_SECRET", "test-secret-not-for-prod")
    from app.main import create_app          # 以 recon 记录的工厂为准
    return create_app(db_path=snapshot_db, media_cache=str(tmp_path_factory.mktemp("cache")))

def _client(app, user, roles):
    c = TestClient(app)
    c.headers.update({"X-Test-User": user, "X-Test-Roles": ",".join(roles)})  # 仅测试模式启用的注入头
    return c

@pytest.fixture
def anon(app):     return TestClient(app)
@pytest.fixture
def viewer(app):   return _client(app, "viewer", ["strobe.view"])
@pytest.fixture
def sales(app):    return _client(app, "sales", ["strobe.view", "cpq.quote"])
@pytest.fixture
def sales_cost(app): return _client(app, "sales2", ["strobe.view", "cpq.quote", "cpq.view_cost"])
@pytest.fixture
def manager(app):  return _client(app, "mgr", ["strobe.view", "cpq.quote", "cpq.view_cost", "cpq.override_margin"])
@pytest.fixture
def admin(app):    return _client(app, "admin", ["strobe.view", "cpq.quote", "cpq.view_cost", "cpq.override_margin", "cpq.view_supplier", "cpq.admin"])
```

- 测试身份注入头只在 `create_app(testing=True)` 或环境变量 `XL_TESTING=1` 时生效；生产模式下该头被忽略（`test_api_contract.py` 中有用例断言这一点）。
- 测试用航线与汇率由 conftest 写入快照库（固定数值，非零），保证引擎可算。

### 5.3 必须存在的测试与断言（测试函数名固定）

**test_engine.py [A]**

| 测试名 | 断言 |
|---|---|
| `test_cbm_and_packages` | qty=3、2 台/箱、箱 60×50×40 → packages=2、CBM=0.24 |
| `test_express_volumetric_weight` | 箱 60×50×40、毛重 18kg、除数 5000 → 单箱计费重 24.0；毛重 25kg → 25.0；体积重 24.2 → 24.5（0.5 进位） |
| `test_lcl_min_wm` | CBM=0.24、GW=50 → wm=1.0（最低计费） |
| `test_lcl_weight_dominates` | CBM=1.0、GW=1800 → wm=1.8 |
| `test_fcl_plan_choice` | CBM=60 → 方案为 1×40HQ（给定费率下最低），利用率≈0.882 |
| `test_fcl_weight_limit` | CBM=20、GW=30000 → 不可用单个 20GP，方案满足重量约束 |
| `test_flight_case_increases_cost` | 同型号同数量，航空箱方案 total_cost > 纸箱方案 |
| `test_ddp_converges` | DDP + 关税 + 增值税，迭代收敛，`|total_cost(P)/(1-m) - P| < 1e-4` |
| `test_unit_price_rounds_up_only` | 所有行 `unit_i ≥ unit_raw_i` 且 `unit_i - unit_raw_i < step` |
| `test_actual_margin_not_below_target_after_rounding` | margin 模式下 `actual_margin ≥ target_margin - 1e-6` |
| `test_price_mode_margin` | 输入单价 → 实际毛利与手算一致（±1e-6） |
| `test_golden_vectors`（参数化） | 每组向量所有输出字段与期望值差 ≤ 0.01 |
| `test_bad_incoterm` | express + FOB → `ValueError("BAD_INCOTERM")` |
| `test_engine_is_pure` | 对 `engine.py` 做 AST 扫描：不得 import `sqlite3/os/requests/httpx/open` |

**test_freight_alert.py [A]**（运费大于货值报警）

```python
def test_freight_exceeds_goods_alert(engine_ctx):
    # 便宜频闪 + 重型航空箱 + DDP 远程航线：出厂价 280 CNY/台，航空箱 1 台/箱 0.2 CBM
    res = compute_case(exw_cny=280, qty=10, packing="flight_case", units_per_pkg=1,
                       pkg_cm=(70, 55, 52), pkg_gw=26, case_cost_cny=150,
                       mode="sea_lcl", incoterm="DDP", lane="TEST-FAR-DDP", margin=0.20)
    assert res.totals.freight_ratio > 1.0
    codes = {a.code: a for a in res.alerts}
    assert "FREIGHT_EXCEEDS_GOODS" in codes
    assert "FREIGHT_EXCEEDS_GOODS" in res.gate.requires_ack
    assert codes["FREIGHT_EXCEEDS_GOODS"].suggestions          # 至少一条改进建议

def test_freight_ratio_bands(engine_ctx):
    # 构造 ratio≈0.25 / 0.50 / 0.85，各自只出现对应级别
    ...

def test_freight_ratio_uses_exw_only(engine_ctx):
    # 分母只取出厂货值：增加航空箱成本不改变分母
    ...

def test_export_blocked_without_freight_ack(sales_cost, freight_heavy_payload):
    r = sales_cost.post("/api/cpq/quotes", json=freight_heavy_payload | {"acks": []})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ACK_REQUIRED"
    r2 = sales_cost.post("/api/cpq/quotes", json=freight_heavy_payload | {"acks": ["FREIGHT_EXCEEDS_GOODS"]})
    assert r2.status_code == 201
```

**test_gates.py [A]**

| 测试名 | 断言 |
|---|---|
| `test_margin_ok_at_15` | actual_margin=0.15 → 无 MARGIN 告警 |
| `test_margin_below_min_requires_override` | 0.10 → `MARGIN_BELOW_MIN`，`requires_override=True`；sales 提交 → 409 `OVERRIDE_REQUIRED`；manager 带 10 字以上原因 → 201；原因不足 10 字 → 409 |
| `test_margin_fuse_hard_block` | −0.03 → `MARGIN_FUSE`；manager 带原因仍 409；draft=true 也 409 |
| `test_margin_fuse_boundary` | 恰好 −0.021 → 触发熔断（≤ 判定） |
| `test_price_floor_fuse` | 单台货值折合 259.99 CNY → `PRICE_FLOOR_FUSE` 阻断；260.00 → 不触发 |
| `test_no_cost_basis_blocks` | 无有效报价型号 → `NO_COST_BASIS` |
| `test_lane_expired_blocks`、`test_lane_unpriced_blocks`、`test_fx_expired_blocks` | 各自阻断 |
| `test_packing_estimate_requires_ack` | 估算包装 → 需确认 |
| `test_thresholds_from_config` | 修改测试配置 min_margin=0.20 → 0.18 时出现 `MARGIN_BELOW_MIN`（证明未硬编码） |

**test_api_contract.py [A]**

| 测试名 | 断言 |
|---|---|
| `test_strobes_list_count` | `/api/strobes` count == 快照库频闪数（live 中再断言 == 37） |
| `test_strobes_list_has_no_cost_keys` | 递归遍历 JSON 所有键，不含 `cost`、`exw`、`supplier`、`offer`、`margin` 子串 |
| `test_unknown_values_are_null` | 解析不到的字段为 null，不为 0 |
| `test_context_masked_for_sales` | sales 调 context：`cost_basis_cny` 键不存在、`cost_masked` 为 true |
| `test_context_cost_for_view_cost` | sales_cost：有 `cost_basis_cny`，无 `offers`、无任何 `supplier` 键 |
| `test_context_suppliers_for_admin` | admin：有 `offers[].supplier_name` |
| `test_calculate_rejects_client_cost` | 请求体任意位置出现 `cost_basis_cny` / `exw_cny` → 400 `CLIENT_COST_REJECTED` |
| `test_calculate_strips_cost_for_sales` | sales 结果中无 §3.4.5 列出的成本字段 |
| `test_auth_required` | anon 访问所有新 API → 401；viewer 调 calculate → 403 |
| `test_test_headers_ignored_in_prod_mode` | `testing=False` 的 app 中 `X-Test-Roles` 无效 → 401 |

**test_media_security.py [A]**（图片路径有效性）

```python
def test_all_strobe_images_resolve(viewer):
    items = viewer.get("/api/strobes").json()["items"]
    for it in items:
        for img in it["images"]:
            for url in (img["thumb_url"], img["large_url"], img["orig_url"]):
                r = viewer.get(url)
                assert r.status_code == 200, url
                assert r.headers["content-type"].startswith("image/")
                assert r.headers["x-content-type-options"] == "nosniff"
                assert r.content[:4] in (b"\x89PNG", b"RIFF") or r.content[:3] == b"\xff\xd8\xff"

def test_media_requires_auth(anon, a_valid_uid):
    assert anon.get(f"/media/a/{a_valid_uid}").status_code == 401

@pytest.mark.parametrize("bad", ["../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "a_%00", "a_zzzz", "/abs/path", "a_..\\x"])
def test_media_traversal_rejected(viewer, bad):
    assert viewer.get(f"/media/a/{bad}").status_code in (400, 404)

def test_media_bad_width(viewer, a_valid_uid):
    assert viewer.get(f"/media/a/{a_valid_uid}?w=999").status_code == 400

def test_media_symlink_escape(viewer, planted_symlink_asset):
    # conftest 在临时 MEDIA_ROOT 中登记一个指向根目录外的符号链接资产
    assert viewer.get(f"/media/a/{planted_symlink_asset}").status_code == 404

def test_uid_not_guessable():
    # asset_uid 与自增 id 不存在可推导关系：相邻 id 的 uid 汉明距离足够大，且不含 id 明文
    ...

def test_legacy_media_route_unchanged(viewer_or_existing_client):
    # 旧 /media/<supplier>/<model>.png 对已登记资产仍返回 200（行为不变）
    ...
```

**test_csp_static.py [B]**

| 测试名 | 断言 |
|---|---|
| `test_no_external_urls_in_templates_and_static` | 扫描 `app/templates/**`、`app/static/css/**`、`app/static/js/**`（新文件 + 页面实际引用的 Tabler/ECharts 文件）：`src=`/`href=`/`url(`/`@import`/`fetch(`/`import ... from` 中不得出现 `http://`、`https://`、`//` 开头的外部地址（XML 命名空间 `http://www.w3.org/` 除外） |
| `test_no_inline_script_or_handlers` | 新模板中：`<script>` 必须带 `src` 或 `type="application/json"`；无 `on[a-z]+=` 属性；无 `style=` 属性；无 `javascript:` |
| `test_rendered_page_has_no_external_refs` | 以 sales 身份 GET `/strobes`，解析 HTML，所有资源引用均为同源相对路径 |
| `test_csp_header_compatible` | 若应用自身设置 CSP，`script-src` 不含 `unsafe-inline`/`unsafe-eval` 时页面仍满足上述规则（即规则本身保证兼容） |

**test_dom_contract.py [B]**

- GET `/strobes`：存在 §3.4.10 中全部静态选择器；`.sg-card` 数量 == `/api/strobes` count；每张卡片的 `data-*` 与 API 数据一致；`#sg-data` 可被 `json.loads` 且条目数一致；HTML 中不含任何成本字段名与供应商名称。

**test_js_parity.py [B]**（marker `js`）

```python
def test_js_engine_matches_python(tmp_path):
    out = subprocess.run(["node", "tests/cpq/js_parity_runner.mjs", "tests/cpq/fixtures/cpq_vectors.json"],
                         capture_output=True, text=True, timeout=60, check=True)
    js = json.loads(out.stdout)
    for name, py_res in python_results_for_vectors().items():
        assert_close_tree(js[name], py_res, tol=0.01)

def test_js_engine_has_no_dom_access():
    src = Path("app/static/js/cpq/cpq-engine.js").read_text()
    for banned in ("document", "window", "fetch(", "localStorage", "XMLHttpRequest"):
        assert banned not in src
```

`node` 不存在时该测试**失败**而非跳过（OC 在 Step 0 已确保安装）。

**test_leak_breaker.py [C]**（底价防泄露断路器，marker `export`）

```python
EXPECTED_DTO_FIELDS = {...}   # 与 §3.4.7 逐字段一致，含嵌套

def test_customer_dto_whitelist():
    assert dataclass_field_tree(CustomerQuoteDTO) == EXPECTED_DTO_FIELDS

def test_export_code_never_reads_internal_snapshot():
    for f in ["app/cpq/export.py", "app/cpq/letter.py"]:
        assert "internal_snapshot" not in Path(f).read_text()

@pytest.mark.parametrize("fmt", ["customer.pdf", "customer.html", "whatsapp.txt", "images.zip"])
def test_all_37_exports_clean(manager, all_strobe_ids, fmt, leak_terms):
    """真实数据全量：37 款逐一冻结报价并导出，扫描供应商名、工厂型号、所有出厂价、人民币标记、内部键。"""
    for mid in all_strobe_ids:
        token = freeze_quote_for(manager, mid)       # 用测试航线，毛利 20%，必要确认项全勾
        r = manager.get(f"/quotes/{token}/{fmt}")
        assert r.status_code == 200, (mid, fmt, r.text[:200])
        text = extract_text(fmt, r.content)          # pdf→pypdf 文本+元数据；zip→文件名+EXIF；其它→去标签文本
        hits = independent_scan(text, leak_terms)    # 测试自带的独立扫描器，不复用 leak_guard 代码
        assert hits == [], (mid, fmt, hits)

def test_breaker_trips_on_injected_supplier(manager, monkeypatch, a_token, a_supplier_name):
    monkeypatch.setattr("app.cpq.export._footer_note", lambda dto: f"Made by {a_supplier_name}")
    r = manager.get(f"/quotes/{a_token}/customer.html")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "LEAK_BLOCKED"
    assert a_supplier_name not in r.text                          # 不回显命中内容
    assert audit_rows(action="LEAK_BLOCKED", quote_token=a_token)  # 写了审计

def test_breaker_trips_on_cny_marker(manager, monkeypatch, a_token):
    monkeypatch.setattr("app.cpq.export._footer_note", lambda dto: "Approx. ¥1,200")
    assert manager.get(f"/quotes/{a_token}/whatsapp.txt").status_code == 422

def test_breaker_trips_on_exw_value(manager, monkeypatch, a_token, a_exw_value):
    monkeypatch.setattr("app.cpq.export._footer_note", lambda dto: f"USD {a_exw_value:.2f}")
    assert manager.get(f"/quotes/{a_token}/customer.pdf").status_code == 422

def test_breaker_trips_on_legacy_media_path(manager, monkeypatch, a_token):
    monkeypatch.setattr("app.cpq.export._footer_note", lambda dto: "/media/x/y.png")
    assert manager.get(f"/quotes/{a_token}/customer.html").status_code == 422

def test_breaker_has_no_bypass():
    src = Path("app/cpq/leak_guard.py").read_text()
    for banned in ("bypass", "skip_scan", "force", "allow_leak"):
        assert banned not in src.lower()

def test_pdf_metadata_clean(manager, a_token, leak_terms): ...
def test_zip_images_no_exif_and_safe_names(manager, a_token): ...
def test_unsafe_flagged_image_excluded(admin, manager, a_model_with_two_images): ...
def test_gallery_html_has_no_supplier_or_cost(sales): ...
```

**test_exports.py [C]**

| 测试名 | 断言 |
|---|---|
| `test_pdf_five_in_one` | PDF 文本同时包含：对客 SKU、至少 3 项参数标签（如 `DMX`、`IP`、`Power`）、包装 CBM、单价与总额（USD）、Incoterm、付款条款、交期；PDF 中嵌入图片数 ≥ 行数 |
| `test_html_self_contained` | 导出 HTML 中无 `src="/`、无 `href="/`、无 `http`；图片全部为 `data:image/` |
| `test_whatsapp_format` | 含 `*Quotation`、每行有 `USD`、无 HTML 标签、长度 ≤ 4000 字符/行组 |
| `test_export_blocked_when_gate_fails` | 熔断报价无法导出（409）；草稿无导出链接 |
| `test_quote_no_unique_under_concurrency` | 20 线程并发冻结，`quote_no` 全部唯一 |
| `test_server_recomputes_on_freeze` | 篡改前端结果字段一并提交 → 服务端忽略，冻结结果与 calculate 一致 |

**test_letter.py [C]**

| 测试名 | 断言 |
|---|---|
| `test_letter_payload_is_dto_only` | 捕获发给 `SOL_LETTER_CMD` 的输入：键集合 ⊆ DTO 字段 + 语气参数；不含 `image_refs`、任何成本键 |
| `test_letter_number_mismatch_rejected` | 模拟 Sol 返回含 DTO 外数字（如 `USD 159.00`）→ 重试 1 次后 422 `LETTER_NUMBER_MISMATCH` |
| `test_letter_leak_rejected` | 模拟返回含供应商名 → 422 `LEAK_BLOCKED` |
| `test_letter_extra_notes_scanned` | 业务员备注含 `出厂` → 不调用 Sol，直接 422 |
| `test_letter_stored_as_draft` | 成功后 `cpq_quote_letter.status == 'draft'` |
| `test_no_send_capability` | 代码中无 SMTP、WhatsApp API、邮件发送调用（静态扫描 `smtplib`、`sendmail`、`graph.facebook`） |

**tests/live/test_live_strobe_catalog.py [A]**（marker `live`）

```python
EXPECTED = int(os.environ.get("EXPECTED_STROBE_COUNT", "37"))

def test_strobe_count(viewer):
    assert viewer.get("/api/strobes").json()["count"] == EXPECTED

def test_every_strobe_has_image_or_is_gated(viewer, sales_cost):
    for it in viewer.get("/api/strobes").json()["items"]:
        safe = [i for i in it["images"] if i["customer_safe"]]
        if not safe:
            res = calc_single(sales_cost, it["model_id"])
            assert "IMAGE_MISSING" in {a["code"] for a in res["alerts"]}

def test_every_strobe_has_cost_basis_or_is_gated(sales_cost): ...
```

### 5.4 OC 现场验收命令（按顺序执行，全部输出摘要写入验收报告）

```bash
cd <APP_REPO>
export XL_DB_PATH=<生产库路径> XL_TESTING=1 EXPECTED_STROBE_COUNT=37

# 1. 静态与单元
python -m pytest tests/cpq -q -p no:cacheprovider -m "not js and not export"
# 2. JS 一致性
node --version && python -m pytest tests/cpq -q -m js
# 3. 导出与断路器（含 37 款全量扫描）
python -m pytest tests/cpq -q -m export
# 4. 真实数据
python -m pytest tests/live -q -m live
# 5. 全量复跑，确认零 skip / 零 xfail
python -m pytest tests -q -rsx | tee /tmp/task01_pytest.txt
grep -E "skipped|xfailed|xpassed" /tmp/task01_pytest.txt && echo "FAIL: skip/xfail present" || echo "OK"
# 6. 规格审计
python scripts/strobe_spec_audit.py > tasks/reports/task_01_spec_audit.md
# 7. 冒烟（应用已以测试配置启动）
python scripts/smoke_task01.py --base http://127.0.0.1:<port>
```

### 5.5 视觉验收（OC 执行）

用无头 Chromium（Playwright，仅测试环境）以 sales 身份：

1. 打开 `/strobes`，1440×900 与 390×844 各截图：卡片模式、表格模式、灯箱打开、抽屉打开（含告警）。
2. 断言：`document.documentElement.scrollWidth <= window.innerWidth`；console 无 error；无 `securitypolicyviolation` 事件。
3. 截图存 `tasks/reports/task_01_screens/`，在验收报告中引用。

### 5.6 防篡改检查（OC 执行）

```bash
# 规定的测试名必须全部存在
for t in test_freight_exceeds_goods_alert test_export_blocked_without_freight_ack test_margin_fuse_hard_block \
         test_price_floor_fuse test_all_strobe_images_resolve test_media_traversal_rejected \
         test_all_37_exports_clean test_breaker_trips_on_injected_supplier test_customer_dto_whitelist \
         test_no_external_urls_in_templates_and_static test_js_engine_matches_python test_golden_vectors; do
  grep -rq "def $t" tests/ || echo "MISSING $t"
done
# 不得出现 skip / xfail
grep -rnE "pytest\.(skip|mark\.skip|mark\.xfail|importorskip)" tests/ && echo "FAIL"
# 业务阈值不得硬编码在 app/ 中
grep -rnE "0\.15\b|-0\.021|\b260\b" app/ --include=*.py --include=*.js && echo "CHECK: hardcoded threshold"
```

### 5.7 上线前业务确认清单（写入验收报告，由业务方逐项勾选）

1. `data/cpq/freight_lanes.yaml` 已按真实货代报价填写并执行 `python scripts/cpq_seed.py`。
2. 当日 USD/CNY 汇率已录入。
3. `config/company_profile.yaml` 公司信息、Logo、付款与质保条款已确认。
4. 37 款中需要航空箱的型号已录入航空箱尺寸与单价（审计表“航空箱方案来源”列）。
5. 带工厂 Logo/水印的产品图已在灯箱中标记为不可对客。
6. 对客 SKU 规则已确认。

### 5.8 验收报告模板 `tasks/reports/task_01_acceptance.md`

```markdown
# TASK-01 验收报告
- 结论：通过 / 不通过
- 代码提交：<commit sha>
- 测试摘要：<passed 数 / failed 0 / skipped 0>
- 37 款导出泄露扫描：0 命中
- 截图：见 task_01_screens/
- 规格审计：见 task_01_spec_audit.md（缺失最多的 5 个字段：...）
- 阻塞项：无 / <列表>
- 业务确认清单：<§5.7 六项状态>
```

---

## 6. 【安全与业务红线】

以下任何一条被违反，验收直接判定不通过，无例外：

1. **出厂底价绝对隔离**：出厂价、航空箱成本、配件成本、供应商名称/简称、工厂原型号、offer id，不得出现在任何对客输出中——包括 PDF 正文与元数据、导出 HTML、WhatsApp 文案、图片包文件名与 EXIF、Sol 函件、以及发给 Sol 的输入。
2. **对客只用 USD**：对客文件不得出现 `¥ ￥ CNY RMB 元 人民币` 任一标记。
3. **断路器不可绕过**：`leak_guard` 没有任何放行开关、参数或权限；新增导出格式必须先接入断路器才能上线。
4. **成本永远来自服务端**：任何接口不接受客户端传入的成本；冻结报价必须服务端重算。
5. **熔断不可放行**：`MARGIN_FUSE`（≤ −2.1%）与 `PRICE_FLOOR_FUSE`（频闪单台货值 < ¥260）任何权限都无法放行；`MARGIN_BELOW_MIN`（< 15%）只能由 `cpq.override_margin` 带原因放行并留痕。
6. **无外部 CDN**：模板、CSS、JS 中不得引用任何外部域名（脚本、样式、字体、图片、接口）；不新增第三方前端库；不使用内联脚本、内联事件、内联样式属性，确保生产 CSP 不拦截。
7. **对客文件不含媒体 URL**：图片一律内联或重新编码打包；旧 `/media/<supplier>/...` 路径不得出现在任何对客输出中。
8. **生产数据安全**：迁移只增不改；迁移前在线备份；测试只用生产库快照副本，严禁测试写生产库。
9. **密钥不入库**：`MEDIA_UID_SECRET`、Sol 调用凭据只走环境变量；仓库中不得出现真实密钥；测试密钥字符串明确标注 not-for-prod。
10. **函件不自动发送**：系统只生成草稿供人工复制，不得集成任何邮件、WhatsApp、阿里站内信发送能力。
11. **审计留痕**：报价冻结、毛利放行、泄露拦截、图片对客标记变更、包装档案修改，全部写 `cpq_audit_log`，日志中不写泄露原文与成本明细。
12. **不破坏现有系统**：现有路由、模板、表结构、旧媒体路由行为保持不变。

---

*TASK-01 结束。OC 完成 §5 全部步骤并提交验收报告后，将本文件移至 `tasks/done/`。*

# TASK-01 验收报告

- **结论:通过(有条件上线)**——代码、测试、部署与真实数据接线全部完成;但运价、汇率、包装档案现网为空,**在业主填入这些数据之前,所有报价会被系统拦截**(工单规定的行为,不是缺陷)。
- **代码提交:** `c2701be`(及此前 A1 `61bf5d4` … C4 `759e1e0`);部署脚本 `deploy/step38_install_t01.sh`,生产已安装(备份后缀 `.bak-20261019152706`)。
- **测试摘要:**
  - 新增测试(镜像,含 37 款真实数据快照):**298 passed / 0 failed / 0 skipped / 0 xfailed**
  - 现有控制台回归(镜像,含新 app.py):**481 passed**(原 473 + 新增 JSON POST 守卫 8)
  - 生产同构测试(step38 内):128 passed
  - 防篡改检查(§5.6):规定测试名齐全;tests 中无 skip/xfail;业务阈值无硬编码
- **37 款导出泄露扫描:0 命中**(4 种格式 × 37 款,测试自带独立扫描器)
- **真实浏览器验收**(Playwright + Chromium 156,1440×900 / 390×844):无横向滚动、0 控制台报错、0 CSP 违规;加入篮子 → 选航线 → 服务端算价 → 运费超货值需确认 → 毛利低于最低线需 ≥10 字原因 → 毛利熔断全禁 → 保存草稿 → 冻结并下载 PDF → 生成英文函件,全流程通过。截图见 `task_01_screens/`
- **生产数据接线自检**(imgw 身份,只读调用):`/api/strobes` 37 款、37 款有图、37 款有成本基数;缩略图 200 webp;`/api/cpq/context` 200(fx_missing=true,2 条占位航线,3 种配件);`calculate` → 400 `PACKING_UNKNOWN`(无包装档案);`/strobes` 页面 200。
- **规格审计:** 见 `task_01_spec_audit.md`。缺失最多的字段:净重 / 外形尺寸 / 纸箱 / 认证(37/37 全缺)、像素分段(32)、白光颗数(23)、IP 等级(16)。来源 PDF 不含这些信息,系统一律显示未知,不猜。
- **公网入口:** https://ad.aednas.xyz/v3/ (侧栏"频闪灯" → `/strobes`)。需登录,我无法代你登录做公网验收。

## 与工单不一致处与决定(均已写入各批说明)
1. 目录映射 `app/` → `console/`;已登录用户默认全部权限(业主批准)。
2. 新增 `cpq_*` 表放**独立工作库** `/var/lib/imgw/console/cpq_work.sqlite3`,目录库保持只读。运价不复用 `cat_freight_rate`(装不下航线结构),汇率/包装复用目录库 `cat_fx_rate` / `cat_packing`。
3. 抽屉数字全部取自服务端 `/api/cpq/calculate`(120ms 去抖),不使用本地 JS 引擎(context 缺引擎元数据,服务端算价更安全)。JS 引擎保留,与 Python 引擎对 12 组黄金向量一致。
4. 泄露断路器:对客文本允许贸易术语 `EXW/FOB/CIF/DDP`;小于 100 的成本值只有带小数点才算命中(避免"2 件"误报)。
5. 控制台中间件放宽:仅 `/api/cpq/{calculate,quotes,assets/*/flag}` 与 `/api/quotes/q_*/letter` 的 JSON POST,要求登录会话 + `X-CSRF-Token` + `application/json` + 64KB 上限;代理免登录通道不适用。
6. **安全姿态变化(请知悉):** `imgw-console` 增加 drop-in `NoNewPrivileges=false`(Sol 的 codex 沙箱需要,与询价雷达一致),并新增 `/etc/imgw/console-cpq.env`(含新生成的 `MEDIA_UID_SECRET`,640 root:imgw)。控制台不再是纯只读:会写工作库(报价、审计)。
7. 函件由 Sol 生成,偶发不通过数字/泄露校验,路由自动重试 1 次;仍失败返回错误,不给半成品。

## 阻塞项
无(代码层面)。业务数据缺口见下。

## 业务确认清单(§5.7)
1. 运价 `data/cpq/freight_lanes.yaml`:**待填**(现为 0 占位,`/opt/imgw/wechat/data/cpq/`,填后执行 `scripts/cpq_seed.py`)
2. 当日 USD/CNY 汇率:**待录入**(`cpq_seed.py --fx 7.12 --as-of YYYY-MM-DD`)
3. `config/company_profile.yaml` 公司名称、地址、联系方式、Logo、条款:**待填**(现为空;PDF 暂用占位公司名 `XL Lighting`)
4. 37 款的航空箱/纸箱尺寸与重量、航空箱单价:**待录入**(现无任何包装数据,`PACKING_UNKNOWN`)
5. 带工厂 Logo/水印的产品图:**待在灯箱中标记**为不可对客(接口 `POST /api/cpq/assets/{uid}/flag` 已就绪,前端按钮未接线)
6. 对客 SKU 规则:**待确认**(当前自动生成 `XL-STB-001…`,冻结后固定)

## 业主需决策的事项
- **¥260 货值红线**:凤舞 37 款中 11 款出厂价低于 ¥260、15 款低于 ¥310;这些型号在 20% 毛利下会触发 `PRICE_FLOOR_FUSE` 被拦截,只能用更高单价报价。请确认红线是否适用于这些型号。
- 灯箱里的"标记为不可对客"按钮本期只渲染未接线(工单 §2.5 要求),需要时可作为后续小工单。

# TASK-01 进度
## Batch A1(纯算价引擎)— 已完成,镜像测试 54 passed / 0 skipped
- 代码:`src/console/cpq/`(部署目标 `/opt/imgw/wechat/console/cpq/`)、`config/cpq.yaml`、`tests/cpq/`
- Codex Astra(xhigh)一次产出 14 个文件,耗时约 29 分钟
- 检查:业务阈值无硬编码;无 skip/xfail;工单指定测试名齐全;黄金向量 12 组
- 未部署:本批是纯库,随 A2 一并接入控制台
## 待办
A2 数据层与接口(repository、规格解析、媒体、/api/strobes、/api/cpq/*)→ B → C

## Batch A2a(频闪灯数据层/安全媒体/权限/接口)— 完成,镜像测试 108 passed / 0 skipped
- 新增:`src/console/{cpq/workdb.py,security/,strobe/,routes/strobe_routes.py}`、`scripts/strobe_spec_audit.py`、`tests/{cpq,live}`、`pytest.ini`
- 真实库快照:频闪灯 37 款,每款 ≥1 张图,均有已接受的有效报价;规格完整度低(来源 PDF 无重量/尺寸/装箱),未知字段一律 null
- `cpq_*` 新表放独立工作库(`CPQ_WORK_DB`),目录库保持只读
- 集成注意:`/media/a/{uid}` 与现网 `/media/{folder}/{name}` 同形,挂载时必须排在旧路由之前
- 工单自相矛盾处:`has_cost_basis` 含 cost 子串,与"列表无 cost 键"冲突,测试对该布尔键豁免

## Batch A2b(汇率/运价/包装/配件数据层、服务层、/api/cpq/context、/api/cpq/calculate、种子脚本)— 完成,镜像测试 151 passed / 0 skipped
- 新增:`store.py`、`service.py`、`seed.py`、`cpq_routes.py`、`scripts/cpq_seed.py`、`data/cpq/{freight_lanes,accessories}.yaml`(数值为 0 占位,待业主填真实货代报价与配件成本)
- 复用目录库 `cat_fx_rate`、`cat_packing`;运价放工作库 `cpq_freight_lane`(现网 `cat_freight_rate` 装不下航线结构)
- 汇率缺失 → 不计算,返回 FX_EXPIRED 阻断;包装档案缺失 → 400 PACKING_UNKNOWN,不编造尺寸
- 测试夹具修补(Claude 手改,仅测试文件):副本中卸掉追加账本触发器、补 fc_units_per_case、live 夹具导出 catalog_copy
- 全量测试约 4 分钟(每个用例复制 222MB 快照),后续可改为会话级只读快照
- A 批合计:151 tests,0 skip/xfail;业务阈值无硬编码
## 待办
接入控制台(放宽 JSON POST、/media/a 排序在旧路由前、挂载路由)→ Batch B 前端 → Batch C 导出/函件 → 部署

## Batch B1(JS 引擎)— 完成:与 Python 引擎对 12 组黄金向量一致(38 tests;改坏引擎迭代次数会被测试抓到)
## Batch B2(/strobes 画廊、灯箱、抽屉静态结构)— 完成,镜像全量 163 passed / 0 skipped
- 新增:`templates/strobe/*`、`static/strobe/{strobe.css,gallery.js,lightbox.js}`、`/strobes` 页面路由、`test_csp_static`、`test_dom_contract`
- Claude 手修:列表字段未知值保持 null(原先页面转成 []),模板判空;测试对 null 兼容
- 仓库布局约定:`src/` 对应生产 `/opt/imgw/wechat/`(`src/config`、`src/data` 为符号链接)
- 集成待办:`v3/base3.html` 需加 `{% block head_extra %}`;导航加"频闪灯";`/media/a/` 排在旧路由前;中间件放宽 JSON POST
## 待办
Batch B3(抽屉交互/报价篮/成本构成图/毛利滑块)→ Batch C(冻结/导出/泄露断路器/Sol 函件)→ 集成部署 → 浏览器验收

## Batch B3(抽屉交互)— 完成,镜像全量 185 passed / 0 skipped;真实 Chromium 验收通过
- 拆分交付:B3a 篮子、B3c1 成本图+样式、B3c2 纯逻辑模块(node 测试)、B3c3 DOM 接线
- 设计调整:抽屉所有数字取自服务端 `/api/cpq/calculate`(120ms 去抖,只采最新响应),不用本地 JS 引擎(context 缺引擎元数据,且成本隔离更稳);JS 引擎保留并与 Python 黄金向量一致
- Claude 修补:context 不带 model_ids 返回空列表(消除每次加载的 400 红字);行金额字段 `amount_usd`;运费比条布局与分区取自阈值;gallery.html 引入抽屉 css/js 与 csrf meta;actionStates 对齐服务端 gate 语义(blocked_by 含可放行项)
- 浏览器验收(Playwright + Chromium 156,合成汇率/航线/包装数据,仅镜像):1440/390 宽度无横向滚动;0 控制台报错;0 CSP 违规;加入篮子→选航线→服务端算价→运费超货值需确认→毛利低于最低线需 ≥10 字原因→毛利熔断全禁,均符合工单;截图见 `task_01_screens/`
- 测试脚本:`tests/browser/{run,run2,run3}.py`(服务:镜像内 `tests/browser/serve.py`,用合成数据)
## 待办
Batch C(冻结/导出/泄露断路器/Sol 函件)→ 集成(中间件放宽 JSON POST + CSRF、路由顺序、base3 head_extra、导航项)→ 部署前备份 → 验收报告

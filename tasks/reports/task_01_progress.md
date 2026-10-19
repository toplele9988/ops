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

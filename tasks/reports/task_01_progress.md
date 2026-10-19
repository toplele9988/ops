# TASK-01 进度
## Batch A1(纯算价引擎)— 已完成,镜像测试 54 passed / 0 skipped
- 代码:`src/console/cpq/`(部署目标 `/opt/imgw/wechat/console/cpq/`)、`config/cpq.yaml`、`tests/cpq/`
- Codex Astra(xhigh)一次产出 14 个文件,耗时约 29 分钟
- 检查:业务阈值无硬编码;无 skip/xfail;工单指定测试名齐全;黄金向量 12 组
- 未部署:本批是纯库,随 A2 一并接入控制台
## 待办
A2 数据层与接口(repository、规格解析、媒体、/api/strobes、/api/cpq/*)→ B → C

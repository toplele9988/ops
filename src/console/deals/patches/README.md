# deals 候选补丁：W3 / W6 / W7，未部署

`production-source.patch` 包括后端业务变更、新增 `console/deals/operations.py`，以及两个现有 API builder 内的新增路由注册。沿用已有 `v3_deals_api.build`、`v3_quotes_api.build`、`v3_fulfil_api.build` 挂载，不另开服务，不重复挂载相同 builder。

`baseline.sha256` 是此工作区修改前文件的 SHA256；`production-baseline.sha256` 使用去除 `src/` 后的后端路径供补丁装配核对。它们不是已核验的生产文件哈希。本工作区没有 `console/v3.py`，本轮也没有读取生产应用；实际父路由是否已挂载三个 builder 必须由总指挥在发布候选中核对。现有文档及 `patches/r7f/production-mount.patch` 提供了相关挂载上下文，不能据此宣称线上已可用。

在隔离候选源码树根目录核对 SHA 后使用 `patch --dry-run -p1` 检查，再应用此补丁。当前源码树已经包含变更，不要再次向当前源码树应用。补丁已在 `/tmp` 的原始基线副本完成：正向预检 → 应用 → 反向预检 → 回滚 → 逐文件比对原始基线。日志在 `artifacts/deals/patch-check.log`。

上线装配由总指挥执行：备份候选对应的代码和业务库；核对基线（不同则重新生成补丁，不强套）；应用补丁；核对路由表含 `POST /api/v1/deals/{id}/drafts`、`POST /api/v1/quotes/{id}/drafts` 和原收款/履约路由；保留鉴权、密码复核与 CSRF；安装匹配的前端构建；在允许上线时遵守项目发布验收。本轮没有执行生产部署脚本。

新表由已鉴权写事务按需建立：`deal_operation_context`（案子目的国）、`deal_operation_receipts`（幂等回执）、`deal_draft_outbox`（演练草稿箱）。不改变既有 archive 迁移版本，也不改渠道 outbox 的发送状态枚举。GET 不建表。报价和履约沿用 Store 的 v9/v8 迁移。维护者可先在候选数据库副本显式初始化 Store，再用虚构操作验证三个扩展表。不得以初始化或演练为由连真实渠道。

回滚：停止候选写入，回退前端到备份构建；对匹配版本的后端反向应用补丁或恢复原代码备份，移除新增 `operations.py`。扩展表保留，不删除已记下的收款、时间线、备货待办或草稿。旧代码忽略扩展表，既有 archive 版本没有升级。不要为了回滚页面而恢复旧业务库覆盖新收款；如需数据库恢复，先由维护者核对回滚期间新增业务记录。整个流程未在生产执行。

`deal_draft_outbox` 是案子内持久化的演练草稿箱，SQL 强制 `state='draft'`、`mode='dry-run'`；没有发送器，不进入现有渠道 outbox 的 `sending` 状态。后续统一草稿箱接入应保持此约束，实际发送入口需独立的人点击与审核。本次没有实现或验证发送。

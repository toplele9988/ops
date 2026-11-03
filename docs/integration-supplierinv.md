# M2 供应商资料只读盘点交付

交付状态：只读盘点与方案，未实现建档流水线，未安装生产、迁移数据库或重启服务。已读 CLAUDE.md、总纲、项目现状和需求账本；总纲第 5 节按老板已确认版本执行。

## 改动文件

- `docs/supplier-dossier-design.md`：可公开提交的设计，含真实源码接口核对、五层拟建表、来源追溯、增量任务、模型分工、跨供应商产品链接、报价规则和后续验收。
- `private_out/supplier-inventory/REPORT.md`：私有统计、匿名样本评估、处理量与时间假设、未完成项、逐库及真实字段清单。报告不抄客户/供应商名字、账号、聊天正文或价格原文。
- 同一私有目录的 `inventory.py`、`measure.py`、`analyze.py`、`build_report.py`、`verify.py`：只读复核与报告生成；JSON 证据、知识检索输出、合成只读探针也只在该目录。部分证据包含私有文本与源定位，不可提交。
- 根 `INTEGRATION.md`：按用户交付要求新增，作为用户末尾文件范围的这一项明确交付例外。其他目录未写；按本任务范围未更新项目状态和需求账本。

工作区 `.git` 元数据不可读，`git status --short` 实际退出 128，不能验证分支、提交或 tracked 状态。已确认 `.gitignore` 有 `private_out/`；未提交、未推送。

## 接口与数据兼容

未新增或调用生产 HTTP 接口。核对了 `/v3/suppliers`、`/v3/products` 的旧投影读取，与 `/api/v1/archive/` 的往来单位、产品、报价读写接口源码。设计中的 `/api/v1/dossiers/` 为待实现草案。

旧 products 绑定单家供应商，offers 经 product_id 归属；旧 merge 拒绝跨供应商。方案用标准品类/型号/配置层链接每家供应商 SKU，保留旧 ID 和各家报价，不能直接复用旧 merge 实现跨家同款。

## 复核命令与真实结果

在当前工作区运行；所有私有输出留在指定目录：

```sh
python3 private_out/supplier-inventory/inventory.py
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python private_out/supplier-inventory/measure.py
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python private_out/supplier-inventory/analyze.py
python3 private_out/supplier-inventory/build_report.py
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python private_out/supplier-inventory/verify.py
```

逐库结构和行数扫描成功；一个辅助恢复库只读打开失败，已记未完成。NAS 文档哈希对账、本地 tokenizer 参考计数、分层去重样本选择和报告生成均退出 0。verify 验证 Python 语法、20 份去重样本和 8 种格式、14 份图片/Office/PDF 原件 SHA、样本复合键与来源记录、合成库只读写入拒绝且文件哈希未变、已知身份/名字/样本正文泄露扫描及忽略规则，结果在私有 `verification.json`。泄露扫描只是已知字符串检查，仍需按私有产物边界保管。

统一知识入口亦已按 CLAUDE.md 执行，退出 0，结果只写私有目录。没有生成模型调用或线上抽取测试；本地 BGE tokenizer 计数仅作估算参考，不是生成模型计费 usage。没有运行 tsc -b、生产端口测试、浏览器或 e2e；本次没有应用代码或部署候选变更。

## 生产安装与回滚

本次文档无需生产安装，生产没有变更，生产回滚不适用。

后续实现时的安装顺序（尚未执行）：

1. 在隔离候选库实现适配器、增量任务、迁移与档案接口，完成合成测试及私有来源回放，验证报价归属、重复回放、部分解析和合并撤销。
2. 依仓库要求完成隔离 e2e_smoke 桌面/手机验收；为所有测试配置独立端口与候选输出路径。
3. 对受影响代码、路由和业务库做可验证备份，确保 SQLite 主库与 WAL 的一致性，记录精确版本及恢复方法。
4. 按候选实现的迁移命令主动迁移，装入实现包、核对路由表、重启对应业务服务，复查只读档案及来源定位。当前没有实现包或迁移命令，不能将本设计直接安装。
5. 回滚先停止新 worker、回切原接口/投影和档案 head，保留来源与历史报价 ID；若未来改写旧表，按该次经验证的库备份恢复步骤执行。代码回滚不能代替数据恢复。

工作区回退只移除本次新增的公开设计及本文件；私有报告和证据按私有资料保留策略处理。没有改网络分流、渠道配置或发送队列，没有向客户/供应商外发消息。

## 未完成

个人供应商联系人总数、全部微信历史/加密桌面库覆盖、全部原件可读性与全页解析、一个辅助恢复库的读取、字段抽取与跨家合并准确率、真实生成模型 usage/吞吐、候选代码与迁移、生产发布均未完成。人工建档审核仅规划合并冲突、价格异常两类；任何工期均为有条件估算。

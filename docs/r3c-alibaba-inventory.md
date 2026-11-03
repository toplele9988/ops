# R3-C 阿里停机缺口复核（只读副本，未恢复生产）

2026-11-01 09:34–09:35 UTC 从运行时只读挂载备份核对。阿里当前的 19 个 pending 窗口包含 455 条来源，其中 **349 条已完成，只有 106 条从未请求**。不能直接重跑 19 窗，更不能重放旧版本的 994 个 pending。当前未知超时的一窗含 16 条来源，永久隔离；与 19 个 pending 的来源交集为 0。

先读本仓库入口、总方案、数据管道、R2-F 全文与 WG-01，再只读真实 `analysis_core.py`、`analysis_runtime.py`、`analysis_multimodel.py`、`analysis_quota_pool.py`、`quota_runtime.py`、`source_freeze.py`、`icbu_history.py` 及额度矩阵源码。没有打开凭据配置，没有启动消费者、调用模型、联系渠道或启停服务。工具和报告不证明生产已恢复。

## 核对口径

通过 `mode=ro`、`PRAGMA query_only=ON` 和 SQLite `backup()`，将下列四库复制到新建 `/tmp/r3c-ali-*` 私有目录（0700，文件0600）；先查看副本 `sqlite_master` 与 `PRAGMA table_info`，后续分析只在副本执行：

- `/var/lib/imgw/icbu/conversation-analysis/analysis.sqlite3`：规划、当前版本、模型尝试、执行绑定、可靠响应、审计和熔断。
- `/var/lib/imgw/icbu/inbound.sqlite3`：来源归档。
- `/var/lib/imgw/icbu/knowledge-sync.sqlite3`：历史索引账本。
- `/var/lib/imgw/video_knowledge_auto/equipment/catalog.sqlite3`：已落库知识资产与片段。

另将明确无凭据的 `quota-pool-20261024/source-freeze.json`、`usage.json` 复制到同一私有目录，仅提取已确认字段和汇总。备份期间有一次 SQLite `unable to open database file`，重新只读打开后完成备份；没有改权限、复制WAL或以可写方式打开原库。四库不是跨文件原子快照，交叉核对为零差异也不能替代投产前由总指挥确认写者边界。

范围由 `current_conversations.analysis_version` 绑定的全部来源建立，再按原始消息 ID 与 payload SHA 去重，逐条和入站归档及 `history_index` 对账。共 14,182 条来源，与全部 `history_index.status='indexed'` 完全相等；来源正文、角色、账号/会话字段、指纹以及索引的 owner/scope/model/status 均吻合。正文仅在分析进程内用于比较和哈希，未输出、写仓库或用作测试资料。本工具不读取授权配置，后续真实执行仍必须通过原来源授权入口。

对全部历史版本的窗口建立来源字符区间覆盖，不能因当前版本改变就将旧证据认定为未请求。完成必须具有完整区间覆盖、持久结果、执行绑定、成功尝试审计，以及匹配的 Catalog 资产和非空片段，或经过验证的 `no_supported_claims` 终态。两条旧 validated 窗没有执行绑定，未用于完成或响应复用判断。未知按实际 `quota_pool_failed.matrix_error_code=api_timeout` 审计及其原始尝试、执行指纹定位，优先于任何已完成或缓存状态。

## 分类与代表

下表是当前来源的互斥分类，单位为原始消息资料，不是模型请求、事实数或窗口数。脱敏标识为 `SHA256(canonical_json([inbound_id,source_sha256]))` 前12位，不能凭它恢复原文或授权执行。

| 分类 | 数量 | 代表性脱敏标识 |
| --- | ---: | --- |
| 已完成 | 14,060 | A-09f6b6da1055、A-d1268716594a、A-3c2bb602b852 |
| 有可靠持久响应但本地未完成 | 0 | 无 |
| 从未请求 | 106 | A-b06d547c9c89、A-88ea40876476、A-299b6282bc7c |
| 结果未知 | 16（1请求） | A-834f20c38a8c、A-848e84b09c6f、A-6776cb102f1f |
| 来源时间待确认（缺失/格式不合法） | 0 | 无 |
| 有请求审计但缺完整可靠响应，需另行核对 | 0 | 无 |

所有当前来源 `sent_at` 都有时区且不晚于收到时间；106 条从未请求资料的来源时间为 2026-10-25 15:48:34.771 至 2026-10-31 15:51:34.331 UTC。未知16条来源时间为 2026-10-30 06:24:19.639 至 15:43:24.333 UTC。以上时间范围是查询结果，不是选取范围的条件；没有用任意日期或把 `after` 往前挪来选出这106条。Win10时钟校准和上游来源时间真实性仍需H12负责人确认，本次只能证明记录内的时间自洽。

当前458个 indexed 会话的资产都存在且 SHA、owner 绑定正确，拥有3,843个知识片段；另3个会话是明确的无支持事实终态。跨历史版本共验证472个知识资产，不能将资产数当资料数或真实业务事实准确率。

| 窗口范围 | pending | validated | retry_wait | needs_repair | infra_blocked |
| --- | ---: | ---: | ---: | ---: | ---: |
| 当前539窗 | 19 | 519 | 0 | 0 | 1 |
| 旧版本1,016窗 | 994 | 19 | 2 | 1 | 0 |

旧994 pending中970属于原冻结来源版本，24不属于冻结集，仍全部是旧版本审计，不能成批排队。旧2条retry_wait也冻结。原冻结清单471个来源版本与账本中的哈希审计匹配，继续保留。当前19pending中13窗混有旧请求证据，只有6窗完全从未请求；补处理清单应从106条原始资料重建稳定单元，并在请求前再次核对旧完成区间与未知源交集。

## 停机、额度与停止条件

本次备份最新扫描为2026-11-01 09:34:34.294 UTC；最新模型尝试仍在06:09:39.514 UTC，约45秒后`api_timeout`写入审计并打开profile熔断。窗口审计仍是准入6次、成功5次、失败1次。扫描持续不代表资料已消化，也没有依据通过重设水位、旧日期或重建账本来恢复。

阿里独立模型账本完成6次，已确认总用量26,819 tokens；`qwen3.8-2.4t-a95b`保留14,782未知预留，`usage_uncertain`，`in_flight=false`。模型账本7次请求与窗口审计6次准入口径不同，不能混称。云端剩余额度、真实单价/费用、未知请求实际计费、新增付费预算均**未知**。源码列出的8个模型是既有配置，不能当成老板批准立即调用所有模型。

预计可补资料106条；若以后采用每条一个稳定来源单元、阿里单读者，则最多106个首次模型请求，具体分块数及限额仍需适配器核对后冻结。不能沿用包含349条旧证据的19原窗凑请求。自然新增优先，积压批大小和速度待老板批准。任何新未知、隔离交叉命中、完成证据变化、预算不确定/触顶或原熔断未审计确认均停止，保留原账、预留与审计。禁止调用`apply_recovery`重排原infra_blocked；本报告未实现或安装阿里worker修复。

## 可复核工具与私有草案

`deploy/kb/inventory_alibaba.py`只有 `/tmp` 私有备份输入，要求四个上述SQLite副本及`source-freeze.json`、`usage.json`，所有输入为当前用户持有的0600无软硬链接普通文件。拒绝生产目录；SQLite均只读连接，不导入消费者或模型客户端。输入不完整或证据变动即失败，不输出异常详情。

```sh
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/inventory_alibaba.py --snapshot "$SNAPSHOT_DIR" --private-review
```

默认仅输出计数与SHA脱敏代表。`--private-review`在固定`/tmp`下另建0700临时目录、0600的`review.json`，包括哈希汇总、阿里唯一hold的来源/正文/prompt摘要、原始来源payload的`source_sha256`、绑定原attempt和execution完整行摘要的request_sha，以及只包含106条未请求资料的`backlog_review_template`。106条entry也绑定真实`source_sha256`，原始ID只在该私有草案内，无正文。349条已完成及16条未知不进入执行草案；106条草案与未知来源ID摘要、正文摘要的交集都为0。它可与微信三条hold组合成`kb-source-quarantine-v1`。该payload摘要跨分析窗口、分析版本、模型保持稳定；上游补富消息若改写payload会改变摘要，因此仍需同时校验不变来源ID；若连来源ID也发生alias变化，后续适配器必须使用平台消息ID的已验证别名映射，不能仅凭新payload摘要放行。

模板中的`after=null`和`prompt_sha256=null`是有意保留的待补项：老板未确认固定恢复边界，且阿里稳定来源适配器尚未交付。`backlog.py build`应拒绝该草案，不能靠虚构时间或伪造提示词摘要变成可执行清单。来源仍需人工复核、通过原授权入口，并由适配器绑定真实新单元提示词后才可冻结。没有真实模型执行验收。

虚构测试`tests/kb_recovery/test_inventory_alibaba.py`验证跨版本完成覆盖、未知优先、部分响应不误判可重跑、可靠响应只续本地步骤，以及生产目录、`..`路径穿越、软硬链接、非私有权限拒绝。本分支运行7项通过；总套件结果见主交付。

## 源码证据基线

| 只读源码 | SHA-256 |
| --- | --- |
| analysis_core.py | 023433809d08e917b1cc11ef6154e9521f1a602c9a830596b3913db36943f82a |
| analysis_multimodel.py | b9bede6f7bd7105f39e3f6fca223811965fc3a2e000713148bdb7a8c45be7abc |
| analysis_quota_pool.py | beb98bf2de43de4e4599c3e8aaf3ec23bc3be80c354304a8cb3274fac22391ec |
| analysis_runtime.py | 7b9362e752a97d4a8153f9874615a63f50dc18997a39f8295f49516eb1c70e21 |
| quota_runtime.py | 5ab05f1ac860d1504259c2a2eab1de21593106c8cc09d1e48edbd39b11d299c7 |
| source_freeze.py | eb902edb84056c4fd47f887b6bb9ae6103cdf172c75dc9de1fe3bc9adcccc824 |

这些文件都在`/opt/imgw/scripts/icbu-conversation-analysis/`，本次零修改。盘点工具无需部署生产；回滚只移除开发副本的新工具、测试和本报告。私有备份由总指挥保留或清理，不倒灌任何调用/额度/业务库。没有新HTTP入口、路由、导航、SQL拼接值或对外发送路径；没有生产权限变更。未修改共享需求账本、README、master-plan或其他负责人模块。

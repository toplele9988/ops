# R5-A 来源核实与保守排除记录（未上线）

本记录补充 R3-C 的四项待办。2026-11-01 在独立快照只读核对源码及 R3-C 已有的 0700 `/tmp` 副本；SQLite 使用 `mode=ro`、`query_only=ON`，副本文件均为当前用户持有的 0600 普通单链接文件。没有打开含凭据的配置，没有运行生产入口、服务或真实模型。该批副本仍是 R3-C 09:34–09:35 UTC 的非跨库原子备份，不能当作上线时点的冻结清单。

## 四条未知请求的逐条核对

新增 `deploy/kb/provenance.py` 重新调用两套原盘点器，在内存重建完整 prompt 与原来源集合。微信再读取真实 `calls` 列顺序，重算全部十二列原行的 canonical tuple SHA，要求它与来源隔离记录的 `request_sha256`、`prompt_sha256` 完全一致；同时核验 `hold/api_timeout`、空响应、正预留，以及 scope 全表只有 `hold=api_timeout`。阿里沿用已校验审计、原 attempt 和 execution 完整行的摘要绑定，不将它伪装成微信 calls 中的第四行。

| 对应项 | 原记录 | 直接来源数 | 处理 |
| --- | --- | ---: | --- |
| 微信旧未知 A | qwen3.8-max，原十二列 SHA 与来源重建一致 | 19 | 保留原 hold、10510 预留与原审计，永久隔离 |
| 微信旧未知 B | qwen3.8-2.4t-a95b，原十二列 SHA 与来源重建一致 | 14 | 保留原 hold、9618 预留与原审计，永久隔离 |
| 微信新增未知 C | kimi-k3，原十二列 SHA 与来源重建一致 | 6 | 保留原 hold、7190 预留与原审计，永久隔离 |
| 阿里未知 A | 原超时审计、attempt、execution、prompt 与来源一致 | 16 | 保留原 infra_blocked、profile 熔断、未知预留与原审计，永久隔离 |

四条检查清单、完整请求摘要、全部来源 ID/原始来源/内容摘要、源副本 SHA、阿里单来源计划摘要只输出到新建 `/tmp/kb-r5a-provenance-*` 的 0700 目录内，`checklist.json` 为 0600。公开报告只有脱敏汇总；不得将该私有文件复制入仓库、测试或日志。工具同时运行原 `source_gate.verify`，要求恰三微信、一阿里及请求摘要唯一。上线前用新的受控副本重新生成，人工核对 `unknown_checklist` 与 `quarantine.holds` 的对应关系和原账本，而不是只对数量。

原 1457 条微信调用仍为 1454 complete、3 hold。其中674条可由完整历史 prompt 和单元精确溯源，783条仍无法证明其完整来源。账本不保存原 prompt 正文或每次动态授权的完整来源快照，当前片段也不能唯一恢复所有历史分组；不通过穷举相似窗口或今天的授权猜测放行。因此4947条覆盖待确认来源继续排除，旧783条请求及1019行未匹配片段/审计完整保留。这项待办的“四未知逐条对上”已验证；“全部旧调用完整恢复来源”仍未完成。

## 动态授权的现行判断

源码核实不是当前真实来源准入证明。本轮没有加载含密钥的 `api_config`、私有 assistant 配置或授权文件；协调器在每轮及每次请求前必须调用原公共入口重建当前范围，不得把盘点的十四个旧提取范围变成新授权。

微信模型输入仅限原 `history_backfill.kb_context()` → `kb_authorized()` 返回的 supplier/learning 资料，再通过 `_eligible()` 和完整性/未知隔离。现行代码的实际判断如下：

- `history_backfill.eligible()` 只接受 `enabled is True`、明确 `business_role` 为 supplier/learning、非空且唯一的 `memory_scope_id`，并要求 `business_history._registration()` 成功。后者要求接收专用模式、当前群绑定、非 passive 观察、当前 owner scope 权限，且显式列入 `policy.business_copilot.owner_corpora`。
- `channel_classifier.current_registration()` 经 `collection_policy.group_view()` 检查当前账号、群生命周期和激活时间；`memory_bindings` 必须唯一匹配当前 profile/scope/app/own 账号，且该 scope 的其他绑定不能指向别的群。supplier/learning 角色与原标记有冲突时不准入，群名含 SUP 不能独立授权。
- 供应商通过 `cpq_batch.context()` 复核 supplier 当前登记与 `assistant_policy.authority()` 范围交集；`cpq_windows._authorized()` 在查询中绑定 owner、scope、account、group 哈希和 `CHANNEL_ROLE_SUPPLIER`。历史文字还须 `business_history.verified_evidence()` 逐条核对原回调、历史授权收据和当前登记。
- 学习资料只取 `CHANNEL_ROLE_NEUTRAL`、明确 registered learning 的 message/historical_message，要求非成本价、`price_kind='not_price'`、非空正文。历史文字走同一原始收据核验；实时文字还逐项核验原回调账号/群/消息 ID/时间/正文、原消息类型1、无 asset、来源 SHA 与原回调相同。事实只允许原 `learning_fact_allowed()` 的规格/新品，不能转成价格或供应商能力。
- 客户询价、customer/冲突/未登记/停用/生命周期关闭/被撤销或不能复核的来源继续排除；自然新增另外要求固定边界后的来源时间和入库时间、非历史补录及安定时间。附件不因已有解析正文自动获得授权。

阿里继续使用 `analysis_runtime.authority()/boundary()` → 原 `icbu_history.load_authority()/check_authority()` 及 `source_record()`：原注册 primary owner 的授权指纹、固定私有库路径、明确 accounts/binding、来源归档状态、`history is True`、排除测试 event 前缀、角色/正文/时间、TM URL 与 message ID/CID 契约均要满足。单独 PWA 来源被现行 `source_record()` 排除；新 MV3 富消息已入站不代表本分析契约已允许它进入模型。`source_groups()` 还要求与 `history_index` 全量精确对账。原冻结版本及 profile 熔断继续生效；本轮没有通过 `apply_recovery()` 清熔断或重排窗口。

这些边界直接来自真实源码，未放宽任何授权判断。当前究竟有多少 supplier/learning/阿里来源仍获授权，必须由总指挥在受控启动窗口使用原入口只读确认；本轮不填一个推测数字。

## 全文完整性结论

R3-C 命中十二块上限的文档已可确定被截断。将原 `_document_units()` 的 `DOC_CHUNKS` 仅在内存提高后，分块实际需要 **21块**；原先12块的内容与哈希逐块保持一致，后面还缺 **9块、8452字符**。不是“刚好十二块但没有尾部”。来源说明行、空行、每行300字符切片、每块2000字符/40行等规则完全沿用原函数，没有另写近似分块器。

处置：**整份文档及其十二个已有块暂时排除自动恢复**。保留两个读者的所有持久响应和原事实；不补发尾部、不改原单元、不把原十二块当作全文成功。以后若处理尾部，需单独审核完整文档计划、已完成字符覆盖、稳定块标识和未知交叉，不能混入本次首批两单元。此前13个不命中未知的本地候选中，受该文档限制的12块仍排除，仅其余1个候选可进入后续授权复核。

## 阿里106来源的稳定单元验证

`provenance.alibaba_single_source()` 只加载 SHA 已锁定的 `analysis_core.py` 中纯函数与提示词常量，不导入 runner。每条资料单独构造 group：保留原 owner/account/CID 的 `group_key`，`source_version` 使用原 `record_version([record])`，再调用原 `plan_windows()`、`prompt_for()` 与完整字符覆盖验证；稳定 unit ID 绑定 group key、inbound ID 和原始 source SHA。计划不依赖同会话后续资料，不沿用含349条已完成来源的19个原窗口。

实际副本结果：**106/106恰为一个完整、稳定、单读者窗口；无法确认而排除0**。最长正文1189字符，最多1个segment、3个evidence spans；重复构造得到相同窗口/registry/prompt SHA。超过原8000字符窗口边界而需要多窗的来源由本适配器直接拒绝，不能截断或谎称一个单元。虚构测试也验证增加同会话后续资料不改变原单源计划、8000字符完整覆盖、8001字符拒绝，以及 owner/key不符拒绝。

这只确认拆分可行。原worker的 `validate_plan()` 绑定完整会话group与版本，不能把此单来源计划塞进旧分析版本冒充原窗。生产协调器须将新单来源计划放入独立进度库，保留原账本，并在实际执行时重新核对授权、源、原完成区间、四未知交叉、额度、profile熔断及持久响应。当前熔断未解除，106不是当前可立即请求数。

## 重复验证和交付边界

```sh
# 输入必须为按 R3-C 规程准备的私有副本；无生产路径参数。
env PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python deploy/kb/provenance.py \
  --wechat-snapshot "$SNAPSHOT_WECHAT" --alibaba-snapshot "$SNAPSHOT_ALIBABA"
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/kb_recovery" \
  /opt/imgw/venv/bin/python -m pytest tests/kb_recovery/test_provenance.py -q -p no:cacheprovider
```

新增8项虚构单测通过，0失败；sudo启动被快照的no-new-privileges限制拒绝后，按约定以当前imgw身份运行。完整恢复与相关已有回归由主交付汇总。所有计划、截断、权限拒绝和未知行变动测试仅使用虚构资料；真实副本只做盘点/哈希核对，没有处理真实资料或验证真实模型质量。

新增文件：`deploy/kb/provenance.py`、`tests/kb_recovery/test_provenance.py`、本报告。没有改共享需求账本、README、master-plan、路由/导航、演练服务或其他业务模块。工具不需安装生产；总指挥审查后合入开发副本、在新的受控副本重新核对。回滚只移除上述开发文件，私有审计清单保留；不回灌任何业务/调用/额度/进度库、不删除hold或审计。

安全核查：无HTTP权限变动、渠道发送或网络；不加载凭据，源正文只在内存比较/哈希，不输出正文、身份、价格或响应。副本路径沿用原0700/0600与软硬链接/路径穿越拒绝；SQL值不拼接，表名为内部常量；纯函数从固定SHA源码选择加载，变更即拒绝。工具生成的摘要清单不构成模型请求授权，也没有重新请求入口。

总指挥上线前仍必须核对：新鲜受控副本与全部源码SHA、四未知逐行与额度预留、真实动态授权返回范围、原profile熔断保持、106单来源完整性与全局未知别名交叉、整份21块文档和4947覆盖待确认来源排除。仍无法确认：783条旧调用完整历史来源、真实当前授权名单、上游来源时间可信度、云端未知计费/余额、真实新消息和积压持续消化质量；不得把8项虚构测试通过写成真实数据可用。

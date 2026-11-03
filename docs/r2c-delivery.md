# R2-C 交付记录（WG-05、WG-04、WG-06B）

开发候选已完成，未部署、未 commit/push。未读取真实业务库或凭据，未连接真实模型、渠道、客户或供应商，
未更改生产文件、网络或档案库版本。所有验收只使用虚构数据、假服务和临时 SQLite；不代表真实数据可用。
本工作区没有 `.git` 元数据，以下以源码 SHA-256 固定基线和候选，不能替代生产现场基线。

## 文件清单

新增后端：

- `src/console/deals/offers.py`
- `src/console/deals/demands.py`
- `src/console/deals/followups.py`
- `src/console/v3_deal_workflow_api.py`

修改后端：

- `src/console/deals/waiting.py`
- `src/console/roles/common.py`
- `src/console/roles/deal_summary.py`
- `src/console/roles/inputs.py`
- `src/console/roles/patrol.py`
- `src/console/roles/service.py`
- `src/console/inbox/ai.py`

新增界面：

- `web/src/ui/DealWorkflow.tsx`
- `web/src/ui/DealOffers.tsx`
- `web/src/ui/WaitingPanel.tsx`
- `web/src/ui/DemandCandidates.tsx`
- `web/src/ui/WorkflowError.tsx`

修改界面：`web/src/pages/DealPage.tsx`，仅新增导入和挂载两行；未修改 QuotePage、路由表或导航表。

新增测试：

- `tests/deals/test_offers.py`
- `tests/deals/test_demands.py`
- `tests/deals/test_followups.py`
- `tests/deals/test_workflow_api.py`
- `tests/deals/test_workflow_integration.py`
- `tests/roles/test_waiting_contract.py`

修改测试：`tests/deals/test_rules.py`（跨会话改为同时保留等待）、
`tests/roles/test_roles_safety_cli.py`（既有语言/管道及新路由数量、存在断言）。

修改文档：`docs/deals.md`、`docs/roles.md`；新增本记录及 `docs/r2c-files.sha256` 候选文件校验清单。
修改演练装配：`scripts/rehearsal_server.py` 仅新增两行，未运行该服务。
不计本校验清单自身，共 29 个源码/测试/文档文件；含清单共 30 个交付文件。

## 验证命令与最终结果

当前身份已是 `imgw`。请求的 sudo 被容器限制拒绝，按任务书改为当前身份执行同样的 env/python 命令。
拒绝原文：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

按套件独立执行，替换两处 `<套件>`：

```sh
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r2c-test/media.key PYTHONPATH="src:tests/<套件>" /opt/imgw/venv/bin/python -m pytest tests/<套件> -q -p no:cacheprovider
npm run build --prefix web
```

| 套件 | 主测试通过 | 子测试通过 | 最终失败 |
| --- | ---: | ---: | ---: |
| deals | 124 | 38 | 0 |
| roles | 242 | 0 | 0 |
| quotes | 34 | 189 | 0 |
| extract | 358 | 0 | 0 |
| archive | 40 | 46 | 0 |
| lang | 73 | 0 | 0 |
| inbox_api | 37 | 99 | 0 |
| 合计 | 908 | 372 | 0 |

前端 `npm run build` 通过，实际脚本为 `tsc --noEmit && vite build`，未运行 `tsc -b`。
最后构建产物为 `index-D4oLNBWK.js`；构建目录是本地验收产物，不是生产部署。
实际 ASGI HTTP 测试 3 项包含在 deals 总数中，没有监听端口；覆盖中文/西语询盘到人工确认、
两家报价与编号、人工选价、指定产品等待、母语草稿/回译、人工催问时间以及宿主 CSRF 拒绝。
重复确认、并发选择、事务故障、单边跨库提交恢复、已发快照不改写、未知母语和路径保护另有覆盖。

测试环境补缺：快照缺少已有的 `src/console/media/`。仅测试期间临时建立该路径的只读源码符号链接，
指向 `/srv/imgw-dev/ops/src/console/media`，关闭 bytecode 写入；未复制媒体实现、未读取任何 key。
`OPS_MEDIA_KEY_PATH` 在进程启动前指向临时文件。所有测试结束后移除符号链接，媒体源码不属于交付。
在完整开发副本重跑时使用其已有媒体模块；若在同样的不完整快照复跑，需要先补齐这项源码依赖。

快照也未安装前端依赖；按既有锁文件执行
`npm ci --ignore-scripts --cache /tmp/r2c-npm-cache --prefix web` 后构建成功，未修改 package.json 或 lockfile。
未运行 `scripts/e2e_smoke.sh`、固定端口演练、浏览器现场走查或真实数据测试。

开发中的中间失败已经纠正，保留关键原文供追溯：

```text
E AssertionError: Lists differ: ['supplier'] != []
E AssertionError: True != False
2 failed, 32 passed, 36 subtests passed in 8.39s
```

这是改为逐会话等待后，旧测试仍按“同单位只取最新会话”断言导致的两个子测试失败；
已更新跨会话期望，单会话方向、48 小时边界断言仍保留。该次测试发生于并行实现途中，不是干净基线。

```text
FAILED tests/roles/test_roles_safety_cli.py::test_rehearsal_auto_mounts_quotes_and_roles_on_copied_archive
E AssertionError: assert 82 == 70
1 failed, 240 passed in 29.26s
```

这是旧路由计数未含已合并的语言/管道路由；修为包含新工作流的 93 条，并加入明确路由存在断言。

```text
sh: 1: tsc: not found
npm error code EROFS
npm error rofs EROFS: read-only file system, open '/var/lib/imgw/sessions/codex/.npm/_cacache/tmp/eec6b39d'
```

前两次依赖/构建尝试分别缺依赖、默认 npm cache 不可写；之后使用临时 cache 安装依赖并构建通过。

## 需要总指挥合并的共享文件改动

已做的最少挂载：

- `web/src/pages/DealPage.tsx:14` 引入 `DealWorkflow`；`:111` 挂载，案子产品/参与方/会话更新会刷新三个面板。
- `scripts/rehearsal_server.py:53–54` 导入并追加新 builder，传 `runner=None`，只使用演练档案同目录独立库。
  原动态挂载列表和已有逻辑未变；未启动演练服务。
- web 路由表和导航表均没有改动；`QuotePage.tsx` 没有改动。

需要总指挥继续合并：

1. 在真实宿主既有认证/CSRF 中间件后的 API 装配中加入
   `routes += v3_deal_workflow_api.build(gate, archive_path, inbox=shared_inbox, lang=shared_lang, ...)`。
   这里的对象名只是装配示意，须先读宿主真实源码；本快照不包含完整生产宿主。
   显式配置三个独立库及语言库路径，Roles 的 `followups_path` 与 workflow 的 `followups_path` 必须一致，
   或统一设置 `IMGW_DEAL_FOLLOWUPS_DB`。生产 runner 按现有受控模型接入，演练继续传假 runner/None。
2. 与 R2-A 合并旧参与方区域的空状态文案：`DealPage.tsx:97` 目前仍读 `STATE_TEXT[p.state]`，
   `none` 在 `:16` 仍写“还没有关联会话”。这是基线旧文案；本任务受限只能加挂载，未改该行。
   应改为优先判断新投影的 `availability`：unlinked 才说尚未关联，unreadable 说已关联但暂不可读，
   unknown 说方向待核对，并保留已知等待。新的 WaitingPanel 已完整区分，后端/秘书/巡检口径已统一。
3. QuotePage 由 R2-B 负责；本案对照组件可把选中 offer 引用到指定现有草稿轮，本任务未在报价页另加入口。
   如需报价页重复展示此对照，由页面负责人复用 `DealOffers` 或同一 API，不重复实现选择逻辑。
4. 总指挥更新需求账本中的 WG-04/WG-05/WG-06B 状态及真实验收结果。
   `web/src/data/requirements.json`、README、master-plan 均未修改。

## 部署与回滚（本轮没有执行）

1. 总指挥先审查所有差异、上面的共享挂载和 R2-A 文案整合。核对候选清单 SHA-256；
   在生产现场记录将替换源码、宿主装配、旧前端资源的 SHA-256，不能拿下方快照基线冒充生产基线。
2. 在完整开发副本合并后重新执行隔离测试与 `npm run build`。总指挥统一跑真实数据只读走查、
   全页面 `e2e_smoke`、桌面/手机浏览器及老板确认；本开发任务没有执行这些检查。
3. 经审查批准后，暂停相关写入口并一致备份源码、前端和 archive 及三个独立库、语言库。
   SQLite 用在线备份或停写一致备份，不直接遗漏 WAL 文件；备份包含恢复标记。
4. 装入候选源码和构建资源、挂载 11 条路由，配置默认 `/var/lib/imgw/ops-app/` 下独立文件及目录权限。
   独立库首次写入以串行事务建表（目录 0700、文件 0600）；GET 不建库。不要执行 archive 版本迁移。
   核对路由、重启，再验证编号来源、候选确认、等待方向、语言锁定与草稿校验。
   所有对外内容仍由人核对后自己发送，不重放任何发送。
5. 回滚先停止新入口，恢复原源码/宿主装配/前端资源并重启；独立库可保留只读复查或成组恢复备份。
   已人工确认的 deal_items 和草稿报价引用是业务写入，不能仅为回滚代码就删除；需要恢复数据时，
   由总指挥核对回滚期间的新业务并一致恢复 archive/sidecars，避免丢失后续工作。
   不降低 archive 版本、不修改发送配置、不重试未知发送。

## 安全自查与未完成项

已验证 principal/旧会话/recheck、宿主 CSRF、案内子项/产品/单位/报价轮归属、原文与身份复查。
所有 SQL 值参数化；客户端不能指定库路径；拒绝符号链接、SQLite 旁路文件和父目录符号链接，
拒绝 sidecar 与 archive 同文件或硬链接。审计仅记操作/字段名，不写表单值、原文或模型输出。
React 按文本显示来源，不执行 HTML、不自动抓 URL。催问提示词仅携带所选产品的允许事实和人工补充，
不读取秘书摘要、任意内部备注、成本或其他产品；新增数字/型号/链接会被拒绝。
模型仍可能误解事实，数字保护和回译不证明语义正确，必须人工核对。

生成草稿、翻译、复制均无发送入口；人工记录催问与生成草稿分开。等我方处理、归属变化、
产品已移除时拒绝催问；未知母语/低把握语言拒绝调用起草。POST 无自动重试，确定性标记和事务保证幂等。
发送结果未知的重试逻辑没有新增或变更。

未完成/不能在本任务完成：R2-A 旧状态文案合并、真实宿主装配、生产基线核验、真实采集完整性、
真实询盘抽取覆盖率、真实翻译质量、真实浏览器/全页面验收、部署与回滚演练。
中/西语规则不覆盖所有自然语言写法，来源不足时仍须手填。没有自动同款识别、换汇、
不同计价单位换算、编号自动合并、附件抽取或自动发送。

## 快照改动前 SHA-256

这些是本次独立源码快照的基线；waiting/common/inputs 同时用原开发副本源码哈希复核，
其余在改动前记录。候选文件哈希见 `docs/r2c-files.sha256`。

| 文件 | SHA-256 |
| --- | --- |
| `docs/deals.md` | `94e075d09cd769c4780e2f5e2b8b3f70f393cb3785b80b6ead5ea361eedc5c9a` |
| `docs/roles.md` | `c8d74eabe12da918a80023a2aafec2a209e10bf1a5dc786025d4f1271331270b` |
| `scripts/rehearsal_server.py` | `ad71a14f2000ecdc6fdc8170863650ff18a4ae0f33bfd95b1cf82fb3068e4cb3` |
| `src/console/deals/waiting.py` | `3041e83c467ab61ba35ba00ee76953354b24bf6ab1e4dce7ce69b559c7d4762f` |
| `src/console/inbox/ai.py` | `341ac6e5eb42367bfb615cf6448b408fcf45710197abb0fa8f8ad88b8dbaafbd` |
| `src/console/roles/common.py` | `19b404834f77e058edbfeaab919bede1037900f037fc56958bb1a2d432b41e06` |
| `src/console/roles/deal_summary.py` | `1dd4b5fb9414e14812e4ad299dbf3d2817907b2fb62087dab08ac36236173830` |
| `src/console/roles/inputs.py` | `fd114f41c179d8c0815e43c850bb9171b18e713d5c704b1f99aa1a7a244f8d75` |
| `src/console/roles/patrol.py` | `24810747eb292a840e75aba1fd012314c8d5c1641725aecdd8d91a725b237776` |
| `src/console/roles/service.py` | `4c1b4d6ece17527afd495aa74a1729ff7c2e54165b675fd67a2a1a93efd55964` |
| `tests/deals/test_rules.py` | `0de0ec90b3dcd4e1b586bcef2cc55690cadccffe5a16ebc35e02274c0697cab8` |
| `tests/roles/test_roles_safety_cli.py` | `fa4fa27db32aa6a2c495f1b48fde5d71d44df977d69c7faef759d6ee9733c4d4` |
| `web/src/pages/DealPage.tsx` | `6cbaa878d19b89620448ee9d2160f0de1dd0bf28cb071a833d884906681cfa90` |

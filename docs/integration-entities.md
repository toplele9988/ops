# 阿里业务实体接收端交付

2026-11-02，当前 workspace。**候选已实现并隔离验证，未安装到生产。** 本轮仅新增指定范围内文件和本报告；历史 v2.1/v2.2 源码、历史补丁、聊天表、Web 路由、需求账本与项目状态文件均未修改。

## 只读生产基线

已完整阅读任务指定文档、两代接收端、v2.2 附件模块与历史补丁，并读取 `/opt/imgw/scripts/` 实际接收端源码、`systemctl cat/show icbu-inbound.service`。生产两份源码与仓库 `deploy/icbu/v2.2/` 当前文件逐字节一致，包含绝对请求截止时间及真实挂载检查；旧文档里的历史候选 SHA 不可作为本次基线。

| 基线 | 本次核对 SHA-256 |
| --- | --- |
| `icbu_inbound_receiver.py` | `5ce1a398a35305ff7a08cfe0d16143fb16ddb401d01bd7dc7d176507bbf4376c` |
| `icbu_attachments.py` | `8abd88efcd165abebad84e6f060fa841a4ebcd83ceacb87dee91bbe134dc82fc` |
| `systemctl cat icbu-inbound.service` 完整有效单元文本 | `795530a6b7eb618cfb5c6bea148855d2b5eb4b643fef9a1d2f447d5e1b66d7a1` |

服务只读检查为 `active`，执行入口在 `/opt/imgw/scripts/icbu_inbound_receiver.py`，服务身份为 `imgw`；现有 `ReadWritePaths=/var/lib/imgw/icbu` 已覆盖新实体库。NAS 配置是软依赖与可选写目录，保持原样。现有网络前置单元只读查看，未执行或修改。鉴权是单个 Bearer Token 的 `hmac.compare_digest`，**没有独立请求签名**；未读取令牌值。未访问生产 8791，包括健康与 GET。

工作区 `.git` 没有可用 Git 元数据，`git status` 返回 `fatal: not a git repository`，无法报告分支/提交或执行 commit/push。本次以源码 SHA 固定基线；已有 `.gitignore` 含 `private_out/`。没有读取、复制或保存真实客户聊天/订单/价格；测试与报告样例全部虚构。

## 改动文件与接口

全部为新增文件：

| 文件 | 用途 |
| --- | --- |
| `deploy/icbu/entities/icbu_inbound_receiver.py` | 基于真实生产 v2.2 的完整新候选；复用鉴权、来源检查、64 KiB 限制、并发上限、截止时间和错误风格 |
| `deploy/icbu/entities/icbu_entities.py` | 标准库实现的独立实体库、校验、事务、去重与只读读回；提供显式初始化/只读自检 CLI |
| `deploy/icbu/entities/manifest.json` | 基线、候选、单元和补丁 SHA |
| `deploy/icbu/receiver-entities.patch` | 从本次生产基线到新候选，新增实体模块；不修改附件模块 |
| `deploy/icbu/deploy_entities.py` | 校验、备份、安装、主动初始化、重启、读回、失败恢复与保留数据库的回滚 |
| `deploy/icbu/install-entities.sh`、`rollback-entities.sh` | 默认 dry-run 的入口 |
| `tests/icbu_entities/conftest.py`、`entities_fixtures.py` | 假依赖、临时目录/库、动态回环端口、虚构实体 |
| `tests/icbu_entities/test_store.py`、`test_validation.py`、`test_http.py`、`test_main.py` | 数据语义、整批回滚、并发、校验、鉴权、路径安全、限流/超时及启动测试 |
| `tests/icbu_entities/test_deployment.py`、`test_compatibility.py`、`test_contract.py` | 补丁、隔离安装/回滚、旧接收断言、Schema 与文档示例验收 |
| `docs/icbu-entities-contract.md` | Windows JSON Schema、虚构样例、字段/错误/重试规则与低频断点补采建议 |
| `INTEGRATION.md` | 本报告 |

新增接口：

- POST `/api/inbound/alibaba/entities`：`{batch_id,entities:[...]}`；1–100 条且总请求≤65536字节；整批提交首次201、原批次重放200。
- GET `/api/inbound/alibaba/entities/stats`：按类型的数量、内容版本数、最早/最新观察时间及最近20批。
- GET `/api/inbound/alibaba/entities/{entity_type}/{encoded_entity_id}`：一个当前完整快照；沿用原鉴权。ID 用 `encodeURIComponent` 编为单段。

新启动参数 `--entities-db` 默认 `/var/lib/imgw/icbu/entities.sqlite3`；拒绝与聊天/附件库使用同一路径。生产现有 ExecStart 无须改动。新库 `application_id=1229144645,user_version=1`，包含 `entity_current`、`entity_versions`、`entity_batches`；不迁移聊天或附件表。

当前快照按最新 `observed_at` 选择，旧补采不覆盖新观察。内容版本按身份及 `fields` 规范 JSON 摘要去重；仅关联/附件引用/采集器版本变化不增版本，A→B→A 复用已有 A 内容版本，不是状态跳转审计流水。相同最新观察时间却有不同内容时整批409。同一批次载荷变化也409。`fields` 保留 JSON 业务键和值，不推算事实；原 JSON 排版不保留，超高精度数字建议使用来源字符串。

本轮只保存附件引用，不上传或核验实体专属附件、不更新聊天附件登记。所有业务路由与草稿/外发队列隔离。新库初始化失败会阻止新候选启动；安装脚本会尝试恢复原代码，保留任何已产生的库，不以空库伪装成功。

## 实际测试命令与结果

独立新增套件：

```sh
env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=src:tests/icbu_v2:tests/icbu_entities \
  /opt/imgw/venv/bin/python -m pytest tests/icbu_entities -q -p no:cacheprovider
```

首轮新增64项通过；扩展旧断言夹具曾出现 `4 failed, 85 passed`：3项是新夹具对旧模块不存在的异常类做了注入，1项选用了v2.1“附件离线须抛错”的旧断言，而真实v2.2是202部分成功。修正新夹具，移除两条仅适用v2.1故障语义的候选复用，改由已有v2.2恢复场景覆盖，旧文件和旧断言未改。之后新增套件87项通过；最终又增加Schema、原始事件引用和绝对截止时间测试，**最终91项在下面组合命令中全部通过**。

干净快照直接运行原套件：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests/icbu_v2 \
  /opt/imgw/venv/bin/python -m pytest tests/icbu_v2 -q -p no:cacheprovider
```

真实结果：收集失败，`test_projection.py: ModuleNotFoundError: No module named 'console.media'`，`1 error in 0.91s`。没有修改其他模块来掩盖依赖缺失。

为了完成原回归，仅把 `/opt/imgw/wechat/console/media/` 的四份明确 Python 源码复制到0700临时覆盖层，不复制配置/密钥/数据库。临时 `sitecustomize.py` 只扩展 `console.__path__`，不替换被测业务函数。四份源码SHA与现有 `docs/icbu-v2.md` 第7节所列一致。最终真实命令：

```sh
env PYTHONDONTWRITEBYTECODE=1 \
  OPS_MEDIA_KEY_PATH=/tmp/icbu-entities-regression-z3svh4ve/media-test.key \
  PYTHONPATH=/tmp/icbu-entities-regression-z3svh4ve:src:tests/icbu_v2:tests/icbu_entities \
  /opt/imgw/venv/bin/python -m pytest tests/icbu_v2 tests/icbu_entities -q -p no:cacheprovider
```

真实结果：**442 passed, 1 skipped in 20.33s**。其中新增实体套件91项；原套件351项通过，1项跳过是原文件已标记“被审查修复替代”的历史v2.2补丁测试，不是跳过本轮补丁。组合完整输出在被忽略的 `private_out/icbu-entities-full-tests.txt`，覆盖层路径记录在 `private_out/icbu-entities-overlay-path.txt`。临时目录不保证跨会话保留；重跑须重新创建覆盖层，不能称干净快照完整回归全绿。

覆盖：全部12类和复杂JSON保存、同批重放、相同内容新批次、元数据变更不增版本、乱序/回退、统计空值、20批上限、并发、同批与同时间冲突、事务中途失败无部分提交、SQLite只读连接、权限/符号链接/硬链接/文件替换、错误脱敏、鉴权/CORS/重复头、64KiB限制、连接超载后安全重试、持续慢速输入的绝对截止时间、临时启动和聊天表不变。兼容测试直接把10项旧行为断言运行于新候选，含动态端口的 `icbu_v2_selftest.py --v21 --v22`、NAS离线部分成功及恢复。

安装测试仅在临时路径演练，systemd重启与服务身份调用均为假实现；验证备份原文、一致性SQLite副本、初始化→重启→只读检查顺序、失败自动恢复原代码、回滚拒绝覆盖后来版本，并证实聊天与新增实体数据保留。不能据此声称真实安装/回滚已跑通。

其他实际检查：

```sh
sh deploy/icbu/install-entities.sh
# {"action":"install","baseline_verified":true,"dry_run":true,
#  "full_smoke_required_before_apply":true,"ok":true}
sh -n deploy/icbu/install-entities.sh deploy/icbu/rollback-entities.sh
# exit 0
```

Python `ast.parse` 检查新源码/测试通过。已对两份只读生产源码的临时副本执行 `patch --batch --fuzz=0 -p1 --dry-run`、实际应用、与两份完整候选逐字节比较、反向dry-run及实际反向恢复，全部通过且无offset/fuzz。最终组合测试也验证最新补丁的正反应用与SHA。最后再次只读核对两份生产SHA和服务active，均保持不变。

## 生产安装步骤（本次未执行）

1. 先审阅候选、契约、补丁及测试记录；重新核对 manifest 的生产源码与完整有效单元 SHA。任一不符立即停下重新比对/生成补丁，不能强行应用。不要安装父目录v2.1或历史v2.2文件。
2. 按 `CLAUDE.md` 的上线纪律先完成 `scripts/e2e_smoke.sh` 的真实只读数据与桌面/手机全页面检查。**本轮没有运行**：已读脚本，发现它会复制到生产前端目录，超出本任务生产只读范围。应由总指挥在授权上线环境执行并确认真实结果，不伪造通过记录；本轮未运行任何TypeScript构建命令。
3. 将真实成功记录写到 workspace 的 `private_out/icbu-entities-smoke.json`，至少包含以下键；时间必须真实且在24小时内，两个SHA来自 manifest。脚本要求这个记录是为遵守上述明确上线检查要求，不是额外人工审批。

   ```json
   {
     "command": "scripts/e2e_smoke.sh",
     "exit_code": 0,
     "completed_at": "<实际完成的UTC时间>",
     "baseline_sha256": "<manifest.baseline.icbu_inbound_receiver.py>",
     "candidate_sha256": "<manifest.candidate.icbu_inbound_receiver.py>"
   }
   ```

4. 从当前仓库根先运行默认dry-run，再在具备业务服务安装权限的环境运行实际安装。下面的apply命令**本轮未执行**，当前受限workspace不提供生产写权限。

   ```sh
   sh deploy/icbu/install-entities.sh
   sudo -n sh deploy/icbu/install-entities.sh --apply \
     --backup-root "$PWD/private_out/icbu-entities-backups" \
     --smoke-evidence "$PWD/private_out/icbu-entities-smoke.json"
   ```

5. 脚本在源文件写入前建立0700唯一备份目录，备份两份生产源码、有效systemd文本、冒烟记录，以及存在的入站/附件/实体库的一致性SQLite备份；库备份超过60秒失败就停止安装。令牌不读取、不复制。备份包含私有数据，只能留在workspace的 `private_out/`，不得移入公开Git。
6. 备份后再核对基线，原子安装新实体模块与完整接收端候选，保留原接收端属主/组/权限。以 `imgw` 身份显式初始化实体库，然后重启 `icbu-inbound.service`；不修改任何unit、NAS配置或网络前置单元，无须daemon-reload。
7. 核对服务active与安装源码SHA，直接以服务身份做 `mode=ro/query_only` 的实体统计读回，stdout只输出总数。**不访问生产8791，不发虚构实体到生产，不读取或输出真实快照。** 新HTTP路由由隔离临时端口测试验证，生产只读自检不能替代Windows真实投递与生产路由联调。
8. 保存安装JSON中的 `backup` 绝对路径。初始化、重启或读回失败时脚本尝试恢复原接收端、移除本次新模块、重启原服务；保留新实体库和所有数据库增量。如果自动恢复也失败，会明确报需人工回滚，按私有备份排查，不宣称恢复成功。

## 回滚步骤（本次未对生产执行）

先在Windows暂停新的实体投递，保留本地持久待投递队列与原批次；聊天采集无需删除或重建。按安装返回的具体备份目录操作：

```sh
# backup_dir 使用安装成功响应中的 backup 绝对路径，不要凭目录时间猜。
sh deploy/icbu/rollback-entities.sh --backup "$backup_dir"
sudo -n sh deploy/icbu/rollback-entities.sh --apply --backup "$backup_dir"
```

回滚校验备份SHA、当前候选/原基线SHA、未变的附件依赖与systemd配置；发现后续版本或配置漂移会拒绝覆盖。恢复原接收端并移除本次新模块，重启服务，保留所有实体库、聊天库、附件库及其增量，绝不把旧备份数据库覆盖回去。实体库存在时再做只读计数检查；旧服务不再有业务实体端点，Windows须继续保留队列，重新安装后可原批次安全补投。没有unit变更需要恢复，也没有反向修改网络、路由或NAS的步骤。

## 未完成项与交接

- 未生产安装、未重启生产服务、未实际生产备份或回滚；只读基线核对与安装dry-run已做。
- `scripts/e2e_smoke.sh`、真实数据只读页面演练与桌面/手机全页面冒烟未做，是脚本实际apply前的必需前提。
- 未实现/运行Win10采集器；两年页面覆盖、平台稳定ID、各类实体真实字段、风控节奏、分页断点与真实投递均待Windows负责人联调。
- 未接入221 Web总览或客户/案子投影；已提供读回接口，但本轮不能声称能在现有总览看到真实订单/客户。
- 实体专属附件上传、历史列表/枚举接口、跨账号归并、多账号接收、状态跳转/元数据完整审计不在本轮实现中。
- 因文件范围限制，没有改需求账本或 `docs/project-status.md`。请总指挥同步M1/H2/WG-01相关状态为“实体接收候选与契约、隔离验证完成，未部署/真实覆盖未验收”，不能记为阿里两年业务数据已回家。
- 未提交/推送Git；当前workspace没有可用Git元数据。

没有客户/供应商外发、真实发送测试、商品发布、收款确认、网络分流修改、`tsc -b` 或生产8791自测。

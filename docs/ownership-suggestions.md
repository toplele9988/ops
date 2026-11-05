# 档案驱动的会话归属建议（OP5）

分拣员把供应商档案中的微信群联系方式转成待确认的身份建议。自动扫描不会确认身份、创建单位或案子，也不会调用模型或发送消息。只有人在「待整理的会话」点击采用后，现有接受接口才会执行绑定；没有单位时由该接口先创建单位。

## 档案规则

档案路径沿用 `console.dossiers.store.DB_PATH`，即 `${OPS_DOSSIER_ROOT}/dossiers.sqlite3`；未配置时为 `/var/lib/imgw/ops-app/dossiers/dossiers.sqlite3`。使用该模块的 `readonly()`，不建库、不迁移、不复制档案。库不存在、必要供应商表不可用、快照不可读时，档案链接为空，普通分拣规则仍可运行。缺少可选的 `sd_legacy_map` 时仍可按精确名称匹配。

只读取 `sd_supplier.contacts_json` 中 `channel=wechat_group` 且群号为数字加 `@chatroom` 的记录。电话、邮箱、普通联系人不参与。会话键使用 `make_key('conversation', 'wechat', '微信', group_id)`。同一供应商重复记录同一群会合并来源；同一群对应多个供应商则标记冲突。

| 条件 | 建议 |
| --- | --- |
| 一个档案供应商、唯一 active 往来单位 | `identity`，置信度 0.95，规则确定，跳过模型 |
| 一个档案供应商、没有 active 往来单位 | `identity`，置信度 0.9，带档案名称和单位类型，等待人采用后建单位 |
| 一个群对应多家档案供应商，或匹配多个 active 往来单位 | 回退原规则，说明「档案冲突」，不作为确定的档案匹配 |
| 档案群已有 identity，或会话已关联案子 | 不再生成建议 |

往来单位先按 `sd_legacy_map` 中指向该 supplier 的 `parties` 映射匹配，只接受 archive 标识（`archive`、`archive.sqlite3` 或当前 archive 的完整路径），排除 `conflict/ambiguous/rejected/unmapped/ignored/pending` 状态。没有有效的 active 映射候选时，再比较单位 `name/short_name/aliases_json` 与档案 `name/aliases_json`；只忽略首尾空白和大小写，不作模糊匹配。一个有效映射优先于名称候选，多个映射候选保留冲突。已有单位沿用其类型；新单位按 `产品→supplier`、`物流→forwarder`、`配套→vendor`。

确定的档案建议原因以 `档案：` 开头，例如「档案：该群是供应商『虚构星光厂』的工作群（来源 SRC-FICTION-1）」。来源和供应商标识也保存在岗位运行证据中。冲突说明不会被人工扫描的模型结果或同名买家合并覆盖。

档案群最近一条消息在 180 天内即可进入扫描，允许最后是我方消息或未知方向；仍排除 `learning/internal/ignored` 和未来时间。其他会话保留原有最近 30 天、最后一条已知消息来自对方等资格规则。

## 自动频率和人工采用

宿主沿用现有 Worker 的 45 秒轮询和文件租约，增加可选 `triage` 组件，经 `_run('triage', ...)` 记录健康。该组件调用 `scan_triage('分拣员(自动)', apply=True, use_ai=False)`，宿主还将其 runner 设为空。人工「让分拣员扫描一遍」继续使用原有流程；不确定的普通建议仍按现有配置处理。

自动扫描开始前，使用 operator 时钟把准入时间持久化到 operator 库现有 `operator_cursors` 的 `triage` 行，间隔至少 600 秒。空扫描和失败扫描也占用该间隔，进程重启不会马上重跑；时钟回拨时继续等待。调度元数据与 `operator_health` 写在 operator 库，建议通过既有 `save_run/save_suggestion` 写入 archive 的 `role_runs/triage_suggestions`（含原有审计）。没有新增数据库迁移。默认仍有每批 100 个会话的既有扫描上限。

页面显示「档案匹配 N 条」；计数和「全部采用档案匹配」只包含 `pending`、`identity`、`confidence>=0.9`、原因以 `档案：` 开头的建议。点击后确认框列出全部「群名 → 单位名」，确认后逐条调用现有 `POST /api/v1/triage/{id}/accept`，沿用表单和 CSRF。每条显示等待、正在采用、成功或失败原因；成功项移出待处理列表，失败项保留，可单独处理或再次采用。不增加后端写接口。

## 生产替换文件

代码根通常为 `/opt/imgw/wechat`；以实际服务安装根目录为准。需发布以下文件：

| 仓库文件 | 生产相对路径 | 操作 |
| --- | --- | --- |
| `src/console/roles/dossier_links.py` | `console/roles/dossier_links.py` | 新增 |
| `src/console/roles/automatic.py` | `console/roles/automatic.py` | 新增 |
| `src/console/roles/triage.py` | `console/roles/triage.py` | 替换 |
| `src/console/roles/service.py` | `console/roles/service.py` | 替换 |
| `src/console/operator/host.py` | `console/operator/host.py` | 替换 |
| `src/console/operator/worker.py` | `console/operator/worker.py` | 替换 |
| `web/dist/` 完整构建产物 | `/var/lib/imgw/ops-app/dist/`，或实际静态目录 | 整目录发布 |

`console/roles/*.py` 应保持同一版本；本次其余角色文件不变。前端源文件为 `web/src/pages/Triage.tsx`，用 `cd web && npm ci && npm run build` 构建。合并总指挥对其他页面的修改后，统一重建和发布整个 dist，避免交叉覆盖。无需替换 `v3.py/app.py/operator/engine.py`，也无需修改路由或网络配置。

## 备份、部署和回滚

1. 在生产主机的私有备份目录记录代码版本、上述文件的存在状态和 SHA-256；备份旧文件及完整静态目录。记录两个新增文件原先是否存在，避免回滚时删除其他版本已有文件。备份保留在主机，不能提交仓库。
2. 在维护窗口停止所有使用该 archive 的运营台进程和 Worker。用 SQLite `.backup` 或 Python `sqlite3.Connection.backup()` 备份 `/var/lib/imgw/ops-app/archive.sqlite3` 和 `/var/lib/imgw/ops-app/operator/operator.sqlite3`，正确覆盖 WAL 中已提交的数据。不要仅复制正在使用的主库文件。档案库只读，本次不变更其内容。
3. 安装表中 Python 文件，保持原服务用户、权限及 `OPS_DOSSIER_ROOT`；把新 dist 先放到同文件系统的临时目录，再在停机窗口替换静态目录。按现有进程管理方式启动原运营台服务，使所有进程加载新代码。不要另外启动一个独立分拣进程。
4. 检查 `triage` 健康为正常，等待下一次 10 分钟准入；在 `/triage` 核对群名、单位名和档案来源。档案不存在时应正常返回空档案匹配；普通会话仍可能有原规则建议。未点采用时，`parties/identities/deals` 不应因本组件增加。
5. 代码回滚时先停服务，恢复备份的 Python 文件和旧 dist；本次新增且备份时不存在的两个角色模块可删除，然后启动服务。正常代码回滚保留数据库和已由人确认的身份，也保留 operator 的调度时间，不删除其他组件游标。无新增迁移需要降级。
6. 如确需数据库灾难恢复，应另行核对备份之后所有人工写入，再在完全停机状态恢复一致备份；不能为撤回几条建议直接覆盖整个业务库。接受后的身份属于正式业务记录，不能随代码回滚自动删除。

本交付只提供代码、构建产物和隔离验证；未执行生产发布或真实档案扫描。验证命令、环境基线限制与结果见仓库根目录 `INTEGRATION.md`。

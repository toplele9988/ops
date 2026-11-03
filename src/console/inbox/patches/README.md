# inbox / W2 候选补丁（未部署）

只在本任务 workspace 开发与演练。未读取、写入生产目录，未核验生产源码 SHA。
`SOURCE_BASELINE.sha256` 是任务开始时 `/srv/imgw-dev/ops` 对应源码的 SHA256，与工作副本原件相同；它不是对生产版本的声明。`CANDIDATE.sha256` 列出补丁应用后的源码。

## 挂载

沿用 `patches/overview` 的统一 diff 方式。补丁路径以 `console/` 为根，须在具有该子目录的**候选源码副本**中使用 `patch -p1`。包含两个已有文件的增量修改和三个新增文件，不是整文件覆盖。

已有控制台挂载 `v3_inbox_api.build(app_module)` 时，新增三个 W2 路由自动随返回列表注册，不额外修改 `v3.py`，也不重挂已有发送路由。本候选页面只调用 `/ops/action` 的演练发送；原 `/send` 路由归既有模块负责。

总指挥后续部署步骤：

1. 在候选副本校验 `sha256sum -c SOURCE_BASELINE.sha256`；三个新增文件必须尚不存在。生产基线不同则停止，基于授权的实际源码重新生成补丁，不能强行应用。
2. 备份将涉及的两个源码文件、原静态资源版本，以及 archive / outbox / drafts 三个 SQLite 库。备份要一致，包含 WAL 提交；使用 SQLite backup 或停写后备份，不能只复制活跃库主文件。
3. 对候选副本运行 `patch --dry-run --batch -p1 -i production-mount.patch`，通过后应用补丁；校验 `CANDIDATE.sha256`。用真实环境同版本依赖在隔离目录复测。
4. 核对宿主登录、密码复核和 CSRF 中间件仍覆盖所有新增 POST；核对路由唯一注册。`archive_path` 应与客户身份和案子服务使用同一库，`outbox_path` 应与现有发送记录使用同一库。
5. 主动触发增量建表（下面只是模板，部署人员传入已经备份且获准的库路径；本任务未执行生产迁移）：

   ```sh
   PYTHONPATH=<候选源码目录> <运行环境Python> -m console.inbox.migrate \
     --archive <已备份的档案库> --outbox <已备份的发送库> --drafts <已备份的AI草稿库>
   ```

   新增 `inbox_workflow`、`inbox_action_receipts`、`inbox_dry_run_drafts`；草稿表新增可空 `request_id` 和唯一索引。重复迁移保留既有草稿。GET 不触发迁移。三个库分别提交，不支持跨库原子迁移，故需逐库核验；失败后可重复执行。
6. 再构建已整合的前端，由总指挥按既有发布方式安装静态资源并重启、核对路由和演练草稿结果。本任务不运行会写生产目录的历史 `scripts/e2e_smoke.sh`。

`inbox_dry_run_drafts.dry_run` 被 CHECK 固定为 1，状态只有 `draft/queued`。它与真实 `outbox` 发送表分开，现有发送器不能读取该表；本候选没有外发代码路径，也不改变任何渠道的发送开关。

## 回滚

切回上一版静态资源；停止相关进程后，在匹配候选 SHA 的源码副本中先执行 `patch --reverse --dry-run --batch -p1 -i production-mount.patch`，通过后反向应用，再按既有发布流程切回并重启。也可恢复一致备份的两个旧源码文件并移除三个新增文件。

新增表和可空字段对旧版无影响，默认保留以免丢失本次跟进记录。若必须回滚数据，先导出新增记录，再由总指挥在停写状态下恢复三个库的一致备份；恢复会撤销备份之后的客户归属、建案及其他业务写入，不应自动执行。

## 已验证范围

`tests/inbox/test_patch.py` 在临时副本验证补丁反向、基线 SHA、正向及逐文件一致。SQLite、HTTP 和浏览器验收结果见工作区根 `INTEGRATION.md`。当前公开源码缺少 `console.media`：隔离测试在渠道组合边界注入虚构会话，不覆盖真实采集和附件归档服务。不得将此补丁视为已部署或真实渠道已验收。

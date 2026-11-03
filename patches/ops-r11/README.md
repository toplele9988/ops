# r11 合并候选（未安装生产）

唯一发布补丁是 `production.patch`。四个子任务原有补丁留作历史记录，不能叠加安装。

- 生产基线：只读 `/opt/imgw/wechat/console/`，14 个已有文件 SHA；13 个新增文件必须不存在。
- 候选：`candidate.sha256` 共 27 个文件；父路由候选完整源码 `v3.candidate.py`。
- `rehearsal.patch` 对准本次工作区的原 `scripts/rehearsal_server.py`；两个 rehearsal SHA 文件和 `rehearsal.baseline.py` 保留可重复验证的基线。
- `verify.py` 只复制生产源码到 `private_out/r11/production-stage`；先正向 dry-run/应用/验 SHA，再反向 dry-run/还原/验 SHA，最后保留暂存候选。再次运行需先把已有暂存目录移到另一个私有路径。
- `install.sh SOURCE_ROOT PRIVATE_BACKUP ARCHIVE_DB OUTBOX_DB DRAFTS_DB PYTHON`：SHA 与新增路径检查、严格 dry-run、源码/SQLite 备份、套用、候选校验、显式迁移。备份路径须尚不存在。root 安装时迁移交给已核对的业务账号 `imgw`；其他身份直接运行迁移。
- `rollback.sh SOURCE_ROOT`：候选校验、反向 dry-run/还原、基线校验；保留业务库与发布后业务记录。
- `check_routes.py SOURCE_ROOT --output PRIVATE_JSON`：禁网络和生产 SQLite，实际构建生产 v3 路由并检查重复。
- `rehearse.sh` 调用原 `scripts/e2e_smoke.sh`，拦截其中生产静态目录复制和广泛 pkill，静态资源直接来自本工作区。只停止本次记录的演练子进程。端口默认 18913，拒绝 8791。真实结果强制进入 `private_out/`。

显式迁移命令：

```sh
PYTHONPATH=/opt/imgw/wechat PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m console.ops_queue.migrate \
  --archive /var/lib/imgw/ops-app/archive.sqlite3 \
  --outbox /var/lib/imgw/ops-app/outbox.sqlite3 \
  --drafts /var/lib/imgw/ops-app/drafts.sqlite3
```

完整安装顺序、服务重启、前端备份与回滚、测试和限制见根目录 [INTEGRATION.md](../../INTEGRATION.md)。本任务未在生产运行以上迁移或安装命令。

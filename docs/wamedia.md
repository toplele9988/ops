# WhatsApp 媒体收取（开发候选）

R5-B 开发候选，尚未部署。后台进程只读 bridge 消息库，优先读取 bridge 已归档的媒体字节，不调用 WAHA 的“按消息取媒体”接口。没有 archived 记录时，才在队列到达处理时间后使用原有 WAHA 后备。后台不发送消息，不修改 bridge、bridge 库、bridge 媒体目录或档案库。

已只读核对实际表结构：`messages.message_key` 关联 `media_jobs.message_key`；按 `messages.session`、`message_id` 和 `has_media=1` 查找 `status='archived'` 的最新记录。元数据只取 `metadata_json` 的 `bytes`、`mimetype`、`sha256`，不读取媒体旁的 JSON 文件。bridge 库通过 `mode=ro` 和 `query_only=ON` 打开。文件名只能是 64 位小写十六进制 SHA-256；逐级打开目录并禁止符号链接，拒绝硬链接、目录、FIFO 和上级路径，读取后核对长度和完整 SHA-256。归档记录存在但文件缺失、元数据不合格或校验失败时拒绝入库，显示具体原因，不转而调用 WAHA。

重复事件仍按 `(session,message_id)` 登记一次，文件按 SHA-256 保存；已有 WAHA 后备所得的 ready 记录也会以有效归档字节更新。链接预览只使用消息已有字段，不抓取链接或远程缩略图。语音作为附件下载，提示用播放器打开；不保证浏览器支持其编码。

## 存储和受控下载

`IMGW_WA_MEDIA_DB` 默认 `/var/lib/imgw/ops-app/whatsapp-media.sqlite3`，`IMGW_WA_MEDIA_ROOT` 默认 `/var/lib/imgw/whatsapp-media/`，`IMGW_WA_MEDIA_QUEUE` 默认 `/var/lib/imgw/ops-app/whatsapp-media-queue.sqlite3`，`IMGW_WA_BRIDGE_MEDIA` 默认 `/var/lib/imgw/whatsapp/media`（可用 `--bridge-media` 覆盖）。数据库与文件均为私有权限，媒体目录权限须为 0700。内容按不透明字节保存，不解析或执行。最多 50 MiB，MIME 使用原白名单；图片、视频、语音须与登记类型一致。

展示仍走既有 `/api/v1/inbox/conversations/{key}` 和 `/api/v1/media/{token}`，没有增加路由或导航。媒体路由保留登录、读取后的权限复核、签名校验、字节特征检测、nosniff 和单段 Range；图片显示预览，文件强制下载。视频先显示占位和大小，点击播放后由浏览器直接读取原文件并显示时长；没有视频缩略图、服务端转码或时长解析。不存在时长元数据时不猜测时长。

WAHA 后备仍仅向配置的本机 HTTP 地址发送 GET，禁代理和重定向，请求有限时和大小上限。只接受同源 `/api/files/` 下的单个安全文件名，不按消息中的任意 URL 请求。只有真正需要后备时才读取渠道配置；有归档字节的 `--once` 不需要 WAHA 配置。未连接或验证现场 WAHA 接口。

独立媒体库新增 `media_errors(id,error_code)` 附表，只存固定错误码，展示文案来自代码中的中文白名单。原 `media` 表维持 11 列，兼容旧版位置插入，便于保留数据回滚。独立队列 `jobs` 加可空 `archive_updated`，由事务自动补齐；原库可直接升级，不升级 archive.sqlite3。队列用写事务和 120 秒租约领取任务，租约有效期内并发领取同一任务只会处理一次；写入、状态与错误记录均通过 SQLite 事务串行化。

## NAS 与本机缓冲

设置 `IMGW_WA_MEDIA_ROOT=/mnt/nas229/edge/imgw-archive/whatsapp/`。对原有自动识别的 `/mnt` 路径，从媒体根目录向上找最近的真实挂载点（`os.path.ismount`），因此内层 CIFS 会优先于外层 autofs/NFS；不再固定推断成 `/mnt/nas229`。选定后进程不会因挂载消失而退回外层或本机目录。`/mnt` 下完全找不到真实挂载时仍启用失败关闭，不视为普通本机存储。其他位置的可移除存储须明确设置 `IMGW_WA_MEDIA_MOUNT`；普通本机路径即使位于独立分区或 tmpfs，也保持原有本机规则。

安装候选明确设置 `IMGW_WA_MEDIA_MOUNT=/mnt/nas229/edge/imgw-archive`，保证启动时 NAS 缺席也检查指定的内层挂载。根目录必须已经存在、权限为 0700，并含普通文件 `.imgw-archive-id`。每次访问检查挂载仍存在、根目录和挂载的 st_dev 一致、目录身份未变及哨兵有效；失败时不在失效挂载路径 mkdir 或写附件。

缓冲默认 `/var/lib/imgw/ops-app/whatsapp-media-buffer`，可用 `IMGW_WA_MEDIA_BUFFER` 改为本机安全目录，不可放在挂载点或 `/mnt` 下。沿用“先写明确配置的缓冲，再尝试 NAS”；NAS 不可用时不产生可预览的 ready 记录，也不把 NAS 路径当本机盘。保留缓冲不等于回落到本机目录。恢复后按队列时间重试；没有归档记录时可复用已验证缓冲，有归档记录时仍先核对 bridge 字节。缓冲目前不会自动清理，容量和保留策略须由总指挥安排。

## 运行和重试

总指挥审查并安装源码后，以受限的 imgw 账号运行长期进程：

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m console.wamedia.ingest --bridge-db /var/lib/imgw/whatsapp/messages.sqlite3
```

游标和队列持久保存。每轮最多四次，间隔至少 60 秒，等待期间显示“附件待重试”，耗尽显示“附件暂不可取”，并附具体校验/渠道原因。失败不会阻止其他任务；新 archived 记录到达或其 updated 更新时，自动为对应旧失败记录开启一轮，绕过原有等待时间，不必手工清空队列。同一未变化的无效归档不会无限复活；修好原文件但 bridge 未更新元数据时间时，可人工重新排队：

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m console.wamedia.ingest --bridge-db /var/lib/imgw/whatsapp/messages.sqlite3 --retry-failed
```

`--retry-failed` 只重排并退出，长期进程继续处理。`--once` 处理当前已到时间的队列后退出，不等待未来重试或其他进程的租约，不重置四次上限：

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m console.wamedia.ingest --bridge-db /var/lib/imgw/whatsapp/messages.sqlite3 --once
```

`--once` 的退出 0 只表示本轮已处理，失败条目和未来重试仍保留，不代表所有附件 ready；源库或队列不可读取时退出 1 并显示中文检查步骤。收件箱已有历史缓存 15 秒、概览缓存 30 秒，刷新后可能稍后才看到状态变化。未新增或调用任何发送路径。

## 部署与回滚

本轮不修改生产 bridge；只读核对的 `/opt/imgw/whatsapp/bridge.py` SHA-256 仍为 `0f23743b95e5feea6277504c8caa8aeddae816a25a7c78f600a2c2b151016a69`。原有视频发送补丁仍独立，不包含在本轮补丁中。

已只读核对以下生产源码与快照基线一致；部署前必须再次检查。校验清单为 `deploy/whatsapp/wamedia-baseline.sha256`，补丁为 `deploy/whatsapp/wamedia-r5b.patch`，路径相对 `/opt/imgw/wechat`，只修改下列三份源码并新增 `console/wamedia/bridge.py`：

| 生产源码 | 修改前 SHA-256 |
| --- | --- |
| `/opt/imgw/wechat/console/wamedia/ingest.py` | `5eadb84fb9f31ccabedcaf90165732227afcc8519aec41b2f12e35bc29d23d3e` |
| `/opt/imgw/wechat/console/wamedia/store.py` | `379a326ce88f37781474776e1bac2e979ed4995a9d4eb0c1f91d345012a8aada` |
| `/opt/imgw/wechat/console/channels/whatsapp.py` | `eb23fdc3183d19e72dc4f34225ae850a976769174056770b7e9e5638ab8b84bf` |

前端 `web/src/ui/Blocks.tsx` 的快照修改前 SHA-256 为 `34ab0a934a4b155332c530d091b8b94a3d0ec1fdbe732ea0aaaca842f077db64`，须串行合并后重新构建，不把该 TSX 直接复制到生产静态目录。

以下均为总指挥审阅后、老板执行的部署步骤，本轮没有执行：

1. 核对上述基线、新增目标文件是否已存在、完整 `console/media` 依赖、imgw 读 bridge 库/文件和写 ops-app 的权限。停止已有附件采集进程及相关控制台写入；在私有备份目录备份三份源码、前端产物、原服务/drop-in，以及独立媒体库和队列的一致快照，记录权限和 SHA-256。备份不进仓库，不删除现有记录。
2. 对 `/opt/imgw/wechat` 执行清单校验与补丁 dry-run。基线相符且没有冲突，再由老板应用补丁；仅在已挂载 NAS 上准备 0700 媒体目录和普通哨兵。确认 imgw 可写 `/var/lib/imgw/ops-app`，不在失效挂载点创建目录。前端由总指挥合并本轮 Blocks 改动后 `npm run build`，按现有流程安装产物。
3. 以普通身份运行 `bash deploy/whatsapp/wamedia_service.sh /tmp/wamedia-systemd-r5b /opt/imgw/wechat` 生成候选。脚本只写指定输出目录，已有候选不覆盖；不安装、不 reload、不启动。审查生成的 `imgw-wamedia.service` 和 `70-wamedia.conf`，按其打印的 sudo install 指令安装，已有同名配置先备份，不直接覆盖其他人的改动。
4. 单元以 imgw 运行，`ProtectSystem=strict`；仅 `/var/lib/imgw/ops-app` 与 `-/mnt/nas229/edge/imgw-archive/whatsapp` 可写。bridge 目录只读。NAS 用 After/Wants 软依赖，无 RequiresMountsFor、BindsTo 或 NAS 哨兵 ExecStartPre。两个既有控制台服务的 drop-in 只补媒体环境及 ops-app 写目录，确保与采集进程读同一媒体库和根目录；如实际单元名称不同，先调整安装目标。
5. 保持长期进程停止，用下列命令触发独立库的兼容初始化、处理当前队列；核对收件箱 ready/原因和媒体返回，不把退出 0 当成功率。不要打印配置文件或任何凭据。NAS 离线/恢复、CIFS 权限、systemd 命名空间及容量须现场验证。

```sh
sudo -n -u imgw env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/imgw/wechat \
  IMGW_WA_MEDIA_ROOT=/mnt/nas229/edge/imgw-archive/whatsapp \
  IMGW_WA_MEDIA_MOUNT=/mnt/nas229/edge/imgw-archive \
  /opt/imgw/venv/bin/python -m console.wamedia.ingest \
  --bridge-db /var/lib/imgw/whatsapp/messages.sqlite3 \
  --bridge-media /var/lib/imgw/whatsapp/media --once
```

6. 总指挥统一运行真实数据只读演练、路由核对、规定的全页面冒烟；通过后由老板启动/启用 `imgw-wamedia.service` 并按原流程重启控制台。不要为验证向任何真实对象发送消息。该候选不修改 `scripts/rehearsal_server.py`。

回滚时停止新采集进程及控制台写入，恢复备份的三份源码、前端产物和原服务/drop-in，reload 后按原流程重启；核对恢复文件 SHA-256。新增 bridge 读取模块可随源码回滚移除。保留媒体库、队列、缓冲和 NAS 字节，**不用旧数据库快照覆盖新增记录**：media 表列数未变，新增附表和 jobs 可空列不影响旧版 SQL。没有 archive 版本变更，不操作 outbox 或 bridge 库，不回放发送。

## 验证与边界

所有自动测试只用虚构字节、临时 SQLite/目录、假 WAHA、进程内 ASGI 请求；没有连接真实渠道，没有运行 e2e_smoke 或固定端口服务。覆盖长度/哈希不符、缺文件、非白名单 MIME、符号链接/硬链接/FIFO/目录/路径穿越拒绝、WAHA 500 下从归档成功、跨会话与 SQL 注入拒绝、重复运行、旧失败/ready 记录恢复、重试上限、并发租约、旧库/回滚 SQL、NAS 嵌套/消失/st_dev 差异及 --once 子进程。

本快照没有 `.git`，没有提交或推送。`sudo -n -u imgw true` 的原始失败如下，故测试直接以当前 imgw 身份运行：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

未补依赖的原命令 `env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/wamedia" OPS_MEDIA_KEY_PATH=/tmp/r5b-baseline-media.key /opt/imgw/venv/bin/python -m pytest tests/wamedia -q -p no:cacheprovider` 有以下原始收集失败，属于已知 WG-13，不算业务测试通过：

```text
ERROR collecting tests/wamedia/test_link_preview.py
E   ModuleNotFoundError: No module named 'console.media'
ERROR collecting tests/wamedia/test_projection.py
E   ModuleNotFoundError: No module named 'console.media'
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
2 errors in 0.25s
```

为验证真实签名/媒体路由，只把 `/srv/imgw-dev/ops/src/console/media/{__init__,tokens,files,alibaba}.py` 源码复制到 `/tmp/r5b-support/console/media`；`/tmp/r5b-support/sitecustomize.py` 只执行 `import console; console.__path__.append('/tmp/r5b-support/console')`。没有复制配置、密钥、令牌文件或业务数据，没有向仓库补入其他任务的模块。四份临时依赖的 SHA-256 依次为：`ced5f3a0f932bbaaa6140ae8ceb081161800f896b998260729deb97fef5880f8`、`25440e4ce6ad69f2e952aaa2c1d55e2d0070807f9731f898faba828ee58ed184`、`7c6a84e3819dc6a50e3e11c8e9f92ba6e5f941c656ba6eda9b6e808b5a21917b`、`282823a19e141425fa2f352eccc9dff2ab019721f67d06a48cc13e2811f76c93`。

最终按套件隔离执行的命令如下。`/tmp/r5b-support` 只补上述源码依赖，媒体签名使用临时文件，命令均以当前 imgw 身份执行：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/wamedia_bridge:/tmp/r5b-support" OPS_MEDIA_KEY_PATH=/tmp/r5b-support/media.key /opt/imgw/venv/bin/python -m pytest tests/wamedia_bridge tests/wamedia -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/channels:/tmp/r5b-support" OPS_MEDIA_KEY_PATH=/tmp/r5b-support/media.key /opt/imgw/venv/bin/python -m pytest tests/channels -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/inbox_api:/tmp/r5b-support" OPS_MEDIA_KEY_PATH=/tmp/r5b-support/media.key /opt/imgw/venv/bin/python -m pytest tests/inbox_api -q -p no:cacheprovider
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/outbox:/tmp/r5b-support" OPS_MEDIA_KEY_PATH=/tmp/r5b-support/media.key /opt/imgw/venv/bin/python -m pytest tests/outbox -q -p no:cacheprovider
```

| 范围 | 最终结果 |
| --- | --- |
| wamedia_bridge + wamedia | 72 passed，0 failed |
| channels | 193 passed，14 subtests passed，0 failed |
| inbox_api | 37 passed，99 subtests passed，0 failed |
| outbox | 50 passed，53 subtests passed，0 failed |
| React 渲染检查 | 3 项通过，0 项失败 |
| `npm run build` | 通过，`tsc --noEmit && vite build` 退出 0 |
| 源码补丁 | 临时副本 dry-run、应用后四份源码逐字节比对、反向 dry-run/回滚及三份原始 SHA-256 复核全部通过 |

其中一次把 `tests/inbox_api tests/outbox` 合在同一 pytest 进程执行时遇到旧测试同名模块的收集冲突，未改其他套件；改回上面的独立命令后通过。原始错误如下：

```text
__________________ ERROR collecting tests/outbox/test_api.py ___________________
import file mismatch:
imported module 'test_api' has this __file__ attribute:
  /var/lib/imgw/sessions/codex/dev-jobs/20261101T135509Z-8ab861f6653a47d6/workspace/tests/inbox_api/test_api.py
which is not the same as the test file we want to collect:
  /var/lib/imgw/sessions/codex/dev-jobs/20261101T135509Z-8ab861f6653a47d6/workspace/tests/outbox/test_api.py
HINT: remove __pycache__ / .pyc files and/or use a unique basename for your test file modules
=========================== short test summary info ============================
ERROR tests/outbox/test_api.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.34s
```

前端在 `/tmp/r5b-web` 复制源码和已存在的 node_modules 后执行 `npm run build`，不联网安装依赖、不运行 tsc -b、不写共享依赖或仓库构建产物；另用 `node tests/wamedia_bridge/check_video.cjs /tmp/r5b-web` 检查真实 React 的占位、图片和下载渲染。补丁检查仅在 `/tmp/r5b-patchcheck` 进行，没有对生产运行 patch，包括 dry-run。工作区检查没有 SQLite、媒体字节、密钥、pyc、tsbuildinfo、rej 或 orig 残留。

本轮修改六份文件：`src/console/wamedia/{store,ingest}.py`、`src/console/channels/whatsapp.py`、`web/src/ui/Blocks.tsx`、`tests/wamedia/test_projection.py`、`docs/wamedia.md`。新增九份文件：`src/console/wamedia/bridge.py`、`deploy/whatsapp/{wamedia_service.sh,wamedia-baseline.sha256,wamedia-r5b.patch}`、`tests/wamedia/test_mounts.py`、`tests/wamedia_bridge/{test_archived.py,test_archived_preview.py,test_service.py,check_video.cjs}`。

**需要总指挥合并的共享文件改动：** 保持 `web/src/data/requirements.json` 的 H4 为 partial；建议更新 now/next，说明 R5-B 已补归档字节主路径、失败恢复、挂载关闭及 --once，只有虚构自动验证，等待统一部署/真实验收，发送候选状态不变。WG-13 须由快照负责人补齐上述媒体公共依赖。`web/src/ui/Blocks.tsx` 是本轮已做的最小视频显示改动，须由总指挥与其他前端改动串行合并。README、master-plan、路由、导航、rehearsal 挂载逻辑均零改动。

安全自查：没有新增权限入口；预览/下载继续走原认证、复核和签名。只按验证后的 digest 构造文件名，逐级禁止链接，拒绝穿越与非普通文件；SQLite 的消息标识和路径值均参数化，动态 SQL 片段只来自代码常量。界面和日志只输出固定中文原因，不透传异常、源路径、内容或凭据。归档校验失败不走后备；下载始终 GET，不涉及发送或未知结果重发。NAS 失效只保留明确配置的缓冲，不写失效挂载目录。仍未做生产安装、真实字节抽验、真实浏览器播放或 NAS/systemd 故障演练，虚构通过不代表真实数据可用。

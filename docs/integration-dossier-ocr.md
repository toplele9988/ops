# 档案增量本地 OCR 交付（2026-11-05）

已完成本地图片补文、哈希缓存、OCR 候选证据与原图展示。真实 CLI 演练按指定参数、以 imgw 执行，但指定报告目录返回 EROFS；该部分需在宿主补跑。没有将真实报告改写到其他目录。

## 改动文件

- `src/console/dossiers/incremental/ocr.py`（新增）：逐级 O_NOFOLLOW、普通文件/20 MiB/哈希/读取一致性校验，Pillow 验图与 25,000,000 像素限制，内存识别，0.5 阈值、bbox 排序、固定错误码、缓存及每轮数量/耗时统计。
- `src/console/dossiers/incremental/schema.py`：幂等新增 sd_ocr；除要求的结果字段外，增加 error_code 用于保存固定失败码。
- `src/console/dossiers/incremental/sources.py`：发现归档状态和来源表，在关闭微信快照后补入 OCR 文字，生成稳定的独立来源版本，标记 method=ocr。
- `src/console/dossiers/incremental/worker.py`、`__main__.py`：默认开启 OCR，增加 --ocr/--no-ocr、--max-ocr（默认 200）；dry-run 的新识别结果只驻内存，不 INSERT sd_ocr；报告/数字摘要区分 ocr、no_text 和 OCR 用量。
- `src/console/dossiers/incremental/project.py`：逐字段按实际引用来源标记 method=ocr、evidence_state=ocr_candidate；报价、历史中位数异常与两类人工队列规则保持原流程。
- `src/console/dossiers/incremental/rollback.py`：完整回滚包含 OCR 缓存表，兼容尚无该表的旧侧库。
- `src/console/dossiers/service.py`：证据保留 method/evidence_state，来源响应也返回 OCR 方法与候选状态。
- `src/console/dossiers/sources.py`：BMP 纳入图片缩略候选，并处理附件 format 为 NULL；仍复用原有签名媒体接口。
- `web/src/dossiers.ts`、`web/src/pages/Dossiers.tsx`：报价、逐字段来源和来源弹层显示“图片识别，待核对”，保留原图与会话入口。
- `tests/dossiers/test_incremental.py`：新增 27 项虚构图片/假引擎用例，包括四种格式、安全边界、缓存/限额、dry-run、CLI 开关、混合来源逐字段标记、异常规则及原图签名回读。
- `web/e2e/fixtures.dossiers.mjs`、`web/e2e/dossiers.mjs`、`web/e2e/dossiers-private.mjs`：OCR/普通来源对照、标记与原图加载断言。
- `docs/dossier-incremental.md`：OCR 引擎、阈值、速度实测、限制和缓存语义；同步现有网关 auto/answer 契约说明。
- `INTEGRATION.md`：本记录。此前 M2 的历史记录仍在 `docs/integration-dossier-incremental.md`，其中旧网关限制不代表当前状态。

没有修改 ai_gateway.py、operator/*、roles/* 或 /opt/imgw；没有线上写库、下载模型、安装依赖、新路由、定时器/服务安装、消息发送或网络配置变更。测试复用已安装的 Python、Node 依赖及 Chromium；临时 loopback 测试进程在验证后关闭。当前 .git 是空的只读占位目录，无法使用 git diff/status，以上清单按实际编辑列出。

## 测试与结果

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python -m pytest tests/dossiers -q -p no:cacheprovider
npm run build --prefix web
```

结果：**72 passed，25.71 秒**；TypeScript 检查与 Vite 构建通过。测试数据库全部为虚构临时库，模型使用注入 runner；不会访问真实数据库或网络。CLI `run --help` 已验证三个新参数。

覆盖缓存跨消息/跨运行命中、失败和无文本结果缓存、每轮不同图片上限、命中不占上限、文件与目录符号链接、FIFO、超大文件、非图片、哈希不符、Pillow 解压炸弹告警/错误、像素上限、低置信行过滤和排序、四种图像格式、OCR 进入模型窗口、独立来源版本、混合字段证据标记、原图回读、关闭 OCR、dry-run 两库字节不变且 sd_ocr 无新行、已有缓存表和未迁移侧库两种情况，以及既有全部档案用例。

浏览器命令（工作副本 web/node_modules 链接到已安装的 `/tmp/r7f-web-deps/node_modules`，未执行安装）：

```bash
XDG_CONFIG_HOME=/tmp/dossier-ocr-chrome-config \
XDG_CACHE_HOME=/tmp/dossier-ocr-chrome-cache \
CHROME=/tmp/ops-deals-browsers/chromium-1248/chrome-linux64/chrome \
DOSSIER_ARTIFACTS=/tmp/dossier-ocr-synthetic \
node web/e2e/dossiers.mjs
```

结果：**passed=true，2 个视口（1440×1000、390×844）**。验证 OCR 报价标记、来源弹层标记、签名原图实际解码、普通报价和普通来源无 OCR 标记，以及原有筛选、配置对比、草稿和故障状态。仅虚构截图保存在上述临时测试目录。

`dossiers-private.mjs` 本次使用真实服务层和现有只读媒体接口、虚构侧库执行；这不是线上真实数据验收。以下为本次完整复现步骤，初始化段在新的空目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python - <<'PY'
from pathlib import Path
import sys, json
sys.path.insert(0, 'tests/dossiers')
from conftest import env
from test_incremental import inc, ocr_image, prime
root = Path('/tmp/dossier-ocr-ui/private_out')
root.mkdir(parents=True, exist_ok=True)
e = env.__wrapped__(root)
i = inc.__wrapped__(e)
o = ocr_image.__wrapped__(i)
prime(i)
o.attach()
i.worker.run()
(root / 'runtime.json').write_text(json.dumps(dict(
    nas_root=str(e.nas), wechat_db=str(e.wx),
    blob_root=str(root / 'blobs'), media_key=str(e.key))))
(root / 'rehearsal-token').write_text('fictional-rehearsal-token')
PY

# 临时测试进程，另开终端运行浏览器，结束后 Ctrl-C 关闭。
PYTHONDONTWRITEBYTECODE=1 /opt/imgw/venv/bin/python tools/dossier/rehearse_ui.py serve \
  --output /tmp/dossier-ocr-ui/private_out --dist web/dist --port 18937

XDG_CONFIG_HOME=/tmp/dossier-ocr-chrome-config \
XDG_CACHE_HOME=/tmp/dossier-ocr-chrome-cache \
CHROME=/tmp/ops-deals-browsers/chromium-1248/chrome-linux64/chrome \
DOSSIER_PRIVATE_STATE=/tmp/dossier-ocr-ui/private_out \
DOSSIER_PRIVATE_BASE=http://127.0.0.1:18937 \
node web/e2e/dossiers-private.mjs
```

结果：**passed=true，2 个视口，readOnly=true，12 张虚构截图**。后端接口未被浏览器 fixture 替换；该进程仅接受 GET。

## 本地 OCR 速度实测

imgw 身份，使用已安装的真实 RapidOCR 引擎和 Pillow 在内存现画的 1200×360 合成中文图片，不使用供应商原图、不落图片副本。初始化 **0.6143 秒**，三次识别 **1.6067 / 0.9577 / 0.8054 秒**，每次保留 **4 行**；不含初始化均值 **1.12 秒/张**。重复同一合成输入仅测速度，不能据此断言线上图片识别准确率或实际吞吐。线程配置为 intra_op=2、inter_op=1。

## 真实只读演练与宿主命令

实际身份 uid=995（imgw）。已执行要求的 `run --once --dry-run --since-rowid 12710 --max-windows 15 --report /srv/imgw-dev/ops/private_out/dossier-incremental/ocr1`，OCR 使用默认开启及默认上限 200。CLI 退出码 **1**、failure_code=incremental_unavailable；单独对同一目录的写入预检确认 **errno=30（EROFS）**。未将真实报告转存到 /tmp 或工作副本。

数字摘要：报告成功落盘 **0**；处理窗口 **0**；OCR 尝试 **0**；模型调用 **0**；拟新增报价 **0**；线上侧库写入 **0**。预检在数据库连接和模型调用前退出，因此没有本轮真实覆盖率、报价提取率或网关可达性的实测结果。没有修改网关或系统环境来绕过限制。

总指挥可在指定目录可写的宿主直接运行（源码来自本交付工作副本；无需发布或修改 /opt/imgw）：

```bash
sudo -u imgw -- env PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/var/lib/imgw/sessions/codex/dev-jobs/20261105T024210Z-d937f1c0fb4b4b2d/workspace/src:/opt/imgw/wechat \
  /opt/imgw/venv/bin/python -m console.dossiers.incremental run --once \
  --database /var/lib/imgw/ops-app/dossiers/dossiers.sqlite3 \
  --wechat /var/lib/imgw/wechat/api_state.sqlite3 \
  --dry-run --ocr --max-ocr 200 --since-rowid 12710 --max-windows 15 \
  --report /srv/imgw-dev/ops/private_out/dossier-incremental/ocr1
```

该命令仅在内存迁移、补文、投影和预算记账；线上两库只读。模型调用仍会真实消耗网关免费额度，完整业务报告仅写指定私有目录。

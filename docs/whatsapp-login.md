# F1 WhatsApp 后台扫码登录后端

这是开发候选，尚未部署。入口面向「系统后台 → 渠道接入 → WhatsApp」。
前端由后续任务接入，本次没有修改 `web/` 或其中的需求账本，也没有修改生产文件。

## 现场接口核对与交付阻碍

2026-10-29 仅进行了只读检查，没有启动、停止、重启、退出会话或发送消息。
复用 `WhatsAppAdapter._configuration()` 读取配置时，发现现场配置不属于 root，
不满足现有读取器要求的 `root:imgw`、普通文件、精确 `0640` 条件，因而被拒绝。
未放宽应用校验，也未修改配置的内容、所有者或权限。

对本机 WAHA 的 `/swagger/json`、`/api/docs`、`/api/docs-json`、`/openapi.json`
进行了不携带凭据的 GET，均为 HTTP 401。**因此尚未取得现场 OpenAPI、版本或实时会话状态，
不能声称以下候选接口已经通过真实 WAHA 核对，也不能据此上线。**
已请求仅在一次性只读核对脚本中接受当前所有者的授权；应用仍须保留原安全校验。
生产配置修复与真实扫码验收不属于本次获准的操作。

当前假 WAHA 契约如下，取得真实 OpenAPI 后必须核对路径、响应体及 WEBJS 限制：

| 方法与路径 | 开发候选的处理 |
| --- | --- |
| `GET /api/sessions/{session}` | 获取 `status`、`me.id`、`engine.engine` |
| `GET /api/{session}/auth/qr?format=image` | 发送 `Accept: image/png`，接收 PNG；兼容 `mimetype=image/png`、`data=base64` 包装 |
| `POST /api/sessions/{session}/start` | 只用于 STOPPED |
| `POST /api/sessions/{session}/restart` | 只用于 FAILED；不先 stop，也不串联多个写请求 |
| `POST /api/sessions/{session}/logout` | 只用于 WORKING |

候选写响应须为带已识别 `status` 的会话对象；空体、格式错误、超时、连接中断或 HTTP 错误
均保守视为结果不明。尤其 logout 是否返回会话对象，仍须现场文档核实。
本功能没有 stop、创建/删除会话、配对码、媒体下载或发送消息路径。

## 宿主挂载与安全边界

```python
from console import v3_whatsapp_login_api
from console.channels import shared_inbox

routes += v3_whatsapp_login_api.build(app_module, archive_path, inbox=shared_inbox())
```

`build(app_module, archive_path=ARCHIVE_PATH, *, service=None, inbox=None, fetch=None,
clock=None, whatsapp_config_path=None, stat_fn=None, group_gid=None, allow_writes=True)`
与 roles 的构建形式一致。默认来源仅供正式宿主使用；指定临时 archive 且未指定配置时，
隔离到同目录的 `disabled-whatsapp.json`，不会自动读取真实配置。

宿主必须保留现有账号认证和 URL 编码表单 CSRF 中间件。接口还校验 principal、
legacy authenticated session 和 recheck：写入前 recheck，读取后再次 recheck，失败返回 403。
表单复用 `v3_archive_api.form_fields`：最多 8KB、40 个字段；重复字段、无效编码、
非 URL 编码内容类型、额外字段和查询参数均拒绝。服务不接受浏览器提供会话名、地址、密钥。

配置通过现有 `_configuration()` 读取，保留权限、O_NOFOLLOW、文件身份、大小以及
`http://127.0.0.1` / `http://localhost` 限制。原 `whatsapp.local_fetch` 仍只支持 GET。
新的登录传输采用独立的闭集路径校验，禁用环境代理与重定向；不暴露任意 WAHA 代理能力。

演练脚本按现有模块列表自动挂载，并显式传 `whatsapp_config_path=CONFIG_PATH` 和
`allow_writes=False`。可以读取状态；start/logout 在路由边界返回 403，服务层也支持禁写。
当前现场配置校验不通过时，演练 GET 同样明确报配置不可用。

## 页面接口

所有路径前缀为 `/api/v1/system/whatsapp/`。JSON 和 PNG 均带
`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。

| 路径 | 约束与响应 |
| --- | --- |
| `GET status` | 实时读取会话，不使用渠道状态的 30 秒缓存；成功 200，明确故障 503 |
| `GET qr` | 先查实时状态，仅 SCAN_QR_CODE 可取 PNG；其余 409 中文原因 |
| `POST start` | 表单 `confirm=1`；STOPPED 启动、FAILED 重启，WORKING 返回 409“已经登录，无需重新扫码” |
| `POST logout` | 表单 `confirm=1&phrase=退出登录`；仅 WORKING 可用，其余 409 |

**默认建议不对用户开放退出登录按钮。** 后端及测试已实现，是否展示由前端决定。
确认操作返回安全的状态字段及 `accepted=true`、`outcome=accepted`。
“已接受”不代表已完成扫码，前端应继续读取 status。

status 字段：

- `session`：配置中的会话名；`status`：已识别的原始状态码；`status_label`：中文名称。
- `needs_qr`：是否等待扫码；`phone_masked`：若 `me.id` 是可识别的手机号，仅末四位可见，否则 null。
- `engine`：只保留 WEBJS/NOWEB/GOWS，不能识别时为 null，不返回引擎原对象。
- `status_changed_at`：当前进程观察到状态变化的 Unix 秒；首次读取为 null。
  `status_changed_at_source=observed` 表示观察时间，不能当作 WAHA 的准确事件时间。
- `last_message_at`：复用共享收件箱 WhatsApp 缓存的最近消息统计；缓存没有数据或正在刷新时为 null。
  不额外读取聊天正文、不调用耗时的 health 刷新；该统计不等于完整入站历史。
- `next_step`：下一步的人话提示。

状态和操作关系沿用现有 WhatsApp 适配器的枚举，尚待现场 OpenAPI 重新核实：

| 原始状态 | 中文 | 下一步 |
| --- | --- | --- |
| STOPPED | 未启动 | 点登录 |
| FAILED | 登录失败 | 点登录，等待重启后的状态 |
| STARTING | 正在启动 | 请稍候，轮询 status |
| SCAN_QR_CODE | 等待扫码 | 获取 qr，用手机扫码 |
| PASSKEY_REQUIRED | 需要通行密钥 | 手机上完成验证，再刷新 |
| PASSKEY_CONFIRMATION_REQUIRED | 等待确认通行密钥 | 手机上确认，再刷新 |
| WORKING | 已登录 | 返回收件箱；禁止重复登录 |
| UNKNOWN | 状态未知 | 刷新或联系管理员；禁止写操作 |

未知的上游状态归为 UNKNOWN，不回显任意上游文本。过渡状态重复点登录返回 409；
不会为了重新显示二维码而把已登录会话退出。

二维码的 `X-Qr-Fetched-At` 是本次获取时间（Unix 秒），不是二维码的有效期承诺。
二维码由 WAHA 刷新，本接口每次重新获取，不落盘、不缓存、不写日志，也不返回原始二维码字符串。
前端在等待扫码时可每隔数秒刷新；变成 WORKING 后应停止取图并清除旧图。
若服务从 FAILED 到二维码耗时几十秒，页面持续显示“正在启动，请稍候”，不能循环 POST start。

## 故障、节流与审计

服务未启动/未监听、密钥无效、超时、配置不合要求分别返回固定中文错误和安全 `code`，
不带原始异常、远端正文、URL、堆栈、会话配置或凭据。写操作只发送一次：

```json
{"error":"结果不明，请稍后刷新状态；不要立即重复点击","code":"outcome_unknown","outcome":"unknown"}
```

该情况返回 503，前端只轮询 status，不自动重试 POST。请求总远端预算为 6 秒，
单次最多 4 秒；使用有上限的工作线程等待，即使传输忽略超时也会停止等待。
超时无法撤销已经送到 WAHA 的操作，所以迟到结果仍保持“结果不明”。
不会在请求中等待登录状态轮转，也没有后续自动写操作。

审计位于 archive 同目录的 `<archive-stem>-whatsapp-login.sqlite3`，不修改业务 archive schema。
只在通过确认校验的写操作中创建，GET 不建库。`login_audit` 记录 `at/actor/action/result`，
先记录 attempt，再记录 accepted/rejected/failed/unknown；不保存表单值、二维码、手机号或密钥。
无法记录开始审计时不向 WAHA 写入。鉴权、CSRF、表单拒绝沿宿主拒绝流程，不作为已执行操作记录。

`login_throttle` 在 SQLite 事务内预留：同一动作、所有用户、共享该文件的进程，
10 秒内最多一次；包含失败和结果不明的尝试。429 带 `Retry-After` 与 `retry_after`。
start 和 logout 分别计时，服务重建不会清除节流。SQLite 锁等待上限 0.1 秒。

## 隔离验证与未做部分

测试用虚构配置、虚构会话和假 WAHA，并禁止 socket connect/connect_ex。
覆盖状态全枚举、QR 准入、已登录不重启、确认短语、鉴权/CSRF/recheck、表单限制、
跨实例并发节流、审计失败、未知结果不重试、错误脱敏和发送路径禁入。
完整相关套件同时运行时需 `--import-mode=importlib`，避免既有同名测试模块冲突。

本轮通过临时 `sitecustomize` 对测试进程及子进程全局禁网，运行 channels、outbox、roles、
inbox_api、archive、deals、quotes 七组回归：**605 passed，439 subtests passed，34.48 秒**。
其中新增登录服务测试 107 项、路由测试 37 项、演练保护测试 1 项。

本次未部署、未执行真实扫码/退出/启动/重启、未验证 WEBJS 真实重启行为、未做前端和全页面
真实数据冒烟，也未 commit/push。现场 OpenAPI 核对和配置所有者阻碍解除前，不能作为完整可上线验收。

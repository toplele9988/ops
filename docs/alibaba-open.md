# R5-C：阿里官方订单只读接入

本轮是开发候选，未部署。全部测试只用虚构订单、临时 SQLite 和进程内假 TOP/GOP 服务，socket 级禁网；没有运行真实授权、订单请求、真实浏览器验收或定时任务。不能据此判断真实账号、订单或权限可用。

## 老板与系统各做什么

```text
总指挥：补齐快照依赖 → 核对下列待确认项 → 审查挂载补丁 → 保留离线保护上线
老板：在服务器终端自行保存 App Key / Secret（不要发到聊天里）
系统：状态页仅检查文件元数据，显示“未配置”或下一步
总指挥：确认协议、权限、网关、时钟和回调规则后，单独决定是否允许联网
老板：生成授权链接 → 在浏览器授权 → 从地址栏复制 code → 粘贴并确认保存
系统：GOP 换授权 → 加密保存到独立库；不记录 code、token 或上游正文
老板：取消暂停 → 确认读取一次订单
系统：按修改时间增量 list → 新增/变化订单 get → 验证字段 → 幂等生成候选
老板：核对订单归属，必要时人工关联已有案子 → 核对实际到账金额和日期
老板：在既有案子收款区登记，并亲自点“确认收款”
既有收款模块：在原事务中确认付款、写确认人/时间，再判断成交
```

**候选绝不确认收款，也不自动成交。** 未知归属创建 `awaiting_payment / open`、无客户的待认领案子，使用既有 `Deals._write` / `_event`。不按买家名、订单号片段或会话名称猜客户、案子。人工认领只关联候选，不创建或确认付款。

候选的金额来自平台 `total_amount`，是**订单总额，不能当实际到账金额**。详情的公开契约没有可靠支付时间，`paid_at_ms=null` 显示“待核对”，不会把修改时间冒充支付时间。复制资料只是帮助填写，老板仍须核对并手填实收。后续平台状态变为未付款或无法判断时，保留历史候选并显示状态变化，不改动已登记的付款和成交状态。

## 协议依据与待确认项

指定的私有 `03a-官方API字段与协议.json` 在当前快照及指定只读目录均未找到；未复制私有报告。以下依据公开官方文档建立保守适配，待 Windows 任务书 12 回填后再决定真实接入。

| 项目 | 当前实现与限制 |
| --- | --- |
| TOP 订单 | `alibaba.seller.order.list` / `alibaba.seller.order.get`，只允许这两个读方法；`session`、`sign_method=hmac`、HMAC-MD5、JSON、v2.0 |
| TOP 时间戳 | 官方经典 TOP 文档要求 GMT+8 的 `yyyy-MM-dd HH:mm:ss`，误差小于 10 分钟。默认据此实现；任务书的毫秒模式通过 `OPS_ALIBABA_OPEN_TOP_TIMESTAMP=milliseconds` 显式选择，尚待核对，不能声称两者都已真实验证 |
| GOP 授权 | `/auth/token/create` / `/auth/token/refresh`，HMAC-SHA256、`sign_method=sha256`、本机 Unix 毫秒；签名串包含接口 path。GOP 文档要求本机与 UTC 偏差小于 7200 秒 |
| 时间来源 | 始终取 221 本机时钟，不取 Win10 时钟；没有外部授时请求，也没有改系统时钟。时钟回退到水位之前会拒绝推进 |
| list | `param_trade_ecology_order_list_query`；`start_page` 从 0 开始；按 `modify_date.timestamp` 做水位；页、条数和详情完整后才推进 |
| get | `e_trade_id`、`total_amount.amount/currency`、`buyer.immutable_eid/e_account_id`、`trade_id/trade_status`；只把明确的 `paid/undeliver` 归为平台显示已付款，其他状态保守归为未付款或待核对 |
| 授权地址 | 默认仅作候选配置：`https://openapi-auth.alibaba.com/oauth/authorize`，回调 `https://www.alibaba.com`；只拼接 URL，生成链接不会访问外部接口 |
| GOP 网关 | 默认候选 `https://openapi-api.alibaba.com/rest`；尚未核对当前 App 的实际网关 |
| TOP 网关 | 配置默认 `https://eco.taobao.com/router/rest`；尚未用真实 App 核对 |

官方公开参考：[TOP 签名和时间戳](https://developer.alibaba.com/docs/doc.htm?articleId=101617&docType=1&treeId=153)、[订单 list](https://developer.alibaba.com/docs/api.htm?apiId=41047)、[订单 get](https://developer.alibaba.com/docs/api.htm?apiId=41148)、[GOP 签名协议](https://developer.alibaba.com/docs/doc.htm?articleId=121120&docType=1)、[GOP 动态门户](https://openapi.alibaba.com/doc/api.htm)。TOP 和 GOP 的参数、时间戳和签名分别实现，不能混用。

上线联网前逐项确认：

- [ ] 当前 App 获得的订单 list/get 权限，以及 get 文档所述“聚石塔内调用”是否限制 221 调用。
- [ ] GOP token 是否可作为这组经典 TOP 订单接口的 `session`，卖家账号与授权范围是否一致。
- [ ] 当前 App 的 GOP 网关、授权入口、token 返回字段、刷新令牌轮换及有效期含义。
- [ ] 现有回调地址是否允许手工从地址栏取得 code；是否必须完全匹配注册回调，是否另有限制。
- [ ] 私有报告中的 TOP 毫秒要求对应哪个网关/协议版本，是否与当前公开经典 TOP 文档冲突。
- [ ] 修改时间查询的包含边界、最大时间跨度、分页稳定性、各状态实际含义和支付时间来源。
- [ ] 平台是否存在分期、平台代收、退款等导致“已付款状态”与可确认实际到账不一致的情况。
- [ ] 221 时钟已校准；真实数据只读演练和页面验收由总指挥统一安排。

## 配置、存储和恢复

| 配置 | 默认值 / 说明 |
| --- | --- |
| `OPS_ALIBABA_OPEN_CREDENTIALS` / `credentials_path` | `/etc/imgw/alibaba-open.json`；schema `alibaba-open-v1`；root:imgw、0640、单链接普通文件；拒绝目录和目标符号链接，使用前再次校验 |
| `OPS_ALIBABA_OPEN_DB` / `db_path` | `/var/lib/imgw/ops-app/alibaba-open.sqlite3` |
| `OPS_ALIBABA_OPEN_KEY_PATH` / `key_path` | 独立库路径后缀 `.key`；Fernet 密钥，0600，不得丢失、输出或入仓库 |
| `OPS_ALIBABA_OPEN_TOP_URL/GOP_URL/AUTH_URL/REDIRECT_URI` | 可覆盖表中候选 HTTPS 地址；不允许凭据、query 或 fragment，不跟随 HTTP 跳转 |
| `OPS_ALIBABA_OPEN_TOP_TIMESTAMP` | `gmt8`（默认）或 `milliseconds`；真实启用前核对 |
| builder `network_enabled` | **默认 False**；无环境变量自动打开；挂载补丁也明确 False |
| service `page_size/initial_days/max_pages` | 默认 50 / 30 / 200，构造参数可调；首次读取最近 30 天修改订单，不是全部历史回填 |

数据库、加密密钥、凭据和档案路径，以及数据库的 journal/WAL/SHM 和锁路径，不能相同或指向同一现存文件。非默认档案路径的 API builder 将未指定的独立库与密钥放在档案同目录，演练另指定不存在的临时凭据路径。状态读取不创建文件、不解密 token、不读取凭据内容；凭据内容只在生成链接、换授权、刷新或订单读取确实需要时读取，错误不含路径和值。

独立库保存 `kv`（水位、开关、UTC 日调用计数、退避、授权尝试摘要）、`token_state`（密文与期限）、`observed_orders`（订单号与修改时间）、`order_candidates`、字段受限的 `audit`。授权码只保存 SHA-256 尝试摘要；token 密文以外不落库、不进日志。库、密钥、锁、存在的 journal/WAL/SHM 都校验 0600。依赖 `cryptography`，沿用项目已有的系统包回退方式；缺失时给固定中文错误。

写入经进程锁、文件锁和 SQLite `BEGIN IMMEDIATE` 事务；同一订单更新幂等。候选通过 ATTACH 在现有档案结构上写，不调用档案迁移、不改档案版本。新案及认领的稳定时间线标记用于恢复；已观察订单引用的案子丢失、编号被无关案子重用，或独立库回滚到旧归属时，也会核对来源及最新人工认领标记，再读详情恢复引用，不会只凭案子编号关联。已有库不具备案子表时明确拒绝，不能由本模块升级。

SQLite 对跨文件、尤其既有档案 WAL 下的断电，不能保证完全同时提交；稳定标记和恢复检查降低重复风险，但不能替代一致备份。不要清理候选来源或认领标记。独立库丢失、档案从旧备份恢复或 key 丢失时，先暂停并核对，不能只删某一文件后继续运行。

默认暂停；手工取消后每次只跑一轮。初始历史边界首次确定后持久化，失败跨天不滑走；成功水位前回看 120 秒处理边界。任何 list/get、分页、金额或币种失败均不推进水位；已完成条目保留幂等缓存。每轮最多配置页数，不创建后台循环。失败等待 30、60、120 秒递增，最长 3600 秒，时间到后仍须手动触发。所有实际 HTTP 尝试（包括失败、换授权和刷新）先扣 UTC 日额度，默认每天 1000 次，最多可设置 100000。

换授权和刷新都无隐式重试；请求前持久化尝试/进行中状态。超时、5xx 或进程中断后不重放同 code / 旧刷新令牌，要求重新浏览器授权。即使请求前的预算或配置检查失败，也保守要求取得新 code；不会为节省操作而猜测上游是否消费过凭证。

目前候选仅展示最近 200 条，尚无分页。只接受已列明的两位小数币种（USD/CNY/EUR/GBP/AUD/CAD/HKD/SGD/NZD/CHF/BRL/MXN/ZAR/AED/SAR/THB/TRY/PLN）；JPY、未知或异常币种拒绝。没有换汇，也没有把不同币种相加。

## 控制台入口

页面 `/app/system/alibaba-open`，系统 → 阿里订单接入。路径相对 SPA 为 `/system/alibaba-open`。

| 方法 | `/api/v1/alibaba-open/` 下路径 | 参数 |
| --- | --- | --- |
| GET | `status` | 不接受查询参数；是否配置/授权、剩余期限、最近读取、退避、开关、额度、下一步 |
| GET | `authorization` | 不接受查询参数；只生成 URL，附随机 state；手工粘贴 code 流程不接收公网回调 |
| GET | `candidates` | 最近 200 条候选、金额币种、买家标识、平台状态与时间、案子和认领状态 |
| POST | `authorize` | `code,confirm=1` |
| POST | `poll` | `confirm=1` |
| POST | `settings` | `paused=0或1,daily_limit,confirm=1`；两个设置至少一项 |
| POST | `candidates/{order_id}/claim` | `deal_id,confirm=1` |

沿用宿主登录、legacy session、CSRF；POST 读表单前和业务操作前两次 recheck，GET 返回前 recheck。URL 编码表单至多 8KB、拒绝未知和重复字段；操作者来自 principal。响应 no-store/nosniff，参数 400、不存在 404、冲突/重授权 409、服务错误 503，错误不回显上游正文。前端所有写操作关闭自动重试，未知结果先重读核对；code 输入不会写 URL、localStorage 或浏览器日志。

可选脚本 `scripts/alibaba_open_poll.py`：不带 `--poll` 只查状态；`--poll --enable-network` 才明确尝试一轮，仍遵守暂停/额度/退避，无自动刷新循环、cron 或 systemd。部署启用前不得在真实环境运行这些联网参数。

## 测试与快照缺口

指定 sudo 命令已尝试，环境原样拒绝：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

后续以当前 imgw 身份执行同样 pytest 参数。未经补齐的快照缺 `src/console/media/`，原始新套件收集 1 error、deals 回归收集 8 errors，fulfil 在 conftest 导入失败（退出码 4）。关键原文：

```text
src/console/channels/alibaba.py:6: in <module>
    from console.media import tokens
E   ModuleNotFoundError: No module named 'console.media'
```

没有修改或补写其他任务的媒体模块。补充验证通过 `/tmp/r5c-test-overlay/console/__init__.py`，其 `__path__` 先指向本快照 `src/console`，再指向临时包；临时包中仅有 `media` 符号链接指向只读 `/srv/imgw-dev/ops/src/console/media`，不会回退其他模块。这不是交付依赖安装，也不能把原始快照回归说成已通过。所读取媒体源码 SHA-256：

```text
ced5f3a0f932bbaaa6140ae8ceb081161800f896b998260729deb97fef5880f8  media/__init__.py
282823a19e141425fa2f352eccc9dff2ab019721f67d06a48cc13e2811f76c93  media/alibaba.py
7c6a84e3819dc6a50e3e11c8e9f92ba6e5f941c656ba6eda9b6e808b5a21917b  media/files.py
25440e4ce6ad69f2e952aaa2c1d55e2d0070807f9731f898faba828ee58ed184  media/tokens.py
```

补充测试命令（suite 分别取 alibaba_open、deals、fulfil、archive）：

```sh
env PYTHONDONTWRITEBYTECODE=1 OPS_MEDIA_KEY_PATH=/tmp/r5c-test-media-key \
  PYTHONPATH="/tmp/r5c-test-overlay:src:tests/<suite>" \
  /opt/imgw/venv/bin/python -m pytest tests/<suite> -q -p no:cacheprovider
```

最终结果见交付末表。新套件包含正确/错误签名、本地时钟模式、权限和符号链接、密文保存/回滚、token 过期刷新与刷新失败、429/超时/5xx 退避、0 基分页、详情失败不推进水位、重复和变化订单、跨库恢复标记、人工认领、金额币种拒绝、凭据缺失、日志哨兵、登录/CSRF/recheck、默认禁网和并发拒绝。全部仅虚构数据。

开发中首次回滚测试发现 schema 跟业务一同回滚，失败原文如下；已改为先提交本模块的 schema 初始化短事务，再开始业务事务，最终通过。

```text
FAILED tests/alibaba_open/test_store.py::test_failed_write_rolls_back_and_writers_serialize
E           sqlite3.OperationalError: no such table: kv
1 failed, 82 passed in 0.90s
```

前端用 `npm ci --ignore-scripts --cache /tmp/r5c-npm-cache` 安装构建依赖，`npm run build` 即 `tsc --noEmit && vite build`；没有运行 `tsc -b`。没有运行 e2e_smoke 或启动固定端口服务。挂载补丁仅在临时源码副本 `patch --dry-run` 通过；生产基线 hash 未变。

## 文件、总指挥合并与部署清单

新增文件：

- `src/console/alibaba_open/{__init__,app_credentials,errors,protocol,store,candidates,service}.py`
- `src/console/v3_alibaba_open_api.py`
- `web/src/alibabaOpen.ts`、`web/src/pages/AlibabaOpen.tsx`
- `tests/alibaba_open/{conftest,test_api,test_protocol,test_credentials,test_store,test_candidates,test_polling}.py`
- `scripts/alibaba_open_poll.py`
- `deploy/alibaba-open/console-mount.patch`、`shared-files.patch`、`baseline.sha256`、`files.sha256`
- 本文 `docs/alibaba-open.md`

**需要总指挥合并的改动：** `shared-files.patch` 保存下列最少变更，重建改前内容的 SHA-256 已与基线清单逐项核对；不要再次把补丁应用到本工作区已修改的文件。

- `web/src/App.tsx:6` 一行 import；`:59` 一行 `/system/alibaba-open` route。
- `web/src/nav.ts:61` 一行系统导航。
- `scripts/rehearsal_server.py:55–56` 仅新增 import 和 builder，两行，不改已有挂载逻辑；演练凭据指向临时不存在文件，网络保持关闭。
- 生产挂载由 `deploy/alibaba-open/console-mount.patch` 提供两行，**未应用**；基线 `/opt/imgw/wechat/console/v3.py` SHA-256 为 `847f8ec6ab2f881f3a9f85e7e610825f3a75bfde1295dcc1eb446c9932a94397`。基线不符先重审上下文，不强行打补丁。
- 补齐快照已引用但缺失的既有 `console.media`。没有改需求账本、README、master-plan、收款组件、语言表或其他业务模块；需求 H3 的状态请总指挥在共享账本更新。

供总指挥执行的部署步骤（本轮均未执行）：

1. 补齐媒体依赖与案子已有结构，检查 cryptography；在完整快照重跑原始套件，保留本轮补充验证与正式验收的区别。
2. 审查文件清单及 `baseline.sha256/files.sha256`，备份现有路由宿主、前端 dist、相关模块；停写期间一致备份 archive、阿里独立库及加密 key（如存在），记录各源 SHA-256。备份不得进入公开仓库。
3. 合并源码及最少共享行，构建前端。审查并应用生产挂载补丁，保持 `network_enabled=False`；不运行档案迁移，不安装定时任务。
4. 安装文件并核对 7 条 API 路由均位于宿主认证/CSRF 之后、SPA catchall 之前；由总指挥安排重启及既有演练/冒烟。先验证未配置状态和中文下一步。
5. 老板自行运行已有凭据保存脚本；管理员只检查存在性、root:imgw/0640，不输出值。服务在第一次确有需要的显式操作才读取。
6. 任务 12 确认上表全部协议/权限问题并完成总指挥审查后，才另行决定修改挂载 `network_enabled=True`、取消暂停和进行真实只读验收。真实收款始终由老板核对点击。是否增加调度另行决定。

回滚：先暂停并关闭联网，停写/停止旧进程后恢复备份的挂载宿主、模块与前端 dist，再重启核对路由。默认不删候选、案子、收款或来源标记；新案子是未成交候选，保留给人工核对。需要回滚数据时须停写后一致恢复档案、独立库和原加密 key，先确认备份之后有无人工作用，不能单独回退任一库覆盖新确认付款。移除本模块路由后既有收款流程继续独立工作；本任务无新增 archive 迁移可逆操作。

安全自查：API 无客户端可选文件路径，主体验证与 CSRF 测试覆盖越权；路径拒绝符号链接/同文件别名；业务值使用 SQL 参数，动态列名来自固定内部字典；token Fernet 加密，凭据与上游异常不进入返回/日志/审计，哨兵检查通过；仅允许两个读订单方法，无渠道发送接口；授权与刷新未知结果不会重放，付款只走既有人工确认入口。未触碰网络配置、生产文件或业务库，未 commit/push，未向任何人发送消息。

## 最终验证结果

| 验证 | 结果 | 限制 |
| --- | --- | --- |
| 新增 alibaba_open | 150 passed，0 failed | 临时 media 依赖补充环境；全虚构、socket 禁网 |
| 既有 deals | 128 passed，62 subtests passed，0 failed | 同上 |
| 既有 fulfil | 174 passed，0 failed | 同上 |
| 既有 archive | 40 passed，46 subtests passed，0 failed | 同上 |
| 既有 web_app | 6 passed，0 failed | 原始快照，直接执行 |
| 前端 | npm run build 通过 | 无浏览器/真实业务验收 |
| 挂载补丁 | patch --dry-run 退出码 0 | 只在临时源码副本，不是部署 |
| 原始快照 | 新套件 1 collection error；deals 8 collection errors；fulfil conftest 导入失败 | 缺 console.media，未掩盖为通过 |

`web_app` 的原始命令：`env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests/web_app /opt/imgw/venv/bin/python -m pytest tests/web_app -q -p no:cacheprovider`。其他原始快照命令相同，仅替换套件名；保留上文失败原文。开发期 schema 回滚失败已修复，最终套件没有失败。

未完成的真实验收包括：真实凭据内容核验、授权换取/刷新、实际订单可读性及覆盖率、生产页面浏览器验收、统一 e2e 冒烟、真实收款核对；本任务明确禁止执行这些操作。TOP 毫秒契约与经典文档冲突及动态 GOP 网关等问题也仍待任务 12 确认，当前实现和默认配置不能替代这些确认。

# 客户页

页面在 `/app/customers`，默认打开「潜在客户」。顶部说明：

> 这里是所有客户。AI 读过聊天记录后，自动分成潜在客户和老客户；你确认后建档。

## 分层和展示

- 五个分段：潜在客户、已报价、老客户、售后中、沉睡。沉睡按最后一次双方往来超过或等于 90 天判断，不只看对方最后发言。
- 同一档案的多个会话合成一行，不按名字猜归属。成交记录保留为老客户；售后优先展示；90 天没有往来时归到沉睡。其余使用聊天分析给出的阶段。
- 分段数量按当前搜索和渠道筛选后的客户行计算。画像接口的 `counts` 是会话口径，不能直接当作去重后的客户数。
- 已建档客户仍保留。没有阶段依据且没有已知的 90 天未联系记录时，在「等待读取聊天」展示，不猜测成交状态。
- 每行显示国家旗帜和中文国名、文字加图标的渠道、最多三个产品、北京时间和相对时间、AI 总结。未知国家、产品、时间均明确提示。
- 点名字或「查看依据」按需读取完整证据。点原话打开其对应会话，使用现有 `/inbox?conversation=…&role=all&state=all` 路由。现有收件箱支持打开会话，暂不支持按 `message_index` 自动定位某条消息；本页不声称已经定位。

## 建档

「建为客户」使用现有 `POST /api/v1/parties-ops`，表单字段为 `kind=customer`、`name`、`country`、`conversation_key`、`request_id`。后端 `Parties._write(create)` 在同一事务中建档并调用 `_link` 关联会话，无需再发一次关联请求。

批量仅处理当前分段中勾选的未建档客户。确认框说明将建立几份档案，再逐个提交。成功后立即成为已建档客户；部分失败保留已成功的结果和未成功的勾选，重试复用原请求编号。响应中断会提示「建档结果暂未确认」，并重读实际档案。保存期间禁用重复点击。

真实档案对 `kind,name` 有唯一约束。同名档案或会话归属冲突时不自动合并，提示到「管理客户档案」核对，使用原资料页的 `POST /api/v1/parties-ops/{id}/link` 关联会话。原有手工新建、客户资料、合并与撤销仍可使用；旧的 `?c=<id>` 和 `/customers/<id>` 地址保留。

## 接口与故障

- `GET /api/v1/profiles?kind=customer`：读取客户聊天分析，每 20 秒自动更新；搜索和分段在客户端按合并后的客户筛选。
- `GET /api/v1/profiles/<编码后的 conversation_key>`：展开后获取完整依据。
- `GET /api/v1/parties-ops?kind=customer&page_size=100&page=…`：逐页读取全部已建档客户。
- `GET /api/v1/parties-ops/conversations`：以真实 `key`、`party_id` 判断会话归属。若仍有未关联画像且已有档案渠道有交集，再以最多四个并发读取 `GET /api/v1/parties-ops/{id}` 的 `identities`，识别历史 `conversation_ref` 关联；不会按名称自动归入客户。
- 分析接口 404、405 或 501 显示「AI 还在读聊天记录，读完会自动出现」。其他接口错误、断网或坏响应显示中文提示与重试按钮。刷新失败时保留已有结果。归属未读完整时暂停建档，避免重复。

本任务没有增加画像后端、数据库表或发送功能，没有改导航和路由文件。页面使用已有设计变量、焦点样式及原生对话框；分段支持方向键，手机分段换行，长文本换行，无整页横向滚动。

## 本地验收

```sh
cd web
npm ci --cache /tmp/customers-npm-cache --no-audit --no-fund
npm run build
npx tsc --noEmit
PLAYWRIGHT_BROWSERS_PATH=/tmp/inbox-browsers node e2e/customers.mjs
PLAYWRIGHT_BROWSERS_PATH=/tmp/inbox-browsers node e2e/ops-parties.mjs
```

浏览器需有与 `playwright-core` 匹配的 Chromium，可改用 `CHROME=/本地/浏览器路径`。脚本只启动本地构建静态服务，拦截所有业务请求；未登记的接口和外部请求直接失败，全部数据为虚构。

截图和结果写入被忽略的 `artifacts/customers/`，包含桌面和手机的分段、依据、已建档、404，以及加载和错误画面。测试覆盖单个/批量建档、取消确认、请求字段与数量、部分失败、原请求编号重试、响应丢失后的核对、归属冲突、历史关联、跨页读取、筛选、键盘和自动出现。实际结果见 `docs/integration-customers.md`。

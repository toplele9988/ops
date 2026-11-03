# H9：询盘到付款成交、履约与返单走查

本轮 P4 的结论：**本地已有能算价、留住还价历史、人工确认定金和记录履约的模块，但老板还不能在页面里顺着完成整笔生意。** 最先要补的是持续进件、付款成交入口、报价数量与物流核价边界、母语报价、付款条款与 PI。成交后还有阶段按钮、责任方选择和返单入口的断点。

本报告核查的是本次独立工作区源码，没有访问线上业务库、渠道、账号配置或真实模型，也没有启动服务器。I2 的接口和报价页比价入口在快照中均存在；按任务背景仍属本地未部署候选，不判成“没有比价”，也不判成“线上可用”。其他文档中的上线、验收日期仅是历史记录，本轮未现场核实。

获客、广告、发品和发现新供应商没有被本主线模拟为成功；本次从已到达的虚构询盘开始，供应方也是预先准备的虚构单位。需求账本 D 组、C6 所描述的增长或寻源能力，不能由本次走查证明。

工作区没有 `.git` 元数据，不能报告提交号或工作树差异；用文件 SHA-256 固定证据基线。未修改业务源码、已有测试、路由、导航或共享需求账本。

## 判定与验证口径

| 判定 | 本文含义 |
| --- | --- |
| 已可用 | 当前已有对应入口或内部函数，本轮虚构主线实际调用通过；不代表真实数据、浏览器或渠道验收通过 |
| 部分可用 | 核心记录存在，但要切页、人工补录或调用内部接口绕行；或者只能核查到局部接入 |
| 缺失 | 当前主线所需的能力或防护没有对应实现；说明具体缺什么，不把整个模块都判为没有 |

严重程度分为“阻塞成交”“拖慢”“体验”。误报成交、漏算/错算价格也列入阻塞成交，因为它们会让老板依据错误结果拍板。成交后断点标“拖慢”，并写明会阻塞哪一项履约操作。

源码静态核查与实际调用分开：脚本验证模块逻辑，页面结论来自源码；本轮没有浏览器操作或截图证明。脚本里的“卡住”可以继续通过明确标注的人工补录/内部函数绕行，不能据此把该步改成已可用。

## 完全虚构的主线

虚构南美舞台买家，通过阿里文字询盘要 24 台 `WX-300` LED 摇头灯及 24 套线缆，目的国、港口、单位、渠道身份、价格和到账均为虚构。假设买家使用西语，**这不是从真实客户识别出的语言**。两家灯厂分别经假微信、假 WhatsApp 会话报价，两家海运商分别标外部货代、阿里平台物流。

| 项目 | 演示取值，均非真实报价 |
| --- | --- |
| 灯厂报价 | 主选 1357 元/台，备选 1412 元/台；主选产品成本 32568 元 |
| 包装 | 每箱 1 台，50×40×30 cm，毛重 20 kg；整票 24 箱、1.44 m³、480 kg |
| 外部海运 | 600 元/m³，最低 900 元，32–38 天；整票 900 元 |
| 平台物流 | 680 元/m³，最低 950 元，25–30 天；整票 979.20 元 |
| 国内拖车、配套线缆 | 分别为人工确认总价 480 元、600 元 |
| 报价条件 | CIF，虚构目的港；汇率 1 USD=7 CNY，成本有效至 2030-02-01，客户报价至 2030-01-20 |
| 首轮 | 260 USD/台，总额 6240 USD；成本 34548 元，利润 9132 元 |
| 还价一次后的第二轮 | 250 USD/台，总额 6000 USD；成本快照不变，利润 7452 元 |
| 成交 | 人工记录并确认 30% 定金 1800 USD，剩余应收 4200 USD |

演示用固定时钟推进等待、生产、航运、签收及签收后 31 天。它不会收到银行流水、真实订舱或实际签收回执。单据、真实采购单和对外发送没有被脚本模拟为成功。

## 老板每天怎么用，以及哪里需要停下来

1. 打开 `/app/` 工作台，先看渠道/体检提示，再主动更新成交简报和巡检。已有简报可能是旧结果，不能只看标题“今日”。
2. 到“商机沟通”或“待整理会话”确认客户、建案。只在通讯录里看见 SUP 群，并不表示系统收到它的消息；没有会话时先处理接入。
3. 在案子添加供应商、货代、配套方，再到对应会话挂入案子。目前要来回切页；已付款的案子不会出现在收件箱默认的“加入已有案子”选择中。
4. 看案子“各方现在的情况”和秘书的等待时长，核对是否真在等报价。当前是最后消息方向判断，不是逐项采购事项的完成证明。催价还要进会话起草并人工核对需求。
5. 到“价格建议”确认产品、单位和有效期；补齐包装。到报价轮手动选产品和费用，再比较整票海运价。换货代先核对旧费用有没有保留。
6. 报价文本仍要补付款条件；西语母语版和 PI 尚未接上本流程。人工到原渠道发出，再回来标“已发出”。复制后的编辑内容没有回写成报价快照。
7. 客户接受后登记定金，**核对实际到账再确认收款**。现有收款正向流程可用，但直接推进阶段也能误标成交，不能以阶段按钮代替核款。
8. 成交后记录备货、验货、订舱、在途及签收。当前页面不能顺畅推进已成交案子的阶段；完成节点不自动代表发货。售后记录可以用，返单草稿仍需接口入口。

以上页面路径依据 `web/src/App.tsx:38`、导航依据 `web/src/nav.ts:28`，不是本轮新加的入口。

## 逐步核对

| 步骤与老板动作 | 判定 | 证据：当前源码位置或接口 | 对老板的影响 | 严重程度 / 缺口 |
| --- | --- | --- | --- | --- |
| 询盘进入 | 部分可用 | `src/console/channels/alibaba.py:17`；`src/console/inbox/model.py:40`；`docs/data-pipelines.md` 第 2、5 节 | 本地文字聚合、去重已验证；不能据此确认 Win10 实时采集、附件、商品卡片或全部账号覆盖 | 阻塞成交 / WG-01 |
| 建案并关联客户 | 已可用 | `POST /api/v1/deals`；`POST /api/v1/triage/{id}/accept`；`src/console/roles/service.py:307` | 人工接受分拣可建案，重复接受复用原案；演示额外手填目的国 | 体验：仍须人工核对 |
| 提取客户产品需求 | 部分可用 | `web/src/pages/DealPage.tsx:181`；`src/console/extract/service.py:46` | 可手填名称、数量；价格抽取员只处理供应方，不会把客户询盘转成待确认需求清单，也不自动提取目的地/电压/配套要求 | 拖慢 / WG-06 |
| 向微信/WhatsApp 产品供应商问价 | 部分可用 | `src/console/inbox/ai.py:40`；`src/console/v3_inbox_api.py:84`；`web/src/pages/DealPage.tsx:205` | 已有采购跟进口吻和复制/受控发送入口；输入以单会话为主，没有携带本案产品、数量、包装、到期日的案内询价/催价动作 | 拖慢 / WG-04 |
| 收到供应商报价并抽取 | 已可用，限文字 | `POST /api/v1/extract/scan`、`extract/suggestions/{id}/accept`；`src/console/extract/storage.py:247` | 人确认有效期才入档，重复接受幂等；图片/PDF/表格内容不在本次抽取范围，仍要人工读 | 附件型询价拖慢 / WG-01、WG-06 |
| 多家产品供应商比较 | 部分可用 | `src/console/archive/store.py:27`、`:46`；`src/console/quotes/service.py:56`；`web/src/pages/QuotePage.tsx:182` | 产品归一个供应商，每个产品取当前报价；能分别存多家，缺少同一需求的价格、MOQ、包装、交期、有效期并排比较及选择依据 | 拖慢 / WG-05 |
| 向外部货代/平台物流问运费 | 部分可用 | `POST /api/v1/archive/freight`；`src/console/extract/storage.py:316`；`web/src/pages/Prices.tsx:46` | 可抽取文字后人工补线路和服务；不是向阿里物流自动询价。价格建议页接受运价未提供来源渠道，省略后会按外部货代入档 | 拖慢 / WG-07 |
| 同票运费比价 | 已可用，本地候选 | `GET /api/v1/quotes/{id}/freight-options`；`src/console/quotes/service.py:161`；`web/src/pages/QuotePage.tsx:202` | 有整票总价、最低收费、时效、有效期、来源和最低价/最快提示；需已有确认数量及包装，人民币才可选入成本 | 本轮无该基础能力缺失 |
| 选择、改选货代 | 部分可用 | `POST /api/v1/quotes/{id}/freight-options/choose`；`src/console/quotes/service.py:388` | 同一运价重复选择幂等；改选另一家会新增一条，不替换旧海运费；界面默认自动填选择原因，不能填写自己的取舍说明 | 阻塞成交：错算 / WG-07 |
| 配套供应商与国内拖车询价 | 部分可用 | `src/console/extract/storage.py:266`；`POST /api/v1/quotes/{id}/costs`；`web/src/pages/QuotePage.tsx:237` | 可存 vendor 价格线索和人工总费用；费用表单没有单位选择、数量计价或结构化来源关联，24 套线缆需人工折总价 | 拖慢 / WG-08 |
| 成本+运费+汇率+利润核价 | 已可用，另有边界缺口 | `src/console/quotes/calc.py:8`；`src/console/quotes/freight.py`；`POST /api/v1/quotes/{id}/status` | 整数金额、有效期、倒挂、缺数量/汇率/售价、自动运费包装缺项有检查；不能把“成本齐全”理解为所有税费和附加费已收齐 | 阻塞成交：边界见 WG-07 |
| 填写/修改报价数量 | 部分可用 | `web/src/pages/QuotePage.tsx:93`、`:189`；`src/console/quotes/service.py:73` | 后端能改数量并重算，页面只编辑售价；新增行固定数量 1。客户增减数量或补配件时不能直接修正 | 阻塞成交：报价数量风险 / WG-06 |
| 客户语言影响报价 | 缺失：母语报价衔接 | `src/console/quotes/validation.py:103`；`src/console/quotes/text.py:5`；`web/src/pages/QuotePage.tsx:57` | 报价仅中文/英文，未读取 P1 的联系人母语、人工锁定和回译；西语客户需要人工翻译 | 阻塞成交 / WG-03，依赖 P1 |
| 报价发给客户，人工确认 | 部分可用 | `web/src/pages/QuotePage.tsx:169`、`:176`；`src/console/quotes/service.py:431`；`web/src/pages/Inbox.tsx:108` | 报价“发出”仅记状态；复制、实际发送与渠道回执没有绑定。WhatsApp 既有受控发送仍须人确认，未知结果已有禁止重复发送提示 | 拖慢，错误文本影响成交 / WG-09 |
| 客户还价一次 | 已可用 | `POST /api/v1/quotes/{id}/negotiations`；`src/console/quotes/service.py:424` | 保留谁说什么、价格、币种和时间；页面可记角色，但未选择具体供应方单位，多供应商协商仍需核对 | 拖慢的细节归 WG-05、WG-08 |
| 再报价、客户接受 | 已可用 | `src/console/quotes/service.py:302`、`:431`；`src/console/deals/service.py:32` | 复制快照，重填客户有效期；旧 sent 轮被替代；接受只到 awaiting_payment/open，不算付款成交 | 本轮正向流程通过 |
| 30% 定金条款、PI | 缺失：新报价轮单据衔接 | `src/console/quotes/text.py:25`；`src/console/quotes/validation.py:7`；`src/console/v3_quotes_api.py:44` | 算价 ready=true 时付款条件仍可能是占位；新报价轮没有条款字段或 PI 导出。仓库另有 CPQ 导出/信函体系，不能自动当成已关联本轮的 PI | 阻塞成交 / WG-09 |
| 登记并确认定金 | 已可用，另有成交旁路 | `src/console/fulfil/payments.py:66`、`:79`；`web/src/ui/DealFulfil.tsx:24` | pending 不成交；确认后写人/时间，定金成交，重复确认不重复入账；不自动查银行。币种差异说明和报价轮选择尚未在表单暴露 | 阻塞成交：旁路与确认歧义见 WG-02 |
| 下单、供应商确认交期、备货 | 部分可用 | `src/console/fulfil/nodes.py:13`、`:31`；`web/src/ui/DealFulfil.tsx:89`；`src/console/deals/rules.py:47` | 有节点、期限、责任类型；不是采购订单。每案每类节点只有一条，不能拆两家供应商分别备货；界面没有具体单位选择，付款后模板不再新增 | 拖慢，阻塞拆单跟进 / WG-10 |
| 验货 | 部分可用 | `src/console/fulfil/nodes.py:48`、`:92`；`web/src/ui/DealFulfil.tsx:117` | 完成须填结果，能提示整改/阶段建议；页面把 reason 读为 text/message，建议原因不显示；已成交案子没有正常推进按钮 | 拖慢，阻塞界面阶段推进 / WG-10 |
| 发货、在途、单据 | 部分可用 | `src/console/fulfil/nodes.py:19`；`web/src/pages/DealPage.tsx:75`；`web/src/ui/DealFulfil.tsx:89` | 可记提单号、ETD/ETA/实际离港、在途；不自动查物流或生成/发送提单、发票、装箱单；不能通过当前案子按钮顺畅推进 shipped | 拖慢 / WG-10 |
| 签收 | 部分可用 | `src/console/fulfil/nodes.py:56`、`:92`；`src/console/fulfil/reorder.py:13` | 签收节点可完成并给返单提供日期；阶段不会自动改，当前页面又缺推进入口；不能把记录日期当承运人回执 | 拖慢 / WG-10 |
| 售后、转厂家/货代、反馈汇总 | 部分可用 | `src/console/fulfil/issues.py:33`、`:134`；`web/src/ui/DealFulfil.tsx:140` | 有问题、追加记录、解决/关闭和安全的复制草稿；界面缺具体单位/产品关联及汇总。waiting_party 不在现有问题超时规则中，已转对方后容易失去提醒 | 拖慢 / WG-12 |
| 返单 | 部分可用 | `src/console/fulfil/reorder.py:55`、`:84`；`web/src/pages/FulfilBoard.tsx:37` | 30/60/90 天线索及复制接口存在，复制不带旧价；页面只有回旧案的链接，不能直接复制，也没并排展示上次反馈/供应商/售价 | 拖慢 / WG-12 |
| 每日成交结果与重点 | 部分可用 | `src/console/roles/brief.py`；`src/console/roles/service.py:254`；`web/src/ui/RolePanels.tsx:96` | 确认定金可进入简报；统计是期间新成交案子的当前已收净额，有报价兜底，不是期间现金流。已有简报/秘书不会随新消息自动重算 | 拖慢 / WG-11 |

## “谁在等谁”的专项结论

| 问题 | 当前答案与证据 | 不能据此推出的结论 |
| --- | --- | --- |
| 能否看到等客户、供应商、货代、配套方 | 能。`deals/waiting.py:25` 按参与方投影；案子页 `DealPage.tsx:88` 显示单位及角色，`roles/deal_summary.py:6` 给出人话和下一步。脚本分别检查供应商、货代、客户的 waiting_on | 不是自动识别“正在等报价/包装/交期/付款”的业务事项 |
| 能否看到等多久、该催谁 | 详情显示最后消息距今，秘书返回 since/waited_hours/elapsed；默认超过 48h 有巡检。两家供应商分别列出，但列表的小标签主要是角色与方向 | 等待从最后观察时间推导，反复催问会重置；同单位多会话只取最新一条，会盖住另一条未办事项 |
| 未知方向、读取失败和结束语 | `deals/waiting.py:41` 的最新 unknown 会变 none；`roles/common.py:137` 起的 last_known/observation 会跳过未知并处理结束语；读取失败也会变 none | 案子、收件箱、秘书可能给不同结论。“还没有关联会话”不能解释已关联但不可读/方向未知的情况 |
| 催的话怎么起草 | 收件箱有 make_prompt 的供应商采购口吻，案子里只有会话链接和建议，没有按未完成事项生成的催价按钮。forwarder/vendor 也未有独立提示词分支 | 秘书内部摘要含内部背景，不能直接拿去发给客户；起草不等于发送 |
| 多家报价能否并排 | I2 对整票运费可以；产品供应商只能分产品查看当前报价，谈判可保留多个角色，但没有同需求比价表 | 不能把报价轮的多产品行误认成同产品的多供应商可选方案 |
| SUP/CUS 编号是否明确 | `roles/common.py:25`、`:48` 提取 SUP/CUS 类型，清洗单位名；`directory/service.py:217` 返回 name_codes，但这些值是 SUP/CUS 标记，**不是数字编号**。案子保留 party_id、角色和会话，原群名可能仍带编号 | 数字编号不是案子/单位的独立可检索字段；群名本身不能证明身份、采集授权或唯一单位，不应自动按编号合并 |
| 付款确认是否顺 | 正向 pending→人工 confirm→paid/won 顺畅，accepted 报价不是成交；`payments.py:102` 首次定金/全款必然成交，即使 qualifies=0 | 页面可取消的“达到成交条件”勾选对首次定金并不起否决作用；改阶段更不能当核款证明 |

## 可直接派发的缺口任务，按严重程度排序

下列是后续任务规格，**本 P4 没有实施这些业务改动**。规模 S=约 1–3 个实现文件、半天至 1 天；M=约 3–6 个、1–3 天；L=跨模块、3–6 天，可按子项分派。均包括虚构单元/集成测试，不含真实数据验收、部署或等待 P1/W1 的时间。

共同验收约束：不得升级 archive.sqlite3 版本；优先使用已有结构，确需新持久状态时用本任务独立、可配置的 SQLite，默认放 `/var/lib/imgw/ops-app/`，单写者、事务和幂等。复用 v3 builder、宿主认证与 CSRF、中文错误、字段名审计；发送和收款仍由人确认，未知发送结果不重试。新字段名、表结构和 P1/P2 契约须在开工时重读实际源码确认，不照抄未落地的设计字段。

### 阻塞成交

**WG-01｜询盘入口的持续性、富消息与案子来源证明。**

- 证据：`channels/alibaba.py:17` 只消费已有入站投影；`docs/data-pipelines.md` 的阿里 v2 尚为双方契约。通讯录发现不代表消息已进入，见 `directory/service.py:229`。
- 文件/边界：由既有 P2b、W1、P3 负责人完成各自模块；集成负责人核查 `src/console/channels/alibaba.py`、`src/console/inbox/model.py`、`web/src/pages/Inbox.tsx`、`web/src/pages/Channels.tsx` 及其套件，不在 P4 重写接收器或采集器。
- 验收：虚构新消息带稳定消息 ID、时间和来源；旧指纹不变；附件前后乱序均能关联、重复只一份；断流原因和补救步骤可见；历史补采不冒充实时；从询盘建案能返回来源会话。真实 5/10 分钟 SLA 另由总指挥现场验收。
- 依赖：P2b↔W1 先对齐 v2，P3 消费其健康状态；不等待其他成交任务。规模 L，复用现有工作包。

**WG-02｜统一“付款才成交”，消除阶段旁路和确认歧义。**

- 证据：`deals/rules.py:10` 仅看阶段就写 won；create 和 update 均走此规则。脚本复现无款改 paid→won。`fulfil/payments.py:102` 又规定首次定金不受 qualifies=0 否决，页面 `DealFulfil.tsx:72` 却允许取消。
- 文件：`src/console/deals/{service,rules}.py`、`src/console/fulfil/payments.py`、必要的 `v3_deals_api.py`；前端由收款表单负责人改 `web/src/ui/DealFulfil.tsx`、`web/src/ui/ManualDeal.tsx`，补 deals/fulfil 测试。
- 验收：新案或付款前案子不能经普通 create/update 跳到 paid 及后续阶段绕过已确认付款；人工确认在同一事务入款、记操作人/时间并成交；接受报价、pending、退款不成交；重复 confirm 不重复记事件。保留“首次已确认定金/全款算成交”的规则时，界面明确说明且不提供无效的否决勾选；余额的显式达标选择仍有效。历史缺收款的 won 案只标待核对，不批量改历史。跨币种说明、关联报价轮能在界面核对，不能靠报错后仍找不到输入框。
- 依赖：无；WG-10 在它之后联调。规模 M。

**WG-06｜先修报价数量，再补客户需求确认。**

- 证据：`DealPage.tsx:193` 只手填名称/数量；`QuotePage.tsx:189` 新行固定 qty=1，`:93` 起只有售价编辑；后端 `quotes/service.py:73` 已支持数量更新、重算。
- 任务 A 文件：`web/src/pages/{DealPage,QuotePage}.tsx`；复用现有 `quotes/{id}/lines/{lid}/update`，不为这个修复新增库。验收：报价页可核对/改数量和单位；新增产品先填数量；24→30 后产品成本、自动运费、售价小计重算；已发轮不可改；未知数量不能因为默认 1 而通过发出检查。
- 任务 B 文件：在 `src/console/deals/` 内新增需求候选适配，复用 `inbox` 的受控输入与 `extract` 的确认方式；页面沿用案子产品清单，必要时增加 deals API，补 `tests/deals/`。验收：虚构中/西语询盘的产品、数量、目的地、规格及配套要求先出有来源的待确认候选；确认后才写案子；歧义留空，重复确认不重复产品项；没有身份或原文不能猜产品/包装/价格。
- 依赖：A 可独立先做；B 的语言服务依赖 P1，产品选择衔接 WG-05。规模 A=S，B=L。页面由一个前端负责人串行落改。

**WG-07｜运费选择、线路和承诺期限的核价边界。**

- 证据：`quotes/service.py:388` 对同一运价去重，对另一条新增；`:161` 比价按目的国筛选，`:375` 旧 cost 路径不核对目的国；`quotes/calc.py:15` 仅检查当天是否过期。脚本复现两条海运费累加、跨目的国成本仍 ready、客户期限晚于成本期限仍 ready。
- 文件：`src/console/quotes/{service,calc,validation}.py`、`src/console/v3_quotes_api.py`、`web/src/pages/{QuotePage,Prices}.tsx`，补 `tests/quotes/`、`tests/extract/`；沿用 I2，不修改已发布迁移。
- 验收 A：选择界面区分“替换同一海运方案”和“追加运输段”，替换须人确认且原子完成；同请求重复提交不重复费用/谈判/事件，失败保留旧选择。所有入口，包括旧 cost 入口，核对目的国、运输方式与可用性；未明确起运地/服务覆盖范围时写明待核对。不能把不同路线、运输段直接排成同类最低价。
- 验收 B：客户有效期超过任一成本有效期时有明确阻断或经总指挥确定的人工锁价确认；DDP 未覆盖税费/附加费不能被描述成全部成本已确认。外币运价只展示其原币结果；未实现换汇不得静默选入人民币成本。价格建议接受运价时可指定平台/外部来源，保留时效和来源；选择原因由人填写/确认，不把推荐标签冒充人的决策。
- 依赖：I2 当前接口已具备起点；WG-01 可供后续真实来源。规模 L，A/B 分工但 service/calc 与 QuotePage 各指定唯一合并人。

**WG-03｜把 P1 的母语确认接到报价文本和发送前检查。**

- 证据：`quotes/validation.py:103` 仅 en/zh；`quotes/text.py:5` 的语言分支没有 P1 查询；`QuotePage.tsx:57` 只有中英文按钮。当前快照没有可供本轮验证的 P1 完整实现。
- 文件：`src/console/quotes/{text,validation,service}.py`、`src/console/v3_quotes_api.py`、`web/src/pages/QuotePage.tsx`；只通过 P1 负责人最终交付的公共服务读取母语和人工锁定，不在报价模块另建语言表，不改 P1 所负责模块。
- 验收：虚构西语买家有母语报价、中文回译和来源/人工锁定提示；母语未知明确要求先确认，不能显示“已准备好发送母语版”；型号、DMX、单位、金额、有效期不被翻译改写；缓存随报价快照/目标语言变化失效；翻译失败留在草稿，不发送，不用英文静默兜底冒充母语。
- 依赖：P1 契约→本任务→WG-09 发送联调。规模 M。

**WG-09｜报价条款、PI、复制文本与实际发出的内容要对得上。**

- 证据：`quotes/text.py:25` 永远输出付款条件占位，`Quotes.read(text)` 的 ready 仅取 calc；`QuotePage.tsx:171` 允许在弹窗改文本但不保存，`:176` 的确认只是记状态。原 CPQ `src/console/routes/letter_routes.py:190` 属另一套报价 token，不能证明新案子轮次已有单据关联。
- 任务 A 文件：`src/console/quotes/{text,service,validation}.py`、`v3_quotes_api.py`、`web/src/pages/QuotePage.tsx`；单据所需新增状态用独立可配置 SQLite，不能升级档案库。验收：本轮确认的 30%/70% 付款条件、交付范围、有效期、销售额生成一致的客户文本和可预览 PI；缺条件/公司开票资料时说明缺项；修改后的可发送版本有来源和审阅记录，不把内部成本、货代报价或其他客户资料带入。
- 任务 B 文件：报价文本交接适配与 `web/src/pages/{QuotePage,Inbox}.tsx`，发送仍复用现有 outbox，不另造发送器。验收：选择正确客户会话后展示收件人、语言和最终版本，再人工确认；阿里/微信复制发送支持人工记录实际发出版本；WhatsApp 有结果/未知状态的明确区分。记录 sent 不冒充送达；未知结果零自动重试；编辑后的文本不能悄悄改变 accepted 轮的金额。
- 依赖：WG-03→母语版，WG-02→收款口径；B 与 P2a/P2b/W1 的已授权能力对齐。A 可先做中文虚构预览。规模 A=L，B=M。

**WG-14｜读取失败不能显示成“没有记录”，登记失败要能续办。**

- 证据：`DealFulfil.tsx:26`、`:89`、`:140` 的查询未呈现 q.isError，分别会显示空收款/空问题或隐藏履约；`FulfilBoard.tsx:12` 的返单查询错误也没有对应显示。`ManualDeal.tsx:50` 已有“案子建好请补完”的恢复提示，应保留。
- 文件：`web/src/ui/DealFulfil.tsx`、`web/src/pages/{DealPage,FulfilBoard}.tsx`、必要的 `ManualDeal.tsx`；避免和 WG-02/WG-10/WG-12 同时改同一组件。
- 验收：假 403/503、超时与真空列表分开呈现，说明哪项没读到、如何重新读取；不能因为读失败提示再次登记付款。已有案子/待核对款项保留可续办入口；POST 结果未知先读回核对，不能自动重放登记、建案、返单或发送。用假服务验证，不改业务状态来消除错误。
- 依赖：可先定统一错误组件，再由各页面负责人应用。规模 S–M。

### 拖慢成交或履约

**WG-04｜统一等待语义，支持对具体事项起草催问。**

- 证据：`deals/waiting.py:41` 与 `roles/common.py:137` 的方向/结束语规则不同；`roles/deal_summary.py:13` 只有建议，没有可发消息；脚本复现 unknown 覆盖供应商等待。
- 文件：`src/console/deals/waiting.py`、`src/console/roles/{common,deal_summary,patrol,inputs}.py`、`src/console/inbox/ai.py` 的公共起草适配、`web/src/pages/DealPage.tsx`；补 deals/roles 测试。若增加逐项等待状态，用独立 SQLite。
- 验收：已关联但不可读、方向未知、尚未关联分别显示；案子/秘书/巡检对同一组假会话口径一致；同单位跨会话未完成事项不被无关新消息覆盖；显示等待起点、上次催问时间、具体对象，催问不重置原事项等待；按客户/产品供应商/货代/配套分别起草，带本案已确认事实和人工补充项；禁止直接发送、禁止把秘书内部摘要变成客户正文。
- 依赖：基础方向修复可独立；事项化与 WG-06/WG-05 对接，母语起草接 P1。规模 L。

**WG-05｜同一需求的多供应商方案和 SUP/CUS 编号关联。**

- 证据：`archive/store.py:27` 的单产品 supplier_id，`quotes/service.py:56` 的每产品当前报价；`roles/common.py:50` 只保留 SUP/CUS 类别并清洗名称；`DealPage.tsx:96` 仅显示单位名和角色。
- 文件：在 `src/console/deals/` 增加只读方案投影，报价选择复用 `quotes/service.py`；`web/src/pages/{DealPage,QuotePage}.tsx` 增加对照。来源编号优先由 identities/原群名、已有 aliases 派生；需新增确认映射时用独立库，不改 parties/档案迁移。
- 验收：同一虚构需求列两家型号匹配、单价/币种/单位、MOQ、交期、包装、有效期和来源；不同规格不能默认算同款；选中后引用对应 offer，并保留选择理由；客户 CUS 与供应方 SUP 并列显示且可回单位/会话；不同单位同编号、一个单位多渠道编号都要求人核对，不据群名自动合并。历史报价快照不被重新选择改写。
- 依赖：可在人工需求上独立做，与 WG-06 的需求候选衔接；WG-08/WG-10 使用其单位选择。规模 L。

**WG-08｜配套与国内段费用关联到具体单位和询价来源。**

- 证据：`quotes/validation.py:13`、`service.py:375` 已接受 party_id；`QuotePage.tsx:237` 表单未传单位/描述；vendor 抽取生成的产品可能 supplier_id 为空，见 `extract/storage.py:283`，不能为方便改成 supplier。
- 文件：`web/src/pages/QuotePage.tsx`、必要的 `web/src/quotes.ts` 与只读来源投影；后端优先复用现有 costs/negotiations 接口，不重做费用计算。
- 验收：能选择具体配套/拖车单位、回看报价来源、人工确认数量折算的人民币总价、有效期；记录供应方协商时能指定具体单位；重新报价保持来源快照，改数量时明确提示人工总费用需复核；不会把线缆既计为产品成本又计为配套费用。测试两家 vendor，禁止静默把空单位配给第一家。
- 依赖：WG-05 的单位选择可复用，基础表单不等待。规模 M。

**WG-10｜已成交案子的阶段推进和履约责任人。**

- 证据：`DealPage.tsx:75` 仅 open 有下一阶段，won 的“重新打开”又会被 `deals/rules.py:27` 拒绝；`fulfil/nodes.py:92` 返回 reason，`DealFulfil.tsx:117` 读取 text/message；`web/src/fulfil.ts:8` 同样缺 reason。`deals/rules.py:47` 不给 won 生成模板，节点结构每案每类只有一条。
- 任务 A 文件：`web/src/pages/DealPage.tsx`、`web/src/ui/DealFulfil.tsx`、`web/src/fulfil.ts`，必要时调整 `deals/rules.py` 的模板策略。验收：确认定金后能人工推进 inspection/shipped/delivered/aftersales，保留 won/won_at；建议原因和确认按钮可见；验货通过不自动当发货；不提供会必然失败的 won→open 按钮；各节点能选具体单位、查看期限，待办与节点不会各维护一套互相冲突的进度。
- 任务 B 文件：若要两家供应商拆分备货，在 `src/console/fulfil/` 的独立存储适配和履约界面增加采购/验货分配，不升级档案库。验收：同案两家供应商各自数量、交期、备货/验货状态能分别记录、汇总；单据/发货通知先预览、再人工确认；没有真正生成采购单时文案明确“只记进度”。
- 依赖：A 在 WG-02 后联调，可先修 reason 类型；B 依赖 WG-05/WG-08。规模 A=M，B=L。

**WG-11｜每天的简报和提醒要说明新旧及金额口径。**

- 证据：`RolePanels.tsx:26`、`:97` 仅第一次 GET 出错时自动运行，已有旧结果只显示；`roles/service.py:254` 的简报不会自动调用巡检/秘书；`roles/brief.py` 使用当前净收款或报价兜底。
- 文件：`web/src/ui/RolePanels.tsx`、`web/src/pages/{Workbench,Roles}.tsx`、必要的 roles 只读新鲜度投影。不要在本任务自动安装 cron/systemd，也不要让“更新简报”自动对外发送。
- 验收：显示生成时间、来源和“新消息/收款后待更新”；老板可一次主动更新内部规则巡检与简报；AI 失败仍有规则结果和固定原因；卡片写清“新成交案子的当前已收”与报价兜底，不冒充订单额或现金流；虚构新增定金、退款、pending 和多币种可核对，重复内部运行复用。
- 依赖：WG-02 统一成交口径；与 P3 仅对齐健康状态，不重复写监控。规模 M。

**WG-12｜售后责任关联、等待责任方提醒及返单入口。**

- 证据：`IssuesPanel` 只提交责任类型；后端 issues 已支持 party_id/product_id/quote_line_id；`roles/patrol.py:54` 只提醒 open，不提醒 waiting_party；`FulfilBoard.tsx:37` 没有调用复制接口。
- 文件：`web/src/ui/DealFulfil.tsx`、`web/src/pages/FulfilBoard.tsx`、`web/src/fulfil.ts`、`src/console/roles/patrol.py` 及必要只读聚合；复用 `fulfil/issues.py`、`fulfil/reorder.py`，不复制内部价格到新案子。
- 验收：同案两家供应商的意见能选对单位、产品/报价行；只能选择本案相关记录；待对方处理也有准确的持续跟进提示，不能一转交就永久消失；能按产品/供应商看汇总。返单线索能预览上次接受售价、反馈和供应商，再人工创建 inquiry/open 草稿；不继承旧价/收款/履约/供应方身份，不自动发送；结果未知先核对新案，不盲目再建。
- 依赖：WG-04 的等待规则、WG-05 的单位选择；基础返单按钮可独立做。规模 M。

### 验证与交付环境

**WG-13｜补齐快照依赖和已有演练路由测试的隔离范围。**

- 证据：本快照缺 `src/console/media/`，但 `channels/wechat.py:7` 无条件导入；旧 directory/channels 演练挂载测试没有隔离后来新增的所有 builder。失败原文见下节。
- 文件：由总指挥在快照导出流程补齐同版本 `console/media/{__init__,tokens,files}.py`；核对 `tests/directory/test_api.py:244`、`tests/channels/test_whatsapp_rehearsal.py:17` 与 `scripts/rehearsal_server.py:53` 的动态列表，补应有的 fake builder 隔离。不是删除新路由来迎合旧断言。
- 验收：干净快照按约定命令可收集相关套件；CLI 子进程也有依赖，不靠父进程注入；演练路由测试只检查本套件负责的路由，且其他 builder 都不读取真实来源/写库；保持测试的真实断言，无网络、固定端口或真实渠道。规模 S–M，无业务依赖。

## 并行派发与合并顺序

| 工作流 | 可先独立做 | 依赖后再接 | 文件冲突与合并责任 |
| --- | --- | --- | --- |
| 渠道 | 已有 P2b/W1/P3，WG-13 环境 | WG-01 来源验收 | 这些模块由原负责人做，P4 不代改 |
| 成交 | WG-02 收款规则；WG-14 错误组件 | WG-10A、WG-11 | deals/rules 由收款负责人统一；DealFulfil 各补丁交同一前端负责人串行整合 |
| 产品需求 | WG-06A 数量表单、WG-05 只读方案 | P1→WG-06B 需求候选→WG-04 事项化催问 | DealPage/QuotePage 不并行覆盖；先定返回契约，再合前端 |
| 运费/费用 | WG-07A/B、WG-08 后端契约与测试 | WG-05 的单位选择可后接 | quotes/service、calc、QuotePage 各一个合并人；按数量→物流→配套顺序集成 |
| 客户报价 | WG-09A 中文条款与 PI 预览 | P1→WG-03→WG-09B 母语发送交接 | 不让报价开发者同时重写 P1 或 P2 发送模块 |
| 成交后 | WG-10A reason 类型、WG-12 返单按钮可先做 | WG-02→完整阶段推进；WG-04/05→售后责任方 | DealFulfil/FulfilBoard 统一安排 UI 合并；拆采购单另为 WG-10B |

各任务合并后先重跑本脚本的对应动态探针和相关套件；页面改动用 `npm run build`，浏览器演练与上线冒烟由总指挥统一安排。脚本中的静态缺口说明不会因为后端修复自动消失，必须逐条复核并更新；没有运行浏览器的步骤继续写“待界面验收”。

## 只读演示脚本与测试

新增 `scripts/workflow_walk.py`，无业务路径参数、无 --apply。默认只创建 `/tmp/fictional-workflow-*`，档案为内存 SQLite，阿里来源为临时 `id/payload` 库。使用已有 Store 的 v9 临时结构、事务、分拣确认、抽取确认、报价计算、付款和履约内部函数；**没有新增/升级真实 archive.sqlite3 版本，也没有新建持久业务库**。

假收件箱的 observe 只添加虚构观察，不是发送器。抽取/岗位 runner=None；采购提示词只做纯函数和假回答解析。媒体包缺失时用只满足导入的替身，任何签名/读取调用都会失败；脚本会单独打印“快照依赖检查：卡住”。这不算媒体功能验收，也没有从真实服务借用业务数据。

运行中的审计闸门禁止 socket、子进程、非临时 SQLite，以及临时目录外文件写入；生产业务文件不在所需读取范围。被业务函数吞掉的外部访问尝试仍会累计违规并使最终执行失败。测试另监视 Outbox.send、BridgeClient.deliver 和真实模型入口为零调用。它是本走查的防误接保护，不是可运行任意不可信 Python 的沙箱。

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/imgw/venv/bin/python scripts/workflow_walk.py
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/imgw/venv/bin/python scripts/workflow_walk.py --strict --json
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="src:tests/workflow_walk" /opt/imgw/venv/bin/python -m pytest tests/workflow_walk -q -p no:cacheprovider
```

本次默认演示：**30 项观察，7 项通过、23 项卡住、0 项执行错误**。卡住包括 3 项缺失、其余部分可用；主线通过人工补录/内部阶段接口继续至返单。默认退出 0 只表示演示完成；`--strict` 因有缺口退出 **2**；内部断言/隔离失败退出 **1**。`--json` 给结构化结果，不输出聊天原文、实际库内容、绝对临时路径或异常详情。

新增套件最终：**13 passed，0 failed**。覆盖主线及 30% 金额/历史轮次/8 节点、重复运行的确定性与清理、预期/未知错误输出、外部读写/SQLite/网络/子进程阻断、被吞掉的隔离错误、退出码、拒收已有库路径，以及媒体替身不可调用。

### 已有相关套件

当前进程身份为 imgw。尝试 `sudo -n -u imgw true` 失败，原文：

```text
sudo: /etc/sudo.conf is owned by uid 65534, should be 0
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
sudo: If sudo is running in a container, you may need to adjust the container configuration to disable the flag.
```

按约定直接用当前身份运行。裸命令先试 deals，因快照缺件在收集阶段失败：

```text
E   ModuleNotFoundError: No module named 'console.media'
ERROR tests/deals/test_api.py
ERROR tests/deals/test_service.py
ERROR tests/deals/test_shared.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!!
3 errors in 0.26s
```

为继续核对已有模块，只把 `/srv/imgw-dev/ops/src/console/media/` 的三个源码文件复制到 `/tmp/p4-runtime/console/media/`；没有复制业务库、配置、密钥或运行数据。临时 sitecustomize 仅补包搜索路径，另禁止真实网络、模型命令、非临时 SQLite 和生产写入。没有把依赖补进本仓库、修改原文件，或替换被测试业务函数。此前该临时闸门两次因漏放行 pytest 的 `/tmp` 临时句柄和 `/dev/null` 自己中止，原始异常均为 `AssertionError: P4 回归禁止生产写入`；修正的是临时测试辅助文件，未改产品/旧测试。

借用的源码来自上述开发副本，哈希见下表；没有 Git 元数据，不能证明它与快照属于同一提交。后面的回归结果仅证明该源码组合，不能表述成未补依赖的干净快照已全部通过。

回归各目录独立进程运行，避免旧测试同名导入冲突。实际命令模式如下，SUITE 依次取下表目录：

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="/tmp/p4-runtime:src:tests/SUITE" /opt/imgw/venv/bin/python -m pytest tests/SUITE -q -p no:cacheprovider
```

| 套件 | passed | failed | 额外 subtests passed | 说明 |
| --- | ---: | ---: | ---: | --- |
| deals | 32 | 0 | 38 | 补临时源码依赖后通过 |
| quotes | 34 | 0 | 189 | 含本地 I2 |
| fulfil | 167 | 0 | 0 | 收款与履约 |
| roles | 237 | 1 | 0 | CLI 子进程将 PYTHONPATH 改为仅 src，丢失临时依赖 |
| extract | 358 | 0 | 0 | 纯虚构输入/fake runner |
| directory | 188 | 1 | 22 | 旧隔离列表遗漏后来新增的 builders |
| archive | 40 | 0 | 46 | 仅临时库 |
| channels | 192 | 1 | 14 | 旧隔离列表遗漏 listing builder |
| outbox | 42 | 0 | 53 | 假发送服务 |
| inbox_api | 37 | 0 | 99 | ASGI/假来源，无监听端口 |
| 已有套件合计 | **1327** | **3** | **461** | 不把失败改算通过；无 skip |

连同新增套件为 **1340 passed、3 failed、461 subtests passed**。不能称“全量回归通过”。三项失败的旧源码/测试与开工哈希一致，本 P4 新文件不在这些旧套件的调用链中；失败定位如下，未越界修旧测试：

```text
FAILED tests/roles/test_roles_safety_cli.py::test_cli_subprocess_defaults_to_preview_and_requires_apply
1 failed, 237 passed in 25.27s
E   ModuleNotFoundError: No module named 'console.media'

FAILED tests/directory/test_api.py::test_rehearsal_automount_uses_throwaway_cache_and_disables_refresh
1 failed, 188 passed, 22 subtests passed in 5.60s
E           AssertionError: assert {'/api/v1/dea.../wechat', ...} == {'/api/v1/dir...chat/summary'}

FAILED tests/channels/test_whatsapp_rehearsal.py::WhatsAppRehearsalTests::test_automounted_routes_keep_real_session_writes_disabled
1 failed, 192 passed, 14 subtests passed in 6.59s
E           AssertionError: Items in the first set but not the second:
E           '/api/v1/listing/drafts/{id}/status'
E           '/api/v1/listing/products-ready'
E           '/api/v1/listing/drafts'
E           '/api/v1/listing/drafts/generate'
E           '/api/v1/listing/drafts/{id}/export'
E           '/api/v1/listing/drafts/{id}'
E           '/api/v1/listing/drafts/{id}/update'
```

roles 的 ModuleNotFoundError 来自其子进程 stderr（父测试显示 returncode=1），其余两项是路由集合多出了新模块。完整本次失败输出保留于当前会话临时文件 `/tmp/p4-runtime/{roles,directory,channels}.log`，不是持久交付依赖。没有运行 e2e_smoke、演练服务器、真实浏览器、固定端口服务或前端构建；本轮不改前端，不需构建。

## 证据基线、共享改动与交付

以下 SHA-256 固定本报告关键源码基线；行号对应本次快照。未来合入其他任务后请先比对，再按新源码复核。

| 文件 | SHA-256 |
| --- | --- |
| `src/console/deals/rules.py` | `b58c2b29b25591e4ed336d0d6afc71a193552d9cc855190fcfa7863275ff537e` |
| `src/console/quotes/service.py` | `8e161a26eb198fb4e6d7bcf61d507f6567538c2c98ec1da3d182b9c62f421784` |
| `src/console/fulfil/payments.py` | `1e397ee1dcb0979bd71872269477dc9c9b8c8ded4a88953f64d06d36e29e691a` |
| `web/src/pages/DealPage.tsx` | `6cbaa878d19b89620448ee9d2160f0de1dd0bf28cb071a833d884906681cfa90` |
| `web/src/ui/DealFulfil.tsx` | `b1b5c13ed587000325f66274ee5a0b79fbc7193758f612e067cc9baa5d6dcae9` |
| `web/src/data/requirements.json` | `a36acd402f537611f19c67608eab848d992966fabe03bbe0e8105830e6b24cc4` |
| 临时补齐依赖 `console/media/__init__.py` | `fb70aa748b35381454dc7145ae05e2fc6a92d88c625ebeb4889596dc6d42d75f` |
| 临时补齐依赖 `console/media/tokens.py` | `1f1c7c9550f53dc5e8b5435396d677ea78a8e8b48c799b81c1e86ec026f56337` |
| 临时补齐依赖 `console/media/files.py` | `135e1c6a1e26344a22a4e400909d5011d4d6d8d621f6e95b2fa4f72db7dbad20` |

本轮新增文件只有：`scripts/workflow_walk.py`、`tests/workflow_walk/conftest.py`、`tests/workflow_walk/test_workflow_walk.py`、`docs/workflow-gaps.md`。未修改任何原有文件。

**需要总指挥合并的共享文件改动：** `web/src/data/requirements.json:540` 的 H9 建议改为 partial，now 写“已完成虚构全流程走查并产出 workflow-gaps.md；模块可绕行至返单，付款旁路、核价边界、母语/PI、履约页面等仍有缺口；未做真实验收”，next 按 WG-02/06/07/03/09 优先派发。`:454` 的 G9 可补充“I2 源码前后端存在，虚构验证通过，未部署”，避免仍写没有横向比价。F1/F9/C4/C5 的旧描述与现有源码有差异，应由总指挥结合真实部署状态统一更新；不要因为本报告就全部标 done。README、master-plan 不在本轮修改范围。路由、导航 **零改动**。

部署：本交付仅分析文档和离线工具，**无需安装到生产、迁移库或重启服务**。总指挥审阅后把上述四个新增文件合入自己的开发副本；在完整源码快照运行脚本、新套件并处理 WG-13 的已有回归失败，再安排后续业务任务。这里没有生产补丁，所以无待覆盖生产文件的基线 SHA 或生产部署命令。后续任务各自提供其真实部署基线、备份和回滚，统一上线前的 e2e 由总指挥跑。

回滚：从开发副本移除本轮四个新增文件即可；脚本退出自动清理自己的临时目录，内存档案关闭。当前会话的 `/tmp/p4-runtime` 只是回归辅助目录，可由执行者清理；它不是产品依赖。没有生产库或业务状态需要回滚。

安全自查：无新增 HTTP 入口/权限，无网络或真实发送；无用户输入路径参数，临时路径由系统生成，SQLite 语句值均参数化，未执行消息/附件内容。未读取实际密钥、令牌、Cookie 或授权头；媒体替身禁用签名读写，借用的只是源码。仓库和输出均只含虚构主体/价格和源码证据，未知异常不透传详情。重复运行重建临时环境；分拣接受、价格接受、运价同条选择及收款确认的幂等性均实际检查。真实渠道未知结果的人工核对仍是后续交接要求，本脚本没有发送后的重试路径。

未完成的范围直说：没有修复任何 WG 业务缺口，没有替 P1/P2/W1 实现接入或翻译；没有生产状态、真实询盘、附件抽取、真实 AI 质量、实际付款、物流或浏览器验收；已有回归仍有三项失败。H9 的走查交付已完成，**H9 的“整条流程成熟可用”尚未完成**。

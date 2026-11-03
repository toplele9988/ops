# 产品与供应商档案（含货代运价）

供老板内部核对供应商、产品候选、采购报价与货代线路运价：先看来源，人工确认，再供后续计算使用。本轮交付数据层、同步脚本、接口及虚构数据测试。C1 对应档案/规格/交期与资源引用，C2 对应包装事实和确认保护，C3 对应运价与有效期；C4 的 FOB/DDP、倒挂计算及发品仍未实现。没有前端、生产挂载或部署，也不发送消息。

遵守本次明确范围，不修改 `web/`（包括需求账本）和已有 packing、supplier_data、kb、inbox、channels、outbox、media、inspiration、cpq、strobe 模块。以下状态是开发交付，不代表页面验收完成。

## 存储与事务

默认 `/var/lib/imgw/ops-app/archive.sqlite3`；`Store(path)`、API builder、CLI 均可注入路径。新文件在短暂的 `umask 0o077` 窗口内创建，随即恢复原 umask；数据库及已有 WAL/SHM 收紧为 0600。使用 WAL、外键、10 秒 busy timeout。首次写入执行迁移，GET 缺库只返回空档案，不创建文件。所有业务写事务使用 `BEGIN IMMEDIATE`，失败整体回滚。

`schema_migrations(version, applied_at)` 当前版本 9；重复迁移不重复建表或审计。未知版本拒绝写入。

| 表 | 内容与约束 |
| --- | --- |
| parties | supplier / forwarder 共用；同 kind 的 name 唯一；别名 JSON、简称、关联会话、备注、active/paused/archived |
| products | 可空 supplier_id（未知供应商不造档案）；名称、型号、分类、功率、描述、三边尺寸、重量、装箱数；candidate/confirmed/hidden |
| product_sources | product_id、equipment_spec/asset/chunk/offer/manual、ref、title、note；同产品同类型同引用唯一；不复制原文 |
| offers | 整数 price_fen、币种、单位、价格/税基、航空箱标志、起订量、交期、日期、corrects_offer_id、来源；suggested/confirmed/rejected |
| freight_quotes | 货代、来源渠道（platform_alibaba/forwarder/other，默认 forwarder）、起点、目的国/详情、方式、服务、计价单位、整数价格/最低收费、时效、日期、来源与备注；同样三种确认状态 |
| terms | 按 lead_time/moq/payment/shipping_origin/warranty/contact_note 保存条款及确认状态 |
| audit | UTC 时间、who、动作、表、行 id、字段名列表；不保存字段值、表单、自由备注、会话引用或金额 |
| product_merges | 额外的内部合并映射 product_id → into_id，阻止恢复/修改已合并源产品，以及循环合并；每次写入同样审计 |

每个业务行的插入、修改、合并去重删除各记一条 audit，与业务变化在同一事务提交。一个合并请求会产生多条审计；迁移也有一条独立记录。API 的 who 只取 `password_principal.username`，脚本默认 `seed_archive` / `sync_packing`。不提供删除档案或审计的接口。

`field_state_json` 是字段名到 `{state, source, at? , conflict?}` 的映射，state 为 extracted / confirmed / manual；API 将它解析为 `field_state`。人工更新（包括清空）写 manual；确认 candidate 产品会将已有的非 manual 字段标 confirmed。同步不会覆盖这两种状态。确认产品不会连带确认报价。

## 有效期与“当前”记录

所有日期按 `YYYY-MM-DD` 校验，today 可注入；缺省使用进程本地日期，接入时须配置业务时区或注入业务日期函数。

| 条件 | validity | 报价日期已到且当前记录 confirmed 时 |
| --- | --- | --- |
| valid_until ≥ 今天 + 7 天 | valid | 可以作为有效价格记录 |
| 今天 ≤ valid_until < 今天 + 7 天 | expiring | 可以，须提醒即将到期 |
| valid_until < 今天 | expired | 不可用 |
| valid_until 为空 | no_expiry | 不可用，提示「没写有效期，请向对方确认」 |

任务中第 7 天的两个区间有重叠，采用明确的 `≥ 今天+7` 规则，因此第 7 天为 valid，第 0–6 天为 expiring。到期当天仍有效。未来 quoted_on 的记录不会标为可用。

`validity.py` 的 `current_offer(offers)`、`current_quote(quotes)` 接收同一产品/线路的记录列表，只选 confirmed，排除被已确认更正记录取代的报价，然后按 quoted_on、id 降序取一条。suggested/rejected 更正不取代旧 confirmed。过期或无期限的最新记录仍是“当前”，不回退到更旧价格。更正仅允许同产品，不允许循环；freight 表没有 corrects 字段，运价按日期/id 选最新。

线路键为 `(forwarder_id, origin, destination_country, destination_detail, mode, service, unit, currency, channel)`，空详情规范为空字符串；按精确值分组，不跨货代、服务、单位、币种或来源渠道混选。

每条价格 DTO 带 `is_cost:true`、`is_current`、`validity`、`validity_notice`、`usable_for_quote`；非当前、未确认、过期、无有效期、未来报价均不可用。这个布尔值只判断价格记录，下游仍须检查产品确认、包装确认、单位、起订量和贸易条件，不能把它当作完整到手价的可用性证明。

汇总中四种有效期计数统计每产品/线路当前 confirmed 记录；suggested 单独统计全部待确认记录；拒绝和被取代历史不计入四档。产品报价汇总排除 hidden 产品。`packing_ready_products` 要求未隐藏产品具备外箱三边、毛重、明确装箱数；`packing_coverage` 仅要求外箱三边加毛重，分母为该供应商未隐藏产品数。两者表示字段齐备，不等于人工确认。

## 来源与种子

实现前只读核对了真实 catalog 列结构、包装报告 v2 字段、现有 packing/outbox 认证写法及收件箱会话 key。来源数据库均以 `mode=ro`、`query_only=ON` 打开，不使用 equipment_specs 的零散价格创建报价，不导入原文、聊天、evidence 或描述正文。报告读取上限 64 MiB，检查读取期间变更及消费字段结构。

```sh
python3 scripts/seed_archive.py --dry-run
# 独立测试/候选环境显式注入路径；去掉 --dry-run 才持久化
python3 scripts/seed_archive.py --catalog /path/to/catalog.sqlite3 \
  --packing-report /path/to/packing_report.json --archive /path/to/archive.sqlite3 --dry-run
python3 scripts/seed_archive.py --packing-report /path/to/packing_report.json \
  --archive /path/to/archive.sqlite3 --sync-packing --dry-run
```

`seed_archive()` 的来源与目标禁止相同路径、符号链接或硬链接别名。dry-run 在内存 SQLite 上执行同一流程；已有档案先只读 backup 到内存，因此可以报告增量。已 checkpoint 且无活动 WAL 时使用 immutable 只读打开，避免 SQLite 为只读预演创建空侧文件；活动 WAL 必须已有 SHM，使用普通只读快照以包含已提交 WAL。切换日志状态或无 WAL 快照期间发现变化时拒绝预演并提示重试，不凭过期快照汇报。

规则集中在 `archive/seed.py`，全部有虚构测试：

1. 供应商名称取 packing 的 supplier_coverage 与 cat_supplier.legal_name 的并集，同名去重；不建立“未注明供应商”。原名进入 aliases。简称依次去括号注释、常见城市前缀、公司后缀、行业尾词；过度清空时退回原名。
2. 只在注册表 `CHANNEL_ROLE_SUPPLIER`、`channel_kind=group` 中匹配简称互相包含（最短至少 2 字）。同 scope 取最新观测；唯一命中才关联，多命中留空。只保存 JSON `{channel:"wechat",scope:...}`，不将注册 scope 猜成收件箱 key。已有人工链接不被种子覆盖。已核对两处角色表，没有物流角色，不自动造货代。
3. cat_supplier_model 导入 candidate；cat_supplier_offer 导入 suggested，价格直接以整数分转入，空有效期保留。来源引用为 `cat_supplier_model:<id>` / `cat_supplier_offer:<id>`，更正映射到新库 id。旧表未给出计价单位，保留为“未注明”，不猜每台/每套。
4. equipment_specs 仅取非空 model，保存 category/wattage 与 equipment/asset 引用，不把其余数千碎片当产品。型号/名称上限 200 字，容纳已核对的真实型号字段长度；非法字段按原因跳过。
5. packing 候选必须有明确 carton_size 或 gross_weight_kg 候选，且 label 满足 2–60 字的型号模式。拒绝序号开头、句子/参数开头、公式、控制字符、句子标点；仅功率/尺寸单位数字（如“230W”）不算型号。航空箱尺寸不冒充外箱。
6. 产品键是 `(supplier_id, NFKC+casefold 后去空白/连字符/下划线的 model 或 name)`；优先按已保存来源找产品，故改名和合并后不会重新创建。跨供应商不自动合并。报价按 cat 表主键引用去重，已有报价不被来源变化静默改写，应新增更正记录。
7. 包装事实按去重后的产品汇聚全部候选。报告内任一冲突、跨条目同字段差异超过 2%、不同来源与现有 extracted 值冲突、越界数据都保守阻止整组写入，出处备注“包装资料有冲突”。发现新冲突会撤回旧 extracted 包装值，保护 manual/confirmed；冲突字段保留 extracted 状态和 conflict 标记，供后续同步恢复。备注作为历史线索保留。
8. `sync_packing_facts()` 只按已有 equipment_spec 来源关联产品，只更新状态仍为 extracted 的包装字段，不建产品、不填没有 extracted 状态的新字段；manual/confirmed 包括空值均跳过。完整 seed 可补充尚未提取的字段。

stdout 仅输出数字汇总：created/updated 的 suppliers/products/offers/sources，skipped 原因计数、conversation_matches；不打印价格、名称、正文、引用或异常字符串。原因计数的单位不同（输入记录、重复关联、字段），不能相加当作“跳过产品款数”。updated.products 是本轮包装写入次数，可能是本轮新建产品的后续补充。

## 接口接入与字段

`console.v3_archive_api.build(app_module, archive_path=..., service=None, today=None, conversation_lookup=None)` 返回 20 条 Starlette 路由，由宿主挂载。本轮没有应用主体/生产路由挂载。必须保留宿主认证及 CSRF 中间件，并仅向内部老板工作台开放这些成本数据接口；沿用现有 principal+legacy session 闸门，本模块不推测额外角色/老板用户名。

GET 返回前 recheck（包括错误），POST 读表单/任何写入前 recheck，I/O 在线程池执行。JSON 带 no-store/nosniff；403 沿用宿主 error_response 并补头；业务错误为固定中文 JSON，400 参数、404 不存在、409 状态/唯一性冲突、503 读写失败，均不回显路径或原异常。

POST 是 URL 编码表单，优先 `request.state.form`，回退 parse_qs；每字段仅一个文本值，最多 40 字段，8 KiB 上限。检查 Content-Length、已缓存正文、流式正文累计长度与规范编码后的表单大小；中间件预解析但既未保留正文也未给 Content-Length 时返回 400，避免仅凭解析后的文本接受无法核验原始长度的表单，不重读已消费的流。csrf 字段仅由宿主校验，本模块不存储。拒绝未知业务字段、非法 UTF-8、过长字段、重复字段。

分页默认 page=1、page_size=30，page 1–9999999、page_size 1–100；返回 `{items,total,page,page_size}`，先筛选再分页，越界页为空。文本 q 为不区分大小写的字面子串，%/_ 无通配意义。

| 路径（均在 /api/v1/archive 下） | 方法及参数 |
| --- | --- |
| /summary | GET；suppliers、forwarders、products:{candidate,confirmed,hidden}、offers/freight:{valid,expiring,expired,no_expiry,suggested}、packing_ready_products |
| /parties | GET kind/q/status/page/page_size；POST kind/name 必填，short_name/notes/conversation_ref/aliases 可选 |
| /parties/{id} | GET；基本字段、aliases、conversation_ref、products 状态数量、offers 计数、packing_coverage、terms 按字段分组、product_items 前 50 个未隐藏产品；货代附当前可用 freight_quotes |
| /parties/{id}/update | POST short_name/notes/status/conversation_ref/aliases |
| /parties/{id}/terms | POST field/value/source_ref，可空来源，直接 confirmed |
| /terms/{id}/status | POST action=confirm/reject，只接受 suggested |
| /products | GET supplier（整数 id）/q/status/packing=ready\|missing/price=valid\|expiring\|expired\|none/page/page_size；缺省排除 hidden；POST name 必填及产品可编辑字段，建 candidate |
| /products/{id} | GET；完整字段、field_state、sources、offers 全部历史、current_offer、validity、packing_ready、merged_into |
| /products/{id}/update | POST 产品可编辑字段，写 manual |
| /products/{id}/confirm | POST 空表单，candidate→confirmed |
| /products/{id}/hide | POST 空表单，置 hidden |
| /products/{id}/restore | POST 空表单，hidden→candidate，字段确认状态保留；合并源不能恢复 |
| /products/{id}/merge | POST into=目标 id；同供应商、目标未隐藏，合并 sources/offers，源置 hidden；目标规格保持原值，避免猜测冲突处理 |
| /products/{id}/offers | POST price/unit/quoted_on 必填；currency/valid_until/price_basis/tax_basis/includes_flightcase/moq_units/lead_days_min/lead_days_max/corrects_offer_id/source_ref；直接 confirmed |
| /offers/{id}/status | POST action=confirm/reject，只接受 suggested |
| /freight | GET forwarder（整数 id）/country（精确目的国）/mode/state=valid\|expiring\|expired\|none/page/page_size；未指定 state 时保留历史/建议，指定时只筛当前 confirmed；POST 运价字段与可选 channel，省略 channel 按 forwarder 写入 |
| /freight/{id}/update | POST 部分更新运价字段，可单独修改 channel；有效期和时效范围按合并后的整行校验 |
| /freight/{id}/status | POST action=confirm/reject，只接受 suggested |

parties 列表带 id/kind/name/short_name、products 状态数量、offers 计数、packing_coverage、notes 前 80 字。只有存在 conversation_ref 且注入的 `conversation_lookup(ref)` 返回有限非负 Unix 秒时才添加 last_activity；不配置、无法解析或回调失败时省略，绝不读消息猜时间。

products 的 `price=none` 包括没有 confirmed 当前报价及当前 no_expiry；freight 的 `state=none` 表示当前 no_expiry（没有运价的线路不会凭空出现）。列表当前价格也使用成本 DTO；不提供任何客户公开报价接口。

产品编辑白名单：supplier_id、name/model（≤200 字）、category（≤100）、description（≤2000）、wattage（0.01–100000）、product_l_cm/product_w_cm/product_h_cm、carton_l_cm/carton_w_cm/carton_h_cm（2–300）、gross_kg/net_kg（0.05–500）、units_per_carton（整数 1–10000）。空可选字段清空并保护为 manual。supplier_id 只能指向 supplier。

运价 POST 必填 forwarder_id/origin/destination_country/mode/service/unit/price/quoted_on；可选 destination_detail/currency/min_charge/transit_days_min/transit_days_max/valid_until/source_ref/notes，直接 confirmed。mode=air/sea/express/rail/truck；unit=kg/cbm/ticket/container；forwarder_id 必须指向 forwarder。国家按输入精确存储，不调用外部地区库，不猜国家代码。

金额输入 price/min_charge 是元的小数字符串，最多两位小数；通过 Decimal 转整数分，允许 0 至 9999999999.99。拒绝科学计数法、浮点值、表达式、多余小数位，`0.1`+`0.2` 对应 10+20 分后输出 `0.30`；字面 `0.1+0.2` 是非法表单值。API 只输出 price/min_charge 两位小数字符串，不输出 *_fen 或浮点金额。currency 为三位大写字母，缺省 CNY。

报价 unit ≤40 字；price_basis/tax_basis、路线/服务文本 ≤200；source_ref ≤500；notes/terms.value ≤2000；parties.name/short_name ≤500；别名最多 30 条、每条 ≤500；会话引用接受现有 decode_key 校验通过的不透明 key，或严格 `{channel,scope}` JSON。交期/时效整数 0–3650，最小值不能大于最大值；moq_units 1–1000000；includes_flightcase 0/1；有效期不能早于报价日期。

## 局限与验收边界

- 名称缩写、型号识别和匹配均为保守规则，可能漏掉真实型号或匹配不到群；候选仍需人工核对。空供应商候选保留 null 归属，不能据此猜厂家。来源字段从目录按原义导入，不证明原目录内容正确。
- 没有自动读取/抽取货代聊天运价、图片内容、合同条款或支付条件；目前货代与运价、条款由人工输入，suggested 状态保留供后续抽取流程使用。资源只关联 asset 引用和文件标题，未实现图片/视频媒体解析与展示。
- 不做单位换算、汇率、阶梯价、多个包装方案、关税或运费计算。运价的货代/地点/服务拼写不同时不会自动合并。当前报价不按币种或起订量挑选，多币种/数量场景需以后明确模型。
- 列表当前以本地只读快照在 Python 内筛选聚合，适合本次小档案；大规模目录需要 SQL 聚合/索引分页优化。没有跨进程缓存。
- 隐藏可恢复为 candidate；合并无自动撤销，目标规格不被源覆盖，source 与报价历史保留可追踪。来源冲突备注保留为历史，人工修正后仍可见。
- 不承诺未运行的生产接口、浏览器、真实人工确认、真实模型或发品流程。真实数据只用于内存只读预演；所有自动测试均为虚构数据。

验收命令：

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/archive -p 'test_*.py'
PYTHONPATH=src python3 -m unittest discover -s tests/archive -p 'test_*.py'
```

同样双环境回归 packing_api、kb、inbox_api、channels、outbox、media、inspiration、web_app、supplier_data。缺 Starlette 时仅跳过接口测试；不安装依赖，不联网。

## 本轮真实只读预演

运行 `scripts/seed_archive.py --dry-run`，退出码 0；默认 archive 文件在执行后仍不存在。以下仅为数字汇总，没有保存真实价格或正文。

```json
{
  "conversation_matches": 2,
  "created": {
    "offers": 39,
    "products": 138,
    "sources": 460,
    "suppliers": 17
  },
  "dry_run": true,
  "skipped": {
    "packing_conflict": 35,
    "packing_fragment_label": 389,
    "packing_no_carton_or_gross": 5267,
    "product_existing": 97,
    "unknown_supplier": 1
  },
  "updated": {
    "offers": 0,
    "products": 94,
    "sources": 35,
    "suppliers": 0
  }
}
```

138 个候选来自 39 个目录型号、48 个非空型号规格和 148 个通过包装候选规则的输入，去重命中 97 次。35 条包装冲突保留出处备注，不把冲突候选数值写成规格。5267 条无明确外箱/毛重事实、389 条标签不符合型号规则，均不从包装路径新建候选；排除 1 个未知供应商占位名称。群名匹配 2 个，不推测货代角色。

## 双环境测试末尾原文

以下 20 次命令均退出 0。系统 Python 的缺依赖跳过如实保留；最终 archive 测试为 38 项。

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/archive -p 'test_*.py'
----------------------------------------------------------------------
Ran 38 tests in 2.692s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/packing_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 24 tests in 0.326s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/kb -p 'test_*.py'
----------------------------------------------------------------------
Ran 35 tests in 10.272s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inbox_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 37 tests in 1.921s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/channels -p 'test_*.py'
----------------------------------------------------------------------
Ran 44 tests in 2.409s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/outbox -p 'test_*.py'
----------------------------------------------------------------------
Ran 42 tests in 4.238s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/media -p 'test_*.py'
----------------------------------------------------------------------
Ran 25 tests in 0.288s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/inspiration -p 'test_*.py'
----------------------------------------------------------------------
Ran 56 tests in 2.256s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/web_app -p 'test_*.py'
----------------------------------------------------------------------
Ran 6 tests in 0.040s

OK
```

```text
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/supplier_data -p 'test_*.py'
----------------------------------------------------------------------
Ran 110 tests in 4.728s

OK
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/archive -p 'test_*.py'
----------------------------------------------------------------------
Ran 38 tests in 1.509s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/packing_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 24 tests in 0.000s

OK (skipped=24)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/kb -p 'test_*.py'
----------------------------------------------------------------------
Ran 35 tests in 8.695s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/inbox_api -p 'test_*.py'
----------------------------------------------------------------------
Ran 37 tests in 0.231s

OK (skipped=19)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/channels -p 'test_*.py'
----------------------------------------------------------------------
Ran 44 tests in 1.479s

OK (skipped=15)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/outbox -p 'test_*.py'
----------------------------------------------------------------------
Ran 42 tests in 4.270s

OK (skipped=12)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/media -p 'test_*.py'
----------------------------------------------------------------------
Ran 25 tests in 0.096s

OK (skipped=9)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/inspiration -p 'test_*.py'
----------------------------------------------------------------------
Ran 56 tests in 1.583s

OK (skipped=11)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/web_app -p 'test_*.py'
----------------------------------------------------------------------
Ran 6 tests in 0.000s

OK (skipped=6)
```

```text
PYTHONPATH=src python3 -m unittest discover -s tests/supplier_data -p 'test_*.py'
----------------------------------------------------------------------
Ran 110 tests in 5.793s

OK (skipped=6)
```


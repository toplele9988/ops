# M1 包装资料核对（第三轮可读性修正）

实现依据：[AI 运营系统设计](ai-operations-design.md)。本模块使用 Python 3.10+ 标准库，只读打开显式传入的 SQLite，生成本地 JSON、HTML 和中文追问草稿；不发送、不写库、不接入 CPQ。本轮仅用虚构文本和临时虚构 SQLite 验证，没有读取或验证真实业务库，没有联网、部署、commit/push，也未修改 `src/console/cpq/`、`src/console/strobe/`。

## 运行与报告

在仓库根目录运行：

```sh
python3 scripts/m1_packing_report.py --catalog /path/to/authorized-catalog.sqlite3 --out-dir private_out/m1
```

生成 `packing_report.json`、`packing_report.html`。HTML 直接在浏览器打开，不依赖服务器、外部字体、CDN 或其他外部资源；支持系统深浅色和窄屏布局，状态同时以文字和颜色展示。CLI 同时输出齐全、可估算、缺项及两类冲突计数。

- 总览使用“资料条目”，并明确说明：“从供应商文档、报价表和聊天中抽出的规格条目，含文档碎片，不等于产品款数”。所有排行、分页和追问均按条目表述，不计算去重后的产品款数。
- 总览、明细和筛选统一使用“可直接报价（资料齐全）”“可估算（须向供应商确认装箱数）”“资料不全”；两类冲突保留原中文原因。页面“资料不全”卡片仅统计 `partial + missing`；旧 JSON `summary.with_missing` 保留原口径，包含可估算、也可能与冲突重叠，命令行用“必需资料有缺项”表述该计数。
- “今天该怎么用”从仍有待追问项的供应商中，按覆盖率升序、同覆盖率按条目数降序、再按供应商名称排序，取前 3 家提示联系；另按疑似多型号条目数建议拆分核对。它只按资料覆盖情况排顺序，不包含采购额、交期、沟通进度或真实商业优先级。空报告提示先补资料；全部齐全时仍提示报价前人工核对。
- “供应商资料覆盖排行”维持覆盖率升序，同覆盖率按供应商名称排序。展示资料条目、有/无包装资料条目、覆盖率，以及可估算、可直接报价两列独立计数。至少一项有效事实即有资料，包含仅产品尺寸、仅净重或有冲突的条目；关键词命中、无效/被丢弃值不算有效资料。
- 明细默认显示已有部分资料、可估算和有冲突条目，每页 50 条。暂无包装资料的条目默认只在供应商维度计数；可切换资料不全（全部 / 已有部分 / 暂无资料）、齐全、全部状态或两类冲突。搜索同时匹配 `label`、来源文件名与供应商，不匹配技术编号；变更筛选/搜索回到第一页，翻页保留筛选条件。
- 条目名称按“型号 → 描述中首行有意义文字 → 灯种与功率”生成，名称下显示来源文件名与供应商。哈希编号不参与名称与追问。编号仅在默认收起的“技术详情”中缩短显示，可复制完整值；原文匹配片段、片段编号、内部可信度标记和来源类型也在该区查看。普通界面、筛选和表头不显示内部英文代码；来源文件名、型号等原始业务数据保持原文。
- 全部条目的轻量记录嵌入 HTML；仅当前页创建 DOM 行。重复字符串及草稿中的重复行通过字典共享，草稿由页面脚本还原到文本框。完整来源上下文只保存在 JSON，减少 HTML 重复体积。
- 每个供应商保留复制追问草稿，筛选和翻页不会改变草稿内容。条目标识使用“某某报价表.xlsx 中的「名称」”；缺文件名时注明未注明来源文件。暂无包装资料的条目合并为一条概括，最多展示前 8 条名称与来源组合，每个截至 100 字符，其余注明省略；其他待处理条目逐条追问，不删减冲突候选数值。齐全条目不生成追问。
- 数据文本统一转义；内嵌 JSON 通过转义的 `<template>` 文本承载，JavaScript 读取后只通过 `textContent` 等 DOM API 写入数据，不拼接数据 HTML。文件页面不可调用剪贴板时，尝试系统复制；仍失败则选中完整编号或草稿供手动复制。禁用浏览器脚本时可看排行和概览，明细及完整草稿需查看 JSON。

报告始终注明“抽取结果须人工核对，非已确认规格”。齐全不等于已确认报价，可估算必须携带假设警示。本报告不计算运费；使用前须核对单位、来源及三项是否属于同一包装方案。报告可能包含业务资料，默认建议放入已忽略的 `private_out/`。

## 输入与只读约束

代码使用 `Path.as_uri()` 编码路径，通过 SQLite URI `mode=ro` 和 `PRAGMA query_only=1` 双重限制打开，读取期间使用同一事务；无任何写库语句。缺库不会自动建库，缺表、缺列、重复/空 equipment_id 或重复的非空 multimedia_assets.asset_id 会给出明确错误；来源关联键重复时拒绝继续，避免候选被重复计数。原库不能与两个输出文件同路径，也不能是其符号链接/硬链接目标。

| 表 | 必需列 | 可选展示列 |
| --- | --- | --- |
| equipment_specs | equipment_id, supplier, fixture_type, wattage, exw_price, currency, asset_id | model, name, product_name, description |
| knowledge_chunks | chunk_id, equipment_id, text | — |
| multimedia_assets | asset_id, filename, source_type | — |

`read_products(db_path)` 返回条目字典迭代器，含上述元数据、`label`、`source_file`、`source_type` 和 `chunks: [{chunk_id, text}]`。通过 `equipment_specs.asset_id = multimedia_assets.asset_id` 做左关联，多个条目可来自同一个文件；无片段、无关联资源或无文件名的条目均保留。缺失的来源文件名、来源类型使用空字符串。按 equipment_id 归属、聚合片段，不跨条目合并。未注明供应商的条目单列一组。

`model` 非空且不是公式时优先作为名称，合并其中的换行和连续空白。否则逐行检查 `description`：规范全角字符，跳过空行、数字序号开头（如“6.光束角度：120°”）、纯数字/分隔线、表头及公式行（包括 `=_xlfn.DISPIMG(...)`）；清理竖线、数字单元格和已知表头（含功率单位），取第一行有意义文字。描述名称最多 36 个 Unicode 字符，超长取前 35 字加省略号。没有有效描述时使用灯种与有效正功率（如“光束灯 230W”）；常见英文灯种有中文映射，未知英文灯种使用“灯具”，缺功率则只显示灯种。旧 `name`、`product_name` 等字段继续保留在 JSON，但不参与该优先级；不再退回哈希编号。

来源表及关联列现在是明确的输入要求；旧库若没有这些表/列，会报出缺失项，不会静默生成缺来源的报告。该输入要求与 JSON 旧字段保留是两件事。

## 抽取、冲突与完整度

`extract_packing_facts(text, *, chunk_id=None)` 是无 I/O 的纯函数，返回 `PackingFacts`（list 的子类）。元素为不可变 `PackingFact`：kind、value、原样 raw、chunk_id、unit_inferred、confidence。尺寸 value 是有序厘米三元组，重量为 kg，装箱数为正整数。结果对象的 `dropped_out_of_range` 和 `dropped_ambiguous` 是本次调用的丢弃计数，无全局累计器；报告记录产品及总计数。本轮未修改抽取语法。

明确标签分别识别产品本体、外箱和航空箱；单独的“尺寸 / Size”归为产品尺寸，不用于运费尺寸。裸尺寸及无标签重量不猜测归类。重量必须有 kg/g 单位。NFKC 处理全角字符，同时保存原文偏移，raw 保留原始全角写法。

`aggregate_facts(facts)` 接收同一产品的事实，返回 kind 到 `AggregatedFact` 的映射。每组保留全部 candidates（包含相同数值的全部来源），没有自动选中的最终值。任意候选对的相对差以较小绝对值为分母；尺寸逐轴比较，不重排长宽高。阈值沿用微小浮点误差容限。外箱和航空箱是独立 kind，不跨 kind 比较。

| 同 kind 任意候选对的差异 | conflict | conflict_reason | 处理文案 |
| --- | --- | --- | --- |
| 每轴/数值相对差均 ≤2% | false | null | 无冲突 |
| 至少一轴/数值 >2%，所有差异 ≤30% | true | value_mismatch | 同一项数值不一致，需向供应商确认 |
| 至少一轴/数值 >30% | true | multi_model_suspected | 疑似一条资料混入多个型号，需拆分后再核对 |

2% 恰好不冲突；30% 恰好归为 `value_mismatch`。重量、尺寸、装箱数均按上述同类数值规则分类。若同组出现多种程度差异，`multi_model_suspected` 优先。该原因只是分流线索，不能证明供应商错误或实际混入型号；**两类冲突均阻止 ddp_ready 和 ddp_estimable，绝不自动择一**。

`assess(...)` 返回 `Completeness`。优先级为 `conflict` → `missing`（没有任何有效事实）→ `estimable` → `partial` → `ready`：

| 状态 | 条件 | ddp_ready | ddp_estimable | assumptions |
| --- | --- | --- | --- | --- |
| ready | 外箱或航空箱三边尺寸、毛重、明确每箱数量均存在，且所有 kind 无冲突 | true | false | [] |
| estimable | 外箱或航空箱三边尺寸、毛重存在，仅每箱数量未注明，且所有 kind 无冲突 | false | true | [每箱数量未注明，按每箱 1 台估算，须向供应商确认] |
| partial | 有有效事实，但不满足上述两档，且无冲突 | false | false | [] |
| missing | 没有任何有效事实 | false | false | [] |
| conflict | 任意 kind 有冲突，包括可选项和航空箱自重 | false | false | [] |

`ddp_estimable` 仅计“每箱数量未知”的估算档，与 `ddp_ready` 互斥，不是含齐全档的上位集合。不会在 facts 中填入假设的装箱数；`missing` 仍包含“每箱数量”。估算档追问固定提示：“请确认包装是否单台一箱，如不是请告知每箱数量”。疑似多型号产品要求“请按型号分别提供”，列出全部不同候选数值及待补字段，不询问从候选中挑选一个正确值；一般不一致则要求确认当前值及版本/批次。

完整度分数保持原算法：运费尺寸 30、毛重 30、每箱数量 20、可选净重和产品尺寸各 10；冲突项不计分。**分数不代替门槛**：缺装箱数但可选项齐备也可能得到 80 分，仍然只能为 estimable。尺寸或毛重缺失时不能估算；航空箱自重不能替代毛重。

## JSON v2 稳定字段契约

`schema_version` 继续为 2；第三轮仅增补来源元数据并调整 `label` 生成语义，旧字段、状态枚举及计分含义保留。下游继续按两个布尔值分别判断齐全/可估算，不能把总条目数当成产品款数。HTML 内部压缩编码不作为对外接口。

| 路径 | 含义 |
| --- | --- |
| products[].equipment_id | 原始唯一条目标识，保持不变，仅技术追溯使用，不作为业务名称或追问标识 |
| products[].asset_id | 源库资源关联键，读取目录库时保留；程序直接传入旧字典时仍按原输入保留 |
| products[].label | 人读名称：有效 model → 清理后的首行有效 description（最多 36 字）→ 灯种与有效功率；不拼接编号，不保证唯一 |
| products[].source_file | 字符串，multimedia_assets.filename；无关联/无文件名时为空字符串；旧字典输入缺失时也补空字符串 |
| products[].source_type | 字符串，multimedia_assets.source_type；无关联/无类型时为空字符串；保留源库类型代码，不强行映射为业务分类 |
| products[].model / name / product_name / description | 存在时保留原值；清理仅作用于 label，不改写源字段 |
| summary.total | 规格资料条目数，含文档碎片，不等于产品款数 |
| products[].facts[kind].kind | product_size、carton_size、flightcase_size、net_weight_kg、gross_weight_kg、flightcase_weight_kg、units_per_carton |
| products[].facts[kind].conflict | 同类候选是否超过 2% 容差 |
| products[].facts[kind].conflict_reason | null / value_mismatch / multi_model_suspected |
| products[].facts[kind].candidates[] | 完整候选列表，保留重复数值的各来源，无 selected/final 值 |
| candidates[].value | cm 数组 / kg 数值 / 每箱数量整数，保持输入轴顺序 |
| candidates[].chunk_id | 来源标识，保留传入类型；纯函数未提供时为 null |
| candidates[].raw | 原始文本中本次数值匹配片段，保留原始全角字符、单位和写法；不是整个 chunk |
| candidates[].unit_inferred / confidence | 单位推断布尔值；confidence 为 labeled 或 inferred，不代表业务确认或概率 |
| products[].source_chunks[] | 完整来源上下文，元素为 {chunk_id, text}，保留全部输入片段（含未命中的）；text 是未经改写的整个原文，可按候选 chunk_id 回查型号上下文 |
| products[].completeness | status、missing、score、ddp_ready、optional_missing、conflicts，以及新增 ddp_estimable、assumptions、conflict_reason |
| completeness.conflicts | 有冲突的 kind 列表；字段有候选但冲突时不列入 missing，而列在此处 |
| completeness.conflict_reason | 产品级分流原因：任意 kind 疑似多型号即归此类，否则有冲突时为 value_mismatch，无冲突为 null |
| summary.ddp_estimable | estimable 条目数；summary.by_status 同时含 estimable，零值也保留 |
| summary.by_conflict_reason | 两种原因各自条目数，互斥；同一条目两类都有时优先计入 multi_model_suspected；逐项原因仍完整保留 |
| summary.with_missing | 存在必需缺项的条目数；包含无资料和可估算条目，与冲突可重叠；旧计数保持不变 |
| supplier_coverage[] | 排序后的供应商列表：supplier、total、with_packing、without_packing、coverage_rate、ddp_estimable、ddp_ready |
| supplier_coverage[].coverage_rate | with_packing / total，取值 0–1，HTML 显示百分比；无产品时列表为空 |
| followups | supplier → 纯文本草稿；无任何待处理项时为空串 |
| rules | 两个相对差阈值、分母口径、抽取范围、单位推断参数、计分权重及体积重默认参数 |

`build_followups(products)` 仍接收带聚合 facts 的产品字典，返回按供应商分组的单个纯文本字符串。条目名称与来源文件用同一套规则生成，不引入哈希编号。报告按供应商分别调用，便于复制；不会发送。

## 显式业务参数

| 规则 | 当前值 / 行为 |
| --- | --- |
| 完整档必需项 | 外箱或航空箱三边尺寸、毛重、明确每箱数量 |
| 估算档假设 | 只在每箱数量未知且其余必需项齐备、无任何冲突时，显式警示按每箱 1 台估算 |
| 可选项 | 净重、产品本体尺寸 |
| 相对差阈值 | CONFLICT_TOLERANCE=0.02；MULTI_MODEL_TOLERANCE=0.30；严格大于才跨入下一档 |
| 重量范围 | 0.05–500 kg，含端点；越界丢弃计数 |
| 单边范围 | 2–300 cm，含端点；越界丢弃计数 |
| 缺尺寸单位 | 三边全部 ≥ INFER_MM_MIN=100 按 mm，否则按 cm；unit_inferred=true、confidence=inferred，仍须人工确认 |
| 装箱数 | 只接受明确正整数，没有另设上限 |
| 体积重除数 | DEFAULT_VOLUMETRIC_DIVISOR=5000，可传 6000 等；须按渠道确认，本报告不计算体积重或运费 |
| 计费重 | 独立工具 max(gross_kg, vol_kg)，不含承运商进位、最低计费重等规则 |

抽取、门槛、体积重常量分别位于 `packing_extractor.py`、`completeness.py`、`volumetric.py`；名称规则位于 `labels.py`；HTML、行动提示和分页编码位于 `report_html.py`。外箱与航空箱两种方案不会自动选择。电池/磁/液体信息未纳入本次指定的最小资料门槛。

## 验证结果（仅虚构数据）

要求的命令：

```sh
PYTHONPATH=src python3 -m unittest discover -s tests/supplier_data -p 'test_*.py'
```

覆盖原抽取语法、三档边界、可选项冲突优先、2%/30% 精确边界、逐轴/逆序比较、多候选来源、覆盖排序、追问合并/多型号/估算文案，以及第三轮名称优先级、公式/序号/竖线表头过滤、36 字截断、来源左关联、缺表/缺列/重复关联键报错、旧字段兼容、只读和输入字节保护、行动提示、转义与 6000 条体积。原始描述和编号仍在 JSON 中保留。

环境已有 Node 时执行实际内嵌 JavaScript，通过无外部依赖的 DOM 替身验证翻页、搜索名称/文件/供应商、状态筛选、空结果、页码重置、数据仅作为文本、完整编号复制及降级选中。测试逐条检查所有状态渲染后的普通页面文字，排除“技术详情”子树后断言不含 `ddp_ready`、`estimable`、`chunk_id`、`equipment_id` 等内部代码；另解析静态页面文字与表单标签。没有 Node 则相关项明确跳过，不安装依赖。本环境有 Node，这些检查均已运行。

本轮完整命令原始输出及体积实测见下（仅虚构数据）。

```text
.......................................................................................................
----------------------------------------------------------------------
Ran 103 tests in 3.671s

OK
```

| 6000 条虚构场景 | HTML 字节数 | 本次耗时 |
| --- | ---: | ---: |
| 24 家供应商、48 个来源文件、960 条有包装资料（16%） | 884,110 | 0.594 秒（含只读读取、JSON/HTML 生成与写入） |
| 全部可估算，旧字典输入、短编号、无来源文件 | 1,982,285 | 0.764 秒（构建报告与 HTML） |
| 全部可估算，64 位虚构十六进制编号、48 个来源文件 | 2,403,565 | 0.799 秒（构建报告与 HTML） |

三种场景均小于 **2,500,000 bytes（2.5 MB）**，均有自动测试门槛；时间不含造数，随机器变化。混合场景完整 JSON 为 7,370,935 bytes；分布为齐全 120、可估算 240、资料不全 5400（已有部分 360、暂无资料 5040）、冲突 240。这些数值仅说明上述虚构输入的表现，不代表真实数据实测或任意文本长度下的体积上限。

另有 144 条虚构资料预览：`private_out/m1-round3-synthetic/packing_report.html` 与相邻 JSON，均在忽略目录中。包含序号碎片、竖线表格和公式描述的虚构案例。未运行其他业务模块测试，不据此声明跨模块验收。

## 已知局限与取舍

- 疑似多型号是幅度阈值提示，包装版本、批次或装箱方案变化也可能触发；同类值接近的多型号又可能漏报。本轮不实现 AI 拆分、版本选择、自动配对或候选择一。保留 chunk_id、匹配原文和完整 chunk 上下文供后续拆分。
- 仍按 equipment_id 信任已有片段归属，不解析自由文本型号。不同型号各提供一部分字段但没有同类冲突时，仍可能假齐备/假可估算；人工应核对尺寸、重量、装箱数是否属于同一方案。
- 每箱 1 台仅是假设，可能不符合供应商实际；可估算不得被下游当作资料齐全。推断尺寸单位仍不单独阻止两档标记，须核对原文。
- 仅支持连续三边、阿拉伯数字与既有标签/分隔符，支持 mm/cm/m、kg/g。中文数字、英寸/磅、科学计数法、千分位、小数逗号、分散的长宽高、跨表格/图片/PDF、型号列继承、复杂口语未覆盖。
- 范围、第四边和显式单位歧义保守丢弃，不取端点；丢弃计数不等于全部未识别文本数。约定范围可能排除特殊产品。
- 覆盖率衡量识别到有效资料，不衡量资料正确或齐全。冲突计数按产品主原因分组，因此有多型号疑点的产品不会再次计入一般不一致，但其每项原因仍可查看。
- 名称是确定性展示规则，不能证明已识别出具体型号；序号开头的真实名称也可能被保守跳过，表头规则无法覆盖所有格式。同灯种同功率可能重名，来源文件帮助定位，但同文件内仍可能存在重名。本轮不据此做产品去重或统计款数；长文件名在无资料合并追问的例子中可能被截短。
- HTML 为达到体积目标省去完整 chunk 上下文，保留逐候选来源在技术详情中展示；完整上下文在 JSON。仍随产品标识、有效候选、供应商数量及非空追问长度增长，不能保证任意超长/极密资料都低于 2.5MB；本轮仅验证所列虚构规模；型号名保留原长，描述名称有 36 字上限。
- 自动测试涵盖 HTML 解析和实际 JavaScript 逻辑，DOM 替身不能验证浏览器布局、深浅色视觉效果、窄屏触控或真实剪贴板授权；尚未做真实浏览器人工验收或真实数据验证。

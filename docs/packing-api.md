# 供应商包装核对 JSON 接口

源码为 `src/console/v3_packing_api.py`，入口为
`build(app_module, report_path=REPORT_PATH, state_path=STATE_PATH)`，返回四个 Starlette
路由，由应用构建入口挂载。此变更只提供接口模块、文档和隔离测试，没有部署或修改生产应用入口。
除 Starlette 外仅使用 Python 标准库，不查询产品原库，不修改报告。

**“已催”只是人工记录“我已经去催了”。系统不会给供应商发送任何消息。**
追问接口也只返回报告中的草稿，供人工审核和复制。

## 来源、认证与错误

- 默认报告：`/var/lib/imgw/supplier-packing/packing_report.json`，读取
  `supplier_data.report.build_report` 生成的 schema version 2。
- 默认记录库：`/var/lib/imgw/ops-app/actions.sqlite3`，仅 POST 创建、追加记录。
- 每次请求须有 `password_principal.username`、可调用的 `password_recheck`
  及已认证的 `session`。GET 返回前再次在线程池调用 recheck，包括参数错误、缺文件和读取失败；
  POST 在读取表单及任何写入前先 recheck。失败交给 `app_module.error_response(request, 403)`。
- 与灵感接口一样，依赖宿主控制台中间件校验 CSRF；挂载时必须保留该中间件。
  POST 优先使用 `request.state.form`（字段映射到单元素列表），缺省使用 `parse_qs`
  解析 URL 编码的请求体，最多 16 个表单字段。
- 所有 JSON 响应含 `Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。
  参数错误为 `400 {"error":"中文原因"}`，未知追问供应商为 404。
  报告损坏、schema 不兼容、超过 64 MiB 或读写失败为 503，响应只含固定中文错误，
  不回显文件路径、原文或异常字符串。
- 报告不存在时，三个 GET 均返回 `200 {"ready":false}`；先校验请求参数。
  此时 POST 返回 503，不能在无法验证供应商时创建记录库。
- 同一个 `build` 的路由共享进程内解析缓存；使用纳秒 mtime、大小和文件身份检测替换，
  未变化时不重读 JSON。解析由锁串行保护，读取限制为 64 MiB + 1 字节，并检测读取期间的修改。
  文件删除、损坏或超限后不返回旧缓存。记录库每次读取，不随报告缓存。

## GET /api/v1/packing/summary

成功返回 `{ready, generated_at, notice, totals, suppliers, next_actions}`。
`generated_at`、`notice` 原样取自报告，`ready` 为 true。

| 字段 | 契约 |
| --- | --- |
| totals.entries | 资料条目数，含文档碎片，不等于产品款数 |
| totals.ready / estimable / partial / missing / conflict | 报告 `summary.by_status` 的互斥计数；partial 仅指已有资料但不全，不能用旧 `with_missing` 替代 |
| totals.multi_model / value_mismatch | 产品级两类互斥冲突计数；同一条存在两类时，疑似多型号优先 |
| suppliers[].supplier / total / with_packing / without_packing / coverage_rate | 报告的供应商覆盖统计，coverage_rate 为 0–1；至少一项有效事实就有资料，包括可选事实或冲突事实 |
| suppliers[].estimable / ready | 分别取 `ddp_estimable`、`ddp_ready`，两个独立档位 |
| suppliers[].last_contacted / contacted_note | 最近一次人工记录的秒级 Unix 时间戳、备注；无记录均为 null，时间戳可含小数 |
| suppliers[].priority | 从 1 开始的整数；数组按该顺序排列 |
| next_actions[] | 最多 3 个 `{supplier, reason, count}`，仅从报告草稿非空的供应商中选取 |

排序按 `without_packing` 降序、覆盖率升序、供应商名升序；
满足 `0 <= 当前时间 - last_contacted < 7 × 86400 秒` 的供应商整体排后，组内仍遵循同样顺序。
正好 7 天不再排后。行动原因优先写“X 条资料没有任何包装信息”，count 为无资料条目数；
如果没有无资料条目，则写“X 条资料的包装信息仍须补充或确认”，count 为该供应商非 ready 条目数。
近 7 天的行动原因追加“X 天前已催过”，X 为已过完整天数，当天为 0。
这沿用 M1“今天该怎么用”的资料缺口口径，不表示采购额、交期或商业优先级。

## GET /api/v1/packing/items

参数：`supplier` 精确匹配（空值为全部）、`status`、`q`、`page`（默认 1）、
`page_size`（默认 30，1–100）。page 为 1–9999999 的整数。供应商名、搜索词最多 500 字。
重复参数、非法状态、非正整数或超出限制返回 400；未知非空 supplier 返回 400。

status 可取 `ready | estimable | partial | missing | conflict`。
不传或空值返回 ready、estimable、partial、conflict，**不含 missing**；
只有显式 `status=missing` 才查无资料条目。q 对 label、source_file、supplier 分别做
Python `casefold` 后的纯子串匹配，不搜索技术编号；`%` 和 `_` 是普通字符，没有 LIKE 语义。
先过滤再分页，排序为 conflict → estimable → partial → missing → ready，同状态按 label 升序。
超出总页数返回空数组，不调整请求页码。

响应为 `{items, total, page, page_size}`，total 是所有匹配条目数。item 契约如下：

| 字段 | 契约 |
| --- | --- |
| id | 原 equipment_id，仅用于技术追溯 |
| label / supplier / source_file / fixture_type / wattage | 报告元数据；无 fixture_type / wattage 时为 null |
| status | 报告 completeness.status |
| status_text | ready：可直接报价（资料齐全）；estimable：可估算（须向供应商确认装箱数）；partial：资料不全；missing：暂无包装资料；conflict：资料有冲突 |
| conflict_reason | null、multi_model_suspected、value_mismatch |
| conflict_text | null 或报告同义中文原因：疑似一条资料混入多个型号，需拆分后再核对 / 同一项数值不一致，需向供应商确认 |
| facts.carton_cm / product_cm | 外箱尺寸 / 产品尺寸，有序 `[长, 宽, 高]` 厘米数组或 null |
| facts.gross_kg / net_kg / units_per_carton | 毛重 / 净重（kg）/ 每箱数量，number 或 null |
| n_candidates | 对象，按报告原 kind 给出候选条数，如 `{"carton_size":2,"gross_weight_kg":1}`；包括重复值的不同来源，也保留航空箱 kind 的数量；缺失 kind 不出现 |
| missing | 原 completeness.missing 中文必需缺项列表，如“外箱或航空箱尺寸（三边）”“毛重”“每箱数量”；冲突字段不当作缺项 |
| assumption | estimable 固定为“按每箱 1 台估算，须向供应商确认”；其他为 null |

每类有冲突的 facts 字段必须为 null，不从其候选中硬选；其他无冲突字段仍可展示。
无冲突时取报告中首个候选作展示值（候选可能在报告容差内略有差异），不是已确认最终规格，
不平均、不跨类推断，也不把估算的每箱 1 台写进 facts。
航空箱与外箱是独立类型；`carton_cm` 只映射 carton_size，不把 flightcase_size 改称外箱。
因此仅有航空箱的条目可以是 ready / estimable 而 carton_cm 为 null；
此精简接口没有航空箱尺寸和自重值字段，完整候选与来源需查看原报告。

## GET /api/v1/packing/followup?supplier=...

supplier 必填，返回 `{supplier, lines, text, last_contacted, contacted_note}`。
lines 是报告 `followups[supplier]` 草稿的 `splitlines()` 结果，含原有空行，
text 为 lines 用换行拼接。复用报告中以“来源文件中的名称”为条目标识的草稿，
不会插入 equipment_id 或哈希编号。齐全供应商可能有空草稿，此时 lines 为 `[]`、text 为 `""`。
supplier 不在 followups 中返回 404。记录字段含义与 summary 一致。

## POST /api/v1/packing/contacted

URL 编码表单：supplier 必填且须在报告供应商列表中；note 可选，最多 200 个 Unicode 字符。
已校验的宿主 CSRF 字段不会写入记录。空或未传 note 保存为 null；不裁剪非空备注。
重复 supplier / note、未知 supplier、过长备注均返回 400，且不会创建记录库。

成功追加一行，返回 `{ok:true, supplier, last_contacted}`。这不是发送操作，也不会更新报告文件。
库使用 SQLite WAL；首次在 umask 0o077 下创建数据库文件，数据库权限设为 0600；临时 umask 随即恢复。
表为：

```sql
CREATE TABLE supplier_followups (
    id INTEGER PRIMARY KEY,
    supplier TEXT,
    contacted_at REAL,
    note TEXT
);
```

不覆盖旧记录。读取按 contacted_at 降序、同时间按 id 降序，取每家第一条；
旧记录的供应商若不再出现在报告中，不会出现在 summary。
GET 以 SQLite `mode=ro`、`query_only=1` 打开；缺库或现有 actions 库中缺该表时按无记录处理，
不会为了读取而建库、建表。报告读取和 SQLite 读写都在线程池执行。

## 验证范围

`tests/packing_api/test_api.py` 使用 `build_report` 从完全虚构的产品、片段构造小报告，
所有 JSON、SQLite、WAL 文件都在临时目录。没有读取或验证真实报告、业务库或供应商数据。
测试使用 fake app_module，不启动线上应用；缺 Starlette 时跳过接口测试，不安装依赖。

```sh
PYTHONPATH=src /opt/imgw/venv/bin/python -m unittest discover -s tests/packing_api -p 'test_*.py'
PYTHONPATH=src python3 -m unittest discover -s tests/packing_api -p 'test_*.py'
```

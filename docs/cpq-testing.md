# CPQ 隔离测试

测试必须显式选择 `CPQ_TEST_MODE=synthetic` 或 `CPQ_TEST_MODE=existing`。合成模式创建临时的虚构产品、供应商、报价和图片，不依赖生产数据。使用 Python 3.12 和 Node.js，在项目根目录执行：

```sh
python -m pip install -r requirements-cpq-test.txt
CPQ_TEST_MODE=synthetic CPQ_TEST_PDF=skip PYTHONPATH=src python -m pytest tests/cpq -q
```

`CPQ_TEST_PDF=skip` 明确跳过真实 PDF 与函件测试，使用仅供测试导入的替身；两个依赖真实供应商前端资源的测试也会跳过。合成页面模板仅用于测试路由契约，不代表真实页面、图表、浏览器或 PDF 已验收。2026-10-26 在隔离环境的结果为 243 项通过、52 项跳过。

同一天在服务器的临时隔离目录，使用真实 WeasyPrint 和虚构数据分两批测试：常规项目 289 项通过、2 项跳过，37 款虚构商品的批量导出 4 项通过。真实 PDF、HTML、文本和图片打包在合成数据下经过测试；这不代表生产数据、真实前端资源或浏览器交互已验收。

如使用已授权的独立测试数据库，选择 `existing` 并同时设置 `XL_DB_PATH`、`MEDIA_ROOT`。测试先以只读方式将源数据库备份到临时目录，再测试副本；请勿将客户数据或生产配置提交到仓库。`CPQ_TEST_PDF=real` 使用真实 WeasyPrint，需要额外安装 WeasyPrint 及其系统依赖，并核验真实模板与资源。

可运行 `python tests/cpq/fixture_catalog.py --directory /path/to/empty-dir` 生成独立合成数据库；生成器不会覆盖已有数据库或媒体目录。只有对该合成库运行 `existing` 模式时，才设置 `CPQ_TEST_FIXTURE_SUPPLIER=1`；不要对业务数据库设置这一变量。

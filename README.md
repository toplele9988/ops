# 乐初 · 业务运营系统

面向外贸业务的运营工作台，目标是把客户沟通、产品资料、供应商、报价和订单整理在同一个系统中，并让 AI 基于可追溯的业务资料提供辅助。

## 仓库已有内容

- `src/console/cpq/`：产品报价计算、运费、报价保存及导出相关逻辑。
- `src/console/strobe/`、`src/console/templates/strobe/`：频闪灯产品展示。
- `src/console/routes/`、`src/console/static/`：相关路由与页面资源。
- `tests/cpq/`：计算、页面契约、导出和安全约束测试。
- `src/console/conversation_notes.py`、`tests/workbench/`：会话跟进状态与内部备注的存储逻辑及合成数据测试。仓库版本调整了数据库路径配置，与线上文件并非逐字相同；页面与渠道适配器仍需整合。
- `src/console/password_candidate/`：账号页面及兼容旧会话的实验性源码，使用合成账号测试。公开版本移除了自动创建默认账户的逻辑，与线上版本不同；缺少其他运行组件，不能直接部署。
- `src/console/owner_inbox.py`、`src/console/v3_owner_inbox.py` 及对应模板：多渠道会话查询、草稿关联和内部跟进页面的脱敏候选。来源适配器与应用主体尚未包含；真实草稿匹配需要显式配置账号及协议标识，未配置时不会匹配，不能直接用于生产。
- `src/whatsapp/bridge.py`：WhatsApp 事件归档与受控发送桥接的源码快照，配置与配对目录路径已参数化。历史补采、媒体解析、渠道回执及完整工作台集成仍需验证；不能凭源码认定收发已完整接通。
- `examples/icbu/`：阿里接收与草稿的脱敏参考快照和独立演示；参考快照缺少现网依赖并禁用直接启动，演示与线上协议不完全相同，详见目录说明。
- `examples/gmail/`：Gmail 授权、同步、重试与状态的匿名开发候选和合成测试；尚未连接真实邮箱或接入运营工作台。
- `deploy/`、`tasks/`：早期部署补丁与项目记录，保留用于追溯；不能代替对现网部署状态的核验。

这些模块来自已有项目历史。本仓库当前并不包含运营平台的全部现网源码，也不是一键部署包。继续整合时应先对照运行版本、补充依赖与测试，不能直接覆盖线上文件。

## 运营系统建设方向

系统按客户工作台和系统管理两部分组织。微信、阿里国际站、WhatsApp、邮箱和其他渠道通过各自适配器接入；聊天、文件及其提炼结果存放在服务端业务数据库与知识库中。不同渠道的收取、历史补采、文件解析与发送需要分别实现和验收，存在代码或页面入口并不代表已接通。

AI 可以辅助整理客户需求、检索产品信息、生成回复和报价草稿；涉及价格、交期与规格时应保留来源，便于业务人员核对。后续平台可以新增适配器，不应依赖某个渠道特有的会话标识。

界面设计方法、已应用的检查与待验收事项见 [界面开发记录](docs/ui-development.md)。

## 运行资料与保密

本仓库面向源码和合成测试数据。真实客户聊天、供应商报价、订单、附件、数据库、会话、密码及 API 凭据应保存在运行环境，不纳入版本控制。公开仓库中的配置数值需要单独核实是否为可公开示例；删除文件不会自动移除 Git 历史。

在变更线上服务之前，应备份当前版本，并使用隔离环境验证；对接真实业务渠道时避免以客户为对象发送测试消息。

## 运行工作台模块的单元测试

使用 Python 3.10 或更新版本，在项目根目录运行：

```sh
python -m pip install -r requirements-workbench-test.txt
PYTHONPATH=src python -m unittest discover -s tests/workbench -p 'test_*.py'
PYTHONPATH=src python -m unittest discover -s tests/workbench -p 'inbox_fixtures.py'
PYTHONPATH=src python -m unittest discover -s tests/whatsapp -p 'test_*.py'
```

测试只使用临时 SQLite 数据库，不需要线上配置。CPQ 测试可选择虚构数据库或明确指定已授权的测试数据库，详见 [CPQ 测试说明](docs/cpq-testing.md)。

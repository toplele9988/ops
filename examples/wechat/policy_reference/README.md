# 微信知识整理与采集策略：真实源码脱敏参考

`company_knowledge.py` 和 `collection_policy.py` 从现网源码只读生成，保留主要函数和类，用于审查代码结构；**不是可部署版本，也不应接入生产配置或数据库**。快照分别保留 53 和 25 个函数/类，源文件和参考文件的哈希在 `snapshot-manifest.json`。为了避免误运行，新增默认拒绝导入或直接执行的审查闸；测试只提取被选中的纯函数 AST，不运行模块初始化或依赖模块。

## 已做的脱敏及行为差异

- 移除全部源代码注释与文档字符串，并重新格式化；不保留逐字节一致性。
- 公司 ID、公司名称、业务群名称和提供商主机改为 `EXAMPLE_COMPANY_ID`、`EXAMPLE_COMPANY_NAME`、`EXAMPLE_GROUP_NAME`、`EXAMPLE_API_HOST` 及虚构默认值；原公司介绍和业务描述改为虚构文案，授权指令标签泛化。
- 原配置路径替换为 `EXAMPLE_WHATSAPP_CONFIG`；没有提供生产默认路径。默认没有 `EXAMPLE_POLICY_AUDIT_ONLY=1` 时，两份参考代码会直接拒绝执行。
- 以上调整会改变身份和白名单匹配、固定公司介绍以及配置读取，因此不能把脱敏后的产物当作现网授权规则或数据。完整系统仍需要另行设计示例配置，不能拿占位值对接现网。

逐处替换清单：

| 文件与位置 | 脱敏后的值 |
|---|---|
| `company_knowledge.py` 的 `KNOWN_WHATSAPP_CONFIG` | 必须提供 `EXAMPLE_WHATSAPP_CONFIG`，无生产默认路径 |
| 该文件的 `_seeds` 和 `_visible` | 公司 ID 与名称分别使用 `EXAMPLE_COMPANY_ID`、`EXAMPLE_COMPANY_NAME` |
| 该文件的 `_seeds` | 两条固定的公司和业务介绍替换成合成文案 |
| `collection_policy.py` 的 `public_selection` | 第三方 API 主机与业务群名称分别使用 `EXAMPLE_API_HOST`、`EXAMPLE_GROUP_NAME` |
| 该文件的 `public_authorization_record` | 业务群专属授权指令标签替换为示例标签 |

`snapshot-manifest.json` 同样记录这些位置、源文件和脱敏文件的摘要及默认审查闸。环境变量均为示例接口，没有为用户写入实际配置。

现网两份源码的常见邮箱、私网 IP、群 ID、路径和凭据赋值模式已逐项扫描；还发现需要替换的公司与群身份及内嵌业务描述，已在候选中泛化。正则扫描和 AST 去注释并不能保证识别所有敏感内容，已进行常见凭据及隐私模式扫描；使用前仍须结合完整依赖及许可证核验。没有复制真实消息、客户资料、配置、密钥或数据库。

## 依赖与未实现范围

`company_knowledge.py` 依赖 `collection_policy`、`company_note_assets`、`company_reference_media`、`group_lifecycle`、`owner_note_assets`、`owner_work_sharing`、`supplier_source_sync` 和 `wechat_note_reader` 等内部模块；`collection_policy.py` 还依赖 `group_lifecycle`、`wechat_outbound_queue`、`wtapi_client` 和 `wtapi_events`。这些依赖、运行配置、SQLite schema、现网回调、授权上下文与模型均不在此目录。本次没有以伪造实现冒充这些生产依赖。

尤其应注意：采集策略源码包含对外回复准备和队列分发代码。保留它是为呈现真实工程结构，不代表已开启自动发送，也不代表本候选经过发送授权或可实际发送。未验证真实群成员授权、跨账号映射、知识库写入、检索、模型调用、媒体处理或渠道回执。

## 合成对照测试

```sh
FIXTURE_IS_SNAPSHOT=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_policy_subset test_snapshot_safety
```

快照在本机和目标服务器临时隔离目录各通过 12 项检查：10 项纯函数/虚构身份与策略测试、2 项审查闸和路径检查。目标服务器上，针对现网原代码的相同纯函数 AST 提取运行 10 项，其中 9 项通过、1 项因原代码绑定真实身份而按设计跳过；公共合成输出摘要与快照一致。测试覆盖标识及时间校验、显式采集开关、群聊静默视图、无分类媒体准入、知识分类、合成文本去敏和来源 ID 稳定性；快照额外验证虚构群和主机的匹配及不匹配。

这只是函数子集对照，并非整模块导入、端到端授权或生产行为验收。测试不加载真实业务模块、不访问真实消息/凭据、不发网络请求、不发送消息，也不修改生产或 staging；临时目录已清理。

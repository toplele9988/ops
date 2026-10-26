# Gmail 同步模块：匿名审查候选

**这是未上线的开发候选，真实邮箱尚未连接。** 代码和测试使用虚构邮箱、模拟网络响应及临时数据库；不能用于发送邮件。它不是完整运营系统，也没有实际附件解析、知识提炼或工作台集成。

## 内容与状态

- `gmail_candidate/`：只读 Gmail 同步、OAuth 处理、访问令牌刷新和加密凭据容器，以及隔离测试。
- `gmail-lifecycle-candidate/`：暂停、核验后恢复、受限重试、持久同步回执、可选的本地演示页面与测试。演示仅使用假 transport，不代表真实 Gmail 已连接。
- 合成测试可检查与预期账号不符时不读取邮件、同步断点保存、失败队列重试和状态恢复；真实授权、回调、服务部署和业务数据处理均未验收。

资料库和状态文件的位置由操作者在配置中显式设置 `private_dir`，内含 `gmail.sqlite3`（消息与断点）、`lifecycle.sqlite3`（同步状态与回执）及加密授权文件。示例使用无意义的占位路径和 `example.test` 邮箱；不要把真实配置、数据库、凭据、聊天、邮件或主密钥加入仓库。**消息数据库当前为明文**，只有 OAuth 客户端/刷新凭据使用 AES-GCM 容器；这不是生产数据加密完成的声明。

## 运行测试

在隔离环境准备 Python 3 和测试依赖，并根据环境提供可用的本地回环端口。只运行使用虚构数据的测试：

```sh
cd gmail-lifecycle-candidate
python -m pip install -r ../requirements-test.txt
PYTHONPATH=../gmail_candidate python -m unittest -q \
  test_recovery_review test_lifecycle test_runtime test_gmail_sync \
  test_gmail_transport test_gmail_oauth test_encrypted_vault
```

本次隔离环境 **75 项通过**；一项测试运行中产生 Python 多线程进程 fork 弃用警告。测试中回环 HTTP 服务器仅服务虚拟资料，不请求 Google 或真实邮箱。

## 真实接入前仍缺少的工作

1. 客户端配置的安全导入、Google OAuth 授权、正确的运行环境与目标账号核验；`GMAIL_EXPECTED_ACCOUNT` 必须由受信任部署者显式设置，且与配置账号和 Gmail profile 均匹配。环境变量在这里仅存预期邮箱标识，不存密码或令牌。该校验不能防止部署者把两个配置同时填错，部署验收必须核实实际账号。
2. 主密钥生命周期、备份及 systemd credential 的所有权/路径验收；刷新凭据加密不能代替对邮件正文、回执数据库的完整保护。暂停会等待当前轮同步结束，长任务的取消体验尚未完成。
3. 失败队列超过重试次数后需要清晰的人工处理入口；已有独立回执不含正文。附件、知识库、写信发送、统一登录和线上告警都需独立实现及真实验收。

不要直接运行服务模板或把候选当成生产部署说明。运行 `service.py` 前还要完成认证入口和上述配置，当前副本未提供客户端配置导入功能。

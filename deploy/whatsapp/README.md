# WhatsApp bridge 视频发送补丁

基线是现场只读文件 `/opt/imgw/whatsapp/bridge.py`，SHA-256：
`0f23743b95e5feea6277504c8caa8aeddae816a25a7c78f600a2c2b151016a69`。
补丁仅将 `sendVideo` 加入原有鉴权路由与发送类型白名单，沿用原有显式发送人、收件人绑定、
20 MiB 上限、持久幂等键和未知结果不重发。它不增加自动发送或 webhook 下载。

WAHA 当前公开文档列出 WEBJS 的 `POST /api/sendVideo`，文件结构见
[发送文档](https://waha.devlike.pro/docs/how-to/send-messages/)。**2026.9.2 现场镜像的请求结构、
权限与结果仍待现场确认**，不可将此补丁的假服务测试当作现场验收。

已对该基线运行 `patch --dry-run -p1 -d /opt/imgw/whatsapp < deploy/whatsapp/bridge-send-video.patch`，
结果通过；测试仅在临时副本应用补丁并注入假 WAHA，不启动现场 bridge 或发送消息。

## 由总指挥部署

1. 保持后台发送总开关关闭；先确认现场文件 SHA-256 与上述基线完全一致，确认部署窗口与 bridge 服务的准确名称。
2. 将现场 bridge 文件按原权限、属主和时间备份到受保护的生产备份位置；保存备份的 SHA-256。
3. 先对现场文件执行上述 dry-run；若基线不符或 dry-run 失败立即停止，重新审查补丁。
4. 在已备份文件上应用补丁，核对 diff 只包含预期三处；编译检查后按现场服务管理方式重启 bridge，并核对路由与运行状态。
5. 保持发送关闭，先由总指挥完成真实数据只读演练及规定的全页面冒烟；只有现场接口和操作流程均核实后，才由老板决定是否开启。

## 回滚

关闭后台发送开关，停止 bridge 服务，恢复第 2 步保存的文件及原属主、权限，核对备份 SHA-256，
再启动服务并检查状态和原有路由。不要清空或回滚 outbox/bridge 数据库：已有结果不确定记录仍须保留，
由人工在 WhatsApp 原渠道核对，不能自动重试。

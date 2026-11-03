# queue 挂载候选（未应用、未部署）

沿用 patches/overview 的独立 import/build 两行挂载。补丁放在本模块内，遵守本任务文件范围。不直接覆盖宿主文件；无数据库升级和数据回滚。

`baselines.sha256` 中生产 SHA 仅复制自仓库 `docs/r7f/production-code-baselines.sha256`，本任务未读取生产目录、没有当前生产源码副本，不能声称当前基线匹配。维护者应在自己的部署候选副本上核对 SHA；不同则重新取基线、核对路由上下文、重新生成补丁。本轮仅验证生产补丁能在匹配的虚构上下文上无模糊应用，不是当前生产适用性验证。演练补丁基于当前 workspace 文件 SHA，在临时副本上验证。

生产补丁适用于已挂载既有 pipeline/deals/inbox/fulfil/overview 模块的宿主；宿主保持既有登录、复核与 CSRF 门。候选新增 `GET/HEAD /api/v1/ops/queue`，没有 POST。部署前备份宿主和旧前端构建；先 dry-run，再应用补丁，核对路由唯一。默认只读本地阿里与微信记录、archive、overview、pipeline；不创建 WhatsApp 桥接客户端，页面明确提示 WhatsApp 未汇入。如注入 `inbox=`，只允许已落盘或内存快照提供者，禁止用会触发桥接请求的 UnifiedInbox 直接装配。

演练补丁只读演练 archive 和临时 fictional inbound；所有省略的来源禁用，不退回真实来源。不运行历史生产 smoke 脚本，不复制构建到生产。

回滚：在同一候选副本 `patch --batch --fuzz=0 -R -p1 < production-mount.patch`（演练使用 rehearsal 补丁），移除两行挂载，恢复备份的前端构建；按既有服务发布流程重启并核对路由。新增纯只读模块可以保留，无业务数据需要撤销。浏览器本机反馈使用 `ops.queue.feedback.v1`，新手提示使用 `ops.queue.welcome.v1`，需要重置时仅清除这两个键。

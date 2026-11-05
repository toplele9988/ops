# 供应商档案增量 M2 交付记录

## 改动文件

- `src/console/dossiers/incremental/{__init__,__main__,schema,sources,model,project,worker,rollback}.py`：幂等增量迁移、rowid 游标、解析复用、严格字段证据、供应商内匹配、追加投影、异常、可恢复任务/预算、CLI、私有报告、精确退回第一批工具。
- `src/console/dossiers/service.py`：新增只读 updates()；缺口补齐覆盖层应用于查询和计数。原 supplier/family 报价已支持 quoted_on 与 source_id/逐字段证据，测试核对保留。
- `tests/dossiers/test_incremental.py`：全部虚构临时库与假 runner；禁止真实子进程模型调用。
- `tests/dossiers/conftest.py`：额外允许内存连接，用于 dry-run 快照；仍禁止真实数据库和网络。
- `tests/dossiers/test_release.py`：在临时代码副本中反向移除已安装的独立包，再核对原有固定 SHA256 基线。修复测试依赖 /opt/imgw 仍停留在 M1 前的问题；没有放宽原有哈希验证，没有修改生产文件或发布包。
- `docs/dossier-incremental.md`：流程、兼容约束、预算/成本假设、后续定时接入、回滚、环境限制。
- `INTEGRATION.md`：本记录。

没有修改 /opt/imgw、src/console/roles/*、src/console/operator/*；没有上线写库、新路由、定时器、消息发送或网络配置变更。当前工作副本的 .git 是空只读占位目录，无法提供 git diff/status；上述清单按本次实际编辑记录列出。

## 测试

命令：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/ops-deals-venv/bin/python -m pytest tests/dossiers -q -p no:cacheprovider
```

最终结果：**45 passed，1 warning，18.40 秒**。warning 是既有 Starlette/httpx TestClient 弃用提醒。没有跳过或放宽失败检查。CLI --help 与 Python AST 语法检查通过。

覆盖首跑尾部初始化、续跑与重放幂等、逐字摘录丢弃、未知币种/税率、supersedes 链、旧行不变、缺口补齐与读取覆盖、严格 >30% 及历史中位数、单窗口失败/退避、过期租约、整窗口投影回滚、dry-run 两库字节不变、预算停止与次日继续、未知用量保守记账、迟到附件文本、来源锚点变更、私聊供应商范围/身份隔离、长文本切分、难例免费模型、七天摘要、恢复第一批。

## 真实只读演练（2026-11-05 UTC）

线上侧库和微信库只使用 mode=ro/query_only。由于指定报告目录在当前沙箱为只读挂载，实际执行要求的 CLI 时报告可写预检未通过，退出码 1（incremental_unavailable），没有发起模型调用；改用完全相同的 Incremental 引擎在内存执行，不传 report，只输出数字。未把详细业务报告改写到任何其他目录或仓库。报告保存这一项尚未完成，需在授权目录可写的宿主环境重跑。

| 项目 | 结果 |
| --- | ---: |
| 回看范围 | 最近 7 天对应的 rowid 范围，since-rowid=12690 |
| 发现窗口 | 38 |
| 发现消息 | 461 |
| 发现来源（含附件） | 673 |
| 附件来源 | 212 |
| 无文本来源 | 185 |
| 尝试处理窗口 | 15（max-windows=15） |
| 成功窗口 | 0 |
| 失败窗口 | 15 |
| 演练 ai-task 调用尝试 | 15 |
| 额外合成诊断调用 | 1 |
| 总调用尝试 | 16，未超过 30 次 |
| 模型成功响应 | 0 |
| 实际 usage token | 未获得（汇总 reported_tokens=0 不代表确认实际消耗为 0） |
| 失败调用记账预留 token | 170131（估计预留，不是实测用量） |
| 拟新增报价 / 补齐缺口 / 异常 | 0 / 0 / 0 |
| 侧库主文件演练前后 SHA256 | 一致 |
| 详细 JSON/Markdown 报告落盘 | 否，EROFS |

演练会话标识：`0b7f54b4845b45049a95c9e857c6f6ea`。原演练归类为 model_failed_or_unknown；单次合成诊断确认 ai-task 信封错误为 gateway_unavailable，随后代码加入固定错误白名单以保留此安全错误码。另一次 max-windows=0 的只读发现核对了来源覆盖，未产生模型调用。

环境限制：

1. `/srv/imgw-dev/ops/private_out/dossier-incremental/` 写入返回 errno 30 / EROFS。没有申请绕过或写入其他业务报告路径。
2. 运行身份已是 imgw，但当前沙箱 NoNewPrivs=1；ai-task 的 sudo 网关路径不可用，合成请求返回 gateway_unavailable。没有成功到达模型的证据，不能据此宣称抽取准确率/吞吐验收通过。
3. 只读核对已安装网关代码发现 bailian_free 仅接受 auto。本任务明确要求显式 deepseek-v4.1-flash/deepseek-v4-pro-0813，因此保留所要求命令；没有改用 auto、付费模型、Gemini，也没有修改网关。

后续负责人需要在报告目录可写、网关支持指定模型的运营台宿主上补做真实成功演练，再接入每日调用。当前代码和虚构数据验收已完成，真实抽取与报告落盘验收仍受上述环境限制。

## 兼容与回滚

旧 sd_offer.tax_basis 为 NOT NULL：未知税口径保留 unknown，未知税率/币种为 NULL，不改旧表。缺口填补使用 sd_gap_fill 覆盖，不 UPDATE 旧 gap。新报价追加 supersedes_offer_id，旧 offer 内容保持原样。

按追加投影的设计，仅 DROP 新增表不足以恢复第一批报价集合。完整回滚工具先按 sd_projection 清除自己追加的行，再删除六张新增表；测试验证旧行集合完整恢复。执行方式见 docs/dossier-incremental.md。本次未在任何线上数据库执行迁移或回滚。

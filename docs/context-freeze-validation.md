# system 与 MEMORY 的周上下文修复

在基线 `d1a9f00` 上核实并修复了两处问题：Chat 第 0 天未初始化 system；Responses 和 Anthropic 在每次请求时重读 MEMORY。修复后，三个 API 都从本周会话中的同一条 system 取内容。基础任务说明、工具说明和 MEMORY 的格式文本均未改动。

## 当前行为

| 时点 | Chat、Responses、Anthropic / Bedrock 的共同规则 |
| --- | --- |
| 第 0 天首次请求 | 建立 system，读取当时 MEMORY，追加 dashboard |
| 同周后续请求与重试 | 复用整份 system；磁盘上的 MEMORY、已读文件变化不会改写历史内容 |
| 同周恢复 | 从 conversation 恢复冻结文本，从私有 `agent_sources` 恢复来源范围 |
| 新周或 reset 后首次请求 | 清空旧会话，重新读取 MEMORY；保留 `strip()` 和 40,000 字符截断 |
| 周初 MEMORY 为空或不存在 | 冻结基础 system；同周新写出的笔记在下周自动加载 |
| 旧 conversation 缺 system | 加载时打印迁移提示，读取一次当前 system/MEMORY 并冻结；原有消息保留 |

旧 conversation 的补全只作用于加载器接受的独立会话快照。完整旧运行仍受 manifest 的源码、环境和产物一致性检查约束；没有放宽恢复条件。旧快照没有保存过的历史 system 无法从当前磁盘还原，补全使用的是迁移时内容。

Agent 仍可主动 `read_file("MEMORY.md")` 取得新内容，工具结果作为新消息追加；这不会替换周初 system。三个请求位置分别为 `/messages/0/content`、`/instructions`、`/system/0/text`。Anthropic 的缓存标记继续按原逻辑添加到请求副本，不改会话正文。

## 代码与既有工作

- `src/saas_bench/agents/bash_agent/agent.py`：初始和 reset 的 `current_day=-1`，使第 0 天触发刷新；`_context_system_prompt` 复用 system，三条调用路径共享它。
- 阶段 2 继续使用 `CapturedText` 和私有来源状态。每次真正加载 MEMORY 产生一次读取事件；同周重发继续引用该版本，每次实际请求仍单独保存出现记录。来源关系不会写进模型提示或公开 conversation JSON。
- SQL 权限、公开回执、Bash 双流、文件版本、周脚本、checkpoint 和分支继承代码未变。全量测试覆盖这些既有实现。
- `scripts/check_evidence_records.py` 改为检查共同冻结规则，并修复已落后于公共 SQL 快照接口的夹具：初始化完整表结构、提供模拟日、允许 HTTP 线程读取测试连接。
- 原始 `docs/evidence-recording/*.json` 保留。新记录在 `docs/evidence-recording/context-freeze/`；验证器明确区分历史 API 分支规则与当前 `weekly_frozen` 规则。
- 本地旧 OpenCode 35 天运行日志经重新核对，第 0 天 36 次 HTTP 请求全部缺少 system。它继续作为旧构建的恢复、评分及用量验收记录，不能用于评价修复后的经营行为。修复后的共同前缀须重新采集。

这是原版组、Git 组、PF 组共用的运行修复；原版任务说明保持逐字不变。新运行通过现有 build/manifest 的 `source_commit`、`patch_sha256`、`source_sha256` 固定补丁。已有正式运行不能在中途替换构建继续比较。

## 验收与证据

当前源码 SHA-256：`a45ce8815bc5998934aeba15d23ab7824a4c7563f6a8f46a80badf4697515b95`。`public` 已重建，实际 zipapp、SDK、文档及源码均经过构建清单核验。

- macOS 全量：198 passed、4 skipped，跳过项为 Linux 专属检查。新增用例复用 `tests/test_preflight_context.py`，覆盖三 API、捕获开关、空／缺失 MEMORY、周中改写、恢复、reset 和旧快照补全。
- 阶段 0 新样例：21 条实际 SDK 序列化请求、99 条来源出现记录、6 个负例全部通过。模型响应来自离线接收端，外部模型调用为 0。增加的一条映射是第 0 天 Chat 的 MEMORY。
- OpenCode Go `deepseek-v4.1-flash`：4 次受控真实请求通过，第 0 天完整 system、同周冻结、连续／恢复请求相同、新周读取 B、捕获健康均通过。使用原版完整基础提示，仅开放并指定 `read_file`，没有启动经营模拟器。
- 通过的真实探针输入 27,081 token、输出 136 token、缓存读取 26,240 token。前两次探针各调用 3 次，因模型提出验收范围外的动作而停止；这些动作没有执行，记录保留在 `opencode_runs/context-freeze-20260924/`，共计 10 次真实 HTTP 请求。未配置价格，因此费用保持未知。
- Linux Python 3.14.7、bubblewrap：202 passed。SSH 恢复后，在独立目录 `/tmp/ceobench-context-HZMHQ2/repo` 原生构建并运行全量检查；源码 SHA-256 与 macOS 一致。原始 JUnit 和构建清单保存在 `docs/evidence-recording/context-freeze/linux/`，旧运行目录没有改动。

原始新证据、macOS JUnit 和 build 清单保存在 `docs/evidence-recording/context-freeze/`。通过的真实探针位于 `opencode_runs/context-freeze-20260924/forced-read/`，验收脚本为同级上一层的 `check.py`；这些运行目录由 Git 忽略。

复跑离线检查：

```bash
PYTHONHASHSEED=0 .venv/bin/python scripts/build_public.py
PYTHONHASHSEED=0 CEOBENCH_TEST_PUBLIC="$PWD/public" .venv/bin/python -m pytest -q tests
.venv/bin/python scripts/check_evidence_records.py --verify docs/evidence-recording/context-freeze
.venv/bin/python scripts/check_evidence_records.py --verify docs/evidence-recording
```

Linux 原生构建后，加 `CEOBENCH_TEST_KIND=formal` 运行相同测试；不要用旧产物替代新构建。

# 事件与证据版本规则及模型可见性验证

本次完成进度页中的「约定最小事件与证据版本」与「验证模型可见性记录」。格式名为 `ceobench.evidence-records.v1`，供阶段 2 接线和试跑使用，正式冻结仍在阶段 6。

依据为 Notion [实施计划](https://app.notion.com/p/3e22b646455381f5a4bdf115437251d4)、[进度跟进](https://app.notion.com/p/3e42b64645538122bb9ed99140b36dab)与[自动执行及模型输入盘点](https://app.notion.com/p/3e32b64645538111bf06df2ba4cb67d1)。核对 checkout 为 `4257981a1a65023115761d4e09233c7276810398`。本次新增检查脚本和交付文件，运行代码未改动。

交付文件：

- `scripts/check_evidence_records.py`：生成样例、执行实际代码路径、复核落盘记录及负例。
- `docs/evidence-recording/event-version-example.json`：事件、不可变证据、引用来源、分支截止点与 Git 对应样例。
- `docs/evidence-recording/model-request-source-map.json`：来源全文、实际 SDK 请求正文、版本与字符范围对照，以及恢复前的会话快照。
- `docs/evidence-recording/validation-results.json`：可机器读取的检查结果。

全部 SQL、公开业务回执、文件和 Bash 操作的自动记录及持久化接入仍属于阶段 2。本次脚本只记录自己执行的固定样例，没有安装运行时捕获器。`registration_fixture` 明确表示构造的 Agent 登记；正式 `create/revise/retire/list` 留在阶段 3。

## 任务 1：事件与证据规则

### 标识与最小字段

事件表示一次操作，版本表示这次操作中取得或生成的一份可引用内容，blob 保存字节。三者分别标识。同一 SQL 执行两次，有两个事件、两个取得记录；相同回执字节共用一个 blob。读取老版本时沿用老版本标识，另记本次读取事件。

| 记录 | 必填内容 | 本次约定 |
| --- | --- | --- |
| 运行／分支 | `run_id`、`branch_id`、父分支和 `fork_event` | 共同前缀名 `prefix`，分支只继承截止事件及以前的版本 |
| 事件 | `event_id`、`seq`、`author`、`kind` | 标识为 `run/branch/seq`；序号从 1 递增且不复用，首轮作者固定 `ceo` |
| 操作及结果 | `request`、`inputs`、`outputs`、`status`、`capture_gaps` | 请求保留实际参数；输入输出引用版本；缺口说明具体没有观察到什么 |
| 操作时点 | `started_at`、`completed_at`、`sim_day` | UTC 墙钟时间与模拟日分开；结果未知时 `completed_at` 为 `null` |
| 证据版本 | `version_id`、`object_id`、`created_by_event`、`previous_version`、`blob_sha256` | 版本标识为 `event_id:slot`；前驱无则 `null`，版本内容禁止原位替换 |
| 证据时点 | `acquired_at`、`content_time` | 取得时点为实际捕获时间；内容日期来自明确公开字段或声明，并保留 `basis` |
| 捕获范围 | `capture.layer`、`extent`、`source_truncated` | 层次独立记；`full` 指这一层的完整回执，SQL 返回 5,000 行时仍须记其原有截断 |
| 关系 | `source`、`target`、`kind`、`origin`、`event_id` | 区分观察到的字节复制、版本修订和 Agent 主动登记的语义引用 |

`version_id` 在 `versions` 对象中作为键保存。`blobs` 的键为原始字节的 SHA-256；样例只含合法 UTF-8 文本，保存 `encoding/text/size_bytes`，对 `text.encode('utf-8')` 求哈希。不排序 SQL 行或 JSON 字段来替换原始证据；若以后增加比较用副本，另建版本并说明转换。

证据对象按来源定义：文件为工作区内真实路径，登记文本为稳定文本 ID，查询视图为数据源范围、实际 SQL 和实际绑定参数的组合。本次 `query_id` 对该组合的固定 JSON 表示求哈希。公共 `query(sql)` 只传 SQL 字符串，`bound_parameters=null` 表示该入口没有绑定参数；不能从 SQL 字符串反推原始参数。查询视图定义改变就产生不同对象。

内容时间未知时写 `{"status":"unknown","reason":"not_declared"}`。样例查询明确返回常量 `day=7`，因此记录 `basis=explicit_sql_literal`；不能据此把一般查询的取得日当作内容日。一个结果覆盖区间或多个时点时保留区间／集合及来源，不能压成单日。普通 Bash 未观察到的文件读取、中间覆盖版本和内部依赖记入 `capture_gaps`，不能根据命令文字补关系。

### 状态与分层

| 情况 | 记录方式 |
| --- | --- |
| 成功且有结果 | `status=succeeded`；输出版本保存完整公开回执 |
| 成功且空结果 | 仍为 `succeeded`；回执保留 `rows=[]` 和 `row_count=0` |
| 公开拒绝或错误 | `status=failed`；保存 HTTP 状态、错误回执；没有结果版本时显式写空输出 |
| 超时且结束已确认 | `status=timed_out`；保留已观察到的部分流、退出状态与捕获范围 |
| 请求已发出，是否生效未知 | `status=result_unknown`、`completed_at=null`；保留请求及已收片段，暂停核对，不自动重发经营动作 |
| 捕获缺失 | 独立写 `capture_gaps`；不能把缺失当作空结果，操作状态按已有事实记录 |

阶段 2 的追加日志先持久化请求事件，完成后追加结果记录。恢复时把只有请求、没有确认结果的操作解析为 `result_unknown`。本次 JSON 是已经完成的小样例，不实现写前日志或崩溃恢复存储。

分层至少区分 `server_public_response`、程序解析／取得的数据、`stdout`、`stderr`、最终 `tool_return`、文件内容、dashboard、登记文本和模型请求。每层各有版本与捕获范围：HTTP 全文只能说明公开边界返回了什么，不能自动记为模型收到全文。合并两路流时保留转换关系；没有捕获两路流就标缺口。

相关现有代码：

- SQL 的公开过滤、5,000 行限制、原始顺序及截断字段：`api_server.py::_APIHandler._handle_query`。
- SDK 只提交 `{"sql": sql}`，返回解析后的公开字典：`novamind_api/_client.py::query`。
- CLI 成功时仅打印 `columns/rows/row_count`：`_public_cli.py::cmd_query`。
- Bash 合并流、退出提示及首尾截断：`agents/bash_agent/tools.py::_exec_bash`。

### 一条小轨迹

1. `prefix/1` 执行 SQL，取得 `:public_response`。结果有两条相同的行，包含整数、文本、`null` 和浮点数。
2. `prefix/2` 用真实 `write_file` 把回执写成 `evidence.json`，保存文件版本。只有已观察到的复制关系记为 `exact_copy`。
3. `prefix/3` 构造一条 Agent 登记，明确引用第一次查询版本，用途为 `historical_only`。这是记录格式样例，不声称仓库已有登记工具。
4. `prefix/4` 用真实 `edit_file` 将文件中的 `row_count` 从 2 改为 99，保存新文件版本和前驱。第一次查询及文件旧版保持原样。
5. `prefix/5` 再执行同一 SQL，公开结果仍为 2 行。新事件与第一次查询共用回执 blob，但标识不同。
6. `git/1` 和 `pf/1` 演示从各自分支解析 `prefix/1` 的旧版本。引用不能越过分叉截止点或读取兄弟分支。

共同前缀中的版本标识在克隆后保持原值。后续事件使用各自分支序号。样例验证标识解析和截止点，不执行线上经营分叉；运行快照的实际克隆与恢复属于已经独立验收的阶段 1。

Git 对应关系只在 `git show <commit>:<path>` 的字节与已捕获版本完全相同时建立。本次用临时样例仓库做了该检查，JSON 保留路径、提交、哈希和检查结果；临时提交不属于实验仓库，也不是正式可引用材料。正式 Git 组只获得 `path+commit`，后台对应关系及 PF 捕获内容保存在 Agent 工作区外，不复制给 Git 组。

关系指向历史版本不自动表示当前条件仍成立。语义引用仅来自 Agent 实际登记；内容相同、字节复制、读取或执行先后都不能替代语义声明。阶段 2 可复用 ProvenanceFS `ProvenanceStore` 的 blob、文件版本和 `get_content`；CEO-Bench 分支标识、查询对象和来源关系需单独接入。当前 store 用 `workspace_id/path/version_id` 定位，不能直接把不同克隆中各自递增的数据库整数当作全局事件 ID。

## 任务 2：实际请求与来源版本对照

### 验证边界

调用当前 `BashAgent.act`、`record_tool_result`、会话保存／恢复、文件工具、Bash 执行器、注册脚本执行器和 dashboard 构造代码。模型请求经过已安装的 OpenAI／Anthropic SDK 序列化，再由 `httpx.MockTransport` 接收。现有 `ModelUsage` 同时记录应用请求与 HTTP 尝试，检查脚本核对二者的输入字段与接收到的请求正文。

记录包含 **21 次真实 SDK 序列化请求、98 条来源出现映射**。外部服务商调用数为 0；接收端和模型回复为离线测试桩，因此本次验证的是请求交付内容及映射机制。数据为工程构造样例，不能列为自由运行或正式三组结果。第 0 日 Chat 的缺项按当前行为记录，尚未在本次修改业务代码。

每个请求保存 `reader=ceo`、`call_id`、`attempt_id/request_id`、`context_id`、实际发送时间、应用请求和最终正文 blob。`wire_blob` 对接收端取得的原始 UTF-8 请求字节求哈希；没有用重新序列化后的 JSON 替代原始请求。恢复请求还关联保存的会话快照 blob。

每条出现记录保存来源版本、来源范围、JSON Pointer、角色、请求字段内范围、交付标识、来源依据和 `full_source`。范围统一使用从 0 开始、右端不含的 Python Unicode 字符索引，均针对解码后的字符串，不能当作 UTF-8 字节位置或 token 位置。emoji 与中文已包含在样例中。

`file@a`、`memory@b` 等为本验证文件内部的来源版本别名，完整内容及哈希保存在同一文件中，不能拿别名跨运行拼接历史。映射来自固定操作已知的来源，再在明确的请求字段核对字串与范围；脚本不会对任意轨迹用相似文本自动推断血缘。未经归因的助手文本与工具调用参数仍完整保留在实际请求中，不补造来源关系。

### 三种 API 的请求路径

| API | MEMORY 位置 | 工具结果位置 | 同周修改 MEMORY 后 |
| --- | --- | --- | --- |
| Chat Completions | `/messages/0/content`，存在 system 时 | `/messages/N/content`，`role=tool` | 沿用周刷新时的 A 版 |
| Responses | `/instructions` | `/input/N/output`，`type=function_call_output` | 发送 B 版 |
| Anthropic Messages | `/system/0/text` | `/messages/N/content/M/content`，`type=tool_result` | 发送 B 版 |

具体下标保存在 `model-request-source-map.json` 每条记录中，不能用表中的 N／M 替代实际下标。Anthropic 的缓存字段保留在原始请求中。

### 对照结果

| 场景 | 每种 API 的请求数 | 检查结果 |
| --- | --- | --- |
| 第 0 日首次请求 | 1 | Chat 只有 dashboard，没有 system/MEMORY；其他两种有 MEMORY A |
| 第 7 日周起点 | 1 | 三种均发送 MEMORY A、完整 dashboard 及脚本输出前 500 字符 |
| 同周修改来源 | 1 | 旧文件 A 的第 2 行仍随工具结果发送，新文件 B 未发送；MEMORY 按上表取版本 |
| 连续执行与同周恢复 | 2 | 两次请求的原始正文逐字节相同；旧文件片段和截断后的 Bash 返回均在其中 |
| 第 14 日新周 | 1 | 旧工具结果清空，发送新 dashboard 与 MEMORY C；入会话后尚未发送的结果被清空 |
| 第 21 日长 MEMORY | 1 | `strip()` 后只交付前 40,000 字符，隐藏尾部缺席，原文件边界空白不记为已交付 |

文件部分读取只映射 A 版第 2 行，添加的行号属于工具格式文本。Bash 输出超过 30,000 字符时，原 stdout 只映射 `[0,15000)` 与 `[len-15000,len)`，最终工具返回自身可完整出现。这里 stdout 的原文为固定脚本的已知预期输出；检查真实执行所得首尾及截断提示，没有接入通用双流捕获。

注册脚本 A 后把同名文件改成 B，实际注册执行器仍运行 A。脚本输出到 dashboard 只映射 `[0,500)`；dashboard 全文另作版本，不能把 dashboard 全文出现记为完整脚本输出出现。`SCRIPT_HIDDEN_TAIL`、文件 B、未发送工具结果和长 MEMORY 尾部均有缺席检查。

每次请求重新列出现记录。同周恢复从实际恢复后的请求重建；新周不能沿用旧出现记录。`full_source=true` 只允许来源原文完整覆盖，本次没有实现 delta/cache read；其选择与节省计量留在阶段 4。

### 实际请求摘录

以下 ID、路径和范围直接取自保存的 `model-request-source-map.json`，可按 `request_id` 定位到全文；读取者均为 `ceo`。

| API／场景 | request_id | 来源版本 | 来源范围 | 请求 JSON Pointer | 请求字段范围 |
| --- | --- | --- | --- | --- | --- |
| chat／same_week_after_source_change | `69d6d4b1cc8f475eaef6f98c49fcb5a6` | `memory@a` | `[2,14)` | `/messages/0/content` | `[199,211)` |
| responses／same_week_after_source_change | `58fe6ad4ca894be898053068c5917835` | `memory@b` | `[2,14)` | `/instructions` | `[199,211)` |
| messages／same_week_after_source_change | `d916be4d5a354e1b8ac49ba379b95d50` | `memory@b` | `[2,14)` | `/system/0/text` | `[199,211)` |
| chat／same_week_restore | `24fc699a3d3c4e85aa316f88e40d9108` | `file@a` | `[22,32)` | `/messages/3/content` | `[7,17)` |
| responses／same_week_restore | `40958e6d9ebe4270a71433af11d2256d` | `bash@stdout` | `[25025,40025)` | `/input/4/output` | `[15064,30064)` |
| messages／new_week | `3036ca0d7d98448daef611b48d40fcb9` | `script@output` | `[0,500)` | `/messages/0/content/0/text` | `[505,1005)` |
| chat／memory_truncation | `d11d90eccd5840d1925d95fd122245be` | `memory@long` | `[2,40002)` | `/messages/0/content` | `[199,40199)` |

### 复跑与检查代码

```bash
uv run python scripts/check_evidence_records.py --verify docs/evidence-recording
uv run python scripts/check_evidence_records.py --output /tmp/ceobench-evidence-check-new
```

`--output` 要求目录不存在，避免覆盖先前证据；`--verify` 从已保存的 JSON 重新检查。生成记录保存 checkout commit、所用源码文件 SHA-256、Python 与 SDK 版本。生成器只允许本机网络连接，模型客户端全部使用测试传输。

另运行现有 `tests/test_preflight_context.py` 和 `tests/test_preflight_usage.py`，18 项通过。

六个负例必须被拒绝：blob 字节篡改、来源范围错误、部分来源冒充全文、跨周残留映射、漏掉来源映射，以及越过分支继承截止点。检查失败以非零退出，不依赖可被 `python -O` 关闭的 `assert`。

主要代码入口：

- `agent.py::_get_system_prompt_with_memory`、`_refresh_context`、`act`、`record_tool_result`：MEMORY、周边界与工具入会话。
- `agent.py::_call_openai/_call_openai_responses/_call_anthropic`：三个实际请求格式。
- `agent.py::_save_conversation_snapshot/load_conversation_snapshot`：同周恢复。
- `model_usage.py::ModelUsage.call/attach`：既有 `call_id/attempt_id` 与请求日志。
- `tools.py::_exec_read_file/_exec_bash`、`api_server.py::_run_daily_scripts_internal`、`environment.py::build_weekly_dashboard`：实际来源及截取位置。

## 阶段 2 的接续范围

按现有计划修复公共 SQL 权限，随后把本页记录格式接到全部公开回执、SQL、文件工具、Bash、脚本执行和模型请求边界，复用现有请求日志标识。对双流、内部 API 父执行、不可观察读取、失败和截断分别记录，再验收捕获开关下的返回一致性和崩溃恢复。本次只完成格式及固定样例的可见性验证，不勾选这些阶段 2 条目。

# 阶段 2 第三项：执行捕获

实现依据：[第三项任务](https://app.notion.com/p/3e42b64645538158ba5eef6241d680bd)、本次用户确认的实施计划及基线提交 `34c4f2be9cad18e8fb4a0af1f4a8570ff1c50f7a`。第四项整体等价与故障恢复验收继续单独保留。

## 启用与读回

新运行使用 `--execution-capture` 开启完整捕获，默认关闭。开启完整捕获会包含 SQL；与 `--no-sql-capture` 同时指定时报错。原来的 `--sql-capture` 继续只捕获 SQL。恢复以 manifest 固定的 `sql_evidence.capture_scope` 为准，旧 SQL-only 运行不会升级。入口为 `src/saas_bench/agents/bash_agent/run_test.py`。

证据仍保存在宿主运行目录的 `sql-evidence.sqlite`，采用已有事件序号、不可变版本、内容哈希及分支截止点。`SQLEvidenceStore.read_event()` 返回请求、操作结果、服务器发送状态、客户端收到状态和输入／输出版本引用；`get_content(version_id)` 校验哈希后读回版本。客户端接收状态来自标准客户端的报告，和服务器已发送状态分开保存。

样例位于 `docs/execution-capture/`。这是**实际执行的离线检查**：本机 HTTP、真实 Bash／文件操作和真实 SDK 序列化，模型传输由 `httpx.MockTransport` 返回固定回执，外部模型调用为零。它不是正式 original/Git/PF 对照实验。

同一次 Bash 中，两次查询分别返回 ledger 数据和常量 99；程序保存第一份数据到文件，只打印其中一个金额。可以读回：

| 层次 | 样例版本／位置 |
| --- | --- |
| 实际命令 | `test-run/prefix/1` 的 `request.request.command` |
| 两份服务器完整公开回执 | `test-run/prefix/2:public_response`、`test-run/prefix/3:public_response` |
| 实际收到字节、解析值、程序返回值 | 各查询的 `client_body`、`client_parsed`、`client_returned` |
| SDK／CLI 返回转换及打印投影 | `client_transformations`、`client_projection` |
| 分离的原始双流／解码双流 | `test-run/prefix/1:stdout_bytes`、`stderr_bytes`、`stdout`、`stderr` |
| 文件边界 | `test-run/prefix/1:workspace_before`、`workspace_after`，其中每个普通文件引用独立版本 |
| 最终工具返回 | `test-run/prefix/1:tool_return` |
| SDK 最终请求字节及来源出现记录 | `test-run/prefix/4:wire`、`occurrences`；另导出 `request.json`、`request-mapping.json` |

来源出现记录包含 context、角色、JSON Pointer、call/attempt、版本、来源范围与请求字段范围。范围均为零起点、右端不含的 Unicode 字符索引。`send_state_event_id` 指向同一次请求事件的最终发送结果：映射在发送前落库，发送结果随后独立追加。重试逐次保存，未打印的第二份查询数据没有被登记为模型已见。没有根据相同文本推断数据血缘。

## 实际记录位置

| 路径 | 捕获行为 |
| --- | --- |
| `api_server.py`、`execution_capture.py` | 在公开序列化后记录状态码、响应头和原始正文；按公开业务字段区分失败、批量部分成功及读／写回执。公开对象和日期附带字段路径与依据；任意 SQL 的来源／内容日期继续按既有规则保留未知。 |
| `novamind_api/_capture.py`、两个 CLI | 不含 PF 版本信息的随机父执行键和单次调用键；通过只追加 `POST /_capture` 报告实际收到数据、返回转换和展示。入口只确认写入，拒绝读历史。CLI Python 另外保存实际代码和双流。 |
| `agents/bash_agent/tools.py` | 六个工具统一开始／结束；实际读文件处保存原始字节、解码文本和选行／搜索范围；Bash、写和编辑工具捕获前后文件状态。失败后的部分写入、删除、文件类型均保留。 |
| `process_boundary.py` | Linux 每次 Bash 使用 subreaper 监督后代，包含新进程组／新会话后代；终态未确认时保留监督进程及服务器，停止分支，拒绝 checkpoint。 |
| `api_server.py`、`environment.py` | 注册／覆盖／撤销独立事件，周执行引用注册时的实际代码副本；dashboard 保存生成版本与脚本前 500 字符的投影。缓存取得事件继续引用原生成版本。第 0 日未建立缓存时仍按原行为重新生成。 |
| `agent.py`、`model_usage.py` | MEMORY 实际读取、strip、40,000 字符截断；私有会话来源状态绑定会话文件哈希；三个模型请求格式均在 SDK 最终序列化边界核验范围。保持 Chat 第 0 日原初始化行为。 |
| `sql_evidence.py`、`server_entry.py`、`run_state.py` | checkpoint 关闭宿主新执行及 HTTP 新请求准入，允许已开始操作的子调用完成，随后备份证据与私有来源状态。冻结等待不持有子调用需要的世界锁或数据库事务。管理记录单独保存，当前 checkpoint 的回执不进入自身快照。 |

> 已修复：表中保留的 Chat 第 0 日初始化缺陷已经修复。三个 API 均冻结每周 system/MEMORY；MEMORY 读取事件只在实际加载时产生，同周出现记录继续指向原版本，恢复保留私有来源范围。本页旧样例和 160／164 项验收数字保留，当前修复及回归结果见 [system 与 MEMORY 修复](context-freeze-validation.md)。

文件工具的正整数校验、真实写入字节数、逐候选路径检查及上限提示，以及进程监督，均作为捕获开关共用的基线。证据与来源关系不会加入 Agent 提示词、工具文本或 Agent 可读会话 JSON。

工作区扫描不跟随外部符号链接，特殊文件只记元数据。manifest 排除 `sessions/*/world.nmdb`、其 SQLite 辅助文件、`*.plain.tmp*` 和 `*.nmdb.tmp*` 模拟器临时数据库。扫描遇到其他并发变化会记录捕获故障并停止后续操作。

## 覆盖矩阵

| 验收项 | 可复跑检查 |
| --- | --- |
| 一次 Bash 两次 SQL、程序只打印部分、文件与实际模型请求分层 | `test_execution_capture.py::test_bash_sdk_receipts_files_and_model_occurrences` |
| 中文、CRLF、空文件、部分写失败、删除、越界、上限与 30,000 字符截断 | 同文件中的 `test_files_boundaries_and_exact_source_ranges`、`test_raw_decode_failure_partial_write_and_limits`、`test_empty_delete_glob_cap_and_invalid_limit`、`test_streams_truncation_failure_and_capture_equivalence` |
| 同文不同来源、未发送工具结果、重试、MEMORY 40,000 字符与 dashboard 切片 | `test_model_retries_identical_sources_and_unsent_result`、`test_memory_strip_limit_and_dashboard_slice_keep_exact_origins` |
| 三个 SDK 请求格式、同周恢复与新周映射 | `test_preflight_context.py::test_real_sdk_context_matches_continuous_after_restore` |
| 注册 A 后改 B、dashboard 500 字符／缓存、恢复及兄弟分支隔离 | `test_preflight_integration.py::test_packed_execution_capture_scripts_cache_restore_and_fork` |
| 捕获开关相同公共返回；完整打包 CLI 跑六周离线循环 | `test_full_harness_uses_packed_cli_and_fake_agent_requests[False/True]`，及分层样例的同命令开关比较 |
| HTTP 200 业务失败、批量部分成功、裸客户端未知关联、包安装 CLI 展示 | `test_http_business_failure_and_unknown_bare_client`、`test_batch_partial_receipt_and_public_object_fields`、`test_package_cli_keeps_sdk_value_and_display_projection` |
| 存储／采集通道失败保留已发生动作并阻止后续操作 | `test_capture_storage_failure_preserves_business_output`、`test_capture_channel_failure_keeps_business_result_and_refuses_next_call`、既有 SQL 故障检查 |
| 请求后崩溃、模型发送失败／SSE 部分接收、启动失败、二次收集超时 | `test_sql_evidence.py::test_committed_data_survives_process_exit`、`test_preflight_usage.py::test_connection_retry_and_interrupted_anthropic_stream`、执行捕获文件中的对应故障检查 |
| 后台进程暂停、保留服务器；checkpoint 等待父执行但允许子调用，拦住新文件操作 | `test_background_descendant_pauses_and_preserves_scene`、`test_harness_preserves_unfinished_descendants_and_server`、`test_checkpoint_drains_parent_while_allowing_child_callbacks` |
| Linux bubblewrap 下宿主证据库不可读、采集入口不能读历史 | `test_sql_evidence.py::test_private_store_is_inaccessible`、`test_preflight_sandbox.py`、`test_unknown_terminal_and_endpoint_is_append_only` |

复跑，不调用外部模型服务：

```bash
.venv/bin/python scripts/build_public.py
CEOBENCH_CAPTURE_ARTIFACTS=/tmp/ceobench-execution-sample \
  .venv/bin/python -m pytest -q tests/test_execution_capture.py \
  tests/test_sql_evidence.py tests/test_public_sql.py tests/test_preflight_*.py
.venv/bin/python scripts/check_evidence_records.py --verify docs/evidence-recording
```

Linux 另设 `PYTHONHASHSEED=0 CEOBENCH_TEST_KIND=formal CEOBENCH_TEST_PUBLIC=$PWD/public`。必须在对应 Python 环境重新构建 public；manifest 校验源码、builder、SDK、文档和实际 zipapp 哈希。源码变化后继续使用旧构建会被拒绝。

## 已确认边界和工程选择

- 裸 curl/requests 绕过标准客户端时保留服务器回执，缺少可靠父关联和实际接收事实的部分为未知。
- 普通 Bash 的内部文件读取、中间覆盖、管道内容和程序计算关系记录为明确缺口；没有建设通用 Bash／SQL 血缘分析器。
- 后台进程未结束时暂停并保留现场，不启动后台任务调度器。macOS 工程运行只检查进程组；完整后代检测和隔离验收在 Linux bubblewrap 执行。
- 采用现有 SQLite、只追加本机 HTTP、文件边界全量枚举与哈希、工作区外私有状态，均为用户计划接受的工程方案。保留 `sql-evidence.sqlite` 文件名以兼容已有恢复代码。
- 实现补充默认：采集附加记录单条上限 64 MiB、确认超时 5 秒。超过上限或缺少确认进入捕获故障／未知结果检查，阻止 checkpoint；不截短回执冒充完整捕获。
- 请求发送失败时，已序列化的请求与来源映射仍可读，发送状态为未知；没有把它记作远端模型确定收到。
- 本项未实现文本登记、PF 查询、Agent 可见版本句柄、stale 重跑、delta/cache read；第四项整体等价与故障恢复不在本项勾选。

## 最终检查与开销

同一最终源码树：`3c7004b66ca2e5bcb688bb8f4c2cdc4425529c76ba38582215420764996eb23e`。两个环境的源码、builder 与公开 SDK 哈希一致；Python 字节码分别在各自解释器下构建。`public/build.json` 保存当前 macOS 运行包哈希，Linux 构建清单和原始 JUnit 结果随样例交付。

- macOS Python 3.13.15：**160 passed、4 skipped**；跳过项为 Linux 专属检查。
- Linux Python 3.14.7、bubblewrap：**164 passed**。
- 阶段 0 已存样例复核：21 条请求、98 条来源出现记录、6 个负例通过。
- 最终数据与附件哈希：`docs/execution-capture/acceptance.json`。两个 SQLite 样例均通过完整性检查、逐 blob SHA-256 和导出映射范围复核。

下表为同一固定样例的一次实际测量，单位毫秒。Bash 总时长只计工具执行，排除后续证据检查；扫描与落库含其内部步骤，分项存在重叠，不能相加。开关捕获时同一命令的 stdout、文件业务内容保持一致。原 Bash、SQL 和模型时限未因本项调大；六周打包运行正常完成。

| 项目 | macOS | Linux |
| --- | ---: | ---: |
| Bash 总时长：捕获开 | 199.729 | 119.868 |
| Bash 总时长：捕获关 | 116.419 | 80.714 |
| 前后边界扫描，含内部哈希与落库 | 33.601 | 10.601 |
| SHA-256 | 0.304 | 0.430 |
| 版本落库，含内部哈希 | 44.110 | 13.178 |
| 客户端采集回传往返 | 16.781 | 8.736 |
| 回传的宿主落库 | 12.397 | 5.806 |
| 最终请求保存与映射 | 5.157 | 1.655 |

这是小型固定样例的捕获成本记录；工作区枚举仍为全量扫描，大型工作区成本需在第四项短程整体检查中继续测量。

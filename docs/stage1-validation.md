# 阶段 1 离线与真实调用验收

依据：[第一轮实验实施计划](https://app.notion.com/p/3e22b646455381f5a4bdf115437251d4) 的阶段 1。先完成离线验收；用户随后授权真实调用，追加 OpenCode Go 验收。阶段 0 仍未完成，没有实现 PF 事件体系或启动正式分组采集，也没有启用响应重放或共享模型缓存。

## 验收结果

2026-09-22，在 `feat/preflight` 完成分项修复。测试初始为 24 项，阶段 1 首轮离线验收为 72 项；追加 Go 路由、缓存字段、计价配置和空工具配置回归检查后共 77 项。

| 环境 | 结果 | 执行对象 |
| --- | --- | --- |
| macOS / Python 3.13.15 | 76 通过，1 跳过（26.14 秒） | 重建后的 `public/novamind-operation`；跳过项为 Linux bubblewrap |
| sheep-rog / Linux / Python 3.14.7 | 77 通过（23.14 秒） | 独立临时目录原生构建；集成测试使用 `formal` 模式和实际 bubblewrap |

Linux 验收目录：`/tmp/ceobench-stage1-n4upJq`。依赖只安装到该目录的 venv。`tests.log`、`tests.xml`、`build.log` 和 `repo/public/build.json` 保留验收记录；原有运行和系统配置未修改。

2026-09-23，跳过快照内重复数据库 C 后复验：macOS / Python 3.13.15 本地重建后 77 通过、1 跳过；sheep-rog / Linux / Python 3.14.7 在新目录 `/tmp/ceobench-omit-c-xc2aog` 原生重建，以 `formal` 模式运行 78 通过。远端 PyPI TLS 连接失败，新 venv 的依赖从旧验收 venv 只读复制；源码、构建和测试均留在新目录，旧验收目录没有写入。

本地重复构建得到相同的运行包、SDK 和文档哈希。构建清单见 [public/build.json](../public/build.json)，其中保存源码基线、补丁及源码指纹、构建脚本指纹、Python 和实际 SDK 依赖版本。SDK 和文档重建后的内容与原版一致，原版任务说明、7/28/84/182 天四个预测期限保持原文。

## 具体检查

- [运行和构建清单](../tests/test_preflight_manifest.py)：SDK、源码、Python、产物漂移均拒绝启动；模拟器环境变量不能覆盖冻结配置。
- [隔离和入口](../tests/test_preflight_sandbox.py)：Linux/bubblewrap 缺失、不同初始哈希种子、相邻工作区越界、真实 bubblewrap 下的读写边界。
- [随机状态](../tests/test_preflight_rng.py)：主流、竞争者、帖文噪声、模板、ShockManager 和每组随机流续接；必要状态缺失时拒绝恢复。
- [脚本](../tests/test_preflight_scripts.py)：注册 A 后修改源文件 B，仍执行 A；顺序、撤销、空集合、内容校验、SDK 回调、异常和带部分输出的超时。
- [快照](../tests/test_preflight_checkpoint.py)：后台保存延迟、失败、等待超时、进行中和结果未知的操作拒绝保存、旧快照拒绝恢复。
- [对话](../tests/test_preflight_context.py)：三个 SDK 格式的实际请求在连续和恢复后相同；工具调用与结果配对，同周接续，新周清空并注入 MEMORY，保留原有 40,000 字符截断规则。
- [预测](../tests/test_preflight_predictions.py)：批量保存中途失败回滚，日期和 RNG 不变；区间内外、边界、零/负现金、未到期、缺失字段和评分数量。
- [用量](../tests/test_preflight_usage.py)：Chat Completions、Responses、Anthropic、Bedrock；完整请求、原始响应、SDK 内部重试、外层重试、缓存读写、缺失用量、流中断及恢复后累计。DeepSeek 缓存字段兼容依据其[接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)。
- [真实打包产物集成](../tests/test_preflight_integration.py)：连续运行与第 21/35 天恢复的两个独立进程运行到第 42 天，逐项比较公开回执、数据库业务表、RNG、脚本输出和用量汇总；覆盖跨月状态、脚本 A/B 跨周切换、MEMORY/Git/请求日志恢复、克隆互不写入、快照指针发布中断、完整 Harness/CLI 循环、故障停止和评分入口。

测试使用 SDK MockTransport 和本地 HTTP 服务；测试进程及模拟器子进程阻止外部 socket 连接。模拟器伪造响应由输入内容确定，没有查询重放数据库。

## 重跑

```bash
PYTHONHASHSEED=0 .venv/bin/python scripts/build_public.py
PYTHONHASHSEED=0 CEOBENCH_TEST_PUBLIC="$PWD/public" .venv/bin/python -m pytest -q tests
```

Linux 原生构建后，增加 `CEOBENCH_TEST_KIND=formal` 即可用相同测试集验收真实隔离。运行包包含 Python 字节码；本次提交的产物来自 Python 3.13.15，换解释器或依赖版本时需要原生重建。启动时会检查这些版本。

## 快照与用量文件

`manifest.json` 冻结生效配置。`checkpoint.json` 只指向全部保存成功的 generation；每份快照的权威数据库是 `checkpoints/<id>/world.nmdb`（B），同一目录还保存配置、脚本、工作区、Git、MEMORY、对话、请求日志及累计数。复制工作区时跳过 `checkpoints/<id>/agent_workspace/sessions/<当前 session_id>/world.nmdb`（C），当前运行中的 `agent_workspace/sessions/<当前 session_id>/world.nmdb`（A）保留原样。恢复时先复制快照工作区，再将 B 复制到 A 的位置。HTTP 保存请求只接受预期日期，导出目录由启动配置指定。

`operation.json` 标记尚未纳入完整快照的操作。发生超时或结果未知时，Harness 停止服务器并写 `branch_stop.json`，保留旧指针，拒绝直接恢复该分支。完整旧 generation 仍可离线分析。缺少必要状态的历史快照不会被补默认值后运行。

`logs/agent_requests.jsonl` 和 `logs/simulator_requests.jsonl` 分别保存逻辑 SDK 调用与 HTTP 尝试。`usage_summary.json` 给出已知 token/费用小计、缺失计数和失败 HTTP 尝试数量；缺失值保留 `null`。费用只按冻结的 `BenchmarkConfig.model_pricing` 中精确服务模型 ID 对应的 USD/千 token 价格计算。默认计价表为空；可用 `--pricing-file` 提交含 `source`、`basis`、`rates` 的 JSON，内容会写入清单，恢复时无需原文件且拒绝换价。分时价格可保存含时区的 `valid_from` / `valid_until`，超出有效期的费用保持未知。

预测可离线评分：

```bash
.venv/bin/python scripts/score_predictions.py RUN_DIRECTORY --outcome completed
```

输出 `prediction_scores.json`，包含逐条到期状态、95% 区间分数、四周均分及可评分数量。目标日没有新增账目时仍按截至该日的累计现金评分。

## 真实调用验收

用户授权后，使用 OpenCode Go 的 `https://opencode.ai/zen/go/v1` 完成验收。`deepseek-v4.1-flash` 和 `kimi-k3` 均通过真实请求；35 天运行的经营 Agent 和模拟器统一使用前者。没有调用 DeepSeek 官方 API。模型 ID、端点和计价依据来自 [Go 官方文档](https://opencode.ai/docs/go/)，并用账户的模型列表与实际响应核实。

Linux 运行使用 `formal` 模式和 bubblewrap，顺序为：真实工具调用 → 第 0 天同周快照 → 两个独立克隆 → 主分支运行至第 7 天并完整保存 → 新进程恢复 → 运行至第 35 天。另一克隆完成真实调用后保留在第 0 天。验收确认：

- 两个克隆恢复后的首条实际 SDK 请求完全相同，Go 会话标识各自独立；恢复后的 45 张数据库表与原快照一致。
- 同周接续已完成的工具结果；第 7 天重启后，首条模型请求清空旧工具对话并注入 MEMORY。
- 注册脚本 A 后将源文件改成 B，最终 dashboard 仍执行注册的 A，SDK 查询回调成功。原快照和另一克隆没有被主分支写入。
- 第 35 天正常结束，最终现金 $775,922.3974282878。20 条预测中 7 条到期评分，13 条目标日超出本次 35 天范围；四周预测可评分 2 条，平均 95% 区间分数为 3,793,474.332618164。

> 后续复核及修复：该运行第 0 天的 36 次请求全部没有 system。当前已补齐 Chat 首周初始化，并统一三个 API 的周初 system/MEMORY 冻结规则。旧运行、评分与快照保持原样，继续说明旧构建的恢复和记账能力；修复后的经营行为需要新运行。完整旧运行继续匹配原 manifest，未放宽跨构建恢复。详见 [修复与影响检查](context-freeze-validation.md)。

| 主分支角色 | SDK 调用 | 输入 token | 输出 token | 缓存读取 token | Go 额度折算 USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| 经营 Agent | 134 | 2,892,373 | 40,744 | 2,631,040 | 0.07153947 |
| 模拟器 | 40 | 20,784 | 3,988 | 0 | 0.00551040 |
| 合计 | 174 | 2,913,157 | 44,732 | 2,631,040 | 0.07704987 |

主分支所有调用均成功，SDK 用量汇总已逐条对回原始回执，没有输入、输出或缓存读取数量缺失。提供商未返回缓存写入字段的调用分别为 112 / 11 次，未返回 reasoning 数量的调用分别为 13 / 24 次，均保留缺失计数。缓存数字来自提供商响应；Harness 没有增加响应重放或共享模型缓存。

计价文件保存了官方来源、USD/千 token 单位和有效时段。此次 DeepSeek 请求均处于文档规定的非高峰时段：输入 0.00015、输出 0.0006、缓存读取 0.000003 USD/千 token。表中数字表示订阅额度消耗的折算值，不能用作另外产生的 API 账单金额。

协议探针另外覆盖 Kimi 可用性、本地注入 503 / 带 Retry-After 的 429 后的 SDK 内部重试、外层重试，以及真实 SSE 响应流被本地截断。重试后的响应来自 Go。中断时保留已收到的内容，未返回的用量和费用保持未知。主运行没有遇到提供商主动限流；故障注入回执明确标记来源。

本次真实请求覆盖所选模型的 Go Chat Completions 路径；Responses、Anthropic 和 Bedrock 使用前述离线 SDK 测试验收。

额外外层重试探针第一次传入了空工具列表，产生 41 条无工具的正常回复后被停止，已知额度折算为 0.002103282 USD；注入错误和中断记录一并保留。修复后，空工具配置在发出请求前报错；使用正式工具定义的重跑得到 `[1, 2]` 两次外层尝试并成功返回 `read_file`。这些探针独立于上表的 35 天主运行。

将所有检查按调用 ID 去除克隆继承日志的重复后，共 227 个逻辑调用、223 次完整成功；已知 Go 额度折算小计 0.08012442 USD。4 条费用未知记录包含 2 次本地注入的逻辑请求失败及 2 次人为中断。汇总见本地 `opencode_runs/stage1-live-20260922/summary.json`。

35 天回执固定于 `99857a2` 对应源码，指纹为 `8eabd43e491cb9f45603064ecceedec0fde69e7453dee6234223ba7c9d81e7ca`。随后 `70d2b37` 增加空工具配置检查，最终源码指纹为 `c5f49d0372b8c28c3d1613d7b6b24b7f98a3024be55335925f7f31b294f48f02`；最终产物重新通过两端全量测试，并另做两次真实调用的同周恢复检查。运行中的 checkout 和构建始终保持冻结。

本地请求回执、评分和核验结果保存在 `opencode_runs/stage1-live-20260922/`（Git 忽略）：`linux/live/verification.json` 是主运行对账结果，`linux/live/restored-a/prediction_scores.json` 是逐条评分；`linux/final-live/` 保存最终产物的测试和恢复结果，`protocol/` 保存额外探针。`acceptance.py` 与 `verify.py` 保留了可检查的验收代码。完整数据库快照和工作区保留在 Linux 原始目录 `/tmp/ceobench-stage1-n4upJq`：主运行在 `live/`，最终产物验收在 `final-live/`。本地镜像不包含这些大文件。

验收结束后已删除 Linux 临时 Go 密钥文件并确认没有遗留模型或模拟器进程；归档文件检查未发现 API 密钥值。原有运行和系统配置未修改。

本次新增实现提交：`f5a3e7d`（Go 路由与会话标识）、`99857a2`（计价冻结和缓存字段）、`70d2b37`（空工具配置拒绝）；`e00d7b5` 重建 Go 修复产物。所有提交只含标题，均未 push。阶段 1 的剩余真实调用检查已完成；正式分组数据采集仍以完成阶段 0 为前提。

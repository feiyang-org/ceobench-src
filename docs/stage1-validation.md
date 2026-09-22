# 阶段 1 离线验收

依据：[第一轮实验实施计划](https://app.notion.com/p/3e22b646455381f5a4bdf115437251d4) 的阶段 1。阶段 0 仍未完成；本次没有实现 PF 事件体系，也没有启动真实模型调用、响应重放或共享模型缓存。

## 验收结果

2026-09-22，在 `feat/preflight` 完成分项修复。测试初始为 24 项，当前共 72 项。

| 环境 | 结果 | 执行对象 |
| --- | --- | --- |
| macOS / Python 3.13.15 | 71 通过，1 跳过 | 重建后的 `public/novamind-operation`；跳过项为 Linux bubblewrap |
| sheep-rog / Linux / Python 3.14.7 | 72 通过 | 独立临时目录原生构建；集成测试使用 `formal` 模式和实际 bubblewrap |

Linux 验收目录：`/tmp/ceobench-stage1-n4upJq`。依赖只安装到该目录的 venv。`tests.log`、`tests.xml`、`build.log` 和 `repo/public/build.json` 保留验收记录；原有运行和系统配置未修改。

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

`manifest.json` 冻结生效配置。`checkpoint.json` 只指向全部保存成功的 generation；数据库、配置、脚本、工作区、Git、MEMORY、对话、请求日志及累计数在 `checkpoints/<id>/`，位于 Agent 工作区之外。HTTP 保存请求只接受预期日期，导出目录由启动配置指定。

`operation.json` 标记尚未纳入完整快照的操作。发生超时或结果未知时，Harness 停止服务器并写 `branch_stop.json`，保留旧指针，拒绝直接恢复该分支。完整旧 generation 仍可离线分析。缺少必要状态的历史快照不会被补默认值后运行。

`logs/agent_requests.jsonl` 和 `logs/simulator_requests.jsonl` 分别保存逻辑 SDK 调用与 HTTP 尝试。`usage_summary.json` 给出已知 token/费用小计、缺失计数和失败 HTTP 尝试数量；缺失值保留 `null`。费用只按冻结的 `BenchmarkConfig.model_pricing` 中精确服务模型 ID 对应的 USD/千 token 价格计算；当前默认计价表为空，费用保持未知。

预测可离线评分：

```bash
.venv/bin/python scripts/score_predictions.py RUN_DIRECTORY --outcome completed
```

输出 `prediction_scores.json`，包含逐条到期状态、95% 区间分数、四周均分及可评分数量。目标日没有新增账目时仍按截至该日的累计现金评分。

## 留待真实调用验收

1. 配置的经营 Agent 和模拟器模型 ID、账户权限及端点真实可用性。
2. 真实响应中的 usage、缓存字段、服务模型 ID、限流/重试和流中断记录。
3. 登记有来源的计价配置，再核对实际费用。
4. Linux 正式模式的短程真实模型运行及恢复冒烟。

以上尚未执行。本次在首次真实模型请求之前停止；正式数据采集还需要完成阶段 0。

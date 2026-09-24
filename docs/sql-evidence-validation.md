# SQL 查询视图版本保存与验收

阶段 2 的第二项实现了后台 SQL 捕获、不可变版本读取及 checkpoint 接入。依据为 [任务](https://app.notion.com/p/3e42b6464553812e82f1e2882294d082)、[实施计划](https://app.notion.com/p/3e22b646455381f5a4bdf115437251d4) 和 [SQL 规则](https://app.notion.com/p/3e32b646455381578c41d0c3632fd021)。代码基于 `3c1452b1a166e70881c9384b4a3302a905cf91f8`，在公共 SQL 权限修复之后，单独提交到 `feat/execution-capture`。

## 已实现的行为

- [sql_evidence.py](../src/saas_bench/sql_evidence.py) 将必要的 SQLite blob、版本及读取实现从 ProvenanceFS `store.py@924e7f7c3c094f8db18dfb17e2fe64c77ec1b843` 移植到 CEO-Bench。运行包不依赖另一个 checkout，也没有新增依赖。
- 查询键由运行、分支、数据源、权限版本、实际 SQL 原文和实际参数构成。当前 `query(sql)` 没有绑定参数，记录为 `null`。首尾空白也保留；不解析 SQL 等价关系或移动日期窗口。
- 每次请求先提交独立的 `run/branch/seq` 事件，结果版本是 `event_id:public_response`。相同回执共用 SHA-256 blob，事件和版本仍分别存在。并发查询按结果提交顺序连接已完成版本的前驱。
- HTTP 捕获保存实际响应头及序列化正文，包括 Server、Date、hint、warning 和错误原文；不改变公开 SQL、SDK 或 CLI 的返回。HTTP、SDK、CLI 及周脚本的 SQL 最终均经过该捕获点。
- 查询记录保存快照模拟日、快照标识、权限版本、取得时间、执行状态和执行耗时。内容时间暂记未知；请求父事件记 `unpropagated_context`。BLOB 转文本、同名输出列及非有限数分别标出比较障碍，不补造丢失值。
- `whole-row-v1` 比较副本单独保存。保留有序列定义、类型、NULL 和每行的重复次数，只排序完整行；截断结果的比较范围是 `returned_subset`。
- 成功、空集、拒绝、错误和超时都有原始回执。发送状态与执行状态分开，发送完成只记 `sent`，客户端接收状态仍为 `unknown`。
- 请求已提交而无结果时，`read_event` 返回 `result_unknown`。结果已提交而无发送确认时，原版本可读、发送状态未知；服务器启动和 checkpoint 检查均拒绝继续采集，避免自动重跑补造历史。
- 捕获失败保留当前查询响应，写独立故障标记；内存故障也进入 `/health`。Runner 在下一次操作前及工具结果记录后检查故障并停止，checkpoint 同样拒绝发布。

实现入口为 [api_server.py](../src/saas_bench/api_server.py) 的 `_handle_query`、`end_headers`、`_send_query_json`，以及 [public_sql.py](../src/saas_bench/public_sql.py) 的内部执行元数据参数。原查询权限继续共用独立只读快照和源列授权。

## 开关、读取和恢复

新运行默认关闭 SQL 捕获。Runner 使用 `--sql-capture` 或 `BashAgentRunner(sql_capture=True)` 开启；manifest 保存格式与来源身份，恢复时沿用配置，显式切换开关会被拒绝。Oracle 调试不能接入本轮公共证据库。

数据库位于 `<run>/sql-evidence.sqlite`，在 Agent 工作区之外，不写入 Agent 的 Git 历史。内部读取示例：

```python
import json
from pathlib import Path
from saas_bench.sql_evidence import SQLEvidenceStore

run = Path('/path/to/run')
identity = json.loads((run / 'manifest.json').read_text())['sql_evidence']
store = SQLEvidenceStore(run / 'sql-evidence.sqlite', identity)
metadata, body = store.get_content(f"{identity['run_id']}/prefix/1:public_response")
event = store.read_event(f"{identity['run_id']}/prefix/1")
```

`get_content` 返回保存的字节并核对哈希，不执行 SQL，也不表示模型已经收到内容。不存在、越过继承截止点及兄弟分支的版本均拒绝读取。

checkpoint 暂停新 SQL 入场，等待在途查询完成记录，然后在世界锁内保存世界和 SQLite backup；不直接复制活跃 WAL 文件。证据库哈希、分支身份及序号截止点写入 checkpoint。并发查询平时独立执行，checkpoint 最多等待 180 秒。

同分支恢复继续已保存序号；活跃证据库有 checkpoint 以外的事件时拒绝回退，保留原库供核对。旧 checkpoint 只能在关闭捕获时继续使用，不能声称包含过去的 SQL 证据。

内部克隆入口显式分配新分支，每个兄弟克隆使用不同名称：

```python
from saas_bench.run_state import clone_sql_run
clone_sql_run('/path/to/source-run', '/path/to/new-run', 'pf')
```

随后通过现有 `--continue-from` 恢复目标目录。新分支从序号 1 开始，前缀事件和版本 ID 保持原值。克隆复制的是已验证 checkpoint，来源分支后续新增的记录不会进入克隆。

## 验收

新增 [test_sql_evidence.py](../tests/test_sql_evidence.py)，并扩展 [打包集成检查](../tests/test_preflight_integration.py)。全部使用合成世界与离线模型响应，没有真实服务商调用。

覆盖原始字节一致、查询身份、重复内容复用、类型与重复行、5,000／5,001 行边界、权限拒绝、语法错误、SQL 超时、并发事件、checkpoint 等待、请求／结果提交后进程退出、存储失败和 socket 发送失败。打包路径验证 HTTP／SDK／CLI／周脚本，以及恢复、两份独立克隆和子分支再次恢复。

macOS / Python 3.13.15：159 项通过、3 项 Linux 专属检查跳过，51.14 秒。Linux / Python 3.14.7 的 formal 检查：162 项全部通过，46.80 秒，包括真实 bubblewrap 下的证据库隔离。阶段 0 的固定样例也通过复核，保留 7 个事件、5 个版本、21 个请求及 98 条出现映射。

两端源码提交基线相同，源码树指纹均为 `5ff8e965476bfcf62c56ae7cb3e0c65c11c8379a1eaa279891aa2bc2c25a0f29`。macOS 运行包 SHA256 为 `7ce5fa6cb2820119494068b3dced178ea8a139d6b121675c00fa687a5a3099ad`，Linux 为 `1f9d47e3ff333b7c54535aa4ce761867530a75a0ee43b87da80b34d35cd12cbe`；不同解释器分别原生构建。原版 Agent 任务说明未改动。

本机完整用例结果为 `/tmp/ceobench-sql-evidence-tests.xml`；Linux 验收目录为 `/tmp/ceobench-sql-evidence-PdjdOS`，保留 build.log、tests.log、tests.xml 和 public/build.json。合并后的 `/tmp/ceobench-sql-evidence-validation.json` 保存两端构建信息、逐项用例结果、源码与测试文件哈希及测量原始数据，并作为附件上传到 Notion。远端复用已有离线验收 venv，没有安装依赖或修改原验收目录。

```bash
PYTHONHASHSEED=0 .venv/bin/python scripts/build_public.py
PYTHONHASHSEED=0 CEOBENCH_TEST_PUBLIC="$PWD/public" .venv/bin/python -m pytest -q tests
```

Linux 增加 `CEOBENCH_TEST_KIND=formal`，使用该平台原生重建的运行包。构建与源码一致性由 `run_state.verify_build` 检查。

## 捕获成本与边界

本机固定合成账本为 50,000 行。每种查询先预热，随后交替开关捕获，各测量五次；计时包含服务器写完发送状态。SQL 与响应超时预算沿用现有分段，捕获增加的工作单独发生在执行和发送边界。

| 查询 | 关闭捕获中位耗时 | 开启捕获中位耗时 | 回执正文 | 六次捕获后的证据库 |
| --- | --- | --- | --- | --- |
| `SELECT sum(amount) AS total FROM ledger` | 12.52 ms | 13.16 ms | 89 字节 | 86,016 字节 |
| `SELECT day,amount,note FROM ledger`，返回 5,000 行 | 29.67 ms | 44.15 ms | 218,669 字节 | 827,392 字节 |

两种查询各产生六个事件、六个公开回执版本，均只存两份 blob：公开正文及比较副本。原始测量保存在 `/tmp/ceobench-sql-evidence-measurement.json` 并随 Notion 验收附件交付。库大小包含 SQLite 表、索引和执行元数据；此测量只代表这两个构造查询。

模型可见 `v37` 句柄、Bash 父子事件关联、业务回执／文件捕获、对象与日期提取、实际模型交付、stale 检查和 delta read 继续由后续任务实现。阶段 2 第三、第四项保持未完成。

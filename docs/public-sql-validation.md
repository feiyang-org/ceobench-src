# 公共 SQL 权限修复与验收

阶段 2 的第一项“修复公共 SQL 权限”已经完成。依据为 [实施任务](https://app.notion.com/p/3e42b64645538123b427de7cbf45da30) 与 [C01](https://app.notion.com/p/3e32b64645538193932ef44ebcfd4bf6)。SQL 查询视图版本保存、运行时捕获和自动 stale 检查由后续任务实现。

## 实现

- 实现基于 `3c1452b1a166e70881c9384b4a3302a905cf91f8`，公共 SQL 权限修复单独提交到 `feat/execution-capture`。
- [public_sql.py](../src/saas_bench/public_sql.py) 从 `TABLE_DOCS.columns` 授权 19 表 145 列，政策版本为 `public-sql-v1`。原 C01 漏记了 `agent_social_media_posts` 的六个既有公开字段；内部评分、分组浏览量与 `reasoning_by_group` 仍拒绝。
- 每次查询在世界锁内复制当前内存数据库，并同时取得模拟日和独立 `snapshot_ref`；不读取异步保存的 world.nmdb。取得快照后释放世界锁，在工作区外的临时文件上以 `mode=ro` 执行。事务未完成、世界状态未知时拒绝取快照，不代为提交或回滚。
- 同名 TEMP VIEW 只投影公开列，`SELECT *` 沿用底层表的公开列顺序。SQLite authorizer 逐项授权源表和源列，使用 `SQLITE_DENY`；直接访问 `main` 也执行相同授权，不按输出别名删列。COUNT 的空列名访问按公开表授权。
- 只允许 SELECT、只读 CTE 和固定的内置分析函数清单。写入、DDL、schema 读取、PRAGMA、ATTACH、事务控制、扩展及其他函数默认拒绝。查询连接另启用 `query_only`，不改变业务连接的权限。双引号只表示标识符，字符串使用单引号。
- Oracle 由服务器启动配置选择，可读内部表列及 schema，仍执行在只读快照上；HTTP 请求不能切换模式。正式服务器和 Harness 均拒绝 Oracle。
- [api_server.py](../src/saas_bench/api_server.py) 的 HTTP 入口保留 SQL 字符串接口、结果结构、5,000 行上限、重复行、NULL、SQL 结果顺序和截断提示。参数校验为 400，授权拒绝为 403，快照不可用为 503，超时为 504；视图缺少所请求列等 SQLite 查询错误沿用 500，并明确返回失败。
- 锁等待、快照复制、SQL 共用 120 秒预算；响应编码和发送共用另一份 30 秒预算。周推进将 shock、世界变化和模拟日发布放在同一锁区间，失败标记在释放锁前设置。周脚本回调在锁外运行。

查询后删除临时快照。`snapshot_ref` 当前用于诊断日志；不可变证据储存和按版本读取由下一项实现。旧 `python_exec` 无 HTTP 服务器时的开发模式不属于本轮三组公开入口。

## 验收结果

2026-09-24 使用最终源码与两端原生构建产物验收，无真实模型或付费 API 调用。

| 环境 | 全量结果 | 运行目录 |
| --- | --- | --- |
| macOS / Python 3.13.15 | 132 通过、2 跳过，30.35 秒 | 当前 checkout；两项跳过均为 Linux bubblewrap 检查 |
| sheep-rog / Linux / Python 3.14.7 | 134 通过，27.05 秒 | `/tmp/ceobench-public-sql-InSXNa`，集成测试使用 `formal` |

[公共 SQL 检查](../tests/test_public_sql.py) 与 [打包入口集成检查](../tests/test_preflight_integration.py) 覆盖：

- WITH 写入、注释前缀、RETURNING、多语句、事务控制、内部表、别名、隐藏列筛选／连接／排序／聚合／嵌套、schema 与函数绕过。
- 全部公开表投影、合法 CTE、窗口函数、公开席位 JOIN、空集、NULL、重复行、明确排序与 5,001 行截断；星号列顺序与底层表相同。
- 数据库字节、全部模拟器随机流与模拟日不变；同日更新可见、日期与快照同步、查询超时后服务恢复。
- 只读文件连接与 authorizer 分别独立拒绝写入；查询／复制失败清理，未完成事务保留原状，发送超时和日志管道关闭不追加第二份 HTTP 响应。
- HTTP、随包 SDK、zipapp CLI、周脚本查询；Oracle 只读与正式模式拒绝；Linux 真实沙箱无法读取正在使用的查询快照；原阶段一连续／恢复／克隆测试全部通过。

Linux 使用原验收 venv 的解释器与依赖，设置 `PYTHONDONTWRITEBYTECODE=1` 和新 checkout 的 `PYTHONPATH`；未安装依赖，未修改旧验收源码。临时目录保留 build.log、tests.log、tests.xml 和原生 public/build.json。验收进程均已退出。

## 重跑

```bash
PYTHONHASHSEED=0 .venv/bin/python scripts/build_public.py
PYTHONHASHSEED=0 CEOBENCH_TEST_PUBLIC="$PWD/public" .venv/bin/python -m pytest -q tests
```

Linux 加 `CEOBENCH_TEST_KIND=formal`。运行包使用 Python 字节码，换解释器时原生重建。构建后用 `saas_bench.run_state.verify_build(Path('public'), Path('.'))` 核对源码、SDK、文档与运行包。

## 产物与成本

两端源代码指纹相同：`61f247fc91b5c7a8ff0bc8dede524fce3d0155dcf79bb756531a96b4524ee13d`。本地产物与运行依赖见 [public/build.json](../public/build.json)；bundle SHA256 为 `8007c1052888effd1d52572dd29244a2bea3a3349c71a7d623c3dc677e8be252`。原版 Agent 任务说明未修改。

固定合成数据：50,000 行 ledger，其余使用 init_database 空表；查询为 `SELECT sum(amount) AS total FROM ledger`。五次测量的快照均为 4,452,352 字节，复制中位耗时 5.71 ms，含复制和 SQL 的总耗时中位数 8.98 ms。当前每次查询复制整个世界，成本随数据库体积增加；尚未测量 GB 级世界，没有引入快照缓存。

本机原始结果保留在 `/private/tmp/ceobench-sql-tests.xml`、`/private/tmp/ceobench-sql-tests.log`、`/private/tmp/ceobench-sql-benchmark.json`；测量脚本为 `/private/tmp/measure-ceobench-sql.py`。Notion 任务附有本报告及包含两端构建指纹、用例结果与测量原始数据的 JSON 验收附件。

# 第二版固定手牌目标实验

> 归档说明：本文保留开发阶段的实验数据与当时测试数量。历史 `artifacts/`、旧版源码快照及运行日志不随源码仓库发布；可按文中脚本重新生成。当前安装和验证命令以根目录 README 为准。

2026-10-20，macOS arm64、Python 3.12.6。代码位于 `src/bridge_dealer/v2.py`，
通过 `--backend v2` 显式启用，默认命令继续使用 v1。

本轮目标已覆盖：

1. 固定一家完整 13 张牌，均匀、有放回采样 100,000 副完整牌局。
2. 固定两家完整手牌，不重不漏枚举另外两家的全部分布。
3. 固定一家完整手牌，并指定其余 1～3 家牌型，均匀、有放回采样 100,000 副。
   `shape`、`shape_any` 和 `shapes` 均可用，已知牌可在任何座位。

## 实现与均匀性

没有额外牌型条件时，先移除全部已知牌，将余牌均匀洗牌后按各家的空位数切分。
每个具体分配对应相同数量的手内排列，因此完整牌局等概率。

有牌型条件时，编译所有兼容的四家花色长度矩阵。完整已知手牌会把对应行唯一确定；
剩余两家有序牌型确定后，最后一行由花色总张数推导，再检查其是否属于允许集合。
无序牌型和牌型并集先归一化、去重，不对重叠表达式重复计数。

设每门花色未固定牌数为 `z_s`，矩阵要求该家该花色还补 `r_ps` 张，则该矩阵的具体补全数为：

```text
w(matrix) = product_s [ z_s! / product_p(r_ps!) ]
total_deals = sum_matrix w(matrix)
```

用任意精度整数累计权重和 `randrange(total_deals)` 选择矩阵，再均匀分配每门花色的具体余牌。
一副合法牌唯一确定其矩阵，在该矩阵内的概率为 `1 / w(matrix)`，
因此完整牌局概率为 `(w / total_deals) / w = 1 / total_deals`。
这里不需要按 AKQJ 和零点牌继续分层，因为本轮没有新增 HCP 限制。

固定两家而不额外限制牌型时，直接遍历余下 26 张中所有 13 张子集；第三家拿子集，第四家拿补集。
座位保持区分，手内不排列，共 `C(26,13) = 10,400,600` 副。

`sample_batches()` 一次调用内只创建一个随机数生成器，分块不重置种子。
同条件、同种子、同算法和 Python 版本下，分块大小不改变拼接后的牌序。

## 实测

固定北家为 `QJ6.K652.J85.T98`。有序牌型依次为东家 3352、南家 2335、西家 5323。
下表每组均生成 100,000 副完整四家持牌，种子为 20261020。

| 条件 | 编译 | 生成 | 独立合法性检查 | 合法牌局总数 |
| --- | ---: | ---: | ---: | ---: |
| 仅固定北家 | <0.001 秒 | 2.430 秒 | 2.053 秒 | 84,478,098,072,866,400 |
| 再限东家有序牌型 | 0.054 秒 | 3.705 秒 | 2.803 秒 | 1,188,863,464,320,000 |
| 再限东、南两家有序牌型 | 0.001 秒 | 4.509 秒 | 2.323 秒 | 26,885,053,440,000 |
| 再限东、南、西三家有序牌型 | <0.001 秒 | 3.574 秒 | 2.532 秒 | 26,885,053,440,000 |
| 再限东、南、西三家无序牌型 | 0.007 秒 | 3.908 秒 | 2.254 秒 | 322,620,641,280,000 |

生成时间为 Python 持牌位掩码流收集到内存的耗时，不含 `Deal` 对象转换或文件输出。
各行是单次测量，受系统负载影响；两家和三家有序牌型的合法分布相同，时间差不代表复杂度差异。
共 500,000 副全部逐副通过每家 13 张、全副 52 张、已知牌归属和原始 JSON 牌型条件检查。
每组均检查固定种子的前缀复现，以及分块大小 1、7、4096 的牌序一致性。

固定北、南两家完整手牌后，实际遍历并验证 **10,400,600 副**，耗时 **34.705 秒**。
逐副检查固定手牌、另外两家各 13 张和余牌互补；用 26 位归属编码加 8 MiB 位图检查重复。
全部结果合法、重复数为零、数量恰好等于组合数，遍历正常穷尽。
该时间包括位图验证，不包括 `Deal` 对象转换和 PBN 导出；位图只用于实验验证，枚举器本身不保留已输出集合。

另通过实际 CLI 将三家无序牌型案例的 **100,000 副**全部写成 PBN，
进程启动、生成、对象转换和写文件合计 **24.272 秒**，文件 **9,588,895 字节**。
随后全部回读为 `Deal` 并重新验证，100,000 副全部通过。
PBN 导出的测量不含这次回读检查，也不含 DDS。

现有与新增测试合计 **140 项通过**。测试包括：

- 小空间与独立组合枚举、CP-SAT 的完整集合比较。
- 固定任意一家，以及固定任意两家的六种座位组合。
- 牌型并集重叠、各矩阵权重不等、三家有序牌型归约、无解。
- 随机种子、分块连续性、数量/时间上限、回调异常、输出管道关闭。
- CLI 的 PBN/JSONL、错误输入、拒绝覆盖已有文件，以及 v1 回归。

有限样本频率检查只用于发现实现错误；严格均匀性的依据是上述整数补全计数与等概率具体分配。

## 使用

```bash
cd BridgeDealer  # 进入已克隆并安装好的仓库

# 目标 1：固定一家，发 10 万副。
./dealer random examples/v2_fixed_one.json --backend v2 \
  --count 100000 --seed 20261020 --output fixed_one_100k.pbn

# 目标 2：固定两家，枚举另外两家；完整文件约有一千万副。
# 首次查看可加 --limit 10；完整枚举时去掉 --limit。
./dealer enumerate examples/v2_fixed_two.json --backend v2 --limit 10

# 目标 3：固定一家，限制其余三家的有序牌型，发 10 万副。
./dealer random examples/v2_fixed_one_three_shapes.json --backend v2 \
  --count 100000 --seed 20261020 --output three_shapes_100k.pbn
```

将 `shape` 换成 `shape_any` 可指定无序牌型；使用 `shapes: [[...], [...]]` 指定牌型并集。
随机采样有放回，可能重复；全枚举不重复。报告中的 `complete` 语义与 v1 相同：
达到输出上限、时间上限、回调停止或中断时不会声称已穷尽。

```python
from bridge_dealer import compile_sampler, enumerate_deals_v2

conditions = {
    "N": {"predeal": "QJ6.K652.J85.T98"},
    "E": {"shape": [3, 3, 5, 2]},
}
plan = compile_sampler(conditions)
print(plan.total_deals, plan.strategy)
for batch in plan.sample_batches(count=100000, seed=20261020, batch_size=4096):
    for deal in batch:
        consume(deal)  # 替换成自己的处理函数

# 枚举回调接收同一个不可变 Deal 类型。
result = enumerate_deals_v2(conditions, on_deal=consume, limit=10)
print(result.count, result.complete, result.stop_reason)
```

## 当前范围

- 纯已知牌补齐可处理任意部分预发牌；带牌型约束时要求至少一家完整手牌已知。
- 本轮实验未整合 HCP DP。未完整固定的玩家若有尚需筛选的 HCP 条件，明确报错并提示使用 v1。
  原先 `v2_batch_sampling_probe.py` 的牌型/HCP 研究原型仍单独保留。
- 默认最多编译 100,000 个合法牌型矩阵，可通过 Python 的 `max_matrices` 调整。
  超预算时整次编译报错，不会对已编译子集重新归一化后抽样。
- 尚未实现自动策略选择、按牌局编号取牌、断点续算或并行采样。
- `--max-attempts` 仅用于 v1；v2 按精确补全分布直接生成，没有拒绝尝试次数。
- 枚举的 `--time-limit` 包含编译时间，并在编译结束、逐副输出之间检查；编译内部不会抢占停止。

## 可复现实验与产物

```bash
cd BridgeDealer  # 进入已克隆并安装好的仓库
# 输出目录必须尚不存在。
.venv/bin/python research/v2_target_experiment.py artifacts/v2-target-experiment-new
MPLCONFIGDIR="$PWD/.cache/matplotlib" XDG_CACHE_HOME="$PWD/.cache" \
  .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests research/v2_target_experiment.py
.venv/bin/ruff format --check src tests research/v2_target_experiment.py
```

- 结构化报告（历史本地产物：`artifacts/v2-target-experiment-20261020/report.json`）：条件、数量、计数、时间、源文件哈希。
- 完整 10 万副 PBN（历史本地产物：`artifacts/v2-target-experiment-20261020/sample100k.pbn`）。
- 测试记录（历史本地产物：`artifacts/v2-target-tests.xml`）。
- 每个案例的条件 JSON 和前 10 副 PBN 保存在同一实验目录。

全量枚举在验证后丢弃各副持牌，保留数量、验证结果和重复检测位图的 SHA-256，避免写出一千多万副文本。

# 条件牌局的 DDS 定约成功率

> 归档说明：本文保留开发阶段的实验数据与当时测试数量。历史 `artifacts/`、旧版源码快照及运行日志不随源码仓库发布；可按文中脚本重新生成。当前安装和验证命令以根目录 README 为准。

2026-10-20 已实现 Python API 和 CLI，将条件处理、发牌、DDS 求解及结果统计放在一次总时间预算中。
默认预算 20 秒、最低目标 10,000 副，按实际求解速度调整样本数；默认返回 S/H/D/C/NT。
另有 `--exact` 不限时、不限数量的全枚举真值模式。
用户可以指定花色、庄家、阶数、预算及样本上限。

## 运行与参数

在已有的项目环境运行：

```bash
cd BridgeDealer  # 进入已克隆并安装好的仓库

# 北家三阶定约，五花色，总预算 20 秒。
./dealer success examples/v2_fixed_one.json --seed 20261020

# 只算南家 4S 和 4H；其余条件仍来自同一种条件 JSON。
./dealer success examples/north_south.json \
  --declarer S --level 4 --strains S,H --budget 20 --max-samples 20000

# 保存一份查询结果 JSON 和 stderr 运行报告。
./dealer success examples/v2_fixed_one_three_shapes.json --seed 20261020 \
  > success.json 2> success.log
```

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `conditions` | 必填 | 原有条件 JSON；已知牌、1～4 家有序/无序/并集牌型、精确/区间 HCP，可混合 |
| `--declarer` | `N` | 庄家，N/E/S/W |
| `--level` | `3` | 定约阶数 1～7；完成要求为阶数＋6 墩 |
| `--strains` | `S,H,D,C,NT` | 逗号分隔，可指定一种或多种；同一庄家和阶数，自动复用条件允许的花色对称性 |
| `--budget` | `20` | 总墙钟时间预算，正数，单位秒 |
| `--max-samples` | `100000` | 采样/求解数量上限；到时可以少于此数 |
| `--min-samples` | `10000` | 最低参考样本数；低于此数报告不足，不是精度保证 |
| `--threads` | `8` | DDS 线程数，1～32 |
| `--seed` | 自动生成 | 0～4294967295，结果中保留实际使用的种子 |
| `--dds-library` | 自动查找 | DDS 3.1.0 动态库路径；也可设置 `BRIDGE_DDS_LIBRARY` |
| `--exact` | 关闭 | 不限时间和数量，完整枚举所有合法牌并求 DDS 真值；忽略 budget 和 max-samples |
| `--no-suit-symmetry` | 关闭 | 对同一副物理牌逐花色求解，关闭总体概率的花色对称复用 |

默认寻找本仓库 `lib/libdds.dylib`（macOS）或 `lib/libdds.so`（Linux/WSL2）。先按根目录 README 编译 DDS；历史性能数据来自 macOS arm64。
直接使用 DDS 3.1.0 C ABI，核对库版本后加载；不需要 DDS 项目里的 Python 3.14 绑定。
现有 `random` / `enumerate` 命令及 `--backend v2` 的行为保留。
`success` 自行选择抽样策略，不需要指定 `--backend`。

Python API：

```python
from bridge_dealer import query_success

result = query_success(
    {
        "N": {"predeal": "QJ6.K652.J85.T98"},
        "E": {"shape_any": [5, 3, 3, 2], "hcp": [8, 15]},
    },
    declarer="N",
    level=3,
    strains=("S", "H", "D", "C", "NT"),
    budget_seconds=20,
    max_samples=100_000,
    min_samples=10_000,
    seed=20261020,
    threads=8,
    confidence=0.95,  # API 可调；CLI 默认 0.95。
)
for item in result["results"]:
    print(item["strain"], item["probability"], item["interval"])
```

API 的预算从函数入口开始；CLI 从包最早导入开始记时，把第三方依赖导入和条件读取计入预算。
下表实测还由外部新进程计时，包含解释器启动、全部计算和 JSON 输出。

## 结果与概率口径

概率是：在满足输入条件的全部完整牌局中均匀抽样，双方都看见四手牌并采用最佳打法时，
该庄家能完成指定定约的比例。它不是单明手实战胜率，也不考虑叫牌策略、失误和对手行为模型。

`results` 每项包含花色、阶数、庄家、所需墩数、成功次数、同批样本数、成功率和区间。
概率和区间均用 0～1 表示。顶层保留条件 SHA-256、种子、方法、生成策略、DDS 版本/路径、
已求解样本数 `samples`、已发牌数 `generated`、未完成数 `unresolved`、提案次数 `proposed`、
预算、总耗时和停止原因。
`sampling_seconds` 和 `dds_seconds` 只累计已完成阶段；总耗时还包含导入、通信及被终止的调用。

| `status` | 解释 | CLI 退出码 |
| --- | --- | --- |
| `exact` | 小空间全部合法牌局已完成求解；概率区间退化到精确值 | 0 |
| `estimated` | 完成数达到 `min_samples`；通常为 Monte Carlo 估计，需结合区间判断精度 | 0 |
| `insufficient_samples` | 时间或数量上限内未达到最低参考数；仍返回已完成计数与可用区间 | 3 |
| `infeasible` | 已证明没有合法牌局；概率/区间为 `null` | 1 |
| `error` | 例如 DDS 库缺失、ABI 不符或求解失败；概率/区间为 `null` | 2 |

输入格式错误由 CLI 报错并返回 2。零样本、尚未证明无解时，概率为 `null`、区间为 `[0,1]`。
`min_samples` 只是状态阈值，不代表达到固定误差；极难条件允许在预算内返回不足。
Monte Carlo 的 0% 或 100% 是样本比例，仍应读取区间，不能当作证明。

Monte Carlo 使用有放回均匀抽样。为允许按实际耗时停止，默认使用 Jeffreys beta-binomial
混合置信序列，而非固定样本数的普通区间。设成功次数为 s、样本数为 n：

```text
E_n(p) = B(s+1/2, n-s+1/2) / B(1/2,1/2) / [p^s (1-p)^(n-s)]
区间   = {p : E_n(p) < 1/alpha}
alpha  = (1-confidence) / 实际求解的代表花色数
```

由非负混合似然比鞅及 Ville 不等式得到全前缀有效性，再按实际求解的代表花色数分配 alpha。
因此默认 95% 是请求的各花色、所有已完成样本前缀的同时覆盖率，适用于按时间停止；
不同花色之间不需要独立，区间会比固定 n 的逐花色区间宽。
实际求解的各代表花色只有在整批完成后才一起更新，耗时较长的牌不会被跳过并替换。
若启用了花色对称复用，输出花色对应的是经过重标记的均匀牌局，不再保证同一副物理牌；
`same_deals_across_strains` 和 `strain_representatives` 明确标记。需要同一物理牌配对比较时关闭复用。

基础预发牌补齐空间不超过 32 副时，先枚举并过滤全部条件，再逐副求解。
只有全部完成才报告 `exact`。若中途停止，确定性枚举前缀不当作随机样本：
`probability=null`，`inference=census_bounds`，区间为 `[s/N,(s+N-n)/N]`。
大空间默认做 Monte Carlo，包括固定两家后的约一千万种分布；加 `--exact` 则完整枚举并计算真值。

按时间停止时，机器负载会影响样本数量。固定种子不保证不同运行得到相同最终数量；
要比较完全相同的随机前缀，应固定 `max_samples`、留足预算，并保持代码、依赖和生成路径一致。

## 时间预算如何执行

父进程设置总截止时间，默认预留 1 秒（预算很短时按比例缩小）用于终止 worker 和整理结果。
worker 的软截止时间再提前约 0.12 秒，批量从 1 副开始，按近期 DDS 速度自适应，最多 `200 / 实际求解花色数` 副。
长时间未返回的原生 DDS 调用由父进程终止自己的独立 worker，返回最近完成的整批结果。
强制终止路径另有慢 worker 测试；此前 13 类初版查询结果保留在历史实测表中。

条件编译使用约 0.6 秒的局部预算，混合条件的 HCP 候选路径最多先用约 0.25 秒。
若不能完整编译，丢弃部分计数表，退回覆盖完整空间的均匀提案，继续对全部条件整副筛选。
可行性检查最多约 0.2 秒，超时视为未知；父进程总截止时间仍优先。

默认目标是及时返回带状态和不确定性的查询结果，不保证任何条件下固定精度或固定样本数。
普通操作系统不是硬实时环境；系统暂停、严重调度延迟、异常文件/输出 I/O 无法给出绝对上界。
`within_budget` 反映内部返回时的实际检查；外部耗时证据见下面的冷启动实测。

## 抽样与 DDS 实现

- 纯已知牌：沿用 v2 的均匀补齐。
- 多家牌型：联合计算各花色的剩余张数矩阵，按具体牌的组合数做整数权重选择；
  不受约束的家均匀分配余牌，支持没有一家全牌已知的条件。
- HCP：按 A/K/Q/J/零点牌五类直接计数，优先编译最多两家的点力分配；其余条件整副拒绝筛选。
- 牌型与 HCP 混合：在完整编译的候选路径中选择提案空间更小的一种，始终筛选全部输入条件。
  北家 37 点这类低接受率条件可以直接构造，不依赖从普通洗牌中碰运气。

每种完整提案都均匀覆盖其定义的空间，整副拒绝筛选后仍为目标条件下的均匀牌局。
编译超时不会把已经遍历的部分分支重新归一化，避免由时间限制引入抽样偏差。

当前默认使用持久线程池，每个线程持有一个独立 DDS SolverContext，使用 10～20 MB 的 small TT，
避免过多大表增加内存负担。ctypes 调用期间释放 GIL，原生求解可并行；结果按提交顺序返回。

DDS 通过二进制牌位掩码接收完整牌局，不逐副转换 PBN，不重复启动外部程序。
只判断定约是否完成，不计算不需要的精确墩数：从庄家左手首攻，询问防守方是否能取得
`8-level` 墩，使用 DDS C context API 的目标墩数模式；若达不到，庄家能完成定约。
已对照 DDS 官方 3 副牌例、4 个庄家、5 种花色、7 个阶数，共 420 个判断。

## 不限时查询真值

```bash
# 只有 85 个合法分布，完整求解五种花色。
./dealer success examples/four_hands_85.json --exact

# 固定两家，枚举其余两家的全部 10,400,600 种分布并求 DDS。
./dealer success examples/v2_fixed_two.json --exact --strains S,H,D,C,NT
```

Python 使用 `query_success(conditions, exact=True)`；也可显式传 `budget_seconds=None`。
此模式忽略采样数量上限，不运行随机采样，不因达到 10,000 或 100,000 副而停止。
先尝试 v2 的组合枚举；条件超出 v2 的范围时，使用通用 CP-SAT 完整枚举，包括多家牌型和 HCP。
逐批送 DDS，内存中不保存全部牌局，不维护随枚举规模增长的去重集合。
全部完成才返回 `status=exact`、`stop_reason=exhausted`、`population_size` 和精确比例；
`budget_seconds`、`max_samples`、`within_budget` 为 `null`。
无解单独返回 `infeasible`。可以按 Ctrl-C 中止，此时不能称为真值。

运行期间在 checkpoint 时每约 2 秒向 stderr 输出一次已完成数量，最终结果仍是一份 stdout JSON。
条件宽松时完整空间可能极大；不限时不意味着一定能在现实可用时间内穷尽。

## 加速与样本目标

检测全部 24 种花色置换，只接受同时保持每家具体预发牌和整个允许牌型集合不变的置换。
同一对称轨道的花色有相同的总体成功率，可只求解一个代表并复用估计和区间；
例如仅 HCP 或无序牌型条件时四种有将概率相同，五个输出只需要求解一种有将与无将。
这不是复制独立样本，样本数按实际随机抽样次数报告。固定完整手牌通常没有这种对称性。

20 秒内尽量达到 10,000 副是目标，不是对所有条件和硬件的数量保证。
低于 10,000 副返回 `insufficient_samples`；可增加预算或算力，也可缩小花色范围。
优化前后实测和仍待解决的瓶颈见 [加速实验](success-optimization.md)。

## 初版历史冷启动实测（10 秒）

Apple M1、macOS 14.5 arm64、Python 3.12.6、DDS 3.1.0，DDS 线程数 4。
每个案例启动一个全新 CLI 进程，外层 `subprocess.run(timeout=10)`；
初版使用预算 10 秒、最大样本 100,000、最低参考数 100、北家庄家、三阶定约、种子 20261020。
每个样本同时算 S/H/D/C/NT，表中概率为该次样本估计，只有 `exact` 行是精确比例。

| 条件（点击查看输入） | 外部秒数 | 已算副数 | 3S | 3H | 3D | 3C | 3NT | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 无附加条件（历史本地产物：`artifacts/success-budget-20261020/unconstrained.json`） | 9.529 | 1,754 | 23.26% | 21.89% | 22.41% | 22.75% | 18.30% | estimated |
| 固定北家全牌（历史本地产物：`artifacts/success-budget-20261020/fixed_one.json`） | 9.485 | 4,491 | 6.57% | 9.02% | 7.39% | 7.10% | 5.81% | estimated |
| 固定南北两家全牌（历史本地产物：`artifacts/success-budget-20261020/fixed_two.json`） | 9.481 | 10,961 | 0.00% | 0.00% | 0.00% | 0.00% | 0.00% | estimated |
| 固定北家＋东家有序牌型（历史本地产物：`artifacts/success-budget-20261020/fixed_one_1_shapes.json`） | 9.489 | 5,143 | 9.43% | 10.87% | 1.50% | 13.84% | 5.56% | estimated |
| 固定北家＋东南有序牌型（历史本地产物：`artifacts/success-budget-20261020/fixed_one_2_shapes.json`） | 9.504 | 6,296 | 0.89% | 9.42% | 1.97% | 14.60% | 8.69% | estimated |
| 固定北家＋其余三家有序牌型（历史本地产物：`artifacts/success-budget-20261020/fixed_one_3_shapes.json`） | 9.513 | 4,964 | 1.11% | 10.48% | 2.28% | 14.99% | 9.15% | estimated |
| 固定北家＋其余三家无序牌型（历史本地产物：`artifacts/success-budget-20261020/fixed_one_3_unordered.json`） | 9.548 | 5,354 | 5.06% | 8.70% | 5.40% | 6.03% | 5.73% | estimated |
| 北 12～16 点、南 8～12 点（历史本地产物：`artifacts/success-budget-20261020/hcp_two.json`） | 9.623 | 646 | 36.84% | 37.00% | 38.70% | 35.60% | 32.04% | estimated |
| 北南均 4333 无序牌型＋上述点力（历史本地产物：`artifacts/success-budget-20261020/shape_hcp_two.json`） | 9.588 | 957 | 14.00% | 14.84% | 14.11% | 13.27% | 26.85% | estimated |
| 北 37 点＋4333 无序牌型（历史本地产物：`artifacts/success-budget-20261020/rare_37_hcp_and_shape.json`） | 9.627 | 18,071 | 99.57% | 99.55% | 99.44% | 99.51% | 100.00% | estimated |
| 北 37 点、其余三家各 1 点（历史本地产物：`artifacts/success-budget-20261020/four_hcp.json`） | 9.529 | 21,185 | 99.50% | 99.43% | 99.40% | 99.51% | 100.00% | estimated |
| 四家各 11 点（无解）（历史本地产物：`artifacts/success-budget-20261020/infeasible.json`） | 1.093 | 0 | — | — | — | — | — | infeasible |
| 仅两张未知牌（精确）（历史本地产物：`artifacts/success-budget-20261020/tiny_exact.json`） | 0.862 | 2 | 100.00% | 0.00% | 100.00% | 0.00% | 0.00% | exact |

13 类查询全部小于 10 秒，最长 9.627 秒；11 类 Monte Carlo 查询完成 646～21,185 副，
即 3,230～105,925 个牌局/花色判断。无解和小空间案例提前返回。
固定两家案例样本中五花色均未成功，报告区间仍有非零上界，不能据此证明真实成功率为零。
这些测量覆盖所列案例，不代表穷尽了全部输入或硬件环境。

固定北家 `QJ6.K652.J85.T98` 的同批 4,491 副示例，外部耗时 9.485 秒：

| 定约 | 成功次数 / 样本数 | 成功率估计 | 默认 95% 同时区间 |
| --- | ---: | ---: | ---: |
| 3S | 295 / 4491 | 6.57% | 5.11%～8.26% |
| 3H | 405 / 4491 | 9.02% | 7.31%～10.94% |
| 3D | 332 / 4491 | 7.39% | 5.85%～9.17% |
| 3C | 319 / 4491 | 7.10% | 5.59%～8.85% |
| 3NT | 261 / 4491 | 5.81% | 4.44%～7.41% |

## 验证与复现

初版回归记录为 169 passed（288 条依赖警告），约 28.60 秒；当前新增优化和精确模式验证见加速实验。
新增测试覆盖独立组合枚举、预发牌、点力/牌型混合、四家 HCP、37 点稀有条件、
超时编译不可用部分表、无解与样本不足区分、慢 worker 终止、缺失 DDS 库、
420 个官方 DDS 判断和默认 10 秒冷启动查询。
置信序列还以 n≤10 的所有 Bernoulli 路径作精确概率枚举，检查全前缀错误排除概率。

```bash
MPLCONFIGDIR="$PWD/.cache/matplotlib" XDG_CACHE_HOME="$PWD/.cache" \
  .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests research/success_budget_experiment.py
.venv/bin/ruff format --check src tests research/success_budget_experiment.py

# 重新运行 13 类实测，输出目录必须尚不存在。
MPLCONFIGDIR="$PWD/.cache/matplotlib" XDG_CACHE_HOME="$PWD/.cache" \
  .venv/bin/python research/success_budget_experiment.py artifacts/success-budget-repeat
```

证据文件：

- 完整回归 XML（历史本地产物：`artifacts/success-tests.xml`）
- 13 类输入、结果、区间、耗时和版本（历史本地产物：`artifacts/success-budget-20261020/report.json`）
- [可复现实测脚本](../research/success_budget_experiment.py)
- [公开 API 与截止时间控制](../src/bridge_dealer/success.py)
- [条件抽样](../src/bridge_dealer/query_sampling.py)、[独立 worker](../src/bridge_dealer/query_worker.py)、[DDS C ABI](../src/bridge_dealer/dds_native.py)

实测报告保存所有 `src/bridge_dealer/*.py`、实测脚本及 DDS 原生库的 SHA-256，
将本轮性能和正确性证据关联到实际源码和库文件。

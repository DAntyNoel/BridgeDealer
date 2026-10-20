# DDS 加速与不限时真值模式

> 归档说明：本文保留开发阶段的实验数据与当时测试数量。历史 `artifacts/`、旧版源码快照及运行日志不随源码仓库发布；可按文中脚本重新生成。当前安装和验证命令以根目录 README 为准。

2026-10-20，本机 Apple M1、8 GB、macOS 14.5、DDS 3.1.0。
目标调整为默认 **20 秒、尽量至少 10,000 副**，同时提供不限时完整枚举的真值查询。
本轮已实现并实测加速；达到样本目标的范围见表，较难条件仍明确报告不足。

## 已实现的方法

1. **花色对称复用。** 枚举 24 种花色置换，检查它是否保持每家的预发具体牌和整个牌型集合不变。
   HCP 不依赖花色。同一对称轨道内的定约有相同总体成功率，只求解一个代表。
   仅给 HCP、无序牌型等条件时，五花色输出常可从五项求解减到一种有将＋无将。
   固定一家具体全牌一般没有此对称性，不做错误复用。
2. **持久的独立 DDS context。** 每个线程独占 SolverContext，ctypes 原生调用释放 GIL，
   使用 small TT（默认 10 MB、上限 20 MB）。与旧方案每个大表 95～160 MB 相比减少内存压力。
   同一线程持续处理任务，按提交顺序收集结果，慢牌不会被跳过。
3. **增加批量、减少等待。** 自适应批量从原最多 16 副扩大到 `200 / 实际求解花色数` 副，
   同时继续保留整批 checkpoint 和父进程截止时间。默认预留 1 秒处理终止和结果。
4. **默认 8 个求解线程。** 在独立 small-TT context 的实测中，8 个线程比 4 个线程更快，
   固定一家从 6,955 副提高到 11,017 副。参数仍可调整；其他机器需重新测量。

复用仅针对总体概率，不能把同一观察复制成四份独立样本。`samples` 仍为实际抽样次数；
`solved_strains`、`strain_representatives`、`same_deals_across_strains`、`dds_positions` 报告实际工作。
若需要同一副物理牌的各花色配对结果，加 `--no-suit-symmetry`。
统计区间仍按完成的随机前缀计算，适用于按时间停止；对称复用的输出共享代表花色的统计量。

## 20 秒冷启动对比

固定种子 20261020、北家庄家、三阶定约、返回 S/H/D/C/NT。
每个测量启动独立 CLI 进程，外部计时覆盖启动、条件处理、发牌、DDS 和结果 JSON。
旧实现与第一轮优化均使用 4 线程、20 秒预算；随后单独测优化后的 8 线程。
当前默认是优化后的 8 线程。8 线程未测项保留已测 4 线程结果，不能当作默认性能。

| 条件 | 旧实现 4 线程副数 | 优化后 4 线程副数 | 优化后 8 线程副数 | 最右已测方案秒数 | 10,000 副目标 |
| --- | ---: | ---: | ---: | ---: | --- |
| 无附加条件 | 2,472 | 5,113 | 7,801 | 18.955 | 未达到 |
| 固定一家全牌 | 6,914 | 6,955 | 11,017 | 18.990 | 达到 |
| 固定两家全牌 | 10,444 | 20,012 | 未测 | 18.992 | 达到 |
| 固定一家＋其余三家有序牌型 | 10,170 | 11,374 | 13,612 | 18.989 | 达到 |
| 两家 HCP 区间 | 1,171 | 2,825 | 3,733 | 19.125 | 未达到 |
| 两家无序牌型＋HCP | 1,871 | 4,190 | 未测 | 19.006 | 未达到 |
| 北家 37 点＋无序 4333 | 48,273 | 100,000 | 未测 | 12.267 | 达到 |

上述完整测量全部在 20 秒内返回。固定一家、固定两家和固定一家＋其余三家牌型的示例曾达到目标；
无附加条件、两家点力以及点力牌型混合的示例仍低于目标。
北家 37 点＋无序 4333 的 4 线程案例在 12.267 秒达到 100,000 副上限，提前返回。
最终不显式指定线程数、使用交付默认值重复固定一家查询时，18.990 秒完成 8,446 副，
低于第一次 8 线程的 11,017 副，返回 `insufficient_samples`。因此固定一家这一例的实测范围为
8,446～11,017 副，不能保证每次都达到目标。表中的具体牌局、阶数和系统负载都会显著影响求解难度。

最初 10 秒探索中出现过一次外部计时超时，保留了部分结果，未计入完整测量。
当前预留时间扩大到 1 秒；普通操作系统不提供暂停、严重调度延迟等情况下的绝对实时保证。

## 为什么有些条件仍达不到一万副

原始记录中，常见条件的发牌仅耗时约 0.02～0.4 秒，DDS 耗时超过 8 秒。
继续将发牌改写成更快代码，对这些查询的总耗时帮助很小。新测量仍由 DDS 主导。
增加线程、批量大小与任务排列只能带来部分改善；完整二十格墩数表需更多搜索，
当前只判断是否达到目标墩数，比计算完整表更适合此任务。

在这台机器上，两家 HCP 的已测优化达到约 3,733 副/20 秒；到 10,000 副还需约 2.7 倍吞吐。
应优先考虑更多 CPU 算力、缩小请求花色范围，或为重复条件缓存可复用的已解牌局。
缓存不会增加第一次查询的新独立样本数。GPU 对分支密集的 DDS 搜索不是直接替换方案。

另一条研究路线是廉价预测模型＋少量 DDS 残差校正，例如
`均值(大批预测) + 均值(DDS真值 - 对应预测)`。这需要独立验证预测误差和新的统计区间，
本轮没有将近似预测计作真实 DDS 样本，也没有用未经验证的模型返回真值。

10,000 副也不自动等于 1 个百分点精度：当前允许按时间停止且对多花色同时覆盖的置信序列，
在五个独立代表参数、成功率约 50%、n=10,000 时半宽约 2.17 个百分点。
若以精度为验收目标，应直接设定所需误差，再选择算力和数量预算。

## 不限时查询真值

```bash
# 默认 20 秒、目标 10,000 副、8 个独立 DDS context。
./dealer success examples/v2_fixed_one.json --seed 20261020

# 85 个合法分布，不限时，完整求五花色精确真值。
./dealer success examples/four_hands_85.json --exact

# 固定南北两家，完整枚举另外两家的 10,400,600 种分布。
./dealer success examples/v2_fixed_two.json --exact --strains S,H,D,C,NT
```

Python 使用 `query_success(conditions, exact=True)`，可以设置 `on_progress=callback` 读取进度。
`--exact` 忽略时间和样本上限，支持完整预发牌、部分预发牌、多家牌型/HCP 与混合条件。
能使用 v2 组合枚举时优先使用，否则回到通用 CP-SAT 枚举，保持不重不漏。
小批量连续求解、累计计数；全部穷尽才返回 `exact` 和退化到精确值的概率区间。
CLI 在 checkpoint 时每约 2 秒向 stderr 输出进度，最终一份结果 JSON 写入 stdout。

固定两家后的千万级枚举可能运行数小时或更久，取决于牌例；更宽松条件可能无法在现实时间内穷尽。
精确模式不会把运行到某个大样本数当成真值。Ctrl-C 会终止自己的 worker，退出码 130。

## 证据与复现

- 4 线程优化前后 14 次完整冷启动（历史本地产物：`artifacts/success-optimization-20261020/final20/query-report.json`）
- 8 线程四类冷启动（历史本地产物：`artifacts/success-optimization-20261020/threads8.json`）
- 原生线程数、批量与任务布局探测（历史本地产物：`artifacts/success-optimization-20261020/microbench.json`）
- 独立 context / TT 选择探测（历史本地产物：`artifacts/success-optimization-20261020/context-microbench.json`）
- 本轮完整回归 XML（历史本地产物：`artifacts/success-optimization-20261020/tests.xml`）
- 85 副全枚举真值（历史本地产物：`artifacts/success-optimization-20261020/four_hands_85-result.json`）
- 最终默认配置端到端验证（历史本地产物：`artifacts/success-optimization-20261020/delivery-validation.json`）

最终默认配置还实际验证了 85 副不限时全枚举：0.989 秒、五花色真值依次为 1、0、1、0、0。

当前完整回归 192 项通过，288 条来自既有依赖的警告，耗时约 34.42 秒。
新增测试覆盖 context 与原生批量求解的一致性、花色置换、小空间精确概率、
无限时枚举不能被采样上限截断、无解和精确进度输出。

对比实验需自行保留旧版 `bridge_dealer/` 源码作为 baseline，并先运行 `research/success_budget_experiment.py artifacts/success-budget-repeat` 生成输入条件；仓库不包含旧版源码快照。

对比报告记录源码和库哈希。`baseline-src` 保存旧实现，`benchmarked-src` 保存第一轮被测优化实现，
`threaded-benchmarked-src` 保存 8 线程测量实现；最终默认线程值改为 8，查询逻辑一致。

```bash
.venv/bin/python research/success_speed_experiment.py artifacts/success-optimization-repeat \
  --baseline-src /path/to/saved-baseline-src \
  --conditions-dir artifacts/success-budget-repeat --budget 20

MPLCONFIGDIR="$PWD/.cache/matplotlib" XDG_CACHE_HOME="$PWD/.cache" \
  .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests research/success_speed_experiment.py research/success_budget_experiment.py
```

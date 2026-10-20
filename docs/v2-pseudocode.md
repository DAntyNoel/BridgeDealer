# 第二版伪代码：牌型 → 大牌 → 小牌

本文将 v2-mathematical-model.md 转成实现规格，未修改生产后端。

批量采样补充：同一条件生成 10 万副、主要限制 1～2 家时，采用 [批量采样设计](v2-batch-sampling-design.md) 的“编译一次、反复抽样”方案。本文件的单次 SAMPLE 会扫描全部候选矩阵，不应直接循环调用 10 万次。

提供三个独立入口：

- ENUMERATE(raw)：逐副输出全部合法牌局，不重不漏。
- COUNT_ALL(raw)：返回任意精度整数总数，不展开具体小牌。
- SAMPLE(raw, seed)：返回一副由种子控制的均匀随机合法牌局；无解则返回 NO_SOLUTION。

枚举无需先调用 COUNT_ALL。独立随机抽样允许重复，完整枚举不重复。

## 1. 数据约定

~~~text
PLAYERS = [N, E, S, W]              # 下标 0..3
SUITS   = [SPADES, HEARTS, DIAMONDS, CLUBS]
HONORS  = [A, K, Q, J]
ZEROS   = [T, 9, 8, 7, 6, 5, 4, 3, 2]
VALUE   = {A:4, K:3, Q:2, J:1}
ZERO4   = (0, 0, 0, 0)

Conditions C:
    shapes[p]                 # 去重、排序后的有序牌型列表
    shape_set[p]              # 同一集合，供成员查询
    low[p], high[p]           # HCP 闭区间
    fixed_owner[s][rank]      # AKQJ 的固定主人，或 NONE
    fixed_zeros[p][s]         # 固定零点牌的具体点数集合
    fixed_count[p][s]         # 固定大牌与零点牌的合计张数
    free_zeros[s]             # 该花色未固定零点牌的有序列表
    shape_min_suffix[i][s]    # 尚未选择的玩家 i..3 的长度下界之和
    shape_max_suffix[i][s]    # 同上，长度上界之和
    impossible               # 已证实条件互相矛盾

HonorOption o:
    owner[rank]              # 该花色全部 AKQJ 的主人，包括已知大牌
    q[p]                     # HCP，包括已知大牌的点数
    remaining[p]             # 尚需分配的零点牌张数
    weight                   # 该花色零点牌的补全数，大整数

MatrixContext X:
    C, L
    options[s]               # 各花色的具体 HonorOption 列表
    K[s][q]                  # 相同 HCP 向量下的 weight 之和
    min_suffix[j][p]         # 剩余花色 j..3 的逐家最小 HCP 之和
    max_suffix[j][p]         # 剩余花色 j..3 的逐家最大 HCP 之和
    feasible_memo            # 仅属于当前 C、L 的布尔缓存
    count_memo               # 仅属于当前 C、L 的计数缓存
~~~

向量加减和比较按分量进行。组合生成器 COMBINATIONS(pool, k) 按固定顺序输出不同的 k 元子集；COMBINATIONS([], 0) 必须输出一次空集。集合相减后保留约定的牌点顺序。

所有计数、权重和累计权重使用任意精度整数。固定候选顺序；集合和散列表不能决定遍历顺序。

## 2. 条件标准化

~~~text
function NORMALIZE(raw):
    validate 输入字段、座位、牌型长度和 HCP 区间格式
    validate 每家已知牌不超过 13 张，且没有重复或跨家冲突
    C = 初始化 Conditions：集合为空、fixed_owner 全为 NONE、张数为 0
    C.impossible = false
    U = 按字典序排列的所有非负整数四元组，其和为 13

    for p in PLAYERS:
        C.low[p], C.high[p] = 规范化 HCP，缺省为 [0, 37]

        if 指定有序 shape:
            rows = {shape}
        else if 指定无序 shape_any:
            rows = shape_any 的所有不同排列
        else if 指定 shapes:
            rows = 指定有序牌型的集合
        else:
            rows = U

        从已知牌中填写 fixed_owner、fixed_zeros、fixed_count
        C.shapes[p] = SORT(DEDUP(
            row for row in rows
            if row[s] >= C.fixed_count[p][s] for every s
        ))
        C.shape_set[p] = SET(C.shapes[p])

        if C.shapes[p] 为空:
            C.impossible = true
        if 该家固定大牌的 HCP > C.high[p]:
            C.impossible = true

    for s in SUITS:
        C.free_zeros[s] = ZEROS 减去所有玩家在该花色的固定零点牌

    if SUM(C.low) > 40 or SUM(C.high) < 40:
        C.impossible = true

    if C.impossible:
        return C

    C.shape_min_suffix[4] = ZERO4
    C.shape_max_suffix[4] = ZERO4
    for i from 3 down to 0:
        for s in SUITS:
            C.shape_min_suffix[i][s] =
                C.shape_min_suffix[i+1][s] + MIN(row[s] for row in C.shapes[i])
            C.shape_max_suffix[i][s] =
                C.shape_max_suffix[i+1][s] + MAX(row[s] for row in C.shapes[i])
    return C
~~~

输入格式错误应报错；格式正确但无解的条件对应空枚举、计数 0 和 NO_SOLUTION。

## 3. 第一层：流式枚举长度矩阵

~~~text
generator ITER_MATRICES(C):
    if C.impossible:
        return

    L = 空的 4×4 矩阵

    generator SEARCH(i, residual):
        # 玩家 0..i-1 已选；residual 是四门花色尚未分配的张数
        if ANY(residual < C.shape_min_suffix[i]) or
           ANY(residual > C.shape_max_suffix[i]):
            return

        if i == 3:
            # 第四家的整行由前三家唯一决定
            if residual in C.shape_set[3]:
                L[3] = residual
                yield IMMUTABLE_COPY(L)
            return

        for row in C.shapes[i]:
            if ANY(row > residual):
                continue
            L[i] = row
            yield from SEARCH(i + 1, residual - row)

    yield from SEARCH(0, (13, 13, 13, 13))
~~~

这里固定 NESW 顺序，方便复现。将允许牌型最少的玩家放在前面是可选优化，但内部必须保留实际座位标签。

## 4. 为一个花色生成具体大牌候选和组合权重

~~~text
function LOCAL_OPTIONS(C, s, lengths):
    zero_counts = (SIZE(C.fixed_zeros[p][s]) for p in PLAYERS)
    fixed_owners = TUPLE(C.fixed_owner[s][rank] for rank in HONORS)
    key = (lengths, fixed_owners, zero_counts)

    if LOCAL_LRU_CACHE contains key:
        return LOCAL_LRU_CACHE[key]

    choices = []
    for rank in HONORS:
        if C.fixed_owner[s][rank] is NONE:
            choices.append(PLAYERS)
        else:
            choices.append([C.fixed_owner[s][rank]])

    result = []
    z = 9 - SUM(zero_counts)

    for owner in CARTESIAN_PRODUCT(choices):       # 最多 256 次
        h = 新建的四元素全零可变向量
        q = 新建的四元素全零可变向量
        for rank in HONORS:
            p = owner[rank]
            h[p] += 1
            q[p] += VALUE[rank]

        remaining = lengths - h - zero_counts
        if ANY(remaining < 0):
            continue
        assert SUM(remaining) == z

        weight = FACTORIAL(z) // PRODUCT(FACTORIAL(n) for n in remaining)
        # 上式为精确整数除法，不经过浮点数
        result.append(IMMUTABLE HonorOption(owner, q, remaining, weight))

    LOCAL_LRU_CACHE[key] = IMMUTABLE(result)
    return LOCAL_LRU_CACHE[key]
~~~

该缓存只保存大牌归属、张数和权重，不保存零点牌身份，因此固定零点牌张数足以作为键的一部分。实际发牌时仍从 C.free_zeros[s] 取具体牌。

大牌 q、h 已包括固定大牌；不能再次加点或扣张数。

## 5. 为固定矩阵建立独立上下文

~~~text
function PREPARE_MATRIX(C, L):
    X = 新的 MatrixContext(C, L)

    for s in SUITS:
        lengths = (L[p][s] for p in PLAYERS)
        X.options[s] = LOCAL_OPTIONS(C, s, lengths)
        if X.options[s] 为空:
            return NONE

        K = 默认值为 0 的映射
        for o in X.options[s]:
            K[o.q] += o.weight
        X.K[s] = 按 q 排序的 K

    X.min_suffix[4] = ZERO4
    X.max_suffix[4] = ZERO4
    for j from 3 down to 0:
        for p in PLAYERS:
            X.min_suffix[j][p] =
                X.min_suffix[j+1][p] + MIN(o.q[p] for o in X.options[j])
            X.max_suffix[j][p] =
                X.max_suffix[j+1][p] + MAX(o.q[p] for o in X.options[j])

    X.feasible_memo = {}
    X.count_memo = {}
    return X

function WITHIN_BOUNDS(X, j, points):
    return ALL(
        points[p] + X.min_suffix[j][p] <= X.C.high[p]
        and points[p] + X.max_suffix[j][p] >= X.C.low[p]
        for p in PLAYERS
    )

function STATE_KEY(j, points):
    assert SUM(points) == 10 * j
    return (j, points[N], points[E], points[S])
    # points[W] = 10*j - points[N] - points[E] - points[S]
~~~

逐家极值只用于必要条件剪枝；它们可能由不同的大牌方案取得。具体可行性仍由以下递归保证。

## 6. 枚举只需布尔可达性

~~~text
function HAS_COMPLETION(X, j, points):
    if not WITHIN_BOUNDS(X, j, points):
        return false
    if j == 4:
        return true

    key = STATE_KEY(j, points)
    if X.feasible_memo contains key:
        return X.feasible_memo[key]

    for q in X.K[j].keys_in_order:
        if HAS_COMPLETION(X, j + 1, points + q):
            X.feasible_memo[key] = true
            return true

    X.feasible_memo[key] = false
    return false
~~~

它忽略补全数，只判断有没有解；遇到第一条可行后缀即停止搜索。全部局部候选的 weight 都为正，所以忽略权重不会影响可达性。

## 7. 第二层：枚举满足 HCP 的具体大牌方案

~~~text
generator ITER_HONORS(X, j, points, chosen):
    if not HAS_COMPLETION(X, j, points):
        return

    if j == 4:
        yield TUPLE(chosen)
        return

    for o in X.options[j]:
        yield from ITER_HONORS(
            X,
            j + 1,
            points + o.q,
            chosen + [o]          # 新列表，不修改祖先节点
        )
~~~

这里只能迭代 options 中的具体方案，不能改成只迭代 K 的 HCP 向量。K 合并了不同的实际大牌归属，仅用于计数与可达性计算。

## 8. 第三层：组合枚举零点牌

~~~text
generator PARTITIONS(pool, remaining):
    for groupN in COMBINATIONS(pool, remaining[N]):
        rest1 = pool - groupN
        for groupE in COMBINATIONS(rest1, remaining[E]):
            rest2 = rest1 - groupE
            for groupS in COMBINATIONS(rest2, remaining[S]):
                groupW = rest2 - groupS
                assert SIZE(groupW) == remaining[W]
                yield (groupN, groupE, groupS, groupW)

generator ITER_ZEROS(X, honors, s, parts):
    if s == 4:
        yield ASSEMBLE(X.C, honors, parts)
        return

    for partition in PARTITIONS(
        X.C.free_zeros[s], honors[s].remaining
    ):
        yield from ITER_ZEROS(X, honors, s + 1, parts + [partition])

function ASSEMBLE(C, honors, parts):
    hands = 四个空的牌集合

    for s in SUITS:
        for p in PLAYERS:
            for rank in C.fixed_zeros[p][s]:
                hands[p].add(CARD(s, rank))

        for rank in HONORS:
            p = honors[s].owner[rank]
            hands[p].add(CARD(s, rank))       # 包括已知大牌，只加入一次

        for p in PLAYERS:
            for rank in parts[s][p]:
                hands[p].add(CARD(s, rank))

    return IMMUTABLE_DEAL(hands)             # 每次输出独立快照
~~~

不能把四门花色的笛卡尔积一次性物化；递归每次只维护一个组合路径。

## 9. 完整枚举入口

~~~text
generator ENUMERATE(raw):
    C = NORMALIZE(raw)

    for L in ITER_MATRICES(C):
        X = PREPARE_MATRIX(C, L)
        if X is NONE:
            continue

        for honors in ITER_HONORS(X, 0, ZERO4, []):
            yield from ITER_ZEROS(X, honors, 0, [])

        # 进入下一矩阵时，旧 X 的 DP 缓存可以释放
~~~

完整耗尽生成器才表示枚举完成。用户取消、输出数量限制或超时，只得到遍历前缀；这些情况不能标记为已经枚举全部解。

## 10. 精确计数

~~~text
function COUNT_SUFFIX(X, j, points):
    if not WITHIN_BOUNDS(X, j, points):
        return 0
    if j == 4:
        return 1

    key = STATE_KEY(j, points)
    if X.count_memo contains key:
        return X.count_memo[key]

    total = BIGINT(0)
    for (q, weight) in X.K[j].items_in_order:
        total += weight * COUNT_SUFFIX(X, j + 1, points + q)

    X.count_memo[key] = total
    return total

function COUNT_ALL(raw):
    C = NORMALIZE(raw)
    total = BIGINT(0)

    for L in ITER_MATRICES(C):
        X = PREPARE_MATRIX(C, L)
        if X is not NONE:
            total += COUNT_SUFFIX(X, 0, ZERO4)

    return total
~~~

COUNT_SUFFIX 对应数学模型的 B_j(t)。它已经乘入未来花色的小牌补全数，因此到终点必须返回 1，而不是再次计算或乘入小牌补全数。

## 11. 无偏整数随机原语

~~~text
function RAND_BELOW(rng, n):
    require n > 0
    if n == 1:
        return 0

    bits = BIT_LENGTH(n - 1)
    repeat:
        u = rng.GET_BITS(bits)
        if u < n:
            return u

function WEIGHTED_CHOICE(rng, entries):
    # entries 按稳定顺序存放 (item, nonnegative_integer_weight)
    total = SUM(weight for (item, weight) in entries)
    require total > 0

    u = RAND_BELOW(rng, total)
    for (item, weight) in entries:
        if u < weight:
            return item
        u -= weight

function UNIFORM_PARTITION(rng, pool, remaining):
    cards = COPY(pool)
    for i from SIZE(cards) - 1 down to 1:    # Fisher–Yates
        k = RAND_BELOW(rng, i + 1)
        SWAP(cards[i], cards[k])

    result = []
    offset = 0
    for p in PLAYERS:
        result.append(cards[offset : offset + remaining[p]])
        offset += remaining[p]
    assert offset == SIZE(cards)
    return result
~~~

RAND_BELOW 使用拒绝法，不能直接用固定宽度随机数取模。NEW_VERSIONED_RNG(seed) 应绑定固定算法和版本，其 GET_BITS(k) 返回指定数量的伪随机位。

UNIFORM_PARTITION 将均匀洗牌结果按固定张数切开。每个具体分配都对应相同的 PRODUCT(FACTORIAL(remaining[p])) 个牌序，所以所有分配等概率。

## 12. 流式选择牌型，再均匀抽取实际牌局

先用加权蓄水池选择一个牌型矩阵，避免保存全部矩阵及其权重。它仍需完成所有矩阵的计数；只降低存储需求，不消除预处理时间。

~~~text
function SAMPLE(raw, seed):
    C = NORMALIZE(raw)
    rng = NEW_VERSIONED_RNG(seed)

    total = BIGINT(0)
    selected_L = NONE

    for L in ITER_MATRICES(C):
        X = PREPARE_MATRIX(C, L)
        if X is NONE:
            continue

        weight = COUNT_SUFFIX(X, 0, ZERO4)   # C(L)
        if weight == 0:
            continue

        total += weight
        if selected_L is NONE or RAND_BELOW(rng, total) < weight:
            selected_L = IMMUTABLE_COPY(L)

    if selected_L is NONE:
        return NO_SOLUTION

    # 全局遍历不保留每个矩阵的 DP 表；重建最终选中矩阵的上下文
    X = PREPARE_MATRIX(C, selected_L)
    points = ZERO4
    honors = []

    for j from 0 to 3:
        entries = []
        for o in X.options[j]:
            weight = o.weight * COUNT_SUFFIX(X, j + 1, points + o.q)
            if weight > 0:
                entries.append((o, weight))

        o = WEIGHTED_CHOICE(rng, entries)
        honors.append(o)
        points += o.q

    parts = []
    for s in SUITS:
        parts.append(UNIFORM_PARTITION(
            rng, C.free_zeros[s], honors[s].remaining
        ))

    return ASSEMBLE(C, honors, parts)
~~~

加权蓄水池的不变量是：处理到任意位置，已见矩阵 i 被保留的概率等于 C(L_i) / SUM_seen C(L)。新矩阵以新权重 / 新总权重替换当前矩阵，旧矩阵概率同时乘以旧总权重 / 新总权重，因此不变量保持成立。

后续大牌选择对应权重 w_s × B_(j+1)，小牌均匀选择；最终每副合法牌局的概率相同。

反复调用同一种子会重现同一副牌；批量抽样应复用一个随机数生成器状态，不能每副牌重置相同种子。若条件固定、需要多次抽样，可另设编译阶段缓存牌型权重或建立带子树权重的搜索树，避免每次扫描所有矩阵。

## 13. 实现时必须保持的不变量

- 每个实际座位和花色都保留标签；只去重相同的牌型输入，不合并对称牌局。
- 每个大牌方案的 owner 保存 AKQJ 的具体归属；相同 HCP 不代表相同方案。
- 所有固定大牌计入 q 和 h 一次；ASSEMBLE 从固定零点牌开始，再加入全部大牌。
- 每门花色 remaining 非负且总和等于 free_zeros 的长度。
- DP key 可以省略 W 的点数，但只能在每次处理完整花色时这样做。
- DP 缓存属于特定条件及长度矩阵；不能仅凭 (j, points) 跨矩阵复用。
- 输出必须冻结或复制；不能让之前输出的牌局随回溯缓冲区一起改变。
- 零张组合也有一个结果：空组合。组合数为 1，不能当成没有方案。
- 输出限制、超时和取消属于未完成枚举，不属于无解或完整枚举。
- 稳定的种子结果依赖规范化顺序、候选顺序、随机算法和算法版本。

数学模型已在上一轮做过计数交叉校验；本文件是伪代码规格，尚未把这些流程实现为第二版生产程序。

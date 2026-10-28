"""去重：URL 精确去重（靠 id）+ 标题相似度去重（字符 bigram Jaccard）。

为什么不用 MinHash / 编辑距离：
    库里只有几千条，两两比较的量级完全撑得住；
    编辑距离对"同一事件改写过的中文标题"极不友好（改几个字距离就很大），
    bigram Jaccard 抓的是共用的二字词，更符合中文标题的改写习惯。
"""

from __future__ import annotations

import re

from .clean import similarity, title_key

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

# ---------------- 实体 + 数字指纹 ----------------
# 字符相似度只能抓"标题改写不多"的重复。「雅培 3.85 亿美元和解」这件事，
# 四家媒体的标题分别写成"雅培同意支付3.85亿美元""Abbott to pay $385 million…"
# 「雅培就婴儿配方奶粉案达成和解」，两两相似度都低于 0.82，一条都拦不住。
# 但它们的共同点是：同一个品牌 + 同一串金额。所以另算一个"实体+数字"指纹，
# 两边都对得上就判为同一事件。
ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "abbott": ("雅培", "abbott"),
    "danone": ("达能", "danone"),
    "nestle": ("雀巢", "nestle", "nestlé"),
    "feihe": ("飞鹤", "feihe"),
    "yili": ("伊利", "yili"),
    "mengniu": ("蒙牛", "mengniu"),
    "junlebao": ("君乐宝", "junlebao"),
    "biostime": ("合生元", "健合", "biostime", "h&h"),
    "ausnutria": ("澳优", "佳贝艾特", "ausnutria", "kabrita"),
    "beingmate": ("贝因美", "beingmate"),
    "synutra": ("圣元", "synutra"),
    "a2": ("a2 milk", "a2milk", "a2牛奶"),
    "fonterra": ("恒天然", "fonterra"),
    "synlait": ("新莱特", "synlait"),
    "friesland": ("菲仕兰", "frieslandcampina", "美素佳儿", "friso"),
    "mead": ("美赞臣", "mead johnson", "reckitt"),
    "lactalis": ("兰特黎斯", "lactalis"),
    "wyeth": ("惠氏", "wyeth"),
    "aptamil": ("爱他美", "aptamil", "nutricia"),
    "hipp": ("喜宝", "hipp"),
    "bellamy": ("贝拉米", "bellamy"),
    "healthbank": ("孩子王", "爱婴室"),
    "pampers": ("帮宝适", "pampers"),
}

# 金额/数量：3.85亿、385 million、$385m、1.2 billion、25万 都要能对上。
# 归一化成"万"为单位的整数，1 亿 = 10000 万，1e8/1e4 = 10000，正好对齐。
_NUM_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(亿|万|million|billion|bn|m(?![a-z]))?", re.I)
_UNIT = {"亿": 1e8, "万": 1e4, "million": 1e6, "billion": 1e9, "bn": 1e9, "m": 1e6}
MIN_AMOUNT = 1e6  # 小于 100 万的数字（第几批、第几届、3 款）不放进指纹，否则误合


def amount_marks(text: str) -> frozenset[int]:
    out: set[int] = set()
    for m in _NUM_RE.finditer(text or ""):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        val *= _UNIT.get(unit, 1)
        if val >= MIN_AMOUNT:
            out.add(round(val / 1e4))
    return frozenset(out)


def event_fingerprint(title: str) -> tuple[str, frozenset[int]] | None:
    """同一品牌 + 同一笔金额 → 判为同一事件。缺一个都不算。"""
    low = (title or "").lower()
    for canon, variants in ENTITY_ALIASES.items():
        if any(v in low for v in variants):
            marks = amount_marks(low)
            return (canon, marks) if marks else None
    return None


def _quality(item: dict) -> tuple:
    """同一事件里，谁"信息更全"——优先级高、有真实时间、摘要长的优先。"""
    return (
        PRIORITY_RANK.get(item.get("priority", "P1"), 1),
        0 if item.get("published_at") and not item.get("published_guessed") else 1,
        -len(item.get("summary") or ""),
        -len(item.get("title") or ""),
    )


def _disp(item: dict) -> str:
    return item.get("title_zh") or item.get("title") or ""


def dedupe(
    candidates: list[dict],
    known_ids: set[str],
    recent: list[dict],
    *,
    threshold: float = 0.82,
    max_per_event: int = 2,
    known_title_keys: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """返回 (入库条目, 被合并掉的条目)。

    known_ids:         库中已有的 id（URL 级去重）
    recent:            最近 N 天的库内条目（事件级去重的比对基准）
    known_title_keys:  全库标题归一化集合。recent 只覆盖 N 天，但同一篇稿被
                       不同站转载可能隔十几天才被抓到，光靠 recent 抓不住，
                       所以这里用全库标题做一次 O(1) 精确拦截。
    """
    stats: list[dict] = []
    fresh = []
    known_keys = set(known_title_keys or ())
    for item in candidates:
        if item["id"] in known_ids:
            stats.append({**item, "_drop": "已收录"})
            continue
        # 标题归一化后完全相同 = 同一篇稿（通常是各家转载），直接拦掉。
        # 不用相似度阈值：阈值放到 1.0 才是"完全一样"，这里就是想抓这种情况。
        key = title_key(item.get("title_zh") or item.get("title") or "")
        if key and key in known_keys:
            stats.append({**item, "_drop": "同标题已收录"})
            continue
        fresh.append(item)

    # 质量高的先入，保证一个事件里留下信息最全的那条
    fresh.sort(key=_quality)

    clusters: list[dict] = []
    fp_index: dict[tuple, dict] = {}
    for rep in recent:
        key = title_key(_disp(rep))
        fp = event_fingerprint(_disp(rep))
        if key:
            cluster = {"key": key, "items": [rep], "count": 1, "external": True, "fp": fp}
            clusters.append(cluster)
            if fp and fp not in fp_index:
                fp_index[fp] = cluster

    kept: list[dict] = []
    for item in fresh:
        key = title_key(_disp(item))
        fp = event_fingerprint(_disp(item))
        matched = None
        matched_sim = 0.0
        for cluster in clusters:
            sim = similarity(key, cluster["key"])
            if sim >= threshold:
                matched, matched_sim = cluster, sim
                break
        if matched is None and fp is not None:
            # 标题写法差得远，但品牌与金额完全一致：同一事件的不同报道
            cluster = fp_index.get(fp)
            if cluster is not None:
                matched, matched_sim = cluster, threshold
        if matched is None:
            cluster = {"key": key, "items": [item], "count": 1, "external": False, "fp": fp}
            clusters.append(cluster)
            if fp and fp not in fp_index:
                fp_index[fp] = cluster
            if key:
                known_keys.add(key)
            kept.append(item)
            continue

        matched["count"] += 1
        if matched.get("fp") is None and fp is not None:
            matched["fp"] = fp
            fp_index.setdefault(fp, matched)
        if matched["external"]:
            # 与库内已有条目重复：不重复入库，但把缺失的信息补上去
            stats.append({**item, "_drop": "与库内条目重复"})
            for old in matched["items"]:
                if not old.get("summary") and item.get("summary"):
                    old["summary"] = item["summary"]
                    old["_patched"] = True
            continue

        # 标题几乎一模一样（同一个 publisher 的两个站会同步发同一篇稿）只留一条；
        # 标题不同但讲同一件事的，才允许 max_per_event 条并存。
        limit_here = 1 if matched_sim >= 0.95 else max_per_event
        if matched["count"] <= limit_here:
            kept.append(item)
        else:
            stats.append({**item, "_drop": "同一稿件重复" if limit_here == 1 else "同事件第 3 条起"})

    return kept, stats

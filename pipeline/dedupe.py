"""去重：URL 精确去重（靠 id）+ 标题相似度去重（字符 bigram Jaccard）。

为什么不用 MinHash / 编辑距离：
    库里只有几千条，两两比较的量级完全撑得住；
    编辑距离对"同一事件改写过的中文标题"极不友好（改几个字距离就很大），
    bigram Jaccard 抓的是共用的二字词，更符合中文标题的改写习惯。
"""

from __future__ import annotations

from .clean import similarity, title_key

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _quality(item: dict) -> tuple:
    """同一事件里，谁"信息更全"——优先级高、有真实时间、摘要长的优先。"""
    return (
        PRIORITY_RANK.get(item.get("priority", "P1"), 1),
        0 if item.get("published_at") and not item.get("published_guessed") else 1,
        -len(item.get("summary") or ""),
        -len(item.get("title") or ""),
    )


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
    for rep in recent:
        key = title_key(rep.get("title_zh") or rep.get("title") or "")
        if key:
            clusters.append({"key": key, "items": [rep], "count": 1, "external": True})

    kept: list[dict] = []
    for item in fresh:
        key = title_key(item.get("title_zh") or item.get("title") or "")
        matched = None
        matched_sim = 0.0
        for cluster in clusters:
            sim = similarity(key, cluster["key"])
            if sim >= threshold:
                matched, matched_sim = cluster, sim
                break
        if matched is None:
            clusters.append({"key": key, "items": [item], "count": 1, "external": False})
            if key:
                known_keys.add(key)
            kept.append(item)
            continue

        matched["count"] += 1
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

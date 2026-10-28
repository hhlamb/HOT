#!/usr/bin/env python
"""清理"同标题重复入库"的存量数据。

为什么会有：早期去重只在最近 7 天的窗口里比对，跨检索式（channel / retail /
demography 三个 Google News 查询覆盖同一篇稿）与跨月转载都漏了过去。
入库层的 known_title_keys 已经改成全库比对，新稿不会再重复；这份脚本是给
历史数据补一刀。

保留规则（同标题组内）：
    1) 有真实发布时间、不是猜的
    2) 有正文/配图/摘要的优先（详情页不至于空）
    3) 源优先级高（P0 > P1 > P2）
    4) 都不分上下时留最新的一条

用法：
    python scripts/purge_dupes.py --dry-run   # 只看要删哪条
    python scripts/purge_dupes.py             # 真删并重建 index.json / latest.js
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import store  # noqa: E402
from pipeline.clean import title_key  # noqa: E402
from pipeline.sources import load_config  # noqa: E402

CST = timezone(timedelta(hours=8))
RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _pub_of(row: dict) -> float:
    return store._pub_ts(row)


def _disp(row: dict) -> str:
    return row.get("title_zh") or row.get("title") or ""


def _keep_score(row: dict) -> tuple:
    """越大越该留（元组从小到大排序，越小的越先留下）。"""
    return (
        0 if (row.get("summary") or "").strip() else 1,
        0 if (row.get("body") or "").strip() else 1,
        0 if (row.get("image") or "").strip() else 1,
        -len((row.get("summary") or "").strip()),  # 摘要更全的优先
        RANK.get(row.get("priority") or "P1", 1),
        0 if row.get("published_at") and not row.get("published_guessed") else 1,
        -store._pub_ts(row),  # 同分时留最新的
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-fingerprint", action="store_true", help="只删同标题，不做品牌+金额/时段合并")
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args()

    index_path = store.DATA_DIR / "index.json"
    old_index = json.loads(index_path.read_text(encoding="utf-8"))
    last_updated = old_index.get("last_updated") or datetime.now(CST).isoformat(timespec="seconds")

    rows: list[dict] = []
    for path in sorted(p for p in store.DATA_DIR.iterdir() if store.MONTH_FILE.match(p.name)):
        for row in store._iter_lines(path):
            row["_month"] = path.stem
            rows.append(row)

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = title_key(_disp(row))
        if key:
            groups[key].append(row)

    keep_ids: set[str] = set()
    drop: list[dict] = []
    dup_groups = 0
    for key, items in groups.items():
        if len(items) < 2:
            continue
        dup_groups += 1
        items.sort(key=_keep_score)
        keep_ids.add(items[0]["id"])
        drop.extend(items[1:])

    print(f"现有 {len(rows)} 条 · 重复组 {dup_groups} 组 · 将删除 {len(drop)} 条")
    shown = 0
    for key, items in groups.items():
        if len(items) < 2:
            continue
        if shown >= args.show:
            break
        shown += 1
        items.sort(key=_keep_score)
        print(f"\n  {len(items)} 条 | {_disp(items[0])[:52]}")
        for i, row in enumerate(items):
            mark = "留" if i == 0 else "删"
            print(
                f"    {mark} {row['source_id'][:18]:18} {(row.get('published_at') or '时间未知')[:10]}"
                f" 摘要{len((row.get('summary') or '')):>3}字 {row['id']}"
            )

    drop_ids = {d["id"] for d in drop}

    # 第二刀：标题写法差得远、但品牌 + 金额/时段对得上的，按"同一事件"合并。
    # 口径与入库层一致（pipeline/dedupe.event_fingerprint），每个事件最多留 2 条。
    if not args.no_fingerprint:
        from pipeline.dedupe import event_fingerprint

        rest = [r for r in rows if r["id"] not in drop_ids]
        rest.sort(key=_keep_score)
        clusters: list[dict] = []
        index: dict[tuple, dict] = {}
        for row in rest:
            fp = event_fingerprint(_disp(row))
            target = None
            if fp:
                for mark in fp[1]:
                    cand = index.get((fp[0], mark))
                    # 只在 7 天内合并：同金额/同时段但隔几周的稿子往往是后续进展
                    # （「雅培 3.85 亿和解后分拆营养业务」），合并会丢掉新信息
                    if cand and abs(_pub_of(row) - _pub_of(cand["items"][0])) <= 7 * 86400:
                        target = cand
                        break
            if target is None:
                target = {"items": []}
                clusters.append(target)
                if fp:
                    for mark in fp[1]:
                        index.setdefault((fp[0], mark), target)
            target["items"].append(row)

        fp_drop = 0
        for c in clusters:
            if len(c["items"]) > 2:
                for row in c["items"][2:]:
                    drop_ids.add(row["id"])
                    fp_drop += 1
        print(f"指纹合并（同品牌 + 同金额/同时段）：再删 {fp_drop} 条")

    print(f"合计删除 {len(drop_ids)} 条 → 保留 {len(rows) - len(drop_ids)} 条")
    if args.dry_run:
        return 0

    kept = [r for r in rows if r["id"] not in drop_ids]
    by_month: dict[str, list[dict]] = defaultdict(list)
    for row in kept:
        month = row.pop("_month", None) or (row.get("published_at") or row.get("fetched_at") or "")[:7]
        by_month[month].append(row)

    for path in sorted(p for p in store.DATA_DIR.iterdir() if store.MONTH_FILE.match(p.name)):
        rows_here = by_month.get(path.stem, [])
        if not rows_here:
            path.unlink()
            print(f"  {path.name} 已空，删除")
            continue
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows_here),
            encoding="utf-8",
        )
        tmp.replace(path)
        print(f"  {path.name}: {len(rows_here)} 条")

    now = datetime.now(CST)
    counts = Counter(((r.get("published_at") or r.get("fetched_at") or "")[:7]) for r in kept)
    index = store.write_index(
        kept,
        dict(counts),
        now,
        extra={
            "total": len(kept),
            "with_summary": sum(1 for r in kept if r.get("summary")),
            "retention_days": load_config()["pipeline"]["retention_days"],
        },
    )
    index["last_updated"] = last_updated
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    n = store.write_latest_js(kept, load_config()["output"]["latest_js_max_items"], last_updated)
    print(f"  index.json total={index['total']} · latest.js {n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

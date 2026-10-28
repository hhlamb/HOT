#!/usr/bin/env python
"""按主题硬门槛清洗**已入库**的数据。

为什么需要它：JSONL 只追加，一条稿子进来后默认躺 90 天。改了 relevance 的
主题门槛只影响新抓的；库里那批「进口腌芒果二氧化硫超标」「日本通报我国出口
烤花生不合格」得单独扫一遍。逻辑与 scripts/run.py 的筛选完全一致：

    品牌点名源（带 must_match）→ 标题+摘要里没点到该品牌就删
    其余源                    → 标题里没有母婴/奶粉核心词就删（relevance.core_hit）
    导航页/功能页标题          → 删（clean.is_nav_title）

用法：
    python scripts/purge_weak.py --dry-run    # 只看要删什么
    python scripts/purge_weak.py              # 真删，并重建 index.json / latest.js
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import store  # noqa: E402
from pipeline.clean import is_junk_title  # noqa: E402
from pipeline.relevance import WEAK_TOPIC, core_hit  # noqa: E402
from pipeline.sources import load_config, load_sources  # noqa: E402

CST = timezone(timedelta(hours=8))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--show", type=int, default=40, help="预演时打印多少条待删标题")
    args = ap.parse_args()

    must: dict[str, list[str]] = {}
    for src in load_sources(enabled_only=False):
        words = [str(w).strip().lower() for w in (src.get("must_match") or []) if str(w).strip()]
        if words:
            must[src["id"]] = words

    old_index = json.loads((store.DATA_DIR / "index.json").read_text(encoding="utf-8"))
    last_updated = old_index.get("last_updated") or datetime.now(CST).isoformat(timespec="seconds")

    keep_all: list[dict] = []
    dropped: list[tuple[str, dict, str]] = []
    per_source_drop: Counter[str] = Counter()

    for path in sorted(p for p in store.DATA_DIR.iterdir() if store.MONTH_FILE.match(p.name)):
        keep_here: list[dict] = []
        for row in store._iter_lines(path):
            title = row.get("title") or ""
            title_zh = row.get("title_zh") or ""
            body = " ".join([title, title_zh, row.get("summary") or ""]).lower()
            sid = row.get("source_id") or ""

            if is_junk_title(title) or is_junk_title(title_zh):
                reason = "导流/选购软文或导航页"
            elif sid in must:
                reason = "" if any(w in body for w in must[sid]) else "没点到品牌名"
            else:
                word, group = core_hit(title + " " + title_zh)
                if not word:
                    reason = "标题无母婴/奶粉核心词"
                elif group != "人群品类" and WEAK_TOPIC.search(title + " " + title_zh):
                    reason = "乳业治理/ESG稿"
                else:
                    reason = ""

            if reason:
                dropped.append((reason, row, path.name))
                per_source_drop[sid] += 1
            else:
                keep_here.append(row)
        keep_all.extend(keep_here)

    keep_all.sort(key=store._pub_ts, reverse=True)
    print(f"现有 {len(keep_all) + len(dropped)} 条 → 保留 {len(keep_all)} 条，删除 {len(dropped)} 条")
    by_reason = Counter(r for r, _, _ in dropped)
    print("  原因：" + " · ".join(f"{k} {v}" for k, v in by_reason.most_common()))
    print("  按源：" + " · ".join(f"{s} {n}" for s, n in per_source_drop.most_common(12)))
    for reason, row, _ in dropped[: args.show]:
        print(f"  - [{reason}] {row.get('source_id', '')} | {(row.get('title_zh') or row.get('title') or '')[:60]}")
    if len(dropped) > args.show:
        print(f"  …另有 {len(dropped) - args.show} 条")

    if args.dry_run:
        return 0

    # 按月重写（内容变少，直接覆盖；月份空了就删文件）
    by_month: dict[str, list[dict]] = {}
    for row in keep_all:
        month = row.get("_month") or (row.get("published_at") or row.get("fetched_at") or "")[:7]
        by_month.setdefault(month, []).append(row)
        row.pop("_month", None)
    for path in sorted(p for p in store.DATA_DIR.iterdir() if store.MONTH_FILE.match(p.name)):
        month = path.stem
        rows = by_month.pop(month, [])
        if not rows:
            path.unlink()
            print(f"  {path.name} 已空，删除")
            continue
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows),
            encoding="utf-8",
        )
        tmp.replace(path)
        print(f"  {path.name}: {len(rows)} 条")

    now = datetime.now(CST)
    counts = Counter(
        ((r.get("published_at") or r.get("fetched_at") or "")[:7]) for r in keep_all
    )
    index = store.write_index(
        keep_all,
        dict(counts),
        now,
        extra={
            "total": len(keep_all),
            "with_summary": sum(1 for r in keep_all if r.get("summary")),
            "retention_days": load_config()["pipeline"]["retention_days"],
        },
    )
    # last_updated 沿用旧值：清洗不是一次抓取，写成"刚刚"会让页面谎报更新时间
    index["last_updated"] = last_updated
    (store.DATA_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    n = store.write_latest_js(keep_all, load_config()["output"]["latest_js_max_items"], last_updated)
    print(f"  index.json total={index['total']} · latest.js {n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

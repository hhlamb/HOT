#!/usr/bin/env python
"""一次性清理：按条件从 data/*.jsonl 里删掉已经入库的条目。

什么时候需要它：JSONL 是只追加的，一条内容进来后默认会留 90 天。
如果某个源灌进了明显不相干的东西（比如内容农场的 SEO 稿），改了规则也管不到
已经写进去的行，就需要这个脚本来清。

用法：
    python scripts/purge.py --url-contains dailypolitical.com --dry-run
    python scripts/purge.py --url-contains dailypolitical.com mtltimes.ca
    python scripts/purge.py --ids 3f2a1b...,9c8d7e...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.store import DATA_DIR, MONTH_FILE, _iter_lines  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url-contains", nargs="*", default=[], help="URL 含这些子串就删")
    ap.add_argument("--ids", nargs="*", default=[], help="按 id 删")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.url_contains and not args.ids:
        print("至少要给 --url-contains 或 --ids", file=sys.stderr)
        return 2

    removed = 0
    for path in sorted((p for p in DATA_DIR.iterdir() if MONTH_FILE.match(p.name))):
        keep, drop = [], []
        for row in _iter_lines(path):
            hit = row.get("id", "") in args.ids or any(
                sub.lower() in (row.get("original_url") or "").lower() for sub in args.url_contains
            )
            (drop if hit else keep).append(row)
        if not drop:
            continue
        removed += len(drop)
        print(f"{path.name}: 保留 {len(keep)} 条，删除 {len(drop)} 条")
        for row in drop:
            print(f"  - [{row.get('source_id')}] {row.get('title', '')[:70]}")
        if args.dry_run:
            continue
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in keep),
            encoding="utf-8",
        )
        tmp.replace(path)

    print(f"{'（预演）' if args.dry_run else ''}共删除 {removed} 条")
    if removed and not args.dry_run:
        print("记得重跑一次 python scripts/run.py 更新 index.json 与 latest.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

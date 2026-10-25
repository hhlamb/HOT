#!/usr/bin/env python
"""对比两次探测结果（本地 vs GitHub Actions），输出四档判定。

用法：
    python scripts/compare_probe.py probe-local.json probe-actions.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load(path: str) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {row["id"]: row for row in data["sources"]}


def usable(row: dict) -> bool:
    """能拿到内容就算可用：有 feed 条目，或有可解析的 HTML 列表。"""
    return row["items"] > 0 or row["candidate_links"] > 0


def judge(local: dict | None, actions: dict | None) -> tuple[str, str]:
    l_ok = bool(local and usable(local))
    a_ok = bool(actions and usable(actions))

    if a_ok and l_ok:
        return "P0 两地都通", "每轮必抓"
    if a_ok and not l_ok:
        return "P1 仅 Actions 通", "可用；本地调试时会被跳过，属正常现象"
    if l_ok and not a_ok:
        return "弃用 仅本地通", "生产跑不了，本地能通没有意义"
    return "弃用 两地都不通", "移出 sources.yaml 或置 enabled: false"


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    local = load(sys.argv[1])
    actions = load(sys.argv[2])
    ids = sorted(set(local) | set(actions))

    buckets: dict[str, list[str]] = {}
    rows = []
    for sid in ids:
        l, a = local.get(sid), actions.get(sid)
        bucket, advice = judge(l, a)
        buckets.setdefault(bucket, []).append(sid)
        name = (a or l or {}).get("name", sid)
        detail = ""
        if not (a and usable(a)):
            detail = (a or {}).get("error", "无数据")[:50]
        elif not (l and usable(l)):
            detail = (l or {}).get("error", "无数据")[:50]
        rows.append((bucket, name, sid, detail, advice))

    lines = ["# 两地探测对比", "", f"本地: `{sys.argv[1]}`　Actions: `{sys.argv[2]}`", "", "## 汇总", ""]
    for bucket in ("P0 两地都通", "P1 仅 Actions 通", "弃用 仅本地通", "弃用 两地都不通"):
        lines.append(f"- **{bucket}**: {len(buckets.get(bucket, []))} 个 {buckets.get(bucket, [])}")

    lines += ["", "## 明细", "", "| 判定 | 源 | id | 说明 |", "|---|---|---|---|"]
    for bucket, name, sid, detail, _ in sorted(rows):
        lines.append(f"| {bucket} | {name} | `{sid}` | {detail.replace('|', '/')} |")

    report = "\n".join(lines) + "\n"
    print(report)
    Path("probe-compare.md").write_text(report, encoding="utf-8")
    print("已写入 probe-compare.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

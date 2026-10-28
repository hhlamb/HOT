"""一次性清洗：把品牌点名源里没点到名的稿子从库里剔掉。

背景：must_match 之前是"命中就放行、没命中走通用关键词闸门"，
于是 Google 检索式顺带返回的泛母婴稿（奶粉横评、门店专栏、幼儿酱油…）
靠"奶粉/母婴"过线，混进 brand_* 源冒充该品牌新闻。
run.py 已改成硬门槛，这个脚本负责把改动之前已经入库的脏数据清掉。

用法：python scripts/clean_brand_offtopic.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from pipeline import store  # noqa: E402

CZ = timezone(timedelta(hours=8))


def brand_rules() -> dict[str, list[str]]:
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text(encoding="utf-8"))
    rules: dict[str, list[str]] = {}
    # sources.yaml 是分组列表，逐层找带 must_match 的源
    def walk(node):
        if isinstance(node, dict):
            sid = node.get("id")
            if sid and node.get("must_match"):
                rules[sid] = [str(w).strip().lower() for w in node["must_match"] if str(w).strip()]
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(cfg)
    return rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写回，否则只报告")
    args = ap.parse_args()

    rules = brand_rules()
    print("品牌点名源：", ", ".join(f"{k}({len(v)}词)" for k, v in rules.items()))
    if not rules:
        print("没读到 must_match，退出")
        return 1

    total_drop = 0
    for path in sorted(store.month_files()):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        keep, drop = [], []
        for r in rows:
            words = rules.get(r.get("source_id") or "")
            if not words:
                keep.append(r)
                continue
            hay = ((r.get("title") or "") + " " + (r.get("summary") or "")).lower()
            (keep if any(w in hay for w in words) else drop).append(r)
        print(f"{path.name}: 共 {len(rows)}，丢弃 {len(drop)}")
        for r in drop:
            print("   -", (r.get("title") or "")[:60])
        total_drop += len(drop)
        if args.apply and drop:
            path.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in keep), encoding="utf-8")

    print("合计丢弃", total_drop, "条")
    if not args.apply:
        print("（试运行，未写盘；加 --apply 生效）")
        return 0

    now = datetime.now(CZ)
    items, counts = store.load_recent(90, now)
    old = json.loads((ROOT / "data" / "index.json").read_text(encoding="utf-8"))
    # last_updated 必须沿用旧值：这轮没抓任何新东西，只删了 7 条脏数据。
    # 写成本机当前时间会让页面显示"更新于刚刚"，读者以为有新内容进来。
    stamp = old.get("last_updated") or now.isoformat(timespec="seconds")
    store.write_index(items, counts, now, extra={
        "sources_total": old.get("sources_total", 0),
        "sources_ok": old.get("sources_ok", 0),
        "with_summary": sum(1 for i in items if i.get("summary")),
        "retention_days": old.get("retention_days", 90),
    })
    # write_index 内部会拿 now 覆盖 last_updated，这里再写回一次
    idx_path = ROOT / "data" / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx["last_updated"] = stamp
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    n = store.write_latest_js(items, 400, stamp)
    print(f"index.json 已重算：{len(items)} 条；latest.js {n} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

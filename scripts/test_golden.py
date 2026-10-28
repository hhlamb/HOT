#!/usr/bin/env python
"""固定样本回归测试。

做的事只有一件：把 tests/golden.yaml 里那批"人工确认过该收 / 该丢"的标题
重新跑一遍 judge()，看判定有没有翻面。

为什么需要：改一个词、调一个阈值，很可能把某个源整个筛空，而线上表现只是
"这个源今天没有新闻"——等发现往往已经过去一两天。这里跑一遍不到 1 秒，
翻面立刻报出来。

用法：
    python scripts/test_golden.py            # 有翻面就退出码 1
    python scripts/test_golden.py -v         # 逐条打印
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from pipeline.relevance import core_hit, judge  # noqa: E402
from pipeline.sources import load_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    min_score = cfg.get("relevance", {}).get("min_score", 4.0)
    require_core = bool(cfg.get("relevance", {}).get("require_core_title", True))

    path = ROOT / "tests" / "golden.yaml"
    cases = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("cases") or []
    if not cases:
        print("tests/golden.yaml 里没有样本")
        return 1

    bad: list[tuple[dict, bool]] = []
    for case in cases:
        title = case["title"]
        want = str(case.get("expect", "keep")).lower() == "keep"
        lang = (case.get("lang") or "zh").lower()
        got, score, _, _ = judge(
            title,
            "",
            min_score,
            require_core=require_core,
            strict_weak=lang != "en",
        )
        if got != want:
            bad.append((case, got))
        elif args.verbose:
            print(f"  ok   {'收' if got else '丢'} {score:>5.1f} {core_hit(title)[1] or '-':6} | {title[:56]}")

    total = len(cases)
    if not bad:
        print(f"固定样本 {total} 条全部符合预期")
        return 0

    print(f"固定样本 {total} 条，{len(bad)} 条翻面：\n")
    for case, got in bad:
        print(f"  期望{'收' if case['expect'] == 'keep' else '丢'}·实际{'收' if got else '丢'} | {case['title'][:60]}")
        print(f"      来源 {case.get('source', '-')} · {case.get('why', '')}")
    print("\n如果这次改动是有意的，改 tests/golden.yaml 里的 expect 再提交；否则回退规则。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""存量数据清洗：把已经落盘的旧条目按当前的入库口径重洗一遍。

为什么需要单独一个脚本
----------------------
`pipeline/guard.py` 只在写盘前拦截新条目，已经躺在 `data/*.jsonl` 里的
历史条目不会自己变干净。而这次发现的摘要串稿（聚合页把别人的正文当成
这一篇的）、同一段摘要挂到多条、配图是二维码/站标/`http://` 链接，
都是历史遗留，必须离线洗一次。

用法
----
    python scripts/repair_data.py            # 演练，只打印会改什么
    python scripts/repair_data.py --write    # 真写盘

写盘是幂等的：再跑一次不会有任何改动。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import guard, store  # noqa: E402
from pipeline.relevance import classify  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CST = timezone(timedelta(hours=8))


def rule(title: str, char: str = "─", width: int = 72) -> None:
    print(f"\n{char * 3} {title} " + char * max(0, width - len(title) - 5))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真正写回文件（默认只演练）")
    args = ap.parse_args()
    now = datetime.now(CST)

    files = store.month_files()
    if not files:
        print("data/ 下没有月度文件")
        return 1

    seen: dict[str, str] = {}
    files_changed = 0
    total = 0
    touched = 0
    samples: dict[str, list[str]] = {}

    for path in sorted(files):
        rows = list(store._iter_lines(path))
        out_lines: list[str] = []
        dirty = False
        for row in rows:
            total += 1
            before = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            item = dict(row)

            notes = guard.clean_item(item, seen)

            # 分类口径也一起更新：老数据是按"摘要 + 标题等权"打的标签，
            # 会把「达能参与能源项目」判成市场数据，这里按新的标题加权重打。
            # 一级标签（主分类）变了才动，避免把历史的多标签全冲掉。
            title = item.get("title_zh") or item.get("title") or ""
            tags = classify(title, item.get("summary") or "")
            old_tags = item.get("tags") or []
            if tags and (old_tags[:1] != tags[:1] or len(old_tags) > len(tags)):
                item["tags"] = tags
                notes.append("retag")

            after = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            if after != before:
                dirty = True
                touched += 1
                for n in notes:
                    key = n.split("(")[0]
                    samples.setdefault(key, []).append(title[:38])
            out_lines.append(after if args.write else before)

        if dirty and args.write:
            path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        if dirty:
            files_changed += 1

    rule("清洗结果")
    print(f"扫描 {total} 条，需要改动 {touched} 条，涉及 {files_changed} 个文件")

    rule("按类型统计")
    if not samples:
        print("没有需要改动的条目")
    for key in sorted(samples, key=lambda k: -len(samples[k])):
        names = samples[key]
        print(f"\n{key}  ×{len(names)}")
        for t in names[:4]:
            print(f"    · {t}")
        if len(names) > 4:
            print(f"    … 另有 {len(names) - 4} 条")

    if not args.write:
        print("\n（演练模式，未写盘。加 --write 生效）")
        return 0

    # 索引与 file:// 兜底文件跟着重算，否则首页读到的是旧 total 和旧标签表
    items, counts = store.load_recent(3650, now)
    store.write_index(items, counts, now)
    store.write_latest_js(items, 60, now.isoformat(timespec="seconds"))
    print(f"\n已重算 index.json / latest.js（{len(items)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

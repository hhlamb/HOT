"""运行简报：同时输出到控制台和 GitHub Actions 的 Step Summary。

不往仓库里写日志文件——Actions 的 Step Summary 本身就是可读的简报页面，
写进仓库只会让 diff 噪声变大。
"""

from __future__ import annotations

import os
from datetime import datetime

from .sources import ROOT


def _md_table(rows: list[dict]) -> str:
    lines = ["| 源 | 状态 | 抓到 | 相关性通过 | 新增 | 耗时 |", "|---|---|---|---|---|---|"]
    for row in rows:
        status = "✅" if not row["error"] else "⚠️"
        note = row["error"][:40].replace("|", "/") if row["error"] else row["tried"]
        lines.append(
            f"| {row['name']} | {status} {note} | {row['fetched']} | {row['relevant']} | "
            f"{row['new']} | {row['elapsed_ms']}ms |"
        )
    return "\n".join(lines)


def build(stats: dict) -> str:
    started: datetime = stats["started"]
    rows = stats["sources"]
    ok = [r for r in rows if not r["error"]]
    failed = [r for r in rows if r["error"]]
    archived = stats.get("archived") or []

    md = [
        f"## 抓取简报 · {started.strftime('%Y-%m-%d %H:%M')} (UTC{started.strftime('%z')[:3]})",
        "",
        f"- 耗时 **{stats['elapsed_seconds']:.1f}s**，源 **{len(ok)}/{len(rows)}** 正常",
        f"- 抓取 {stats['fetched']} 条 → 关键词筛选后 {stats['relevant']} 条 → 去重后 **新增 {stats['new']} 条**"
        f"（跳过重复 {stats['dup']} 条）",
        f"- 库内总计 **{stats['total']} 条**（保留 {stats['retention_days']} 天）",
    ]
    if stats.get("weak"):
        md.append(f"- 主题不符丢弃 **{stats['weak']} 条**（标题里没有母婴/奶粉核心词）")
    if stats.get("llm") and stats["llm"].get("enabled"):
        llm = stats["llm"]
        md.append(
            f"- 大模型：{llm['done']} 条改写 / 判不相关 {llm['dropped']} 条 / "
            f"{llm['calls']} 次调用 / token {llm['tokens_in']}+{llm['tokens_out']}"
        )
    if stats.get("with_summary") is not None:
        md.append(f"- 有摘要的条目 **{stats['with_summary']}/{stats['total']}**")
    if archived:
        md.append(f"- 归档：{', '.join(archived)} 已压缩移入 `data/archive/`")
    if failed:
        md.append(f"- 失败源（不影响站点）：{', '.join(r['name'] for r in failed)}")

    md += ["", "<details><summary>各源明细</summary>", "", _md_table(rows), "", "</details>", ""]
    return "\n".join(md)


def emit(stats: dict) -> None:
    md = build(stats)
    print("\n" + "-" * 62)
    print(md)
    print("-" * 62)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(md + "\n")

    # 本地运行时留一份，方便回溯（已在 .gitignore 中忽略）
    if not os.environ.get("CI"):
        (ROOT / "last-run.md").write_text(md, encoding="utf-8")

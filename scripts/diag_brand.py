"""品牌检索源诊断：这几个品牌名在 Google News 到底有没有货。

为什么单独写一个诊断脚本：
    brand_harker / brand_cenovis / brand_friso 接进来之后一轮抓取 0 条入库，
    但"0 条"有四种可能——查询写错、Google 返回空 feed、返回了但都超过
    max_age_days、返回了但过不了相关度阈值。本地访问不到 news.google.com，
    只能在 CI 里把每一步的数字打出来，才知道该改哪一层。

产物写到 meta/brand-probe.md（仓库里能直接看），不落任何抓取数据。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedparser  # noqa: E402
from curl_cffi import requests as cffi  # noqa: E402

from pipeline.relevance import judge  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "meta" / "brand-probe.md"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 候选查询：既有当前配置里的写法，也有更宽的写法，跑一轮看哪个真的有货
QUERIES: dict[str, str] = {
    "当前·哈克草本": '哈克草本 OR "Harker Herbals" OR 哈克 草本',
    "宽·Harker Herbals": '"Harker Herbals"',
    "宽·哈克": "哈克 儿童 草本 OR 哈克口服液",
    "当前·萃益维": '萃益维 OR Cenovis OR "Cenovis 维生素"',
    "宽·Cenovis": "Cenovis",
    "当前·美素佳儿": '美素佳儿 OR "Friso 奶粉" OR "皇家美素佳儿"',
    "宽·美素佳儿": "美素佳儿",
    "宽·Friso": "Friso 奶粉 OR Friso formula",
}


def rss_url(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote(query, safe="")
        + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )


def probe(query: str, now: datetime) -> dict:
    row: dict = {"query": query, "status": None, "entries": 0, "fresh": 0, "kept": 0, "samples": []}
    try:
        resp = cffi.get(rss_url(query), impersonate="chrome124", timeout=25, headers={"User-Agent": UA})
        row["status"] = resp.status_code
        if resp.status_code >= 400:
            row["error"] = f"HTTP {resp.status_code}"
            return row
        parsed = feedparser.parse(resp.content)
        entries = parsed.entries or []
        row["entries"] = len(entries)
        for entry in entries[:15]:
            published = ""
            for key in ("published_parsed", "updated_parsed"):
                st = entry.get(key)
                if st:
                    published = datetime(*st[:6], tzinfo=timezone.utc).isoformat()
                    break
            title = (entry.get("title") or "").strip()
            outlet = ""
            meta = entry.get("source") or {}
            if isinstance(meta, dict):
                outlet = str(meta.get("title") or "")
            fresh = False
            if published:
                fresh = datetime.fromisoformat(published) >= now - timedelta(days=45)
            ok = False
            if title:
                ok, score, _hits, tags = judge(title, entry.get("summary", ""))
            if fresh:
                row["fresh"] += 1
            if fresh and ok:
                row["kept"] += 1
            if len(row["samples"]) < 5:
                row["samples"].append(
                    f"{title[:60]} | {outlet[:14]} | {published[:10]} | "
                    f"{'入库' if (fresh and ok) else ('过期' if not fresh else '被筛')}"
                )
    except Exception as exc:  # 单条失败不能影响其他查询
        row["error"] = f"{type(exc).__name__}: {exc}"[:120]
    return row


def main() -> int:
    now = datetime.now(timezone.utc)
    lines = [
        "# 品牌检索源探测",
        "",
        f"跑于 {now.isoformat(timespec='seconds')}（UTC）。"
        "`entries` 是 Google 返回条数，`fresh` 是 45 天内的条数，`kept` 是能过相关度阈值的条数。",
        "",
        "| 查询 | HTTP | 返回 | 45天内 | 可入库 | 备注 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label, query in QUERIES.items():
        row = probe(query, now)
        lines.append(
            f"| {label} | {row['status']} | {row['entries']} | {row['fresh']} | {row['kept']} | "
            f"{row.get('error', '')} |"
        )
        for sample in row["samples"]:
            lines.append(f"|  |  |  |  |  | {sample} |")
    lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

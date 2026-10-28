"""品牌检索源诊断：这几个品牌名在新闻索引里到底有没有货。

为什么单独写一个诊断脚本：
    brand_harker / brand_cenovis / brand_friso 接进来之后一轮抓取 0 条入库，
    但"0 条"有四种可能——查询写错、索引里真没有、有但都超过 max_age_days、
    有但过不了相关度阈值。本地访问不到 news.google.com，只能在 CI 里把
    每一步的数字打出来，才知道该改哪一层。

两份表：
    表一 同一个品牌的不同查询写法（Google 大陆版）
    表二 同一个查询换个索引（Google 港台/美国、Bing 新闻、百度新闻）

产物写到 meta/brand-probe.md（仓库里能直接看），不落任何抓取数据。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedparser  # noqa: E402
from curl_cffi import requests as cffi  # noqa: E402

from pipeline.fetchers import fetch_source  # noqa: E402
from pipeline.http import PoliteClient  # noqa: E402
from pipeline.relevance import judge  # noqa: E402
from pipeline.sources import load_config, load_sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "meta" / "brand-probe.md"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 表一：同一个品牌，几种写法
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

# 表二：同一个查询，换索引。Google 的 CN 版对中文媒体收录不全，
# 港台版、Bing、百度各有各的库，哪家能搜到最近的稿子就换哪家。
BRANDS: dict[str, str] = {
    "哈克草本": "哈克草本 OR Harker Herbals",
    "萃益维": "萃益维 OR Cenovis",
    "美素佳儿": "美素佳儿 OR Friso",
}

ENGINES: list[tuple[str, str]] = [
    ("Google·大陆", "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("Google·港台", "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"),
    ("Google·美国英", "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"),
    ("Bing 新闻", "https://www.bing.com/news/search?q={q}&format=RSS"),
    ("百度新闻", "https://news.baidu.com/ns?word={q}&tn=news&from=news&cl=2&rn=20&ct=1"),
]

CUTOFF_DAYS = 45


def _entry_date(entry) -> str:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime(*st[:6], tzinfo=timezone.utc).isoformat()
    return ""


def _fetch(url: str):
    return cffi.get(url, impersonate="chrome124", timeout=25, headers={"User-Agent": UA})


def probe_feed(url: str, now: datetime) -> dict:
    """RSS 类索引：条数、45 天内条数、能过阈值的条数、最新一条。"""
    row: dict = {"status": None, "entries": 0, "fresh": 0, "kept": 0, "newest": "", "samples": []}
    try:
        resp = _fetch(url)
        row["status"] = resp.status_code
        if resp.status_code >= 400:
            row["error"] = f"HTTP {resp.status_code}"
            return row
        entries = feedparser.parse(resp.content).entries or []
        row["entries"] = len(entries)
        for entry in entries[:20]:
            published = _entry_date(entry)
            title = (entry.get("title") or "").strip()
            meta = entry.get("source") or {}
            outlet = str(meta.get("title") or "") if isinstance(meta, dict) else ""
            fresh = bool(published) and datetime.fromisoformat(published) >= now - timedelta(days=CUTOFF_DAYS)
            ok = False
            if title:
                ok, _score, _hits, _tags = judge(title, entry.get("summary", ""))
            if fresh:
                row["fresh"] += 1
            if fresh and ok:
                row["kept"] += 1
            if not row["newest"] and published:
                row["newest"] = published[:10]
            if len(row["samples"]) < 3:
                row["samples"].append(f"{title[:52]} | {outlet[:12]} | {published[:10]}")
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"[:110]
    return row


def probe_html(url: str) -> dict:
    """HTML 列表型索引（百度新闻）：只数标题链接数，够判断"有没有"就行。"""
    row: dict = {"status": None, "entries": 0, "samples": []}
    try:
        resp = _fetch(url)
        row["status"] = resp.status_code
        text = resp.text or ""
        anchors = re.findall(r"<a[^>]+>(.*?)</a>", text, re.S)
        hits = [re.sub(r"<[^>]+>", "", a).strip() for a in anchors]
        hits = [h for h in hits if len(h) >= 10][:20]
        row["entries"] = len(hits)
        row["samples"] = [h[:52] for h in hits[:3]]
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"[:110]
    return row


def probe_pipeline(now: datetime) -> list[tuple[str, str, int, int, int, list[str]]]:
    """按生产路径把几个源真抓一遍，返回 (源, 结果, 抓到, 过期, 过阈值, 样例)。"""
    cfg = load_config()
    fetch_cfg = cfg["fetch"]
    max_age = int(cfg.get("pipeline", {}).get("max_age_days", 45) or 45)
    wanted = ["brand_harker", "brand_cenovis", "brand_friso", "gnews_cn_brand_news"]
    by_id = {s["id"]: s for s in load_sources()}
    client = PoliteClient(fetch_cfg)
    rows: list[tuple[str, str, int, int, int, list[str]]] = []
    for sid in wanted:
        src = by_id.get(sid)
        if not src:
            rows.append((sid, "源不存在/未启用", 0, 0, 0, []))
            continue
        try:
            res = fetch_source(client, src, fetch_cfg, now)
        except Exception as exc:  # 单源异常不能炸整张报告
            rows.append((sid, f"{type(exc).__name__}: {exc}"[:90], 0, 0, 0, []))
            continue
        stale = kept = 0
        titles: list[str] = []
        for item in res.items:
            published = None
            if item.get("published_at"):
                try:
                    published = datetime.fromisoformat(item["published_at"])
                except ValueError:
                    published = None
            if published is not None and published < now - timedelta(days=max_age):
                stale += 1
                continue
            ok, _score, _hits, _tags = judge(item["title"], item.get("summary", ""))
            if ok:
                kept += 1
            if len(titles) < 2:
                titles.append(f"{item['title'][:48]}（{item.get('source_name', '')[:10]}）")
        rows.append((sid, res.error or "ok", len(res.items), stale, kept, titles))
    return rows


def main() -> int:
    now = datetime.now(timezone.utc)
    lines = [
        "# 品牌检索源探测",
        "",
        f"跑于 {now.isoformat(timespec='seconds')}（UTC）。`45天内` 是能进库的时间窗，"
        "`可入库` 是再过了相关度阈值的条数。",
        "",
        "## 一、不同查询写法（Google 大陆版）",
        "",
        "| 查询 | HTTP | 返回 | 45天内 | 可入库 | 最新一条 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for label, query in QUERIES.items():
        row = probe_feed(ENGINES[0][1].format(q=quote(query, safe="")), now)
        lines.append(
            f"| {label} | {row['status']} | {row['entries']} | {row['fresh']} | {row['kept']} | "
            f"{row.get('newest', '')} | {row.get('error', '')} |"
        )
        for sample in row["samples"]:
            lines.append(f"|  |  |  |  |  |  | {sample} |")

    lines += [
        "",
        "## 二、换索引（查询词固定为「品牌名 OR 外文名」）",
        "",
        "| 品牌 | 索引 | HTTP | 条数 | 45天内 | 最新一条 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for brand, query in BRANDS.items():
        for engine, template in ENGINES:
            url = template.format(q=quote(query, safe=""))
            row = probe_html(url) if "baidu" in template else probe_feed(url, now)
            lines.append(
                f"| {brand} | {engine} | {row['status']} | {row['entries']} | "
                f"{row.get('fresh', '-')} | {row.get('newest', '-')} | {row.get('error', '')} |"
            )

    # 表三最关键：走的是生产那条路（PoliteClient + fetch_source + judge），
    # 前面两张表证明"索引里有稿"，这张表才能证明"抓进来为什么没入库"。
    lines += [
        "",
        "## 三、走生产抓取路径（含 robots、限速、45 天窗口、相关度阈值）",
        "",
        "| 源 | 结果 | 抓到 | 过期丢弃 | 过阈值 | 样例 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for sid, note, got, stale, kept, samples in probe_pipeline(now):
        sample = samples[0][:56] if samples else ""
        lines.append(f"| {sid} | {note} | {got} | {stale} | {kept} | {sample} |")

    lines.append("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

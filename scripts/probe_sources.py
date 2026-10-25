#!/usr/bin/env python
"""Stage 0：信息源可用性探测。

用法：
    python scripts/probe_sources.py                      # 默认输出 probe-local.json
    python scripts/probe_sources.py --label actions --out probe-actions.json
    python scripts/probe_sources.py --only kr36,efsa

判定逻辑（不依赖 sources.yaml 里声明的 type，一律按响应内容实测）：
    1. 内容像 RSS/Atom  → 解析，统计条目数
    2. 内容像 JSON      → 统计数组长度
    3. 是 HTML          → 先找页面里的 <link rel="alternate"> 自动发现真实 feed；
                          找不到则统计"疑似新闻链接数"，仅用于判断该页是否值得做解析
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.http import PoliteClient  # noqa: E402
from pipeline.sources import ROOT, load_config, load_sources  # noqa: E402

try:  # Windows 控制台中文输出
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FEED_HINTS = ("application/rss", "application/atom", "application/xml", "text/xml")
SUSPECT_DATE = re.compile(r"(19|20)\d{2}[-/年]\d{1,2}")


def looks_like_feed(resp) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    if any(h in ctype for h in FEED_HINTS):
        return True
    head = resp.content[:400].lstrip().lower()
    return head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head


def parse_feed(content: bytes) -> tuple[int, str, str]:
    parsed = feedparser.parse(content)
    entries = parsed.entries or []
    if not entries:
        return 0, "", ""
    first = entries[0]
    title = (first.get("title") or "").strip()
    date = (first.get("published") or first.get("updated") or first.get("created") or "").strip()
    return len(entries), title, date


def discover_feed(html: str, base_url: str) -> str | None:
    """从页面 <link rel=alternate> 里找 RSS/Atom 地址。"""
    soup = BeautifulSoup(html, "lxml")
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        ltype = (link.get("type") or "").lower()
        if "alternate" in rel and any(h in ltype for h in FEED_HINTS):
            return urljoin(base_url, link["href"])
    # 兜底：页面里出现的 .../feed 、.../rss.xml
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"/(feed|rss)(\.xml)?/?$", href):
            return urljoin(base_url, href)
    return None


def count_candidate_links(soup: BeautifulSoup, base_url: str) -> int:
    host = urlparse(base_url).netloc
    n = 0
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        if len(text) < 10:
            continue
        href = a["href"]
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).netloc != host:
            continue
        if re.search(r"\d{4,}", full) or SUSPECT_DATE.search(text):
            n += 1
    return n


def probe_one(client: PoliteClient, src: dict, max_items: int) -> dict:
    started = datetime.now(timezone.utc)
    result = {
        "id": src["id"],
        "name": src.get("name", src["id"]),
        "declared_type": src.get("type", ""),
        "priority": src.get("priority", "P1"),
        "lang": src.get("lang", "zh"),
        "url": src["url"],
        "final_url": "",
        "status": None,
        "content_type": "",
        "bytes": 0,
        "elapsed_ms": 0,
        "detected": "",
        "items": 0,
        "first_title": "",
        "first_date": "",
        "discovered_feed": "",
        "candidate_links": 0,
        "robots": "unknown",
        "error": "",
        "checked_at": started.isoformat(timespec="seconds"),
    }

    allowed = client.allowed_by_robots(src["url"])
    result["robots"] = "allow" if allowed else ("deny" if allowed is False else "unknown")
    if allowed is False:
        result["error"] = "robots.txt 禁止抓取，已跳过"
        return result

    t0 = datetime.now(timezone.utc)
    try:
        resp = client.get(src["url"])
    except Exception as exc:
        result["elapsed_ms"] = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return result
    result["elapsed_ms"] = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    result["status"] = resp.status_code
    result["final_url"] = str(resp.url)
    result["content_type"] = resp.headers.get("content-type", "")
    result["bytes"] = len(resp.content)

    if resp.status_code >= 400:
        result["error"] = f"HTTP {resp.status_code}"
        return result

    # --- 1) 是 feed ---
    if looks_like_feed(resp):
        n, title, date = parse_feed(resp.content)
        result.update(detected="feed", items=n, first_title=title[:120], first_date=date[:32])
        if n == 0:
            result["error"] = "内容像 feed 但解析出 0 条"
        return result

    # --- 2) 是 JSON ---
    ctype = result["content_type"].lower()
    if "json" in ctype:
        try:
            data = resp.json()
            arr = None
            for key in ("articles", "items", "results", "data"):
                if isinstance(data, dict) and isinstance(data.get(key), list):
                    arr = data[key]
                    break
            if arr is None and isinstance(data, list):
                arr = data
            n = len(arr or [])
            first = (arr or [{}])[0]
            title = (
                first.get("title")
                or first.get("headline")
                or first.get("product_description")
                or first.get("reason_for_recall")
                or ""
            )
            result.update(
                detected="json",
                items=n,
                first_title=str(title)[:120],
                first_date=str(first.get("seendate") or first.get("report_date") or first.get("date") or "")[:32],
            )
            if n == 0:
                result["error"] = "JSON 里没找到条目数组"
        except Exception as exc:
            result["detected"] = "json"
            result["error"] = f"JSON 解析失败: {exc}"[:200]
        return result

    # --- 3) 是 HTML：先尝试自动发现 feed ---
    html = resp.text
    feed_url = discover_feed(html, result["final_url"] or src["url"])
    if feed_url:
        result["discovered_feed"] = feed_url
        try:
            fresp = client.get(feed_url)
            if fresp.status_code < 400:
                n, title, date = parse_feed(fresp.content)
                if n > 0:
                    result.update(detected="html→feed", items=n, first_title=title[:120], first_date=date[:32])
                    return result
                result["error"] = "发现的 feed 解析出 0 条，改用 HTML 分析"
            else:
                result["error"] = f"发现的 feed 返回 {fresp.status_code}，改用 HTML 分析"
        except Exception as exc:
            result["error"] = f"发现的 feed 抓取失败: {exc}"[:150]
        # 注意：这里不能 return，要继续走 HTML 分析

    soup = BeautifulSoup(html, "lxml")
    result["detected"] = "html-list"
    result["candidate_links"] = count_candidate_links(soup, result["final_url"] or src["url"])
    page_title = (soup.title.get_text(strip=True) if soup.title else "")[:80]
    result["first_title"] = f"[页面] {page_title}"
    if result["candidate_links"] == 0:
        result["error"] = "未发现 feed，且没有可识别的新闻链接（可能 JS 渲染）"
    return result


def verdict(row: dict) -> str:
    if row["error"] and row["items"] == 0 and row["candidate_links"] == 0:
        return "❌ 不可用"
    if row["items"] > 0:
        return "✅ RSS 可用"
    if row["candidate_links"] > 0:
        return "🔧 需 HTML 解析"
    return "⚠️ 可疑"


def render_markdown(rows: list[dict], label: str) -> str:
    ok = sum(1 for r in rows if r["items"] > 0)
    html_ok = sum(1 for r in rows if r["items"] == 0 and r["candidate_links"] > 0)
    bad = len(rows) - ok - html_ok
    lines = [
        f"# 源探测报告（{label}）",
        "",
        f"- 环境：`{platform.platform()}`",
        f"- 时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 共 {len(rows)} 个源：**RSS 直接可用 {ok} 个**，需 HTML 解析 {html_ok} 个，不可用 {bad} 个",
        "",
        "| 状态 | 源 | id | 优先级 | 条目数 | 链接数 | 首条时间 | 备注 | first_title |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: (x["items"] == 0, x["id"])):
        note = r["error"] or (r["discovered_feed"] and f"发现 feed: {r['discovered_feed']}") or r["robots"]
        title = (r["first_title"] or "").replace("|", "/")[:60]
        date = (r["first_date"] or "")[:16]
        lines.append(
            f"| {verdict(r)} | {r['name']} | `{r['id']}` | {r['priority']} | {r['items']} | "
            f"{r['candidate_links']} | {date} | {str(note)[:60].replace('|', '/')} | {title} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="local", help="报告标签，如 local / actions")
    ap.add_argument("--out", default="", help="JSON 输出路径，默认 probe-<label>.json")
    ap.add_argument("--only", default="", help="只探测指定 id，逗号分隔")
    ap.add_argument("--limit", type=int, default=0, help="只探测前 N 个（调试用）")
    args = ap.parse_args()

    cfg = load_config()
    fetch_settings = cfg["fetch"]
    sources = load_sources()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        sources = [s for s in sources if s["id"] in wanted]
    if args.limit:
        sources = sources[: args.limit]

    out_json = Path(args.out or f"probe-{args.label}.json")
    if not out_json.is_absolute():
        out_json = ROOT / out_json
    out_md = out_json.with_suffix(".md")

    print(f"探测 {len(sources)} 个源，label={args.label}，并发={fetch_settings['concurrency']}")
    client = PoliteClient(fetch_settings)
    rows: list[dict] = []

    try:
        with ThreadPoolExecutor(max_workers=fetch_settings["concurrency"]) as pool:
            futures = {pool.submit(probe_one, client, s, fetch_settings["max_items_per_source"]): s for s in sources}
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    row = fut.result()
                except Exception as exc:  # 单源异常不能炸整轮
                    row = {
                        "id": src["id"], "name": src.get("name", src["id"]), "url": src["url"],
                        "priority": src.get("priority", "P1"), "lang": src.get("lang", "zh"),
                        "declared_type": src.get("type", ""), "final_url": "", "status": None,
                        "content_type": "", "bytes": 0, "elapsed_ms": 0, "detected": "",
                        "items": 0, "first_title": "", "first_date": "", "discovered_feed": "",
                        "candidate_links": 0, "robots": "unknown",
                        "error": f"未捕获异常: {exc}", "checked_at": "",
                    }
                rows.append(row)
                mark = verdict(row)
                print(f"  {mark:<12} {row['id']:<22} items={row['items']:<3} links={row['candidate_links']:<3} {row['error'][:60]}")
    finally:
        client.close()

    rows.sort(key=lambda r: r["id"])
    payload = {
        "label": args.label,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "summary": {
            "total": len(rows),
            "feed_ok": sum(1 for r in rows if r["items"] > 0),
            "html_only": sum(1 for r in rows if r["items"] == 0 and r["candidate_links"] > 0),
            "failed": sum(1 for r in rows if r["items"] == 0 and r["candidate_links"] == 0),
        },
        "sources": rows,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(render_markdown(rows, args.label), encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"RSS 直接可用 : {payload['summary']['feed_ok']}")
    print(f"需 HTML 解析 : {payload['summary']['html_only']}")
    print(f"不可用       : {payload['summary']['failed']}")
    print(f"报告已写入   : {out_json.name} / {out_md.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

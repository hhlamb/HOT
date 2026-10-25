#!/usr/bin/env python
"""一键跑完整流水线：抓取 → 筛选 → 补摘要 → 去重 → 写盘 → 生成前端数据。

各步骤是叠加的，缺一步也能出数据：
    RSS 自带 description  →  直接当摘要
    HTML 列表页没摘要      →  抓详情页抽首段（pipeline/extract.py，零成本）
    有 ZHIPU_API_KEY      →  再用模型把摘要压到 90 字、翻成中文、重打标签
    没有 Key              →  用 data/zh_cache.json 里的译文缓存兜底

用法：
    python scripts/run.py                 # 完整跑一轮并写入 data/
    python scripts/run.py --dry-run       # 只在控制台看结果，不写盘
    python scripts/run.py --only efsa,dairyreporter
    python scripts/run.py --no-llm        # 强制不调模型（省额度）
    python scripts/run.py --no-extract    # 跳过详情页抽取（调试抓取层时更快）
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import llm, report, store  # noqa: E402
from pipeline.clean import clip  # noqa: E402
from pipeline.dedupe import dedupe  # noqa: E402
from pipeline.extract import fill_summaries  # noqa: E402
from pipeline.fetchers import fetch_source  # noqa: E402
from pipeline.http import PoliteClient  # noqa: E402
from pipeline.relevance import judge  # noqa: E402
from pipeline.sources import load_config, load_sources  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CST = timezone(timedelta(hours=8))


def assign_id(item: dict) -> dict:
    raw = f"{item['source_id']}|{item['original_url']}".encode()
    item["id"] = hashlib.sha1(raw).hexdigest()[:16]
    return item


def within_window(iso: str, now: datetime, days: int) -> bool:
    if not iso:
        return False
    try:
        return datetime.fromisoformat(iso) >= now - timedelta(days=days)
    except ValueError:
        return False


def sort_key(item: dict) -> str:
    """按发布时间倒序；时间未知的排最后（而不是按抓取时间排到最前面）。

    伊利、飞鹤这类品牌官网的列表页不带日期，若按 fetched_at 排序，
    两年前的旧稿会顶到信息流第一条。
    """
    return item.get("published_at") or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑指定源，逗号分隔")
    ap.add_argument("--dry-run", action="store_true", help="不写盘")
    ap.add_argument("--limit", type=int, default=0, help="调试用：最多跑 N 个源")
    ap.add_argument("--days", type=int, default=0, help="覆盖保留天数")
    ap.add_argument("--show", type=int, default=10, help="dry-run 时打印前 N 条")
    ap.add_argument("--no-llm", action="store_true", help="不调用大模型")
    ap.add_argument("--no-extract", action="store_true", help="跳过详情页摘要抽取")
    ap.add_argument("--extract-max", type=int, default=0, help="本轮最多抽取多少篇详情页")
    args = ap.parse_args()

    cfg = load_config()
    fetch_cfg = cfg["fetch"]
    dedupe_cfg = cfg["dedupe"]
    pipe_cfg = cfg["pipeline"]
    retention = args.days or pipe_cfg["retention_days"]
    min_score = cfg.get("relevance", {}).get("min_score", 4.0)

    sources = load_sources()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        sources = [s for s in sources if s["id"] in wanted]
    if args.limit:
        sources = sources[: args.limit]

    now = datetime.now(CST)
    started = now
    t0 = time.monotonic()
    print(f"[1/6] 抓取 {len(sources)} 个源（并发 {fetch_cfg['concurrency']}）…")

    results = []
    client = PoliteClient(fetch_cfg)
    with ThreadPoolExecutor(max_workers=fetch_cfg["concurrency"]) as pool:
        futures = {pool.submit(fetch_source, client, src, fetch_cfg, now): src for src in sources}
        for fut in as_completed(futures):
            src = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # 单源异常绝不能炸整轮
                from pipeline.fetchers import FetchResult

                results.append(
                    FetchResult(src["id"], error=f"{type(exc).__name__}: {exc}"[:160], tried=src.get("type", ""))
                )

    # ---------------- 逐源筛选 ----------------
    print("[2/6] 关键词筛选…")
    max_age_days = pipe_cfg.get("max_age_days", 45)
    deny_domains = tuple(fetch_cfg.get("deny_domains") or ())
    per_source: dict[str, dict] = {}
    candidates: list[dict] = []
    for res in results:
        src = next(s for s in sources if s["id"] == res.source_id)
        row = {
            "id": res.source_id,
            "name": src.get("name", res.source_id),
            "tried": res.tried,
            "error": res.error,
            "fetched": len(res.items),
            "relevant": 0,
            "new": 0,
            "stale": 0,
            "elapsed_ms": res.elapsed_ms,
        }
        kept_here: dict[str, dict] = {}
        for item in res.items:
            title = item["title"]
            if deny_domains and any(d in item["original_url"] for d in deny_domains):
                continue
            if item.get("published_at"):
                try:
                    published = datetime.fromisoformat(item["published_at"])
                except ValueError:
                    published = None
                if published is not None:
                    if published > now + timedelta(days=2):
                        # 时区标注错误的源，宁可当"时间未知"也别让它排到未来
                        item["published_at"], item["published_guessed"] = "", True
                    elif published < now - timedelta(days=max_age_days):
                        row["stale"] += 1  # 品牌官网页脚里的陈年旧稿
                        continue
            ok, score, hits, tags = judge(title, item.get("summary", ""), min_score)
            if not ok:
                continue
            item["score"] = round(score, 1)
            item["matched"] = hits[:6]
            item["tags"] = list(dict.fromkeys((item.get("tags") or []) + tags))[:3]
            item["summary"] = clip(item["summary"], cfg["llm"]["max_summary_chars"] * 2) if item.get("summary") else ""
            assign_id(item)
            kept_here[item["id"]] = item  # 同源同 URL 只留一条
        max_per_source = int(pipe_cfg.get("max_per_source", 0) or 0)
        if max_per_source and len(kept_here) > max_per_source:
            ordered = sorted(kept_here.values(), key=sort_key, reverse=True)[:max_per_source]
            row["capped"] = len(kept_here) - max_per_source
            kept_here = {i["id"]: i for i in ordered}
        row["relevant"] = len(kept_here)
        per_source[res.source_id] = row
        candidates.extend(kept_here.values())

    # ---------------- 补摘要（只处理库里还没有的条目）----------------
    print("[3/6] 补摘要…")
    all_recent, _ = store.load_recent(retention, now)
    known_ids = {i["id"] for i in all_recent}
    fresh = [c for c in candidates if c["id"] not in known_ids]

    extract_cfg = cfg.get("extract", {})
    if args.no_extract or not extract_cfg.get("enabled", True):
        print("  已跳过详情页抽取")
    else:
        limit = args.extract_max or int(extract_cfg.get("max_per_run", 80))
        fill_summaries(
            fresh,
            client,
            cfg,
            min_chars=int(extract_cfg.get("min_chars", 40)),
            max_per_run=limit,
            workers=int(extract_cfg.get("workers", 4)),
            summary_chars=int(extract_cfg.get("summary_chars", 120)),
        )
        # 回填：库里已有但一直没有摘要的条目（首次接入抽取时会有几百条）
        backfill = [i for i in all_recent if not (i.get("summary") or "").strip()]
        cap = int(extract_cfg.get("backfill_per_run", 40))
        if backfill:
            fill_summaries(
                backfill[:cap],
                client,
                cfg,
                min_chars=int(extract_cfg.get("min_chars", 40)),
                max_per_run=cap,
                workers=int(extract_cfg.get("workers", 4)),
                summary_chars=int(extract_cfg.get("summary_chars", 120)),
            )
            for old in backfill:
                if (old.get("summary") or "").strip():
                    old["_patched"] = True
    client.close()

    llm_cfg = cfg.get("llm", {})
    llm_stats = {"enabled": False, "calls": 0, "tokens_in": 0, "tokens_out": 0, "done": 0, "failed": 0, "dropped": 0}
    if args.no_llm:
        print("  --no-llm：跳过模型")
    else:
        llm_stats = llm.enrich(fresh, llm_cfg)
        if llm_stats["enabled"]:
            print(
                f"  模型：{llm_stats['done']} 条改写成功 / 判不相关 {llm_stats['dropped']} 条 / "
                f"{llm_stats['calls']} 次调用 / token {llm_stats['tokens_in']}+{llm_stats['tokens_out']}"
            )
        else:
            print("  未配置 API Key：跳过模型，仅用正文抽取 + 译文缓存")

    # 没有模型、或模型漏处理的条目 → 套本地译文缓存
    hit = llm.apply_cache(fresh)
    if hit:
        print(f"  译文缓存命中 {hit} 条")

    # 库里已有的外文条目：新补的译文（缓存或模型）要能回填进去，否则会一直是英文
    stale_en = [i for i in all_recent if i.get("language") == "en" and not i.get("title_zh")]
    if stale_en:
        back = llm.apply_cache(stale_en)
        if back:
            for old in stale_en:
                if old.get("title_zh"):
                    old["_patched"] = True
            print(f"  旧条目补译文 {back} 条")

    # 模型判定不相关的剔除
    dropped_by_llm = [c for c in fresh if c.get("_drop")]
    if dropped_by_llm:
        fresh = [c for c in fresh if not c.get("_drop")]
        for bad in dropped_by_llm:
            if bad.get("source_id") in per_source:
                per_source[bad["source_id"]]["relevant"] -= 1

    # ---------------- 去重 ----------------
    print("[4/6] 去重…")
    recent_for_sim = [
        i for i in all_recent if within_window(i.get("published_at") or i.get("fetched_at", ""), now, dedupe_cfg["window_days"])
    ]
    kept_all, dropped = dedupe(
        candidates,
        known_ids,
        recent_for_sim,
        threshold=dedupe_cfg["title_similarity"],
        max_per_event=dedupe_cfg["max_per_event"],
    )
    kept = [i for i in kept_all if not i.get("_drop")]
    for item in kept:
        per_source[item["source_id"]]["new"] += 1

    # ---------------- 写盘 ----------------
    patched = [i for i in all_recent if i.get("_patched")]
    archived: list[str] = []
    if args.dry_run:
        print("[5/6] --dry-run：跳过写盘")
        print(f"\n候选 {len(kept)} 条，按时间排序后前 {args.show} 条：\n")
        kept_sorted = sorted(kept, key=sort_key, reverse=True)
        for item in kept_sorted[: args.show]:
            when = (item.get("published_at") or "时间未知")[:16]
            print(f"  [{when}] {item['source_name']} · {','.join(item['tags'])}")
            print(f"    {item.get('title_zh') or item['title']}")
            print(f"    分数 {item['score']} · {item['original_url'][:100]}")
            if item["summary"]:
                print(f"    摘要 {item['summary'][:80]}")
            print()
        final_items, counts = all_recent, {}
    else:
        print("[5/6] 写盘…")
        written = store.append(kept, now)
        if patched:
            print(f"  补全已有条目摘要 {store.apply_updates(patched)} 条")
        archived = store.prune(retention, now)
        final_items, counts = store.load_recent(retention, now)

    # ---------------- 索引与 file:// 兜底 ----------------
    last_updated = now.isoformat(timespec="seconds")
    if not args.dry_run:
        print("[6/6] 生成 index.json / latest.js…")
        ok_sources = sum(1 for r in per_source.values() if not r["error"])
        store.write_index(
            final_items,
            counts,
            now,
            extra={
                "sources_total": len(per_source),
                "sources_ok": ok_sources,
                "with_summary": sum(1 for i in final_items if i.get("summary")),
                "retention_days": retention,
            },
        )
        n_latest = store.write_latest_js(
            sorted(final_items, key=sort_key, reverse=True),
            cfg["output"]["latest_js_max_items"],
            last_updated,
        )
        print(f"  latest.js 写入 {n_latest} 条（file:// 双击用）")
    else:
        print("[6/6] --dry-run：跳过索引生成")

    stats = {
        "started": started,
        "elapsed_seconds": time.monotonic() - t0,
        "sources": list(per_source.values()),
        "fetched": sum(r["fetched"] for r in per_source.values()),
        "relevant": len(candidates),
        "new": len(kept),
        "dup": len(dropped),
        "total": len(final_items),
        "retention_days": retention,
        "archived": archived,
        "llm": llm_stats,
        "with_summary": sum(1 for i in final_items if i.get("summary")),
    }
    report.emit(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

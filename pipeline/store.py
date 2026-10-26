"""存储层：按月分片的 append-only JSONL + 索引 + file:// 兜底数据。

为什么是 JSONL 而不是一个 JSON 数组：
    每天 6 次运行、每次把"最近 90 天全量"重写一遍，一年会在 git 历史里累计
    数百 MB 的 blob（即使 zlib 压缩后也有 340MB 量级）。
    改成只往文件末尾追加新行，同样的数据一年只有 4MB 上下，降了两个数量级。

代价与对策：
    JSONL 不能原地修改，所以前端读取时对重复 id 取最后一条（last-write-wins）。
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .sources import ROOT

DATA_DIR = ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
MONTH_FILE = re.compile(r"^(\d{4})-(\d{2})\.jsonl$")


def _month_of(iso: str, fallback: datetime) -> str:
    if iso:
        try:
            return datetime.fromisoformat(iso).strftime("%Y-%m")
        except ValueError:
            pass
    return fallback.strftime("%Y-%m")


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def _iter_lines(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # 单行损坏不影响整份数据


def month_files() -> list[Path]:
    if not DATA_DIR.exists():
        return []
    return sorted((p for p in DATA_DIR.iterdir() if MONTH_FILE.match(p.name)), reverse=True)


def load_recent(days: int, now: datetime) -> tuple[list[dict], dict[str, int]]:
    """读取最近 N 天的条目，返回 (条目列表, 每月条数)。id 重复时后者覆盖前者。"""
    cutoff = now - timedelta(days=days)
    merged: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for path in month_files():
        month = path.name[:7]
        try:
            # 必须补 tzinfo，否则和带时区的 cutoff 比较会抛 TypeError
            file_month = datetime.strptime(month, "%Y-%m").replace(tzinfo=now.tzinfo)
        except ValueError:
            continue
        # 整个月份都早于窗口就跳过（下个月 1 号才需要它）
        if file_month.replace(day=1) + timedelta(days=32) < cutoff:
            continue
        n = 0
        for item in _iter_lines(path):
            n += 1
            item["_month"] = month
            published = item.get("published_at") or item.get("fetched_at", "")
            try:
                if published and datetime.fromisoformat(published) < cutoff:
                    continue
            except ValueError:
                pass
            merged[item["id"]] = item  # 后写覆盖先写
        counts[month] = n
    items = sorted(merged.values(), key=lambda x: x.get("published_at") or "", reverse=True)
    return items, counts


def append(items: list[dict], now: datetime) -> dict[str, int]:
    """只追加新行。返回 {月份: 新增条数}。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    by_month: dict[str, list[dict]] = {}
    for item in items:
        month = _month_of(item.get("published_at") or "", now)
        by_month.setdefault(month, []).append(item)

    written: dict[str, int] = {}
    for month, rows in by_month.items():
        path = DATA_DIR / f"{month}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(_clean(row), ensure_ascii=False, separators=(",", ":")) + "\n")
        written[month] = len(rows)
    return written


def apply_updates(items: list[dict]) -> int:
    """对已存在条目做少量补全（比如补上摘要），原地重写受影响的月份文件。

    append-only 的唯一例外：只有"给已有条目补信息"这一种情况才重写，
    且只在确实有变化时发生，正常情况下每次运行都不会触发。
    """
    by_month: dict[str, dict[str, dict]] = {}
    for item in items:
        month = item.get("_month")
        if month:
            by_month.setdefault(month, {})[item["id"]] = item
    if not by_month:
        return 0

    updated = 0
    for month, patches in by_month.items():
        path = DATA_DIR / f"{month}.jsonl"
        if not path.exists():
            continue
        lines: list[str] = []
        for row in _iter_lines(path):
            patch = patches.get(row.get("id", ""))
            if patch is not None:
                changed = False
                new_sum = patch.get("summary") or ""
                # 摘要升级：旧条目是"正文截断"（extract），模型重写后（llm）要能覆盖它。
                # 这是 apply_updates 唯一允许覆盖已有值的地方——不然后填的永远改不动。
                upgrade = patch.get("summary_source") == "llm" and row.get("summary_source") != "llm"
                if new_sum and (not row.get("summary") or upgrade):
                    row["summary"] = new_sum
                    changed = True
                if upgrade:
                    row["summary_source"] = "llm"
                    changed = True
                if patch.get("tags") and patch.get("tags") != row.get("tags"):
                    row["tags"] = patch["tags"]
                    changed = True
                # 译文后补：外文条目接上模型/缓存后，把中文标题补进去
                if patch.get("title_zh") and not row.get("title_zh"):
                    row["title_zh"] = patch["title_zh"]
                    row["translated"] = True
                    changed = True
                # 正文摘录后补：详情页要展示原文核心。老条目只有 120 字摘要，
                # 这里把后抓到的正文写进去；已有正文的不覆盖。
                if patch.get("body") and not (row.get("body") or "").strip():
                    row["body"] = patch["body"]
                    changed = True
                # 配图后补
                if patch.get("image") and not (row.get("image") or "").strip():
                    row["image"] = patch["image"]
                    changed = True
                # 要点后补（模型新版输出）
                if patch.get("points") and not row.get("points"):
                    row["points"] = patch["points"]
                    changed = True
                if changed:
                    updated += 1
            lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)
    return updated


def prune(retention_days: int, now: datetime) -> list[str]:
    """整月早于保留窗口的文件 → 压缩归档后从 data/ 移除（历史仍可查，仓库不再膨胀）。"""
    cutoff_month = (now - timedelta(days=retention_days)).strftime("%Y-%m")
    archived: list[str] = []
    for path in month_files():
        month = path.name[:7]
        if month >= cutoff_month:
            continue
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        target = ARCHIVE_DIR / f"{month}.jsonl.gz"
        with path.open("rb") as src, gzip.open(target, "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)
        path.unlink()
        archived.append(month)
    return archived


def write_index(items: list[dict], months_count: dict[str, int], now: datetime, extra: dict | None = None) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    months = [
        {"month": m, "file": f"{m}.jsonl", "count": months_count.get(m, 0)}
        for m in sorted(months_count, reverse=True)
    ]
    index = {
        "last_updated": now.astimezone().isoformat(timespec="seconds"),
        "total": len(items),
        "months": months,
        "tags": sorted({t for item in items for t in item.get("tags", [])}),
    }
    if extra:
        index.update(extra)
    (DATA_DIR / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return index


def write_latest_js(items: list[dict], max_items: int, last_updated: str) -> int:
    """file:// 双击打开时用：只放最近 N 条，避免手机上加载几 MB。"""
    latest = [{k: v for k, v in item.items() if not k.startswith("_")} for item in items[:max_items]]
    payload = {"last_updated": last_updated, "total": len(items), "items": latest}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    (DATA_DIR / "latest.js").write_text(
        "/* 自动生成，勿手改。仅供 file:// 直接双击打开时兜底使用。 */\n"
        f"window.__FEED__ = {body};\n",
        encoding="utf-8",
    )
    return len(latest)

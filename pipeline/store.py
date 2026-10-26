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
                # 另外老库里还有一批 90 字上限时代的短摘要（平均 43 字），
                # 重抓正文后能给出完整段落，这类也允许覆盖——前提是模型没加工过。
                new_sum = patch.get("summary") or ""
                old_sum = (row.get("summary") or "").strip()
                # 老库里有大批「90 字上限时代」的短摘要（模型也只写六七十字），
                # 现在目标 180 字，所以「明显更长」的版本要能盖掉它——包括模型覆盖模型。
                short_old = len(old_sum) < 120
                longer = len(new_sum) > len(old_sum) + 40
                upgrade = (
                    (patch.get("summary_source") == "llm" and row.get("summary_source") != "llm")
                    or (patch.get("summary_source") == "extract" and row.get("summary_source") != "llm" and short_old and longer)
                    or (patch.get("summary_source") == "llm" and row.get("summary_source") == "llm" and short_old and longer)
                )
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
                # 未来时间夹取后的落盘：排期稿给的未来日期要改成当前时间，
                # 否则它永远排在时间轴最前面
                if (
                    patch.get("published_guessed")
                    and patch.get("published_at")
                    and patch["published_at"] != row.get("published_at")
                ):
                    row["published_at"] = patch["published_at"]
                    row["published_guessed"] = True
                    changed = True
                # 跳转链后补：Google News 中转链解出原文地址后必须落盘，
                # 不然每轮都要重新解码一遍（每条一次 600KB 壳页 + 一次接口调用）
                if (
                    patch.get("link_resolved")
                    and patch.get("original_url")
                    and patch["original_url"] != row.get("original_url")
                ):
                    row["original_url"] = patch["original_url"]
                    row["link_resolved"] = True
                    changed = True
                # 正文摘录后补：详情页要展示原文核心。老条目只有 120 字摘要，
                # 这里把后抓到的正文写进去；已有完整正文的不覆盖。
                old_body = (row.get("body") or "").strip()
                new_body = (patch.get("body") or "").strip()
                if new_body and not old_body:
                    row["body"] = patch["body"]
                    changed = True
                elif new_body and len(old_body) < 300 and len(new_body) > len(old_body) + 200:
                    # 早先 800 字上限时代的短正文，用新抓的完整段落升级
                    row["body"] = patch["body"]
                    changed = True
                # 配图后补
                if patch.get("image") and not (row.get("image") or "").strip():
                    row["image"] = patch["image"]
                    changed = True
                # 要点后补（模型新版输出）。模型的要点优先于抽取式要点，
                # 所以抽取式只是占位，模型产出后要能盖掉。
                new_pts = patch.get("points") or []
                if new_pts and (
                    not row.get("points")
                    or (patch.get("points_source") == "llm" and row.get("points_source") != "llm")
                ):
                    row["points"] = new_pts
                    row["points_source"] = patch.get("points_source", "")
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

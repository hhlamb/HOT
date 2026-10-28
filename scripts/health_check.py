#!/usr/bin/env python
"""健康检查：跑完一轮抓取后判断"这次是不是正常"。

为什么不在站点首页放状态：首页是给读者看的，运维信号放上去只会碍眼，而且
站点 200 不代表数据是对的。这里读 scripts/run.py 落下的 meta/last-run.json
和 data/index.json，做一组机械判断，命中就写 meta/health-alert.md，
由 .github/workflows/health.yml 开 issue 并 @hhlamb（GitHub 会发邮件）。

判断口径（阈值都写在这里，改一行就生效）：
  P0 库空或索引与数据不一致   → 站点看起来正常，实际没内容
  P0 成功源 < 45              → 大面积抓取失败
  P0 连续 6 轮新增 0          → 过滤规则把内容全筛掉了，比失败更难发现
  P0 最新一条超过 36 小时      → 已经没有新内容进来
  P1 同一源连续 3 轮报错       → 单个源挂掉，通常是 WAF 改规则
  P1 源连续 3 轮候选为 0，但历史有产出 → 静默拦截（返回 200 解析出 0 条）
  P1 库内总数比 7 天前少 25%   → 回洗/误删事故

用法：
    python scripts/health_check.py            # 检查，命中则写 meta/health-alert.md
    python scripts/health_check.py --weekly   # 出周报，写 meta/weekly.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import store  # noqa: E402

META = ROOT / "meta"
STATE = META / "health-state.json"
LAST = META / "last-run.json"
ALERT = META / "health-alert.md"
WEEKLY = META / "weekly.md"

MIN_SOURCES_OK = 45
ZERO_STREAK = 6
SOURCE_FAIL_STREAK = 3
SOURCE_SILENT_STREAK = 3
STALE_HOURS = 36
TOTAL_DROP_RATIO = 0.75
HISTORY_RUNS = 120


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _actual_total() -> tuple[int, int]:
    """返回 (唯一 id 数, 物理行数)。两者不等说明有重复入库。"""
    ids: set[str] = set()
    lines = 0
    for path in sorted(p for p in store.DATA_DIR.iterdir() if store.MONTH_FILE.match(p.name)):
        for row in store._iter_lines(path):
            lines += 1
            if row.get("id"):
                ids.add(row["id"])
    return len(ids), lines


def _hours_since(iso: str) -> float:
    if not iso:
        return 1e9
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 1e9
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true")
    args = ap.parse_args()
    META.mkdir(exist_ok=True)

    state = _load(STATE, {"runs": [], "source_streak": {}, "source_zero": {}, "source_avg": {}})
    last = _load(LAST, None)
    index = _load(store.DATA_DIR / "index.json", {})
    actual, lines = _actual_total()

    if args.weekly:
        runs = state.get("runs", [])[-84:]  # 约一周
        added = sum(r.get("new", 0) for r in runs)
        fails: dict[str, int] = {}
        for r in runs:
            for sid in r.get("failed", []):
                fails[sid] = fails.get(sid, 0) + 1
        md = [
            "## 母婴站周报",
            "",
            f"- 本轮周期：{runs[0]['at'][:16] if runs else '-'} → {runs[-1]['at'][:16] if runs else '-'}",
            f"- 抓取轮次：**{len(runs)}**，累计新增 **{added}** 条，平均每轮 {added / max(len(runs), 1):.1f} 条",
            f"- 库内总计：**{actual}** 条（索引记 {index.get('total', '?')}）",
            "",
            "### 常挂的源",
            "",
        ]
        if fails:
            md += [f"- `{sid}` 失败 {n} 次" for sid, n in sorted(fails.items(), key=lambda kv: -kv[1])[:10]]
        else:
            md.append("- 无")
        md += ["", "### 本轮仍失败的源", ""]
        errs = [s for s in (last or {}).get("sources", []) if s.get("error")]
        md += [f"- `{s['id']}`：{s['error']}" for s in errs[:15]] or ["- 无"]
        WEEKLY.write_text("\n".join(md) + "\n", encoding="utf-8")
        print("\n".join(md))
        return 0

    alerts: list[tuple[str, str]] = []  # (级别, 描述)
    runs: list[dict] = state.get("runs", [])

    if actual == 0:
        alerts.append(("P0", "库内一条数据都没有"))
    if index.get("total") not in (None, actual):
        alerts.append(("P0", f"index.json 记 {index.get('total')} 条，实际 {actual} 条（物理行 {lines}）"))
    elif lines != actual:
        alerts.append(("P1", f"物理行 {lines} 条，唯一 id {actual} 条，存在重复入库"))

    if last:
        ok = last.get("sources_ok", 0)
        total_src = last.get("sources_total", 0)
        if ok < MIN_SOURCES_OK and total_src:
            alerts.append(("P0", f"成功源只有 {ok}/{total_src}（阈值 {MIN_SOURCES_OK}）"))
        if last.get("new", 0) == 0:
            zero = state.get("zero_streak", 0) + 1
            if zero >= ZERO_STREAK:
                alerts.append(("P0", f"连续 {zero} 轮新增为 0，过滤规则可能把内容全筛掉了"))
        hours = _hours_since(last.get("newest") or "")
        if hours > STALE_HOURS:
            alerts.append(("P0", f"最新一条已是 {hours:.0f} 小时前，内容断流"))

        streak = state.get("source_streak", {})
        zero_map = state.get("source_zero", {})
        avg = state.get("source_avg", {})
        for src in last.get("sources", []):
            sid = src["id"]
            if src.get("error"):
                streak[sid] = streak.get(sid, 0) + 1
                if streak[sid] >= SOURCE_FAIL_STREAK:
                    alerts.append(("P1", f"`{sid}` 已连续 {streak[sid]} 轮报错：{src['error']}"))
            else:
                streak[sid] = 0
            # 抓到 0 条候选、但历史上平均有产出：WAF 换规则后返回 200 的空页就是这种表现
            if not src.get("error") and src.get("relevant", 0) == 0:
                zero_map[sid] = zero_map.get(sid, 0) + 1
                if zero_map[sid] >= SOURCE_SILENT_STREAK and avg.get(sid, 0) >= 5:
                    alerts.append(
                        ("P1", f"`{sid}` 连续 {zero_map[sid]} 轮 0 条（历史均值 {avg[sid]:.0f}），疑似被静默拦截")
                    )
            else:
                zero_map[sid] = 0
            if src.get("relevant", 0) > 0:
                avg[sid] = round(avg.get(sid, src["relevant"]) * 0.7 + src["relevant"] * 0.3, 1)

        states = {"source_streak": streak, "source_zero": zero_map, "source_avg": avg}
    else:
        states = {}

    if last and last.get("new", 0) == 0:
        state["zero_streak"] = state.get("zero_streak", 0) + 1
    elif last:
        state["zero_streak"] = 0

    runs.append(
        {
            "at": last.get("at") if last else datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "new": last.get("new", 0) if last else 0,
            "total": actual,
            "failed": [s["id"] for s in (last or {}).get("sources", []) if s.get("error")],
        }
    )
    # 7 天前那次的总量：拿最早一条够老的记录比
    week_ago = [r for r in runs if _hours_since(r["at"]) >= 24 * 7]
    if week_ago:
        base = week_ago[-1]["total"]
        if base and actual < base * TOTAL_DROP_RATIO:
            alerts.append(("P1", f"库内 {actual} 条，比 7 天前的 {base} 条少了 {(1 - actual / base) * 100:.0f}%"))

    state.update(states)
    state["runs"] = runs[-HISTORY_RUNS:]
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    if not alerts:
        if ALERT.exists():
            ALERT.unlink()
        print(f"健康检查通过：库内 {actual} 条，本轮新增 {last.get('new', 0) if last else 0} 条")
        return 0

    md = [
        f"## 站点健康告警 · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        "@hhlamb 这轮抓取有异常，明细如下：",
        "",
        *[f"- **{level}** {text}" for level, text in alerts],
        "",
        f"- 库内实际 {actual} 条（索引记 {index.get('total', '?')}）",
        f"- 本轮成功源 {last.get('sources_ok', '?') if last else '?'}/{last.get('sources_total', '?') if last else '?'}，"
        f"新增 {last.get('new', '?') if last else '?'} 条",
        "",
        "健康检查是机械判断，可能误报；确认没问题就先关掉这个 issue，下轮还异常会再开。",
    ]
    ALERT.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

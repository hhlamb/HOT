"""配置加载：config.yaml 与 sources.yaml。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"找不到配置文件：{path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_config(path: Path | None = None) -> dict:
    return _read_yaml(path or ROOT / "config.yaml")


def load_sources(path: Path | None = None, *, enabled_only: bool = True) -> list[dict]:
    data = _read_yaml(path or ROOT / "sources.yaml")
    sources = data.get("sources") or []

    seen: set[str] = set()
    cleaned: list[dict] = []
    for src in sources:
        sid = str(src.get("id", "")).strip()
        if not sid or not src.get("url"):
            print(f"[warn] 跳过缺少 id 或 url 的源：{src}", file=sys.stderr)
            continue
        if sid in seen:
            print(f"[warn] 重复的源 id，已跳过：{sid}", file=sys.stderr)
            continue
        seen.add(sid)
        if enabled_only and not src.get("enabled", True):
            continue
        cleaned.append(src)
    return cleaned

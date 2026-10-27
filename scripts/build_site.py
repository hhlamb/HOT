#!/usr/bin/env python
"""把要对外发布的文件拷到 _site/。

为什么要这一步：源码目录里还有抓取脚本、方案文档、.workbuddy 记忆等不该出现在
公开站点上的东西。只把 index.html / assets / data / robots.txt 拷出来，发布
就只发布站点本身，跟 CI 里的做法保持一致。

用法：
    python scripts/build_site.py
    python scripts/build_site.py --out dist   # 换输出目录
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE_DIRS = ["assets", "data"]
INCLUDE_FILES = ["index.html", "article.html", "robots.txt"]
# 这几支文件被页面直接 <script>/<link> 引用。改完推上去，浏览器仍可能拿缓存里的
# 旧版本（实测改了榜单条数，刷新还是老样子），所以发布时按内容摘要加 ?v=，
# 内容一变 URL 就变，缓存自然失效。
VERSIONED_ASSETS = ["assets/style.css", "assets/feed-data.js", "assets/app.js", "assets/article.js"]


def _asset_version(rel: str) -> str | None:
    path = ROOT / rel
    if not path.exists():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def _versioned_html(raw: bytes) -> str:
    """给 HTML 里的静态资源引用加上内容摘要，避免读者拿到缓存里的旧 js/css。"""
    text = raw.decode("utf-8")
    for rel in VERSIONED_ASSETS:
        v = _asset_version(rel)
        if v:
            text = text.replace(f'"{rel}"', f'"{rel}?v={v}"')
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site")
    args = ap.parse_args()

    out = ROOT / args.out
    if out.exists():
        # 逐项删除而不是 rmtree：Windows 下目录被资源管理器/预览进程占用时，
        # rmtree 会整体失败，而逐项删除可以跳过单个被占用的文件继续。
        for child in sorted(out.iterdir()):
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError:
                print(f"! 无法删除 {child}（可能被占用），已跳过", file=sys.stderr)
    out.mkdir(parents=True, exist_ok=True)

    for name in INCLUDE_FILES:
        src = ROOT / name
        if not src.exists():
            print(f"! 缺少 {name}", file=sys.stderr)
        elif name.endswith(".html"):
            # 用二进制读进来再整体替换，避免 read_text 把 CRLF 折成 LF
            html = _versioned_html(src.read_bytes())
            (out / name).write_bytes(html.encode("utf-8"))
        else:
            shutil.copy2(src, out / name)

    for name in INCLUDE_DIRS:
        src = ROOT / name
        if not src.exists():
            print(f"! 缺少目录 {name}", file=sys.stderr)
            continue
        shutil.copytree(src, out / name, ignore=shutil.ignore_patterns("__pycache__", "*.tmp"))

    # 每条新闻的专属示意图：不存仓库（会被 .gitignore 掉），构建时现生成，
    # 这样历史数据攒得再多也不会把仓库撑大，而占位只有 3KB/张。
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from gen_item_art import generate_all  # noqa: PLC0415 - 只在构建时需要

        ok, bad = generate_all(out / "assets" / "items")
        print(f"  逐条配图 {ok} 张" + (f"，失败 {bad} 张" if bad else ""))
    except Exception as exc:  # noqa: BLE001 - 配图失败不该拖垮整站发布
        print(f"! 逐条配图生成失败：{exc}", file=sys.stderr)

    (out / ".nojekyll").write_text("", encoding="utf-8")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    n_data = len(list((out / "data").glob("*.jsonl")))
    print(f"站点已生成：{out}")
    print(f"  分片 {n_data} 个 · 合计 {total / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

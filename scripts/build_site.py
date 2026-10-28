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
import concurrent.futures
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
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


# ---------------------------------------------------------------- 配图自托管
# 三成外链图在读者浏览器里是打不开的：一是 Cloudflare/OSS 防盗链直接 403，
# 二是 Google 图床在国内访问不了。原文有图、页面却显示占位，读者会以为
# 这个站"没有图"。构建时把图抓下来放到站点里，读者就只跟 GitHub Pages 打交道。
# 抓不下来的保留原 URL，前端仍有分类主题图兜底，不会出现空框。
MIRROR_DIR = "assets/mirror"
MAX_IMAGE_BYTES = 1_600_000
EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}


def _local_name(url: str) -> str:
    key = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]
    path = url.split("?")[0].split("#")[0].lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"):
        if path.endswith(ext):
            return key + (".jpg" if ext == ".jpeg" else ext)
    return key + ".jpg"


def _fetch_image(url: str):
    """下载一张图。返回 (bytes, ext) 或 (None, 失败原因)。"""
    host = url.split("/")[2] if "//" in url else ""
    req = urllib.request.Request(url, headers={
        # 部分站按 UA 拦；带上常见浏览器头，能多救回一批。
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": f"https://{host}/" if host else "",
    })
    try:
        with urllib.request.urlopen(req, timeout=9) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                return None, ctype or "not-image"
            data = resp.read(MAX_IMAGE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 单张失败不影响整站
        return None, type(exc).__name__
    if len(data) > MAX_IMAGE_BYTES:
        return None, "too-large"
    if len(data) < 1200:
        return None, "too-small"
    return data, EXT_BY_TYPE.get(ctype, ".jpg")


def mirror_images(site_dir: Path) -> None:
    """把 data/*.jsonl 里的外链配图抓进站点，改写为相对路径。"""
    data_dir = site_dir / "data"
    shards = sorted(data_dir.glob("*.jsonl"))
    if not shards:
        return

    rows_by_shard: dict[Path, list[dict]] = {}
    urls: set[str] = set()
    for shard in shards:
        rows = []
        for line in shard.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows.append(row)
            img = row.get("image") or ""
            if img.startswith("http://") or img.startswith("https://"):
                urls.add(img)
        rows_by_shard[shard] = rows

    if not urls:
        return

    mirror_dir = site_dir / MIRROR_DIR
    mirror_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    failed: dict[str, int] = {}
    mapping: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_fetch_image, u): u for u in sorted(urls)}
        for fut in concurrent.futures.as_completed(futures):
            url = futures[fut]
            data, info = fut.result()
            if data is None:
                failed[info] = failed.get(info, 0) + 1
                continue
            name = _local_name(url)
            try:
                (mirror_dir / name).write_bytes(data)
            except OSError:
                failed["write"] = failed.get("write", 0) + 1
                continue
            mapping[url] = f"{MIRROR_DIR}/{name}"
            ok += 1

    if mapping:
        for shard, rows in rows_by_shard.items():
            changed = False
            for row in rows:
                img = row.get("image") or ""
                if img in mapping:
                    row["image"] = mapping[img]
                    changed = True
            if changed:
                shard.write_text(
                    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8",
                )
        # file:// 直接打开时读的是 latest.js，同样要换成本地路径
        latest = site_dir / "data" / "latest.js"
        if latest.exists():
            text = latest.read_text(encoding="utf-8")
            for src, dst in mapping.items():
                text = text.replace(src, dst)
            latest.write_text(text, encoding="utf-8")

    detail = "，".join(f"{k} {v}" for k, v in sorted(failed.items(), key=lambda kv: -kv[1])[:4])
    print(f"  配图自托管 {ok} 张" + (f"，失败 {sum(failed.values())} 张（{detail}）" if failed else ""))



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

    # 配图自托管：读者只跟 GitHub Pages 取图，不再受原站防盗链和网络影响。
    # 放在复制之后、生成逐条示意图之前——镜像成功的条目就不再需要占位图。
    try:
        mirror_images(out)
    except Exception as exc:  # noqa: BLE001 - 配图失败不该拖垮整站发布
        print(f"! 配图自托管失败：{exc}", file=sys.stderr)

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

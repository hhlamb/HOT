"""正文抽取：给"列表页只给了标题"的源补一段真实摘要。

为什么需要：HTML 列表页（食品伙伴网、中童传媒、中婴网、市监总局…）本身不带摘要，
页面上就会出现大量只有标题的卡片。两种补法：

1. 抓详情页取首段（本模块）—— 零成本，忠实原文，不编造。
2. 交给大模型润色（pipeline/llm.py）—— 更短更规整，但要 API Key。

两者是叠加关系：先抽取正文，再由模型基于正文写摘要。没有 Key 时第 1 步就已经
能让页面可读，所以默认开启。

注意：只取 first 段/描述，不落全文；每域仍走 PoliteClient 的限速。
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from bs4 import BeautifulSoup

from .clean import clip, strip_html

# 站点常见正文容器（命中一个就用它的首段）
CONTENT_SELECTORS = [
    "article .article-content",
    "div.article-content",
    "div.rich_media_content",
    "div.entry-content",
    "div.post-content",
    "div.content-detail",
    "div.art-content",
    "div.detail-content",
    "div.TRS_Editor",  # 政府站常见的正文容器
    "div#content",
    "div.content",
    "article",
    "main",
]

# 样板话术：命中的段落视为非正文
BOILERPLATE = re.compile(
    r"(版权所有|免责声明|免责条款|转载请注明|未经授权|扫码关注|长按识别|关注我们|"
    r"点击查看|点击阅读|更多精彩|上一篇|下一篇|相关阅读|编辑[:：]|来源[:：]|"
    r"copyright|all rights reserved|subscribe|sign up for our newsletter|"
    r"read more|share this article|advertisement)",
    re.I,
)

# 导航/列表痕迹：含有大量分隔符、或以"首页 >"开头
NAV_SMELL = re.compile(r"(»|›|>>|\|\s*\S+\s*\|)")

DATE_ONLY = re.compile(r"^[\d\s\-/:年月日\.]+$")


def _clean_text(raw: str) -> str:
    text = re.sub(r"\s+", " ", strip_html(raw or "")).strip()
    return text


def _acceptable(text: str, title: str, min_chars: int) -> bool:
    if len(text) < min_chars:
        return False
    if DATE_ONLY.match(text) or BOILERPLATE.search(text):
        return False
    if len(NAV_SMELL.findall(text)) >= 2:
        return False
    # 摘要不该是标题的复述
    if title and (text == title or text.startswith(title) and len(text) < len(title) + 10):
        return False
    return True


def _meta_desc(soup: BeautifulSoup) -> str:
    for attr, key in (
        ("property", "og:description"),
        ("name", "description"),
        ("name", "twitter:description"),
        ("itemprop", "description"),
    ):
        tag = soup.find("meta", attrs={attr: key})
        if tag and tag.get("content"):
            text = _clean_text(tag["content"])
            if len(text) >= 30 and not BOILERPLATE.search(text):
                return text
    return ""


def _paragraphs(soup: BeautifulSoup, min_chars: int, title: str, limit: int = 6) -> str:
    """按"先容器后全文"的顺序挑段；返回拼好的前几段正文。"""
    for selector in CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node is None:
            continue
        picked: list[str] = []
        for p in node.find_all(["p", "div"], recursive=True):
            # div 里往往还套着 p，重复取会拼接两遍
            if p.name == "div" and p.find("p") is not None:
                continue
            text = _clean_text(p.get_text(" ", strip=True))
            if not _acceptable(text, title, min_chars):
                continue
            picked.append(text)
            if sum(len(x) for x in picked) >= 600 or len(picked) >= limit:
                break
        if picked:
            return " ".join(picked)

    # 兜底：不猜容器，直接扫全文 p 标签
    picked = []
    for p in soup.find_all("p"):
        text = _clean_text(p.get_text(" ", strip=True))
        if not _acceptable(text, title, min_chars):
            continue
        picked.append(text)
        if sum(len(x) for x in picked) >= 500 or len(picked) >= 3:
            break
    return " ".join(picked)


def extract_from_html(html: str, url: str, title: str, min_chars: int = 40) -> tuple[str, str]:
    """返回 (摘要, 正文片段)。摘要用于直接展示，正文片段留给模型精加工。"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header"]):
        tag.decompose()

    body = _paragraphs(soup, min_chars, title)
    desc = _meta_desc(soup)
    if body:
        summary = body[:400]
    else:
        summary = desc
    return summary, body[:1500]


def fill_summaries(
    items: list[dict],
    client,
    cfg: dict,
    *,
    min_chars: int = 40,
    max_per_run: int = 60,
    workers: int = 4,
    summary_chars: int = 120,
    log=print,
) -> dict[str, int]:
    """给缺摘要的条目抓详情页补摘要，原地修改 items。

    返回统计：{"tried": n, "ok": n, "failed": n}
    """
    targets = [i for i in items if not (i.get("summary") or "").strip()]
    targets = targets[:max_per_run]
    if not targets:
        return {"tried": 0, "ok": 0, "failed": 0}

    stats = {"tried": 0, "ok": 0, "failed": 0}
    lock_free_counter = []

    def work(item: dict) -> None:
        url = item.get("original_url", "")
        if not url.startswith("http"):
            return
        try:
            resp = client.get(url)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "xml" not in ctype:
                raise RuntimeError(f"content-type={ctype[:30]}")
            summary, body = extract_from_html(resp.text, resp.url or url, item.get("title", ""), min_chars)
            summary = clip(re.sub(r"\s+", " ", summary).strip(), summary_chars)
            if summary:
                item["summary"] = summary
                item["summary_source"] = "extract"
                if body:
                    item["_body"] = body  # 只在内存里给模型用，落盘时会被 _clean 过滤
                lock_free_counter.append(1)
            else:
                item["summary"] = ""
        except Exception as exc:  # 单条失败不影响其它条目
            item.setdefault("_extract_error", str(exc)[:80])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, targets))

    stats["tried"] = len(targets)
    stats["ok"] = len(lock_free_counter)
    stats["failed"] = stats["tried"] - stats["ok"]
    log(f"  正文抽取 {stats['tried']} 条 → 成功 {stats['ok']} 条")
    return stats

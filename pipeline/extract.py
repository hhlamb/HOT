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

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote

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

# 正文摘录的最低长度：低于这个值说明详情页没抽到正文（或只有一段导语），
# 既不值得存，也说明下一轮可以再试一次。存与重试用同一个阈值，避免反复抓同一条。
MIN_BODY = 100


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


def _paragraphs(soup: BeautifulSoup, min_chars: int, title: str, limit: int = 14, target: int = 2400) -> str:
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
            if sum(len(x) for x in picked) >= target or len(picked) >= limit:
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
        if sum(len(x) for x in picked) >= target * 0.7 or len(picked) >= 10:
            break
    return " ".join(picked)


def _meta_image(soup: BeautifulSoup, page_url: str) -> str:
    """详情页主图。og:image 命中率最高，其次 twitter:image、首图 <img>。"""
    for attr, key in (
        ("property", "og:image"),
        ("name", "og:image"),
        ("name", "twitter:image"),
        ("property", "twitter:image"),
        ("itemprop", "image"),
    ):
        tag = soup.find("meta", attrs={attr: key})
        content = (tag.get("content") or "").strip() if tag else ""
        if content.startswith("//"):
            content = "https:" + content
        if content.startswith("http"):
            return content
    for img in soup.find_all("img", limit=8):
        src = (img.get("src") or img.get("data-src") or img.get("data-original") or "").strip()
        if src.startswith("//"):
            src = "https:" + src
        # 站点常把广告条、二维码、角标做成 <img>，这些进详情页当主图很难看
        if src.startswith("http") and not re.search(
            r"(logo|icon|avatar|sprite|qrcode|placeholder|advert|/ads?/|banner|ad\d{6,})", src, re.I
        ):
            return src
    return ""


def extract_from_html(html: str, url: str, title: str, min_chars: int = 40) -> tuple[str, str, str]:
    """返回 (摘要, 正文片段, 配图)。摘要用于直接展示，正文片段留给模型精加工。"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header"]):
        tag.decompose()

    image = _meta_image(soup, url)
    body = _paragraphs(soup, min_chars, title)
    desc = _meta_desc(soup)
    if body:
        summary = body[:400]
    else:
        summary = desc
    return summary, body[:4000], image


GNEWS_HOST = "news.google.com"

# 每轮最多解码多少条 Google News 跳转链。每条要两次请求，
# 全量解码会把抓取阶段拖长一倍，这里按优先级取前 N 条。
GNEWS_DECODE_MAX = 40

_DECODE_ENDPOINT = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


def _decode_gnews_once(client, url: str, html: str = "") -> str:
    """按 Google News 前端自身用的 batchexecute 接口解开跳转链。

    中转页现在是纯 JS 壳，GET 只会拿到空页面，但页面里仍带有
    data-n-a-sg（签名）与 data-n-a-ts（时间戳），拿这两个值调内部接口
    就能换回原文地址。接口一旦改版，这里会静默失败、退回原链接。
    """
    if not html:
        resp = client.get(url)
        html = getattr(resp, "text", "") or ""
    m_sig = re.search(r'data-n-a-sg="([^"]+)"', html)
    m_ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    if not (m_sig and m_ts):
        return ""
    article_id = url.split("/articles/")[-1].split("?")[0]
    inner = (
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
        f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{article_id}",{m_ts.group(1)},"{m_sig.group(1)}"]'
    )
    body = "f.req=" + quote(json.dumps([["Fbv4je", inner, None, "generic"]]))
    r = client.post(
        _DECODE_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
    )
    text = getattr(r, "text", "") or ""
    # 返回体是 ")]}'" 开头的多段文本，真正结果在第二段
    for chunk in text.split("\n\n"):
        if "garturlres" in chunk:
            try:
                payload = json.loads(chunk)
                inner_json = json.loads(payload[0][2])
                real = inner_json[1]
                if real.startswith("http"):
                    return real
            except Exception:
                continue
    return ""


def resolve_google_news(client, url: str) -> str:
    """把 news.google.com 的跳转链解析成媒体原文地址，失败就原样返回。"""
    if GNEWS_HOST not in (url or ""):
        return url
    # 依次尝试：重定向后地址 → 页面里明写的真实地址 → batchexecute 解码
    try:
        resp = client.get(url)
    except Exception:
        resp = None
    if resp is not None:
        final = getattr(resp, "url", "") or ""
        if final and GNEWS_HOST not in final:
            return final
        html = getattr(resp, "text", "") or ""
        m = re.search(r'data-n-au="(https?://[^"]+)"', html)
        if m and GNEWS_HOST not in m.group(1):
            return m.group(1)
    try:
        real = _decode_gnews_once(client, url, html)
        if real and GNEWS_HOST not in real:
            return real
    except Exception:
        pass
    return url


def fill_summaries(
    items: list[dict],
    client,
    cfg: dict,
    *,
    min_chars: int = 40,
    max_per_run: int = 60,
    workers: int = 4,
    summary_chars: int = 120,
    body_chars: int = 800,
    need_body: bool = False,
    log=print,
) -> dict[str, int]:
    """给缺摘要（或 need_body 时缺正文）的条目抓详情页，原地修改 items。

    落盘字段：
        summary  120 字以内，列表页展示
        body     800 字左右正文摘录，站内详情页展示（不存全文，避免整篇转载）
        _body    更长的正文，只在内存里给模型用，落盘时被 _clean 过滤

    返回统计：{"tried": n, "ok": n, "failed": n}
    """

    def lacks(item: dict) -> bool:
        if not (item.get("summary") or "").strip():
            return True
        if not need_body:
            return False
        # 补 body 模式：正文摘录太短，或者图片还空着，都值得再抓一次
        return len((item.get("body") or "").strip()) < MIN_BODY or not (item.get("image") or "").strip()

    targets = [i for i in items if lacks(i)][:max_per_run]
    if not targets:
        return {"tried": 0, "ok": 0, "failed": 0}

    stats = {"tried": 0, "ok": 0, "failed": 0}
    lock_free_counter = []
    decode_budget = [0]  # 本轮还剩多少次跳转链解码额度

    def work(item: dict) -> None:
        url = item.get("original_url", "")
        if not url.startswith("http"):
            return
        try:
            # 检索类来源先换成真实地址，否则拿到的永远是空壳页
            if GNEWS_HOST in url and decode_budget[0] < GNEWS_DECODE_MAX:
                decode_budget[0] += 1
                real = resolve_google_news(client, url)
                if decode_budget[0] == 1 and GNEWS_HOST in real:
                    # 首次失败留个现场：中转页是否给到签名、有多长，决定下一步怎么改
                    try:
                        probe = client.get(url)
                        h = getattr(probe, "text", "") or ""
                        log(
                            f"  · 跳转链解码失败（html {len(h)} 字节 / "
                            f"sg={bool(re.search(chr(34) + 'data-n-a-sg' + chr(34), h))} / "
                            f"n-au={bool(re.search('data-n-au', h))})"
                        )
                    except Exception:
                        pass
                if real and GNEWS_HOST not in real:
                    item["original_url"] = real
                    item["link_resolved"] = True
                    url = real
            resp = client.get(url)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}")
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype and "xml" not in ctype:
                raise RuntimeError(f"content-type={ctype[:30]}")
            summary, body, image = extract_from_html(resp.text, resp.url or url, item.get("title", ""), min_chars)
            summary = clip(re.sub(r"\s+", " ", summary).strip(), summary_chars)
            body = re.sub(r"\s+", " ", body or "").strip()
            if image and not (item.get("image") or "").strip():
                item["image"] = image
            if body and len(body) >= MIN_BODY:
                item["body"] = clip(body, body_chars)
                item["_body"] = body  # 更长的正文，只给模型用，落盘时会被 _clean 过滤
            if summary:
                cur = (item.get("summary") or "").strip()
                # 模型精加工过的摘要（summary_source=llm）永远不动；
                # 其余情况：没摘要，或者旧摘要明显偏短（截断版），就用刚抓到的完整段落替换
                if not cur or (len(cur) < 90 and item.get("summary_source") != "llm"):
                    item["summary"] = summary
                    if item.get("summary_source") != "llm":
                        item["summary_source"] = "extract"
                lock_free_counter.append(1)
            elif body:
                lock_free_counter.append(1)
        except Exception as exc:  # 单条失败不影响其它条目
            item.setdefault("_extract_error", str(exc)[:80])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, targets))

    stats["tried"] = len(targets)
    stats["ok"] = len(lock_free_counter)
    stats["failed"] = stats["tried"] - stats["ok"]
    log(f"  正文抽取 {stats['tried']} 条 → 成功 {stats['ok']} 条")
    return stats

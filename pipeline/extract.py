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

# "标题 + 媒体名 + 2026-10-26 09:08" 这种导语条：很多站把标题栏一起塞进正文容器，
# 抽出来就是重复标题加一串时间戳，放在摘录第一段很难看。
META_LINE = re.compile(
    r"(\d{4}-\d{2}-\d{2}\s*\d{1,2}:\d{2}|来源[:：]|作者[:：]|编辑[:：]|发布于\s*\d{4}|阅读量|点击量)"
)

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
    # 短段落里带时间戳/来源署名 = 标题栏被塞进了正文容器，跳过
    if len(text) < 130 and META_LINE.search(text):
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


BAD_IMG = re.compile(
    r"(logo|icon|avatar|sprite|qrcode|erweima|placeholder|noimage|no_image|default_img|"
    r"defaultimg|share_img|shareimg|watermark|advert|/ads?/|banner|blank\.|spacer|pixel|"
    r"weixin|wechat|wx_|foot|header_|/static/logo|ad\d{6,}|\.gif$|\.svg$)",
    re.I,
)


def _bad_image(url: str) -> bool:
    """明显不是内容配图的 URL：站标、二维码、分享卡片、广告条、追踪像素。"""
    return bool(BAD_IMG.search(url or ""))


def _meta_image(soup: BeautifulSoup, page_url: str) -> str:
    """详情页主图。og:image 命中率最高，其次正文容器里的首图、再退到全页首图。

    站点会拿「分享卡片」「栏目横幅」「站标」当 og:image，做成大图就是一块大色块，
    所以这里除了按 URL 关键词过滤，还看 og:image:width/height 的宽高比——
    横幅（太宽）和竖图（太窄）都不适合当缩略图。
    """
    og_w = soup.find("meta", attrs={"property": "og:image:width"})
    og_h = soup.find("meta", attrs={"property": "og:image:height"})

    def ratio_ok(tag_w, tag_h) -> bool:
        try:
            w, h = float(tag_w), float(tag_h)
        except (TypeError, ValueError):
            return True
        if w <= 0 or h <= 0:
            return True
        return 0.45 <= w / h <= 2.4

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
        if content.startswith("http") and not _bad_image(content):
            if ratio_ok(
                og_w.get("content") if og_w else None,
                og_h.get("content") if og_h else None,
            ):
                return content

    # 正文容器里的图最贴近内容，优先于全页扫
    candidates = []
    for selector in CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            candidates.extend(node.find_all("img", limit=6))
            break
    candidates.extend(soup.find_all("img", limit=10))

    for img in candidates:
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-original")
            or img.get("data-lazy-src")
            or ""
        ).strip()
        if src.startswith("//"):
            src = "https:" + src
        if not src.startswith("http") or _bad_image(src):
            continue
        if not ratio_ok(img.get("width"), img.get("height")):
            continue
        return src
    return ""


def extend_summary(summary: str, body: str, title: str, target: int = 180) -> str:
    """小模型经常只写六七十字就收尾，这里把正文里还没提到的信息句补进去。

    只在明显偏短时动手，且只补「摘要里没出现过」的句子（按前 10 字比对），
    所以读起来是"摘要 + 补充细节"，不是同一句话说两遍。
    """
    s = (summary or "").strip()
    if not body or len(s) >= target:
        return s
    need = target - len(s)
    extra: list[str] = []
    for sent in _sentences(body):
        if len(extra) >= 3 or need <= 0:
            break
        if len(sent) < 16 or len(sent) > 110:
            continue
        if BOILERPLATE.search(sent) or NAV_SMELL.search(sent):
            continue
        if sent[:10] in s or sent.strip("。") == (title or "").strip():
            continue
        if len(INFO_HINT.findall(sent)) == 0:
            continue
        if any(sent[:10] == x[:10] for x in extra):
            continue
        extra.append(sent)
        need -= len(sent)
    return (s + "".join(extra)) if extra else s


SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])")

# 有信息量的句子：带数字、比例、金额、时间点，或出现动作性动词
INFO_HINT = re.compile(
    r"(\d+(\.\d+)?\s*(%|％|亿|万|吨|家|款|元|人|倍|个百分点)|同比|环比|增长|下降|提升|"
    r"召回|下架|处罚|抽检|不合格|注册|获批|批件|发布|宣布|签署|收购|投资|投产|停产|"
    r"涨价|降价|提价|调价|份额|排名|预计|计划|目标)"
)


def _sentences(text: str) -> list[str]:
    parts = []
    for piece in SENT_SPLIT.split(text or ""):
        s = piece.strip()
        if 8 <= len(s) <= 220:
            parts.append(s)
    return parts


def make_digest(text: str, title: str, max_chars: int = 220) -> str:
    """把正文句子按信息量排序，拼成一段 200 字上下的概括。

    模型不可用时的兜底：与其把正文硬截断成半句话，不如挑出带数据、
    带动作的那几句按原文顺序重排——读起来像摘要，也确实是原文，不编造。
    """
    sents = _sentences(text)
    if not sents:
        return clip(text or "", max_chars)
    if len(sents) <= 2:
        return clip(" ".join(sents), max_chars)

    scored = []
    for idx, s in enumerate(sents):
        score = 0.0
        # 靠前的句子通常是导语，含新闻要素
        score += max(0, 12 - idx * 2)
        score += 8 * min(3, len(INFO_HINT.findall(s)))
        if 25 <= len(s) <= 110:
            score += 6
        if BOILERPLATE.search(s) or NAV_SMELL.search(s):
            score -= 20
        if title and (s == title or s.strip("。") == title.strip()):
            score -= 12
        if s.endswith(("？", "?")):
            score -= 4
        scored.append((score, idx, s))

    picked: list[tuple[int, str]] = []
    total = 0
    for score, idx, s in sorted(scored, key=lambda x: -x[0]):
        if total >= max_chars:
            break
        picked.append((idx, s))
        total += len(s)
    if not picked:
        return clip(text, max_chars)
    picked.sort(key=lambda x: x[0])  # 还原原文顺序，读着才顺
    return clip("".join(s for _, s in picked), max_chars)


def make_points(text: str, title: str, n: int = 3, max_chars: int = 46) -> list[str]:
    """从正文里挑 n 条带硬信息（数字/动作）的短句当要点。

    要点是给"摘要之外还想知道的细节"用的，所以导语句（第 0 句）基本都在摘要里了，
    有别的候选时跳过它，免得要点变成摘要的截断复读。
    """
    cands = []
    for idx, s in enumerate(_sentences(text)):
        s = s.strip("。；;，, ")
        if not (14 <= len(s) <= max_chars + 20):
            continue
        if BOILERPLATE.search(s) or NAV_SMELL.search(s):
            continue
        if title and s == title.strip():
            continue
        hits = len(INFO_HINT.findall(s))
        if hits == 0:
            continue
        cands.append((hits * 10 + (5 if re.search(r"\d", s) else 0) - (6 if idx == 0 else 0), idx, s))
    if not cands:
        return []
    cands.sort(key=lambda x: -x[0])
    out: list[str] = []
    for _, idx, s in cands:
        if idx == 0 and len(cands) > n:
            continue
        if any(s[:12] == o[:12] for o in out):
            continue
        out.append(clip(s, max_chars))
        if len(out) >= n:
            break
    return out


def extract_from_html(html: str, url: str, title: str, min_chars: int = 40) -> tuple[str, str, str]:
    """返回 (摘要, 正文片段, 配图)。摘要用于直接展示，正文片段留给模型精加工。"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "header"]):
        tag.decompose()

    image = _meta_image(soup, url)
    body = _paragraphs(soup, min_chars, title)
    desc = _meta_desc(soup)
    if body:
        summary = make_digest(body, title, 260)
    else:
        summary = desc
    return summary, body[:4000], image


GNEWS_HOST = "news.google.com"

# 每轮最多解码多少条 Google News 跳转链。每条要两次请求（壳页 600KB + 接口），
# 全量解码会把抓取阶段拖长一倍，这里按优先级取前 N 条。
GNEWS_DECODE_MAX = 80

_DECODE_ENDPOINT = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


_DECODE_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}


def _garturlreq(article_id: str, ts: str, sig: str) -> str:
    """batchexecute 里 garturlreq 的参数串，字段位置不能动，动了就只回空。"""
    return (
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
        'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
        f'"{article_id}",{ts},"{sig}"]'
    )


def _parse_decode_reply(text: str) -> str:
    """从 batchexecute 的多段响应里抠出原文地址。

    响应体形如：")]}'" + 空行 + '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://...\\"]",...]]'
    真正有用的东西在第三列那个被二次转义的 JSON 字符串的第 2 个元素里。
    """
    for chunk in (text or "").split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith(")]}'"):
            nl = chunk.find("\n")
            chunk = chunk[nl + 1 :] if nl >= 0 else chunk[4:]
        if "garturlres" not in chunk:
            continue
        try:
            parsed = json.loads(chunk)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue
        for row in parsed:
            if not (isinstance(row, list) and len(row) > 2):
                continue
            payload = row[2]
            if not (isinstance(payload, str) and "garturlres" in payload):
                continue
            try:
                inner = json.loads(payload)
            except Exception:
                continue
            if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], str):
                if inner[1].startswith("http"):
                    return inner[1]
    return ""


def resolve_google_news(client, url: str) -> tuple[str, str]:
    """把 news.google.com 的跳转链解析成媒体原文地址。

    返回 (真实地址, 页面 html)。失败时真实地址为原链接、html 可能为空。
    中转页现在是一张 600KB 的纯 JS 壳，地址不在 HTML 里，只能靠
    data-n-a-sg（签名）+ data-n-a-ts（时间戳）调内部接口换。
    """
    if GNEWS_HOST not in (url or ""):
        return url, ""
    try:
        resp = client.get(url)
    except Exception:
        return url, ""
    final = str(getattr(resp, "url", "") or "")
    if final and GNEWS_HOST not in final:
        return final, ""
    html = getattr(resp, "text", "") or ""
    # 老版本中转页会直接把地址写在 data-n-au 上，仍然兼容
    m = re.search(r'data-n-au="(https?://[^"]+)"', html)
    if m and GNEWS_HOST not in m.group(1):
        return m.group(1), html
    m_sig = re.search(r'data-n-a-sg="([^"]+)"', html)
    m_ts = re.search(r'data-n-a-ts="([^"]+)"', html)
    if not (m_sig and m_ts):
        return url, html
    article_id = url.split("/articles/")[-1].split("?")[0]
    inner = _garturlreq(article_id, m_ts.group(1), m_sig.group(1))
    # 请求体是 [[[方法名, 参数串]]] —— 外面必须再包一层数组，多带参数会直接空响应
    body = "f.req=" + quote(json.dumps([[["Fbv4je", inner]]]))
    try:
        r = client.post(_DECODE_ENDPOINT, data=body, headers=_DECODE_HEADERS)
    except Exception:
        return url, html
    real = _parse_decode_reply(getattr(r, "text", "") or "")
    if real and GNEWS_HOST not in real:
        return real, html
    return url, html


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
                real, shell = resolve_google_news(client, url)
                if real and GNEWS_HOST not in real:
                    item["original_url"] = real
                    item["link_resolved"] = True
                    url = real
                elif decode_budget[0] == 1:
                    # 首次失败留个现场：壳页有多长、签名在不在，决定下一步怎么改
                    log(
                        f"  · 跳转链解码失败（壳页 {len(shell)} 字节 / "
                        f"sg={bool(re.search(chr(34) + 'data-n-a-sg' + chr(34), shell))} / "
                        f"ts={bool(re.search(chr(34) + 'data-n-a-ts' + chr(34), shell))}）"
                    )
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
                # 模型不可用时也要有要点，先从正文里挑几句带硬信息的顶上
                if not item.get("points"):
                    pts = make_points(body, item.get("title", ""))
                    if pts:
                        item["points"] = pts
                        item["points_source"] = "extract"
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

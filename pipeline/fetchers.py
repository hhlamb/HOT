"""抓取层：RSS / Atom、HTML 列表页、JSON API 三种抓取器。

约定：
- 每个抓取器只负责"拿到条目"，不做相关性判断，也不写盘。
- 单源失败抛 FetchError，由调用方捕获——绝不允许一个源炸掉整轮任务。
- 所有条目统一成同一个 dict 结构，字段见 make_item()。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin, urlparse

import feedparser
from bs4 import BeautifulSoup

from .clean import clip, is_cjk_text, normalize_url, strip_html

CST = timezone(timedelta(hours=8))  # 中文站点默认按北京时间解释"无时区"的时间

DATE_FULL = re.compile(r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})")
DATE_MD = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")
DATE_URL_DIR = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)")
DATE_URL_YM = re.compile(r"/(20\d{2})/(\d{1,2})(?:/|$)")
DATE_URL_HYPHEN = re.compile(r"/(20\d{2})-(\d{2})-(\d{2})(?:/|_|-|\.|$)")

# 明显不是文章页的路径片段
BAD_PATH = re.compile(
    r"/(about|contact|login|register|signup|search|tag|tags|category|categories|topic|"
    r"column|author|user|member|video|photo|gallery|live|special|download|app|help|"
    r"privacy|terms|advertis|subscribe|rss|feed|sitemap|english|index)(/|$|\?|\.)",
    re.I,
)
BAD_CLASS = re.compile(r"(nav|menu|footer|header|sidebar|breadcrumb|pagination|share|related|rank|hot-list)", re.I)


class FetchError(Exception):
    pass


# 常见二级后缀，用于把 news.foo.com 与 www.foo.com 认成同一个站
MULTI_SUFFIX = ("com.cn", "gov.cn", "org.cn", "net.cn", "edu.cn", "co.uk", "co.jp", "com.au", "co.nz")


def same_site(host_a: str, host_b: str) -> bool:
    """判断两个 host 是否属于同一个站。

    只比 netloc 会误杀大量站内链接——中婴网首页在 www.baobei360.com，
    文章却在 info. / news. 子域上，严格相等会把整站链接全丢掉。
    """
    a = (host_a or "").lower().split(":")[0]
    b = (host_b or "").lower().split(":")[0]
    if not a or not b:
        return False
    if a == b:
        return True

    def root(host: str) -> str:
        parts = host.split(".")
        if len(parts) <= 2:
            return host
        if ".".join(parts[-2:]) in MULTI_SUFFIX:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])

    return root(a) == root(b)


@dataclass
class FetchResult:
    source_id: str
    items: list[dict] = field(default_factory=list)
    error: str = ""
    elapsed_ms: int = 0
    http_status: int | None = None
    tried: str = ""  # 实际生效的抓取方式，用于排查 rss 退化成 html 的情况


# ---------------------------------------------------------------- 通用工具


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone(CST).isoformat(timespec="seconds")


def _to_iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST).isoformat(timespec="seconds")


def _struct_to_iso(struct_time) -> str:
    if not struct_time:
        return ""
    try:
        dt = datetime(*struct_time[:6], tzinfo=timezone.utc)
        return _to_iso(dt)
    except Exception:
        return ""


def parse_date_text(text: str, now: datetime) -> tuple[str, bool]:
    """从一段文本里挖日期。返回 (ISO 时间, 是否是猜的)。"""
    if not text:
        return "", True
    m = DATE_FULL.search(text)
    if m:
        try:
            return _to_iso(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))), False
        except ValueError:
            pass
    m = DATE_MD.search(text)
    if m:
        try:
            dt = datetime(now.year, int(m.group(1)), int(m.group(2)), tzinfo=now.tzinfo)
            if dt > now + timedelta(days=1):  # 年底跨年的情况
                dt = dt.replace(year=now.year - 1)
            return _to_iso(dt), True
        except ValueError:
            pass
    return "", True


def _date_from_url(url: str, now: datetime) -> tuple[str, bool]:
    for pattern, fmt in ((DATE_URL_DIR, "dmy"), (DATE_URL_HYPHEN, "dmy"), (DATE_URL_YM, "ym")):
        m = pattern.search(url)
        if not m:
            continue
        try:
            if fmt == "dmy":
                return _to_iso(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))), False
            return _to_iso(datetime(int(m.group(1)), int(m.group(2)), 1)), True
        except ValueError:
            continue
    return "", True


def _date_by_regex(pattern: str, text: str) -> tuple[str, bool]:
    """源的日期排版太特殊时（如中童传媒把日/年/月拆成三个标签），用命名分组兜底。

    date_regex 里用 (?P<y>..)(?P<m>..)(?P<d>..) 三个命名组，顺序随意。
    """
    try:
        m = re.search(pattern, text or "")
    except re.error:
        return "", True
    if not m or not m.groupdict():
        return "", True
    groups = m.groupdict()
    try:
        dt = datetime(int(groups["y"]), int(groups["m"]), int(groups["d"]))
    except (KeyError, TypeError, ValueError):
        return "", True
    return _to_iso(dt), False


MEDIA_URL = re.compile(r"^https?://\S+\.(mp4|m3u8|jpe?g|png|gif|webp|svg)(\?\S*)?$", re.I)


def _clean_summary(raw: str) -> str:
    """摘要清洗：去标签；如果正文只剩一个图片/视频地址（母婴前沿的 RSS 就是这样），直接丢弃。"""
    text = strip_html(raw or "")
    if not text or MEDIA_URL.match(text.strip()):
        return ""
    if re.fullmatch(r"https?://\S{10,}", text.strip()):
        return ""  # 纯链接，没有信息量
    return text


def make_item(
    *,
    source: dict,
    title: str,
    url: str,
    summary: str = "",
    published_at: str = "",
    date_guessed: bool = True,
    author: str = "",
    extra_tags: list[str] | None = None,
) -> dict | None:
    title = re.sub(r"\s+", " ", strip_html(title or "")).strip()
    if len(title) < 6:
        return None
    original = normalize_url(url)
    if not original.startswith("http") or not urlparse(original).netloc:
        return None
    return {
        "title": clip(title, 160),
        "summary": clip(_clean_summary(summary), 240) if summary else "",
        "source_id": source["id"],
        "source_name": source.get("name", source["id"]),
        "source_url": source["url"],
        "original_url": original,
        "published_at": published_at,
        "published_guessed": date_guessed,
        "fetched_at": _now_iso(),
        "language": source.get("lang", "zh"),
        "author": clip(author, 60) if author else "",
        "tags": extra_tags or [],
        "priority": source.get("priority", "P1"),
    }


# ---------------------------------------------------------------- RSS / Atom


def fetch_rss(client, source: dict, fetch_cfg: dict, now: datetime) -> FetchResult:
    result = FetchResult(source["id"], tried="rss")
    started = time.monotonic()
    resp = client.get(source["url"])
    result.http_status = resp.status_code
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}")

    parsed = feedparser.parse(resp.content)
    entries = parsed.entries or []
    if not entries:
        raise FetchError("feed 解析出 0 条")

    limit = fetch_cfg.get("max_items_per_source", 40)
    for entry in entries[:limit]:
        title = entry.get("title", "")
        link = entry.get("link", "")
        # Atom 可能出现多个 link，优先 alternate
        if not link:
            for candidate in entry.get("links", []) or []:
                if candidate.get("rel") in (None, "alternate") and candidate.get("href"):
                    link = candidate["href"]
                    break
        summary = entry.get("summary") or entry.get("description") or ""
        if not summary:
            content = entry.get("content") or []
            if content and isinstance(content, list):
                summary = content[0].get("value", "")

        published, guessed = "", True
        for key in ("published_parsed", "updated_parsed", "created_parsed"):
            iso = _struct_to_iso(entry.get(key))
            if iso:
                published, guessed = iso, False
                break
        if not published:
            published, guessed = parse_date_text(entry.get("published", "") or entry.get("updated", ""), now)

        item = make_item(
            source=source,
            title=title,
            url=link,
            summary=summary,
            published_at=published,
            date_guessed=guessed,
            author=entry.get("author", ""),
        )
        if item:
            result.items.append(item)

    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


# ---------------------------------------------------------------- HTML 列表页


def _is_nav_like(anchor) -> bool:
    for parent in list(anchor.parents)[:6]:
        name = (parent.name or "").lower()
        if name in ("nav", "header", "footer", "aside"):
            return True
        classes = " ".join(parent.get("class") or [])
        pid = parent.get("id") or ""
        if BAD_CLASS.search(classes) or BAD_CLASS.search(pid):
            return True
    return False


def _context_text(anchor, max_chars: int = 400) -> str:
    """在固定层级的祖先里找日期文本，避免把整页正文都当成上下文。"""
    for depth, parent in enumerate(anchor.parents):
        if depth > 4:
            break
        text = parent.get_text(" ", strip=True)
        if not text:
            continue
        if len(text) > max_chars and depth >= 1:
            continue
        return text[:max_chars]
    return ""


def _extract_title(anchor, source: dict) -> str:
    """从 <a> 里取标题。

    很多列表页的 <a> 把日期、栏目名、摘要一起包了进去（中童传媒的日期还是
    拆成三个标签的），直接 get_text() 会把"21 2026 10 行业新闻"拼到标题前面。
    策略：优先 title_selector → 其次内部最长的标题标签（h1~h6）→ 再次最长文本节点。
    """
    sel = source.get("title_selector")
    if sel:
        try:
            node = anchor.select_one(sel)
        except Exception:
            node = None
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text

    heads = [
        h.get_text(" ", strip=True)
        for h in anchor.find_all(re.compile(r"^h[1-6]$"))
        if len(h.get_text(" ", strip=True)) >= 6
    ]
    if heads:
        return max(heads, key=len)

    nodes = [s.strip() for s in anchor.find_all(string=True) if s and len(s.strip()) >= 6]
    if nodes:
        return max(nodes, key=len)
    return anchor.get_text(" ", strip=True)


def fetch_html_list(client, source: dict, fetch_cfg: dict, now: datetime) -> FetchResult:
    result = FetchResult(source["id"], tried="html")
    started = time.monotonic()
    resp = client.get(source["url"])
    result.http_status = resp.status_code
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}")

    base = str(resp.url or source["url"])
    host = urlparse(base).netloc
    soup = BeautifulSoup(resp.text, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    # 支持两种配置：
    #   selector: "div.list a"   —— 直接给链接的选择器（推荐，最省事）
    #   selector: "div.list"     —— 给容器，内部再找 a
    selector = source.get("selector")
    if selector:
        anchors = soup.select(selector)
        if anchors and not all(getattr(a, "name", "") == "a" for a in anchors):
            expanded = []
            for node in anchors:
                expanded.extend(node.find_all("a", href=True))
            anchors = expanded
    else:
        anchors = soup.find_all("a", href=True)

    must_include = source.get("url_must_include")
    must_re = re.compile(must_include, re.I) if must_include else None

    limit = fetch_cfg.get("max_items_per_source", 40)
    seen: set[str] = set()
    for anchor in anchors:
        if len(result.items) >= limit:
            break
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        full = urljoin(base, href)
        if not same_site(urlparse(full).netloc, host):
            continue
        if BAD_PATH.search(urlparse(full).path):
            continue
        if must_re and not must_re.search(full):
            continue

        text = _extract_title(anchor, source)
        if not text or len(text) < 10:
            text = (anchor.get("title") or "").strip()
        # 中文标题短也有信息量；英文标题需要更长才算文章
        if not (is_cjk_text(text) or len(text) >= 24):
            continue
        if _is_nav_like(anchor):
            continue

        key = normalize_url(full)
        if key in seen:
            continue
        seen.add(key)

        published, guessed = parse_date_text(_context_text(anchor), now)
        if not published and source.get("date_regex"):
            published, guessed = _date_by_regex(source["date_regex"], _context_text(anchor))
        if not published:
            published, guessed = _date_from_url(key, now)

        item = make_item(
            source=source,
            title=text,
            url=key,
            summary="",  # 列表页没有摘要，Stage 2 由模型补一句话
            published_at=published,
            date_guessed=guessed,
        )
        if item:
            result.items.append(item)

    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    if not result.items:
        raise FetchError("页面里没有识别出文章链接（可能 JS 渲染，需要给 selector）")
    return result


# ---------------------------------------------------------------- JSON API


def _openfda_url(days: int = 30) -> str:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    window = quote(f"report_date:[{start:%Y%m%d}+TO+{end:%Y%m%d}]", safe="+[]:")
    return f"https://api.fda.gov/food/enforcement.json?search={window}&limit=50&sort=report_date:desc"


def _fmt_openfda_date(raw: str) -> str:
    try:
        return _to_iso(datetime.strptime(raw, "%Y%m%d"))
    except Exception:
        return ""


# 只保留和乳/婴幼儿沾边的召回：食品召回库里绝大多数是奶酪、沙拉、熟食，
# 不过滤的话一轮能灌进 40 条无关内容，既占体积也浪费后面的模型调用
OPENFDA_KEEP = re.compile(
    r"(infant|baby|toddler|child|pediatric|formula|breast|milk|dairy|whey|"
    r"lactose|yogurt|yoghurt|cream|butter|casein|nutrition)",
    re.I,
)


def fetch_openfda(client, source: dict, fetch_cfg: dict, now: datetime) -> FetchResult:
    result = FetchResult(source["id"], tried="api:openfda")
    started = time.monotonic()
    # 日期窗口必须按运行日期动态生成，否则每轮都拿到同一批旧数据
    resp = client.get(_openfda_url(int(source.get("days", 30))))
    result.http_status = resp.status_code
    if resp.status_code == 404:
        # openFDA 无结果时返回 404，不是错误
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}")

    data = resp.json()
    records = data.get("results") or []
    for rec in records[: fetch_cfg.get("max_items_per_source", 40)]:
        firm = (rec.get("recalling_firm") or "").strip()
        product = (rec.get("product_description") or "").strip()
        reason = (rec.get("reason_for_recall") or "").strip()
        recall_no = (rec.get("recall_number") or "").strip()
        if not OPENFDA_KEEP.search(f"{product} {reason}"):
            continue
        # 标题用中文表述，但注意别把"召回"写进标题——它会让关键词打分虚高
        title = f"{firm}：{product} 被通报召回" if firm else f"{product} 被通报召回"
        url = (
            "https://www.accessdata.fda.gov/scripts/enforcement/enforce_rpt-Product-Tabs.cfm"
            f"?action=select&recall_number={quote(recall_no)}&view=4"
        )
        item = make_item(
            source=source,
            title=title,
            url=url,
            summary=f"{reason}（分类 {rec.get('classification', '未标注')}）" if reason else "",
            published_at=_fmt_openfda_date(rec.get("report_date", "")),
            date_guessed=False,
        )
        if item:
            result.items.append(item)

    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def fetch_gdelt(client, source: dict, fetch_cfg: dict, now: datetime) -> FetchResult:
    result = FetchResult(source["id"], tried="api:gdelt")
    started = time.monotonic()
    resp = client.get(source["url"])
    result.http_status = resp.status_code
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}")

    try:
        data = json.loads(resp.text)
    except Exception as exc:
        raise FetchError(f"JSON 解析失败: {exc}") from exc

    for rec in (data.get("articles") or [])[: fetch_cfg.get("max_items_per_source", 40)]:
        published, guessed = "", True
        raw = rec.get("seendate", "")
        if re.fullmatch(r"\d{8}T\d{6}Z", raw or ""):
            published = _to_iso(datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc))
            guessed = False
        item = make_item(
            source=source,
            title=rec.get("title", ""),
            url=rec.get("url", ""),
            summary="",
            published_at=published,
            date_guessed=guessed,
            author=rec.get("domain", ""),
        )
        if item:
            result.items.append(item)

    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


# ---------------------------------------------------------------- 调度入口


API_FETCHERS = {
    "openfda": fetch_openfda,
    "gdelt": fetch_gdelt,
}


def fetch_source(client, source: dict, fetch_cfg: dict, now: datetime) -> FetchResult:
    """按 type 分派；RSS 声明但实际返回 HTML 时自动降级为列表页解析。"""
    declared = (source.get("type") or "rss").lower()
    handler = source.get("handler") or declared
    try:
        if handler == "api":
            # 没显式写 api 字段时按域名推断，避免所有 API 源都退化成 openFDA
            name = source.get("api") or ("gdelt" if "gdeltproject" in source["url"] else "openfda")
            fn = API_FETCHERS.get(name)
            if fn is None:
                raise FetchError(f"未知 api handler: {name}")
            return fn(client, source, fetch_cfg, now)
        if handler == "html":
            return fetch_html_list(client, source, fetch_cfg, now)
        return fetch_rss(client, source, fetch_cfg, now)
    except FetchError as exc:
        # RSS 解析不出来时，如果页面其实是 HTML，再给一次机会
        if handler == "rss" and "0 条" in str(exc):
            try:
                fallback = fetch_html_list(client, source, fetch_cfg, now)
                fallback.tried = "rss→html"
                fallback.error = ""
                return fallback
            except FetchError:
                pass
        raise

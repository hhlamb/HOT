"""URL / 标题归一化，以及标题相似度计算。

这一层的目标只有一个：让"同一篇报道"在不同来源、不同 URL 形态下能被认出来。
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 常见的追踪参数，去重前必须剔除，否则同一篇文章的 URL 会各不相同
TRACKING_EXACT = {
    "from", "from_source", "ref", "refer", "referer", "referrer", "spm", "wfr",
    "share_token", "share_from", "share_source", "isappinstalled", "scene",
    "clicktime", "sessionid", "s_kwcid", "gclid", "fbclid", "yclid", "mc_cid",
    "mc_eid", "oc", "hl", "gl", "ceid", "cmpid", "ncid", "sr_share",
}
TRACKING_PREFIX = ("utm_", "pk_", "mtm_", "hmsr", "hmpl", "hmcu", "hmkw", "hmci", "wt.mc_")

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 标题里没有信息量的前后缀，例如 "[图]""【视频】""独家 |"
NOISE_PREFIX = re.compile(
    r"^(\s*[\[【（(][^\]】）)]{1,6}[\]】）)]\s*|"
    r"^\s*(独家|原创|快讯|重磅|最新|突发|视频|直播|图解|深度|观察)\s*[|｜:：]\s*)"
)


def normalize_url(url: str) -> str:
    """统一 URL 形态：去追踪参数、去 fragment、小写 host、去掉末尾斜杠。

    注意：只做"等价变形"，不做可能改变指向的改写（比如不删 path 里的 id）。
    """
    url = (url or "").strip()
    if not url:
        return ""
    # 协议相对链接
    if url.startswith("//"):
        url = "https:" + url
    try:
        parts = urlparse(url)
    except ValueError:
        return url

    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    # 去掉默认端口
    netloc = netloc.replace(":80", "") if scheme == "http" else netloc.replace(":443", "")

    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        low = key.lower()
        if low in TRACKING_EXACT or low.startswith(TRACKING_PREFIX):
            continue
        # 常见的文章 id 参数要保留（id / p / aid / articleId 等）
        query_pairs.append((key, value))
    query = urlencode(query_pairs, doseq=True)

    path = re.sub(r"/{2,}", "/", parts.path)
    if path.endswith("/index.html") or path.endswith("/index.htm"):
        path = path.rsplit("/", 1)[0] or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_title(title: str) -> str:
    """把标题压成可比对的形态：全半角统一、去标点、转小写、合并空白。"""
    title = unicodedata.normalize("NFKC", title or "")
    title = title.replace("\u200b", "").replace("\ufeff", "")
    title = NOISE_PREFIX.sub("", title)
    # 去掉尾部常见的站点后缀 " - 食品伙伴网" / "_新浪新闻"
    # Google News 偶尔把媒体名写成 " - https://www.xxx.com/"，长度超阈值会漏掉，
    # 这种必须单独剥，否则同一篇稿在不同转载站会被当成两条。
    title = re.sub(r"\s*[-_|｜]\s*https?://\S+$", "", title)
    title = re.sub(r"\s*[-_|｜]\s*[^-_|｜]{2,12}$", "", title)
    title = title.lower()
    title = re.sub(r"\s+", " ", title).strip()
    return title


def title_key(title: str) -> str:
    """只保留字母数字与汉字，作为相似度与精确去重的输入。"""
    key = normalize_title(title)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", key)


def bigrams(text: str) -> set[str]:
    if not text:
        return set()
    if len(text) == 1:
        return {text}
    return {text[i : i + 2] for i in range(len(text) - 1)}


def similarity(key_a: str, key_b: str) -> float:
    """字符 bigram Jaccard 相似度。

    中文不用编辑距离：两篇讲同一件事但改写过的标题，编辑距离会很大，
    bigram 集合的交集反而能抓住共用词组。
    """
    if not key_a or not key_b:
        return 0.0
    if key_a == key_b:
        return 1.0
    a, b = bigrams(key_a), bigrams(key_b)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def is_cjk_text(text: str) -> bool:
    return len(CJK_RE.findall(text or "")) >= 6


# 平台导流稿 / SEO 稿的典型标题。GDELT 这类全球检索源会带进不少这种内容，
# 它们往往过得了关键词筛（标题里确实有 baby formula），但没有信息价值。
JUNK_TITLE = re.compile(
    r"(how to (choose|pick|buy)|a complete guide|buying guide|things to know|"
    r"you (need|should) to know|^what .{2,40} said on|^top \d+ |"
    r"best \d+ .{0,20}(formula|bottles?|breast pumps?)|"
    r"^(review|recap)\s*[:：]|sponsored|advertorial|"
    r"优惠|促销|优惠券|代购|招募|加盟|怎么选|如何选|排行榜|测评榜)",
    re.I,
)


# 站点导航页/功能页被列表页当成文章抓进来（市场监管总局的"缺陷产品召回查询"
# "婴幼儿配方乳粉产品配方注册申请系统"就是这么进来的）：没有正文、没有发布时间，
# 占着卡片位置点进去还是空页。特征是"光秃秃的名词短语 + 查询/系统/指南"，
# 所以先排除带动作动词的标题（"某乳企上线追溯系统"是真的新闻）。
NAV_TITLE = re.compile(
    r"^(?![^\n]{0,24}(上线|发布|启动|启用|建成|投用|通过|获批|验收|开通))"
    r"[^\n]{0,18}(查询系统|查询|申请系统|申报入口|登记系统|办事指南|下载中心|服务指南)$"
)


def is_nav_title(title: str) -> bool:
    return bool(NAV_TITLE.search(title or ""))


# 电商导流/选购软文。聚合号（搜狐号、百家号、各种"XX说育儿"）把
# "奶粉怎么样""十大品牌盘点""双十一囤货攻略"这类词堆进标题，正文里没有一条
# 新事实，只有一份不知谁写的榜单。它们能过主题门槛（标题里全是奶粉），
# 所以必须单独判。注意口径：只认"导流/榜单"这类明确标记，
# 不认单独的"测评"——"人民锐评：纸尿裤风波落地 网络测评不可儿戏"是真的舆论新闻。
SELECTION_TITLE = re.compile(
    r"(选购指南|选购建议|选购参考|选购横评|购买攻略|囤货攻略|囤货全攻略|省钱攻略|"
    r"哪个便宜|价格比较|价格分析|推荐榜|排行榜|十大|横评|横测|测评榜|"
    r"怎么选|如何选|避坑指南|避坑|白皮书盘点|值得买|哪个牌子好|哪款好|"
    r"怎么样[？?]|深度评测|深度测评|对比与推荐|"
    # 表格页：监管部门的"抽查结果汇总表/一览表"整页都是品名与批次号，
    # 没有任何一条新闻事实（"成品油车用尿素纸尿裤坐便器…抽查结果汇总表"），
    # 靠标题里的"纸尿裤"混过主题门槛。
    r"汇总表|一览表)"
)


def is_selection_title(title: str) -> bool:
    return bool(SELECTION_TITLE.search(title or ""))


def is_junk_title(title: str) -> bool:
    return bool(JUNK_TITLE.search(title or "")) or is_nav_title(title) or is_selection_title(title)


# 企业公关稿/获奖软文。品牌官网的媒体中心通篇都是这类，会把信息流冲淡，
# 但它们往往命中大量品牌词，关键词打分拦不住，只能单独判。
PROMO_TITLE = re.compile(
    r"(荣膺|蝉联|斩获|包揽|摘得|问鼎|夺冠|连冠|七连冠|双冠|"
    r"荣获|再获|荣登|登顶|榜榜首|摘星|"
    r"周年记|周年庆|揭牌|启航|启幕|闪耀|亮相|首发|首秀|"
    r"邀您|诚邀|敬请期待|倒计时|圆满|成功举办|隆重|"
    r"座谈会|莅临|走访)"
)


def is_promo_title(title: str) -> bool:
    return bool(PROMO_TITLE.search(title or ""))


def strip_html(text: str) -> str:
    """去掉摘要里的标签与实体，保留纯文本。"""
    import html as html_lib

    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def clip(text: str, limit: int) -> str:
    """按字符截断，尽量在句末切，不留半句。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for mark in ("。", "！", "？", ". ", "；", "; "):
        idx = cut.rfind(mark)
        if idx >= limit * 0.6:
            return cut[: idx + len(mark)].strip()
    return cut.rstrip() + "…"

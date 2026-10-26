"""入库质量闸门：摘要与标题是否说的同一件事、摘要是否被复用、正文段落是否对得上、配图是否合规。

为什么需要单独一层
------------------
抓取层只保证「拿到了文本」，模型层只保证「写得像人话」，中间没人检查
「这段摘要说的到底是不是这篇稿」。实测踩过一类聚合页：行业日报每天更新十几条，
正文第一段是另一条新闻，模型照着第一段写摘要，于是《蒙牛悄悄养了三年的特医品牌》
的摘要讲的是新莱特人事任命，同一段摘要还挂到了《雀巢Q3》下面。
这类错误靠人工抽查拦不住（一轮几百条），必须在写盘之前机械校验。

校验口径
--------
1. 摘要相关性：标题与摘要的二字组重合度（标题侧覆盖率）。中文没有分词，
   二字组比整词召回稳，配合停用组过滤掉「公司/市场/行业」这类通用词。
2. 摘要唯一性：同一段摘要不得挂到两条记录上；近似重复（Jaccard ≥ .86）同样算复用。
3. 正文对齐：聚合页的正文是多条新闻拼起来的，按标题找最匹配的连续段落窗口，
   只保留那一段。找不到显著匹配就说这段正文不可用，宁可空着。
4. 文本可用性：乱码（U+FFFD）、中文标题配英文摘要（翻译失败）都不算摘要。

边界
----
这一层只降级、不生成。判定不可用的摘要会被清空并打上 `summary_source="extract"`，
下一轮 `scripts/run.py` 的「旧摘要升级」池会自动把它捡回去重写，
所以清空只是「暂时不展示错的」，不是「永久没有」。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- 文本相似度

# 二字组停用表：这些组合在任何行业稿里都高频，留着会把不相关的内容算成相关。
STOP_GRAMS = set(
    (
        "公司 企业 品牌 市场 行业 中国 母婴 产品 消费 全球 增长 发展 数据 报告 表示 显示 "
        "已经 正在 可以 如何 什么 为什么 我们 他们 一个 一家 这些 那些 以及 但是 因为 "
        "所以 目前 未来 继续 开始 进行 通过 相关 主要 重要 今年 去年 季度 亿元 万元 "
        "同比 环比 以上 以下 其中 此外 同时 对于 关于 根据 记者 编辑 来源 原文 本文 "
        "the and for with from that this will have has are was were its it's been more "
        "than into over such about after before while their they said says also new"
    ).split()
)

_WORD = re.compile(r"[0-9a-zA-Z\u4e00-\u9fff]+")


def _txt(value) -> str:
    return "" if value is None else str(value)


def grams(text: str) -> set[str]:
    """文本 → 二字组集合（英文按整词，长度 ≥4 的词才留，避免 the/and）。"""
    out: set[str] = set()
    low = _txt(text).lower()
    for seg in _WORD.findall(low):
        if seg.isascii():
            if len(seg) >= 4 and seg not in STOP_GRAMS:
                out.add(seg)
            continue
        for i in range(len(seg) - 1):
            g = seg[i : i + 2]
            if g not in STOP_GRAMS:
                out.add(g)
    return out


def coverage(needle: str, haystack: str) -> float:
    """needle 的二字组有几分之几出现在 haystack 里。"""
    a = grams(needle)
    if not a:
        return 0.0
    b = grams(haystack)
    if not b:
        return 0.0
    return len(a & b) / len(a)


def jaccard(a: str, b: str) -> float:
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


# ---------------------------------------------------------------- 文本可用性

CJK = re.compile(r"[\u4e00-\u9fff]")


def cjk_ratio(text: str) -> float:
    s = re.sub(r"\s+", "", _txt(text))
    if not s:
        return 0.0
    return len(CJK.findall(s)) / len(s)


def is_mojibake(text: str) -> bool:
    """替换字符（U+FFFD）出现较多 = 抓取时编码判错，这段文字不可用。"""
    s = _txt(text)
    if not s:
        return False
    return s.count("\ufffd") >= 2 or s.count("\ufffd") / max(1, len(s)) > 0.01


def is_untranslated(text: str, title: str) -> bool:
    """中文标题配一段几乎全英文的摘要 = 翻译没落地，对中文读者等于没有摘要。"""
    if cjk_ratio(title) < 0.3:
        return False
    return cjk_ratio(text) < 0.12 and len(_txt(text)) > 40


# ---------------------------------------------------------------- 正文对齐


def _units(text: str) -> list[str]:
    """把正文切成句子级单元，短句向相邻句合并，避免窗口里全是碎句。"""
    s = re.sub(r"\s+", " ", _txt(text)).strip()
    if not s:
        return []
    parts = re.split(r"(?<=[。！？!?；;])", s)
    units: list[str] = []
    buf = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) < 24 and len(buf) + len(p) <= 90:
            buf += p
            continue
        if buf:
            units.append(buf)
            buf = ""
        units.append(p)
    if buf:
        units.append(buf)
    return units


def align_body(title: str, body: str, max_chars: int = 900) -> tuple[str, float]:
    """聚合页正文里挑出与标题对应的那一段。

    返回 (对齐后的正文, 该段与标题的覆盖率)。找不到像样的段落就返回 ("", 0.0)。
    短正文（< 400 字）不做切分，直接原样返回，避免把正常文章拆坏。
    """
    text = re.sub(r"\s+", " ", _txt(body)).strip()
    if not text or len(text) < 400:
        return text, coverage(title, text)

    units = _units(text)
    if len(units) < 4:
        return text, coverage(title, text)

    best_i, best_j, best_cov = 0, min(3, len(units)), -1.0
    for size in (2, 3, 4, 5):
        for i in range(0, len(units) - size + 1):
            window = " ".join(units[i : i + size])
            cov = coverage(title, window)
            # 同样的覆盖率取更短的窗口：说明这几句就够支撑标题了，别把邻居拽进来
            if cov > best_cov + 0.02 or (abs(cov - best_cov) <= 0.02 and (i, size) == (best_i, best_j - best_i)):
                best_i, best_j, best_cov = i, i + size, cov

    picked = " ".join(units[best_i:best_j]).strip()
    if len(picked) > max_chars:
        picked = picked[:max_chars]
    return picked, round(best_cov, 3)


# ---------------------------------------------------------------- 摘要校验

# 摘要与标题重合度低于这个值、同时两边没有任何共同品牌，判为「完全跑题」。
MIN_TITLE_COV = 0.08
# 摘要与正文的重合度：够高说明摘要确实是从这篇的正文里写出来的，
# 即使它没有复述标题（读者不需要摘要复述标题），也不算串稿。
MIN_BODY_COV = 0.30

# 品牌/机构词表：只用来判断「摘要和标题说的是不是同一家」，
# 不做实体识别，所以名字要写全（"新莱特""恒天然"），不要用简写。
# 后半段是母婴渠道与纸尿裤品牌——聚合页的"日报"最常把它们的动态拼成一段，
# 不收录就抓不到「摘要讲的是别人家」这种串稿。
BRANDS = (
    "蒙牛", "伊利", "雀巢", "达能", "飞鹤", "君乐宝", "澳优", "贝因美", "合生元", "惠氏",
    "雅培", "爱他美", "美素佳儿", "佳贝艾特", "圣元", "完达山", "光明乳业", "三元", "新乳业",
    "蓝河", "明治", "和光堂", "新莱特", "恒天然", "菲仕兰", "萨普托", "拉克塔利斯", "雅士利",
    "健合", "贝拉米", "孩子王", "宜品", "和氏", "红星美羚", "花冠", "a2",
    "帮宝适", "可心柔", "汤臣倍健", "宝洁", "花王", "尤妮佳", "大王", "露安适", "babycare",
    "嘉宝", "亨氏", "喜宝", "小皮", "英氏", "秋田满满", "窝小芽", "红色小象",
    "arla", "danone", "nestle", "nestlé", "fonterra", "synlait", "lactalis",
    "frieslandcampina", "saputo", "wyeth", "aptamil", "hipp", "meiji", "yashili",
    "biostime", "ausnutria", "beingmate", "pampers", "kimberly-clark", "kao", "unicharm",
)


def brand_set(text: str) -> set[str]:
    low = _txt(text).lower()
    return {b for b in BRANDS if b in low}


def _summary_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？!?；;])", _txt(text)) if s.strip()]


def summary_key(summary: str) -> str:
    """摘要的唯一性键：去掉全部空白后取前 60 字（排版差异不该算不同摘要）。"""
    return re.sub(r"\s+", "", _txt(summary))[:60]


def summary_drifts(title: str, summary: str) -> bool:
    """摘要前半段还扣题、后半段全在讲别的公司 —— 聚合页串稿的典型形态。

    判定条件：最后三句里有两句以上提到的品牌，一个都不在标题里。
    正常的综述稿（"某某等 6 款产品盘点"）品牌集中在前半句，不会命中。
    """
    sents = _summary_sentences(summary)
    if len(sents) < 3:
        return False
    tb = brand_set(title)
    foreign = 0
    for sent in sents[-3:]:
        sb = brand_set(sent)
        if sb and not (sb & tb):
            foreign += 1
    return foreign >= 2


def summary_matches(title: str, summary: str, body: str = "") -> tuple[bool, str]:
    """判断摘要是否可信。返回 (是否可用, 原因)。

    命中任意一条即不可用：
      1. 摘要后半段整段在讲别的公司（聚合页串稿）。
      2. 标题里的主角在摘要里一次都没出现，摘要却在讲别的品牌（张冠李戴）。
      3. 摘要与标题的二字组几乎不重合，且两边没有任何共同品牌/机构（完全跑题）。
      4. 摘要既不复述标题，又和这篇的正文对不上（无来源的概括）。
    """
    s = _txt(summary).strip()
    if not s:
        return False, "empty"
    if len(s) < 20:
        return False, "too-short"
    if is_mojibake(s):
        return False, "mojibake"
    if is_untranslated(s, title):
        return False, "untranslated"

    tb, sb = brand_set(title), brand_set(s)
    if summary_drifts(title, s):
        return False, "digest"
    if tb and sb and not (tb & sb):
        return False, "wrong-subject"

    t_cov = coverage(title, s)
    if t_cov >= MIN_TITLE_COV:
        return True, "title"
    if tb & sb:
        return True, "brand"
    if body and coverage(s, body) >= MIN_BODY_COV:
        return True, "body"
    # 走到这里说明：字面几乎不重合、也没有共同品牌。只有「摘要确实在讲具体公司、
    # 而标题里一家都没有」才敢判跑题；两边都没提公司时（泛行业背景陈述），
    # 字面不重合是正常的，不能当串稿处理。
    if sb and not tb:
        return False, "off-topic"
    return True, "generic"


def collapse_repeats(text: str, max_unit: int = 40) -> str:
    """把正文里"同一段话连着出现好几遍"折叠成一遍。

    有些站的 RSS/AMP 页会把标题、作者、日期拼进 description，抽取时又叠上
    og:description，结果正文开头变成
    「标题 作者 2026-09-17 2 min read 标题 标题 作者 作者 作者…」，
    读者看到的是同一句话刷屏，比没有正文更劝退。

    做法是按词切分，在当前位置找"最长的、紧跟着又重复一遍"的片段，
    命中就跳过第二遍。反复几轮直到稳定——因为重复的粒度不一样
    （先重复标题、再重复署名），一轮只能消一层。
    """
    words = _txt(text).split()
    if len(words) < 12:
        return _txt(text)
    for _ in range(6):
        out: list[str] = []
        i, n, changed = 0, len(words), False
        while i < n:
            k = 0
            for cand in range(min(max_unit, (n - i) // 2), 2, -1):
                if words[i : i + cand] == words[i + cand : i + 2 * cand]:
                    k = cand
                    break
            if k:
                out.extend(words[i : i + k])
                i += 2 * k
                changed = True
            else:
                out.append(words[i])
                i += 1
        words = out
        if not changed:
            break
    return " ".join(words)


def first_sentences(text: str, limit: int = 160) -> str:
    """从正文里截一段完整句子，作为降级摘要（不生成，只搬运）。"""
    s = re.sub(r"\s+", " ", _txt(text)).strip()
    if not s:
        return ""
    out = ""
    for m in re.finditer(r"[^。！？!?]+[。！？!?]?", s):
        piece = m.group(0).strip()
        if not piece:
            continue
        if len(out) + len(piece) > limit and out:
            break
        out += piece
        if len(out) >= limit * 0.7:
            break
    return out.strip()


# ---------------------------------------------------------------- 配图

# 不是内容配图：站标、二维码、分享卡片、追踪像素、占位图。
BAD_IMAGE = re.compile(
    r"(ewm[-_a-z0-9]*\.|qrcode|erweima|wxshare|share/img|shareimg|share_img|"
    r"feedbackpics|/logo[s]?[._/-]|logo\.(png|jpe?g|webp|gif)|"
    r"placeholder|noimage|no_image|blank\.|spacer|pixel|/ads?/|advert|banner_)",
    re.I,
)


def normalize_image(url: str) -> str:
    """配图 URL 规整：去掉明显不是配图的、把 http 升到 https。

    https 升级是硬要求：站点本身是 https，引用 http 图片会被浏览器按
    Mixed Content 拦掉，控制台一片红，图也是裂的。少数站点的 https 端口
    没开，前端 error 兜底会把这张缩略图隐藏掉，不会留裂图。
    """
    u = _txt(url).strip()
    if not u:
        return ""
    if u.startswith("//"):
        u = "https:" + u
    if BAD_IMAGE.search(u):
        return ""
    if u.startswith("http://"):
        u = "https://" + u[len("http://") :]
    if not u.startswith("https://"):
        return ""
    return u


# ---------------------------------------------------------------- 批量清洗


def clean_item(item: dict, seen: dict[str, str] | None = None) -> list[str]:
    """就地清洗一条记录，返回改动了什么（供报告统计）。

    seen: 已占用的摘要 → 占用它的条目 id，用于唯一性约束。
    """
    notes: list[str] = []
    title = _txt(item.get("title_zh") or item.get("title")).strip()

    # 1) 配图
    img = _txt(item.get("image")).strip()
    fixed = normalize_image(img)
    if img and not fixed:
        item.pop("image", None)
        notes.append("image-dropped")
    elif fixed != img:
        item["image"] = fixed
        notes.append("image-https")

    # 2) 正文：编码坏了直接丢，其余留到最后和摘要一起判断——
    #    正文本身很难用覆盖率单独定性（标题常是创意化的，正文复述不到标题的二字组），
    #    但一旦摘要被判成"讲的是别人家"，这段正文就是共犯，要一起处理。
    body = _txt(item.get("body")).strip()
    if body and is_mojibake(body):
        item["body"] = ""
        body = ""
        notes.append("body-mojibake")
    if body:
        # 聚合站的正文常把标题/署名/日期拼好几遍，折叠成一遍再判断摘要
        fixed_body = collapse_repeats(body)
        if fixed_body != body:
            item["body"] = fixed_body
            body = fixed_body
            notes.append("body-collapsed")

    # 3) 摘要
    summary = _txt(item.get("summary")).strip()
    ok, why = summary_matches(title, summary, body)

    # 摘要在讲别人家 → 先看看正文里有没有与标题对应的那一段（聚合页），
    # 找得到就只留那一段，找不到就说明这篇的正文抓错了，整段撤掉。
    if not ok and why in ("digest", "wrong-subject") and body:
        seg, cov = align_body(title, body)
        if seg and cov >= 0.35:
            item["body"] = seg
            body = seg
            notes.append("body-aligned")
        else:
            item["body"] = ""
            body = ""
            notes.append("body-dropped")
        ok, why = summary_matches(title, summary, body)

    if ok:
        dup_key = summary_key(summary)
        if seen is not None:
            owner = seen.get(dup_key)
            if owner and owner != item.get("id"):
                ok = False
                why = "duplicate"
        if ok and seen is not None:
            seen[dup_key] = item.get("id", "")

    if not ok:
        # 有正文就搬一句原文顶上，没有就留空——空着时前端写「暂无摘要」，
        # 比挂一段别人的话诚实。两种都打上待重写标记，下一轮模型优先补。
        fallback = first_sentences(body) if body else ""
        if fallback and coverage(title, fallback) >= MIN_TITLE_COV:
            item["summary"] = fallback
            item["summary_source"] = "extract"
            notes.append(f"summary-fallback({why})")
        else:
            item["summary"] = ""
            item["summary_source"] = "none"
            item["summary_kind"] = "none"
            notes.append(f"summary-cleared({why})")
        item["needs_summary"] = True

    # 4) 要点也跟着摘要一起作废：要点是模型读同一段错正文写的，
    #    留着一排跟标题无关的“要点”比没有更糟
    if any(n.startswith("summary-cleared") for n in notes) and (item.get("points_source") or "") == "llm":
        item.pop("points", None)
        item.pop("points_source", None)
        notes.append("points-dropped")

    return notes


def sanitize(items: list[dict], seen: dict[str, str] | None = None) -> dict:
    """批量清洗。返回统计，方便在流水线日志里报一行。

    seen 可由调用方预填（存量条目已占用的摘要），这样新条目不会复用旧条目的原话。
    """
    seen = {} if seen is None else seen
    stats: dict[str, int] = {}
    for item in items:
        for note in clean_item(item, seen):
            key = note.split("(")[0]
            stats[key] = stats.get(key, 0) + 1
    return stats

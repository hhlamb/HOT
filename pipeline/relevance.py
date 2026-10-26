"""关键词相关度粗筛 + 初步分类。

设计口径（和 PLAN.md 一致）：
    先用关键词把明显无关的内容挡掉，省下大模型调用；Stage 2 再接"模型精筛"。
    打分制的目的不是精确，而是可控——阈值放在 config.yaml 里，随时能调松紧。

为什么不能只用"奶粉"一个词：
    语料里有大量"乳业/乳制品/生鲜乳/乳清蛋白"的内容，它们属于供应链范畴，
    一个"奶粉"关键词会把这一半以上的有效信息筛掉。
"""

from __future__ import annotations

import re

from .clean import is_junk_title, is_promo_title

# ---------------- 关键词表 ----------------
# 权重含义：5=直接命中主题，3=行业强相关，2=事件/政策/资本类修饰词，1=泛行业词
KEYWORDS: dict[str, int] = {
    # —— 直接主题（命中即基本可以收）
    "奶粉": 5, "婴配粉": 5, "婴配": 5, "配方奶粉": 5, "婴幼儿配方": 5,
    "婴幼儿": 4, "婴儿": 4, "新生儿": 3, "母乳": 4, "辅食": 4, "奶瓶": 3,
    "infant formula": 5, "baby formula": 5, "follow-on formula": 5,
    "infant nutrition": 5, "baby food": 4, "toddler": 3, "breast milk": 4,
    "human milk": 3, "newborn": 2, "infant": 3,
    # —— 品牌
    "达能": 3, "雀巢": 3, "飞鹤": 3, "伊利": 3, "蒙牛": 3, "君乐宝": 3,
    "爱他美": 3, "美素佳儿": 3, "惠氏": 3, "雅培": 3, "a2": 2, "贝因美": 3,
    "合生元": 3, "澳优": 3, "佳贝艾特": 3, "圣元": 3, "完达山": 3, "蓝河": 3,
    "光明乳业": 2, "三元": 2, "新乳业": 2, "明治": 2, "和光堂": 2,
    "danone": 3, "nestle": 3, "nestlé": 3, "wyeth": 3, "aptamil": 3,
    "friso": 3, "similac": 3, "abbott": 2, "meiji": 2, "lactalis": 3,
    "beingmate": 3, "yashili": 3, "ausnutria": 3, "biostime": 3, "hipp": 3,
    "reckitt": 2, "kraft heinz": 2, "arla": 2, "frieslandcampina": 3,
    # —— 原料与供应链
    "乳粉": 3, "大包粉": 4, "基粉": 4, "生鲜乳": 3, "原奶": 3, "奶源": 3,
    "牧场": 2, "奶牛": 2, "乳清": 4, "乳清蛋白": 4, "乳铁蛋白": 4, "酪蛋白": 3,
    "hmo": 3, "母乳低聚糖": 4, "dha": 2, "ara": 2, "益生菌": 2, "羊奶粉": 4,
    "dairy": 3, "whey": 3, "casein": 2, "lactoferrin": 4, "milk powder": 4,
    "skim milk": 3, "whole milk powder": 4, "raw milk": 3, "herd": 1,
    # —— 新西兰奶源（2026-10-26 接入 Farmers Weekly / Fonterra / DairyNZ 后补）
    # 这几家的标题几乎都很短，只写 "milk price"、"payout"、"collections"，
    # 不补这些词的话，一条讲奶价的稿子只有 "dairy" 的 3 分，过不了 4.0 阈值。
    "farmgate": 4, "milk price": 4, "milk supply": 4, "milk collection": 4,
    "milk production": 3, "payout": 3, "new zealand dairy": 4,
    "dairy farmer": 3, "dairy sector": 3, "dairy export": 3, "dairy cow": 2,
    "fonterra": 3, "synlait": 3, "open country": 3, "westland milk": 3,
    # —— 政策与监管
    # 注意：这里**故意不收录机构名**（市场监管总局、海关总署、卫健委、FDA、EFSA…）。
    # 因为机构官网的每条新闻标题里都有自家名字，把它当关键词会让该站所有内容
    # 无条件通过筛选（实测市场监管总局的"党建/计量"新闻全部混了进来）。
    # 机构名只保留在 TAG_RULES 里用于打标签。
    "配方注册": 4, "注册制": 3, "食品安全国家标准": 4, "国标": 2, "抽检": 2,
    "召回": 3, "通报": 2, "不合格": 3, "重金属": 2, "阪崎肠杆菌": 4,
    "克罗诺杆菌": 4, "沙门氏菌": 3, "塑化剂": 3, "检出": 2, "处罚": 2,
    "recall": 3, "food safety": 2, "contaminat": 2, "outbreak": 2,
    # —— 渠道 / 跨境电商 / 资本
    "跨境电商": 3, "保税": 3, "正面清单": 4, "关税": 3, "进口": 2, "出口": 2,
    "代购": 2, "海淘": 2, "母婴店": 3, "母婴": 3, "渠道": 2, "经销商": 2,
    "涨价": 2, "降价": 2, "价格": 1, "收购": 2, "并购": 2, "财报": 2,
    "营收": 2, "市占率": 2, "市场份额": 2, "销售额": 2, "业绩": 1,
    "ipo": 1, "融资": 1, "上市": 1, "裁员": 2, "关厂": 2, "产能": 2,
    "tariff": 3, "cross-border": 3, "e-commerce": 2, "acquisition": 2,
    "merger": 2, "earnings": 2, "market share": 2,
    "supply chain": 2, "capacity": 2, "joint venture": 2,
}

# 明显无关的主题，命中直接丢弃（避免"奶粉"出现在综艺/体育新闻里被误收）
BLOCKLIST = [
    "星座", "彩票", "球赛", "足球", "篮球", "世界杯", "奥运会", "选秀", "综艺",
    "电影", "票房", "明星", "恋情", "演唱会", "手游", "网游", "cosplay",
    "celebrity", "box office", "football", "basketball", "olympic", "movie review",
    # 政治类时政新闻：不属于行业资讯，个人小站也不掺和
    "习近平", "总书记", "政治局", "全国人大", "政协", "外交部", "国防部",
    "中美关系", "台独", "涉台", "涉港",
]

# 分类标签规则：按顺序命中，一条可多标签。
# 顺序即优先级，前面的更"硬"。
TAG_RULES: list[tuple[str, list[str]]] = [
    ("质量安全", ["召回", "通报", "不合格", "检出", "超标", "沙门氏菌", "阪崎肠杆菌",
                  "克罗诺杆菌", "污染", "中毒", "投诉", "处罚", "下架", "停售",
                  "recall", "contaminat", "outbreak", "warning", "safety alert",
                  "listeria", "salmonella", "cronobacter"]),
    ("政策法规", ["配方注册", "注册制", "国标", "食品安全国家标准", "海关总署", "市场监管总局",
                  "卫健委", "农业农村部", "意见", "办法", "规定", "条例", "政策", "关税",
                  "正面清单", "监管", "抽检", "备案", "许可", "标准",
                  "regulation", "policy", "standard", "fda", "efsa", "fsanz", "fsma",
                  "tariff", "ban", "rule"]),
    ("市场数据", ["营收", "财报", "业绩", "市占率", "市场份额", "销售额", "增长", "下滑",
                  "销量", "价格", "涨价", "降价", "数据", "报告", "调研",
                  "earnings", "sales", "revenue", "market share", "forecast", "price",
                  "outlook", "quarter",
                  "milk price", "farmgate", "payout", "auction", "gdt"]),
    ("供应链", ["奶源", "原奶", "生鲜乳", "乳清", "乳铁蛋白", "大包粉", "基粉", "牧场",
                "奶牛", "产能", "工厂", "原料", "供应", "库存", "物流",
                "dairy", "whey", "lactoferrin", "milk powder", "raw milk", "supply",
                "ingredient", "plant", "capacity",
                "milk collection", "milk supply", "milk production", "dairy farmer",
                "dairy cow", "herd", "farm system"]),
    ("跨境电商", ["跨境电商", "保税", "海淘", "代购", "进口", "出口", "跨境",
                  "cross-border", "e-commerce", "import", "export", "bonded"]),
    ("品牌动态", ["达能", "雀巢", "飞鹤", "伊利", "蒙牛", "君乐宝", "爱他美", "美素佳儿",
                  "惠氏", "雅培", "a2", "贝因美", "合生元", "澳优", "佳贝艾特", "圣元",
                  "新品", "上市", "发布", "官宣", "签约", "合作", "代言", "收购", "并购",
                  "danone", "nestle", "nestlé", "wyeth", "aptamil", "friso", "similac",
                  "abbott", "meiji", "lactalis", "launch", "partnership", "acquisition",
                  "merger", "appoint", "ceo"]),
    ("海外动态", ["欧盟", "美国", "澳新", "新西兰", "澳大利亚", "日本", "韩国", "荷兰",
                  "爱尔兰", "德国", "法国", "英国", "加拿大", "海外", "全球",
                  "eu", "europe", "usda", "new zealand", "australia", "japan",
                  "global", "overseas", "international"]),
]

_NORMALIZED: list[tuple[str, int]] = [(k.lower(), v) for k, v in KEYWORDS.items()]
_BLOCKED = [b.lower() for b in BLOCKLIST]


def _count(text_low: str, word: str) -> int:
    """英文按词边界，中文按子串。"""
    if re.fullmatch(r"[a-z0-9 .'-]+", word):
        return len(re.findall(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text_low))
    return text_low.count(word)


def score(title: str, summary: str = "") -> tuple[float, list[str]]:
    """返回 (分数, 命中的关键词)。标题的权重算 1.6 倍，因为标题才是主题。"""
    title_low = (title or "").lower()
    body_low = (summary or "").lower()
    total = 0.0
    hits: list[str] = []
    for word, weight in _NORMALIZED:
        if _count(title_low, word):
            total += weight * 1.6  # 出现在标题里才说明主题相关
            hits.append(word)
        elif _count(body_low, word):
            total += weight * 0.5
            hits.append(word)
    return total, hits


def is_blocked(title: str, summary: str = "") -> bool:
    text = f"{title} {summary}".lower()
    return any(word in text for word in _BLOCKED)


def classify(title: str, summary: str = "") -> list[str]:
    """打标签，最多 3 个，避免卡片上一堆标签。"""
    text = f"{title} {summary}".lower()
    tags: list[str] = []
    for tag, words in TAG_RULES:
        if any(_count(text, w.lower()) for w in words):
            tags.append(tag)
        if len(tags) >= 3:
            break
    return tags or ["行业动态"]


def judge(title: str, summary: str, min_score: float = 4.0) -> tuple[bool, float, list[str], list[str]]:
    """粗筛入口。返回 (是否保留, 分数, 命中词, 标签)。"""
    if is_blocked(title, summary):
        return False, 0.0, [], []
    if is_junk_title(title):
        # SEO 导流稿：标题里往往正好带"baby formula"，关键词分很高但没信息价值
        return False, 0.0, [], []
    if is_promo_title(title):
        # 获奖/庆典/邀约类公关稿，信息量为零
        return False, 0.0, [], []
    sc, hits = score(title, summary)
    tags = classify(title, summary)
    return sc >= min_score, sc, hits, tags

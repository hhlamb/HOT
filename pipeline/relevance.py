"""关键词相关度粗筛 + 初步分类。

设计口径（和 PLAN.md 一致）：
    先用关键词把明显无关的内容挡掉，省下大模型调用；Stage 2 再接"模型精筛"。
    打分制的目的不是精确，而是可控——阈值放在 config.yaml 里，随时能调松紧。

    2026-10-28 补了一道**主题硬门槛**（见下面的 CORE_GROUP_*）：分数再高，
    标题里没有母婴/奶粉核心词照样丢。原因见那一段的注释。

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
    # —— 母婴品类与人群（2026-10-28 放宽）
    # 站名叫"跨境母婴"，但原来词表几乎只认奶粉与乳品：纸尿裤、喂养用品、
    # 孕产、母婴渠道的稿子过不了 4.0 全线阵亡（专业媒体一天十几条都进不来）。
    # 只补"品类 + 人群"，不碰"融资/上市/渠道"这类泛商业词——否则界面、
    # 第一财经的财经稿会成批涌进来，把奶粉主题冲淡。
    # 权重 3 的词出现在标题 = 4.8 分，单条即可过线；权重 2 的必须再叠一个词。
    "孕婴": 4, "孕婴童": 4, "婴童": 4, "母婴连锁": 4,
    "宝宝": 3, "宝妈": 3, "孕妈": 3, "孕妇": 3, "孕期": 3, "孕产": 3,
    "月嫂": 3, "月子": 3, "育儿": 3, "早教": 2, "亲子": 2, "儿童": 2,
    "纸尿裤": 3, "拉拉裤": 3, "学步裤": 3, "湿巾": 3, "奶嘴": 3, "婴儿车": 3,
    "童装": 2, "玩具": 1, "洗护": 2,
    "特医食品": 4, "特殊医学用途": 4, "营养品": 3, "维生素": 2, "钙铁锌": 3,
    "maternal": 3, "baby care": 3, "diaper": 3, "parenting": 2, "pregnancy": 2,
    # 母婴渠道与纯母婴用品品牌（2026-10-28 第二轮补）
    # 渠道稿（关店潮/加盟/单店流水）标题里通常只有品牌名，没有"奶粉"也没有品类词。
    # 只收纯母婴标的；花王、宝洁这种横跨化妆/日化的不进来，否则美妆稿会跟着混。
    "孩子王": 4, "爱婴室": 4, "乐友": 3, "孕婴世界": 4, "贝贝熊": 3,
    "帮宝适": 3, "好奇": 2, "尤妮佳": 3, "moony": 3, "babycare": 3,
    "可心柔": 3, "全棉时代": 3, "好孩子": 3, "小白熊": 3, "世喜": 3,
    "米粉": 3, "果泥": 3, "溶豆": 3, "零辅食": 3, "磨牙棒": 3,
    # —— 品牌
    "达能": 3, "雀巢": 3, "飞鹤": 3, "伊利": 3, "蒙牛": 3, "君乐宝": 3,
    "爱他美": 3, "美素佳儿": 3, "惠氏": 3, "雅培": 3, "a2": 2, "贝因美": 3,
    "合生元": 3, "澳优": 3, "佳贝艾特": 3, "圣元": 3, "完达山": 3, "蓝河": 3,
    # 点名跟踪的三个品牌（2026-10-28）：给 4 分是为了让"只说品牌、不带品类词"
    # 的稿子也能过 4.0 的阈值——"萃益维上新礼盒"这类标题里没有"奶粉""营养品"。
    "哈克草本": 4, "哈克": 3, "harker herbals": 4,
    "萃益维": 4, "cenovis": 4,
    # 剖蓓舒是渠道稿里出现频率很高的品牌，之前只进核心词表、没给权重，
    # 「象上家&剖蓓舒共启渠道增长逻辑」这种标题分数只有 3.2，过不了线。
    "剖蓓舒": 4,
    "光明乳业": 2, "三元": 2, "新乳业": 2, "明治": 2, "和光堂": 2,
    "danone": 3, "nestle": 3, "nestlé": 3, "wyeth": 3, "aptamil": 3,
    "friso": 3, "similac": 3, "abbott": 2, "meiji": 2, "lactalis": 3,
    "beingmate": 3, "yashili": 3, "ausnutria": 3, "biostime": 3, "hipp": 3,
    "reckitt": 2, "kraft heinz": 2, "arla": 2, "frieslandcampina": 3,
    # —— 原料与供应链
    "乳粉": 3, "大包粉": 4, "基粉": 4, "生鲜乳": 3, "原奶": 3, "奶源": 3,
    "乳业": 3, "乳企": 3, "乳品": 3, "液态奶": 3, "常温奶": 3, "巴氏奶": 3,
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
    # 2026-10-29：奶源倾斜后英文牧场稿大量进来，标题往往只有
    # "dairy farm""milk solids"，不给权重过不了 4.0；中文"奶牛场/奶农/收奶"同理。
    "dairy farm": 4, "milk solids": 4, "milk payout": 4, "dairy herd": 3,
    "奶牛场": 4, "奶农": 4, "收奶": 3,
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
    "代购": 2, "海淘": 2, "母婴店": 4, "母婴": 4, "渠道": 2, "经销商": 2,
    "涨价": 2, "降价": 2, "价格": 1, "收购": 2, "并购": 2, "财报": 2,
    "营收": 2, "市占率": 2, "市场份额": 2, "销售额": 2, "业绩": 1,
    "ipo": 1, "融资": 1, "上市": 1, "裁员": 2, "关厂": 2, "产能": 2,
    "tariff": 3, "cross-border": 3, "e-commerce": 2, "acquisition": 2,
    "merger": 2, "earnings": 2, "market share": 2,
    "supply chain": 2, "capacity": 2, "joint venture": 2,
}

# ---------------- 主题核心词（硬门槛） ----------------
# 站点是"跨境母婴 · 奶粉"，不是"食品"。泛食品源（食品伙伴网、中国食品安全报、
# 市场监管总局）每天都在发抽检与召回，标题里只要有"通报/出口/不合格"，
# 关键词分就能轻松过 4.0，于是「进口腌芒果二氧化硫超标」「波兰冷藏鸡肉检出沙门氏菌」
# 「日本通报我国出口烤花生不合格」整批混进来。这是打分模型治不了的病：
# 它的任务是"别漏行业稿"，而这里要的是"别把别的行业收进来"——加权重只会两头误伤。
# 所以另开一道不看分数、只看标题的硬门槛：标题里必须出现下面三类词之一。
#
#   A 人群/品类：奶粉、婴幼儿、纸尿裤、辅食、孕期、特医食品…
#   B 跟踪品牌：飞鹤、达能、雀巢、美素佳儿…（点名即主题）
#   C 奶粉供应链：原奶、生鲜乳、乳清、乳铁蛋白、HMO、羊奶粉、farmgate…
#
# 故意**只作打分、不作门槛**的词：乳业/乳品/牧场/奶牛/dairy/herd/synlait 之外的乳企。
# 实测它们的稿子长这样——「独立乳农敦促国会推进家庭牧场法案」
# 「奶牛池塘与人工湿地对减少氮流失效果相当」「乳品加工商食品安全指南」
# 「全球乳品复配市场2035年达154亿美元」，全是产业内政、农场环保、加工设备，
# 母婴读者看了没有任何用。
CORE_GROUP_A = [
    "奶粉", "婴配", "配方奶粉", "配方乳粉", "婴幼儿", "婴儿", "幼儿", "新生儿",
    "宝宝", "宝妈", "孕妈", "母婴", "婴童", "孕婴", "孕婴童", "母乳", "辅食",
    "纸尿裤", "拉拉裤", "学步裤", "湿巾", "奶瓶", "奶嘴", "婴儿车",
    "孕妇", "孕期", "孕产", "月子", "月嫂", "育儿", "妇幼", "儿童",
    "特医食品", "特殊医学用途", "营养品", "米粉", "果泥", "溶豆", "零辅食", "磨牙棒",
    "童装", "洗护", "母婴店", "母婴连锁",
    "infant", "baby", "toddler", "formula", "maternal", "diaper", "newborn",
    "breast milk", "human milk", "baby food", "baby care", "parenting", "pregnancy",
]
CORE_GROUP_B = [
    "飞鹤", "伊利", "蒙牛", "君乐宝", "达能", "雀巢", "惠氏", "雅培", "贝因美",
    "合生元", "澳优", "佳贝艾特", "圣元", "完达山", "蓝河", "爱他美", "美素佳儿",
    "美赞臣", "喜宝", "贝拉米", "健合", "妙可蓝多", "认养一头牛", "恒天然", "菲仕兰",
    "光明乳业", "新乳业", "明治", "和光堂", "孩子王", "爱婴室", "帮宝适", "尤妮佳",
    "哈克草本", "哈克", "萃益维", "润本", "汤臣倍健", "金领冠", "优博", "剖蓓舒",
    "启赋", "蓝臻", "能立多", "a2", "babycare",
    "danone", "nestle", "nestlé", "wyeth", "aptamil", "friso", "similac", "abbott",
    "meiji", "lactalis", "fonterra", "frieslandcampina", "biostime", "ausnutria",
    "yashili", "beingmate", "hipp", "nutricia", "harker", "cenovis",
    # 英文报道里品牌名是英文写法，只认中文名会把真稿子漏掉
    "feihe", "mengniu", "yili", "junlebao", "mead johnson", "synutra", "kabrita",
    "bellamy", "h&h", "a2 milk",
]
CORE_GROUP_C = [
    "原奶", "生鲜乳", "奶源", "乳清", "乳铁蛋白", "hmo", "母乳低聚糖", "大包粉",
    "基粉", "羊奶粉", "奶价", "乳粉", "乳制品", "婴幼儿配方",
    "milk powder", "whey", "lactoferrin", "milk price", "farmgate", "payout",
    "milk collection", "milk supply", "milk production", "raw milk",
    "dairy ingredient", "dairy export", "synlait", "westland", "open country",
    # 2026-10-29：站里明确"往海外奶源倾斜"。英文牧场端稿子的标题通常只写
    # "dairy farm""milk solids""co-op"，按原来的词表过不了门槛，整类内容
    # 一条都进不来。这几个词只描述奶源与收奶，不涉及 ESG 议题，可以放心收。
    "dairy farm", "milk solids", "milk cheque", "milk payout", "dairy co-op",
    "dairy cooperative", "supplier farmer", "dairy herd",
    # 中文稿写"奶牛场/奶农/收奶"，英文稿写 dairy farm——同一件事两种表述，
    # 只认英文会让中文奶源稿被误拦。
    "奶牛场", "奶农", "收奶",
]
_CORE_ALL: list[tuple[str, str]] = sorted(
    [(w.lower(), "人群品类") for w in CORE_GROUP_A]
    + [(w.lower(), "跟踪品牌") for w in CORE_GROUP_B]
    + [(w.lower(), "奶粉供应链") for w in CORE_GROUP_C],
    key=lambda kv: len(kv[0]),
    reverse=True,  # 长词优先，命中"婴幼儿配方乳粉"就不必再报"奶粉"
)

# 只剩"品牌/供应链"线索、但话题落在乳业治理、ESG、牧场科研、产业政治上的稿子。
# 典型标题：「Fonterra Board Pay Rise: Chair Fee Up to $528,500」
# 「梅西大学副校长称大学最适合支持乳制品业」「贝氏集团超越达能成为甲烷减排领先者」
# 「新西兰考虑对加拿大乳制品补贴采取 WTO 行动」。
# 判据是两条同时成立：标题里没有母婴人群/品类词（CORE_GROUP_A），
# 却出现下面这些话题词。只命中话题词不算——「婴幼儿奶粉碳足迹」这种还是要收的，
# 所以必须先确认"没有人"。
WEAK_TOPIC = re.compile(
    r"(薪酬|年薪|董事会|换届|育种|配种|甲烷|碳排放|碳足迹|氮肥|氮流失|"
    r"湿地|池塘|动物福利|福利新标准|科研|大学|学院|法案|国会|补贴|世贸|wto|"
    r"环保|绿色转型|净零|net zero|emission|welfare|board|pay rise|salary|"
    r"congress|senate|subsid)",
    re.I,
)

# 治理/人事/薪酬：和"奶源一手信息"无关，中英文稿都拦。
# 2026-10-29 把这一组从 WEAK_TOPIC 里拆出来，是因为英文源开始按"奶源倾斜"
# 放开 ESG/牧场话题——但董事会加薪、CEO 任命这类产业内政仍然没有信息价值。
WEAK_GOVERNANCE = re.compile(
    r"(薪酬|年薪|董事会|换届|pay rise|salary|remuneration|board (fee|pay))",
    re.I,
)

# 商品导购/种草稿：标题只有卖点，没有新闻事实。
# 判据是"卖点词 + 没有任何行业信号词"，单独出现卖点词不算——
# 「婴幼儿纸尿裤抽检不合格」这种稿子不会写"不闷屁屁"。
SHOP_CLAIM = re.compile(
    r"(一觉睡到|不闷|超薄|轻薄|瞬吸|返渗|干爽|亲肤|柔软|透气|"
    r"种草|开箱|亲测|好物|闭眼入|到手价|券后|活动价|包邮)"
)
NEWS_SIGNAL = re.compile(
    r"(召回|通报|检出|抽检|不合格|超标|处罚|下架|新规|国标|标准|注册|批件|"
    r"财报|营收|业绩|收购|并购|融资|上市|关店|份额|涨价|降价|奶价|"
    r"recall|alert|withdraw|recall|fine|standard|regulation|earnings|acquisition)",
    re.I,
)

# 社会新闻 / 地方政务稿：标题里有人物情节、有母婴店，但没有行业事实。
# 这类稿子靠"母婴店""纸尿裤"能过主题门槛，读起来却是本地广告或家长里短。
SOCIAL_TITLE = re.compile(
    r"(民警|警方|辅警|消防|救援|走失|失联|拐卖|盗窃|偷|诈骗|打架|纠纷|醉酒|"
    r"网友|吐槽|秒懂|妻子|丈夫|婆婆|公公|夫妻|情侣|大爷|大妈|"
    r"吓坏|后悔|崩溃|哭了|送医|卡住|窒息|烫伤)"
)
SOCIAL_INCIDENT = re.compile(
    r"((\d+岁|男童|女童|男婴|女婴|幼童)[^\n]{0,12}(感染|受伤|烫伤|窒息|身亡|送医|卡住|误食))"
)
LOCAL_GOV = re.compile(
    r"(乡村振兴|便民|增收|驻村|第一书记|基层治理|共同富裕|新时代|"
    r"(县|镇|乡|村)\s*[：:])"
)
# 本地门店广告：营业时间、服务清单、探店、同城。这类稿子标题里一定有地名
# 和"母婴店"，但内容是给本地消费者看的，跨境的读者拿不到任何信息。
LOCAL_AD = re.compile(
    r"(还营业|营业时间|服务清单|一次说清|探店|同城|附近|地址电话|预约到店|\bfaq\b)",
    re.I,
)


def is_local_or_social(title: str) -> bool:
    """本地广告、社会趣闻、地方政务稿——主题沾边但信息价值为零。

    只要标题里出现行业信号词（召回/通报/新规/财报…）就放行：
    「某地通报母婴店销售过期奶粉」是真的新闻，不该被"母婴店 + 县"误伤。
    """
    title = title or ""
    if NEWS_SIGNAL.search(title):
        return False
    if LOCAL_GOV.search(title):
        return True
    if LOCAL_AD.search(title):
        return True
    if SOCIAL_INCIDENT.search(title):
        return True
    if SOCIAL_TITLE.search(title) and not SHOP_CLAIM.search(title):
        return True
    return False


def is_shop_claim(title: str) -> bool:
    """只有卖点、没有任何新闻事实的导购稿。"""
    title = title or ""
    return bool(SHOP_CLAIM.search(title)) and not NEWS_SIGNAL.search(title)


def _is_digest(title: str) -> bool:
    """「新闻速递丨A；B；C」这类一日多条汇总稿。

    它一定含"揭牌/发布/上线"这些公关稿常见词，但整条里并排了好几个事实，
    信息量比单篇公关稿高得多，不能按公关稿丢掉。
    """
    return (title or "").count("；") + (title or "").count(";") >= 2

# 明显无关的主题，命中直接丢弃（避免"奶粉"出现在综艺/体育新闻里被误收）
BLOCKLIST = [
    "星座", "彩票", "球赛", "足球", "篮球", "世界杯", "奥运会", "选秀", "综艺",
    "电影", "票房", "明星", "恋情", "演唱会", "手游", "网游", "cosplay",
    "celebrity", "box office", "football", "basketball", "olympic", "movie review",
    # 政治类时政新闻：不属于行业资讯，个人小站也不掺和
    "习近平", "总书记", "政治局", "全国人大", "政协", "外交部", "国防部",
    "中美关系", "台独", "涉台", "涉港",
]

# 分类标签规则：一条可多标签，第一个是主分类。
# 顺序即同分时的优先级，前面的更"硬"。
# 第三个元素是权重：地名、公司名只是"维度"不是主题——一条讲英国出口的稿子
# 当然会提到英国，但它的主题是出口数据。所以「海外动态」压到 0.5、
# 「品牌动态」压到 0.75，让标题里的行业词（召回/价格/奶源/注册）优先。
# 硬编码在列表里而不是另开一张表：改词的时候顺手就能看到权重，不用两头对。
TAG_RULES: list[tuple[str, list[str], float]] = [
    ("质量安全", ["召回", "通报", "不合格", "检出", "超标", "沙门氏菌", "阪崎肠杆菌",
                  "克罗诺杆菌", "污染", "中毒", "投诉", "处罚", "下架", "停售",
                  "recall", "contaminat", "outbreak", "warning", "safety alert",
                  "listeria", "salmonella", "cronobacter"], 1.0),
    ("政策法规", ["配方注册", "注册制", "国标", "新国标", "食品安全国家标准", "海关总署", "市场监管总局",
                  "卫健委", "农业农村部", "意见", "办法", "规定", "条例", "政策", "关税",
                  "新规", "标签标识", "征求意见", "修订", "审议", "施行", "生效", "禁售", "禁令",
                  "正面清单", "监管", "抽检", "备案", "许可", "标准",
                  "regulation", "policy", "standard", "fda", "efsa", "fsanz", "fsma",
                  "tariff", "ban", "rule"], 1.0),
    ("市场数据", ["营收", "财报", "业绩", "市占率", "市场份额", "销售额", "增长", "下滑",
                  "销量", "价格", "涨价", "降价", "数据", "报告", "调研",
                  "参考价", "交易价", "收购价", "奶价", "价格指数", "批发价", "零售价",
                  "存栏", "出栏", "产量", "进口量", "出口量", "均价",
                  "earnings", "sales", "revenue", "market share", "forecast", "price",
                  "outlook", "quarter", "index",
                  "milk price", "farmgate", "payout", "auction", "gdt"], 1.0),
    ("供应链", ["奶源", "原奶", "生鲜乳", "乳清", "乳铁蛋白", "大包粉", "基粉", "牧场",
                "奶牛", "产能", "工厂", "原料", "供应", "库存", "物流", "冷链",
                "能源", "限电", "电力", "停产", "复工", "运输",
                "dairy", "whey", "lactoferrin", "milk powder", "raw milk", "supply",
                "ingredient", "plant", "capacity", "energy", "logistics",
                "milk collection", "milk supply", "milk production", "dairy farmer",
                "dairy cow", "herd", "farm system"], 1.0),
    # 「进口/出口」不放这里：那只是贸易流向，讲出口数据的稿子不该被打成跨境电商。
    # 真正的跨境电商要有渠道词（跨境/保税/海淘/代购/直邮/海外仓）。
    ("跨境电商", ["跨境电商", "保税", "保税仓", "海淘", "代购", "跨境", "直邮", "海外仓",
                  "cross-border", "e-commerce", "bonded", "direct mail"], 1.0),
    # 0.72 太低：品牌名出现在标题里是强信号，却会被摘要里随手提两个
    # "监管/标准"这类背景词压过去。「蒙牛终止与姆巴佩合作」当时被打成
    # 政策法规，就是 4×0.72=2.88 输给了摘要命中 3 分。提到 0.85 后，
    # 标题点名品牌（3.4）稳定胜过摘要背景词（上限 3.0），而标题里真出现
    # "新规/国标"时政策法规仍有 4 分压回来。
    ("品牌动态", ["达能", "雀巢", "飞鹤", "伊利", "蒙牛", "君乐宝", "爱他美", "美素佳儿",
                  "惠氏", "雅培", "a2", "贝因美", "合生元", "澳优", "佳贝艾特", "圣元",
                  "哈克草本", "哈克", "萃益维", "cenovis", "harker",
                  "新品", "上市", "发布", "官宣", "签约", "合作", "代言", "收购", "并购",
                  "融资", "A轮", "B轮", "战略合作", "投产", "开店", "新店", "任命", "卸任",
                  "参与", "项目", "投资", "分拆", "重组", "战略", "转型", "加码",
                  "danone", "nestle", "nestlé", "wyeth", "aptamil", "friso", "similac",
                  "abbott", "meiji", "lactalis", "launch", "partnership", "acquisition",
                  "merger", "appoint", "ceo"], 0.85),
    ("海外动态", ["欧盟", "美国", "澳新", "新西兰", "澳大利亚", "日本", "韩国", "荷兰",
                  "爱尔兰", "德国", "法国", "英国", "加拿大", "海外", "全球",
                  "eu", "europe", "usda", "new zealand", "australia", "japan",
                  "global", "overseas", "international"], 0.5),
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


def core_hit(title: str) -> tuple[str, str]:
    """标题里有没有"本主题"的核心词。返回 (命中的词, 所属组)；没有则 ("", "")。

    只看标题，不看摘要。摘要不可信：一条「云南检出12批次不合格食品，芒果…」
    的摘要末尾常带一句"其中涉及婴幼儿配方食品"，拿摘要判就会把它放进来；
    而真正讲婴幼儿的稿子，标题里一定有这个词。
    """
    title_low = (title or "").lower()
    for word, group in _CORE_ALL:
        if _count(title_low, word):
            return word, group
    return "", ""


def is_blocked(title: str, summary: str = "") -> bool:
    text = f"{title} {summary}".lower()
    return any(word in text for word in _BLOCKED)


def classify(title: str, summary: str = "") -> list[str]:
    """打标签，最多 3 个：第一个是主分类（列表只展示它），后面是辅分类。

    为什么要按标题加权而不是「按规则表顺序命中就收」：
        原实现顺着 TAG_RULES 取前三个命中的标签，导致「质量安全」「政策法规」
        永远排在前面——只要摘要里出现过"污染""标准"这类字眼，一条讲达能
        把酸奶废料转化为能源的稿子也会被打成"质量安全"。
        标题才是这篇稿在讲什么，摘要里的词可能只是举例或背景。
    具体口径：
        标题命中一次算 4 分，摘要命中一次算 1 分（最多计 3 分），
        于是"标题里明确提到的品类"必然压过"摘要里顺带一提的词"。
        同分时按 TAG_RULES 的顺序决定主分类，保证结果可复现。
    """
    title_low = (title or "").lower()
    body_low = (summary or "").lower()
    scored: list[tuple[float, int, str]] = []
    for idx, (tag, words, weight) in enumerate(TAG_RULES):
        t_hit = sum(1 for w in words if _count(title_low, w.lower()))
        b_hit = sum(1 for w in words if _count(body_low, w.lower()))
        # 摘要单独的命中不足以成为一个标签：它经常是背景举例，
        # 要求它至少出现两个不同关键词，或者标题里已经点到
        if not t_hit and b_hit < 2:
            continue
        raw = t_hit * 4 + min(b_hit, 3)
        scored.append((round(raw * weight, 2), -idx, tag))
    if not scored:
        return ["行业动态"]
    scored.sort(reverse=True)
    return [tag for _, _, tag in scored[:3]]


def judge(
    title: str,
    summary: str,
    min_score: float = 4.0,
    require_core: bool = True,
    strict_weak: bool = True,
) -> tuple[bool, float, list[str], list[str]]:
    """粗筛入口。返回 (是否保留, 分数, 命中词, 标签)。

    两道闸门，顺序不能反：
      1) 主题硬门槛 core_hit()：标题里没有母婴人群/品类、没有跟踪品牌、
         也没有奶粉供应链核心词 → 直接丢，**不看分数**。
         泛食品源的抽检稿就是在这里被拦住的。
      2) 关键词分 ≥ min_score：过了第一道再用分数确认它确实是行业稿。

    strict_weak：英文源传 False。海外奶源是本站的选题方向之一，牧场管理、
    甲烷、收奶量这类稿子在英文语境里就是"奶源一手信息"，用中文那套
    "产业内政一律不要"的口径会把整类内容清空。治理/人事/薪酬
    （WEAK_GOVERNANCE）对中英文一视同仁，仍然拦。
    """
    if is_blocked(title, summary):
        return False, 0.0, [], []
    if is_junk_title(title):
        # SEO 导流稿：标题里往往正好带"baby formula"，关键词分很高但没信息价值
        return False, 0.0, [], []
    if is_promo_title(title) and not _is_digest(title):
        # 获奖/庆典/邀约类公关稿，信息量为零
        return False, 0.0, [], []
    if is_shop_claim(title) or is_local_or_social(title):
        # 商品导购稿、本地广告、社会趣闻、地方政务稿：主题沾边但没有行业事实
        return False, 0.0, [], []
    word, group = core_hit(title)
    if require_core and not word:
        # 主题不对：不是母婴/奶粉这条线上的东西，分数再高也不收
        return False, 0.0, [], []
    if group != "人群品类":
        if WEAK_GOVERNANCE.search(title or ""):
            # 董事会加薪、CEO 任命：中英文都没有信息价值
            return False, 0.0, [], []
        if strict_weak and WEAK_TOPIC.search(title or ""):
            # 中文源：乳业治理/ESG/牧场科研属于产业内政，不是母婴读者要的
            return False, 0.0, [], []
    sc, hits = score(title, summary)
    tags = classify(title, summary)
    return sc >= min_score, sc, hits, tags

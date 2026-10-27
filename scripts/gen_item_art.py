"""按每条新闻的内容生成一张专属插图（SVG）。

背景：原来的做法是「一个分类一张图」，8 张图轮着用，一条关于召回的新闻和一条关于
营收的新闻配的是同一张画，读者读不出任何信息。这个脚本改成**逐条生成**：

  data/*.jsonl 里每条 item → assets/items/<id>.svg

怎么"结合内容"：不调任何模型，用关键词表把标题 + 摘要 + 全文开头归到两类视觉元素上——

  · 主元素：新闻在讲什么（召回→盾牌、政策→文件+印章、财报→折线柱图、出海→地球、
    物流→货轮、产能→厂房、渠道→购物车、新品→星标）
  · 次元素：新闻涉及什么品类（奶粉→罐、液态奶→利乐砖、酸奶→杯、奶酪→楔块、
    母婴→奶瓶、营养品→瓶）

  主元素画大（居右），次元素画小（左下），配色取自分类色，点缀位置由 item id 的
  hash 决定——所以每张图都不一样，但同一分类的图看起来是一套。

为什么仍是 SVG：同一张图要同时当 660px 大图和 108×76 缩略图，位图的细线缩到缩略图
会被压没；矢量 + non-scaling-stroke 让线宽在两种尺寸下都是 1.5 CSS 像素。

用法：
    python scripts/gen_item_art.py                 # 写 assets/items/
    python scripts/gen_item_art.py --out _site/assets/items
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import html
import json
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 配色

PAPER = "#f1e8db"
INK = "#3b4a42"
INK_SOFT = "#78857c"
GOLD = "#b8873b"

# 分类强调色，只用来画背景光晕和点阵（paper 底下压一层极淡的色块）
TONE = {
    "品牌动态": "#b8873b",
    "市场数据": "#45597b",
    "质量安全": "#b4472f",
    "供应链": "#6b7c3f",
    "跨境电商": "#2f7d7a",
    "政策法规": "#6b5b7b",
    "海外动态": "#4a6b8a",
    "行业动态": "#7a7f78",
}

W, H = 960, 612

STYLE = f"""<style>
  .k  {{ fill: none; stroke: {INK}; stroke-width: 1.5; vector-effect: non-scaling-stroke;
         stroke-linecap: round; stroke-linejoin: round; }}
  .kd {{ fill: none; stroke: {INK}; stroke-width: 1.3; stroke-dasharray: 7 7;
         vector-effect: non-scaling-stroke; opacity: .5; stroke-linecap: round; }}
  .ks {{ fill: none; stroke: {INK_SOFT}; stroke-width: 1.1; vector-effect: non-scaling-stroke;
         opacity: .6; stroke-linecap: round; }}
  .g  {{ fill: none; stroke: {GOLD}; stroke-width: 1.6; vector-effect: non-scaling-stroke;
         stroke-linecap: round; stroke-linejoin: round; }}
  .gd {{ fill: none; stroke: {GOLD}; stroke-width: 1.4; stroke-dasharray: 5 7;
         vector-effect: non-scaling-stroke; opacity: .85; stroke-linecap: round; }}
  .kb {{ fill: {INK}; opacity: .1; }}
  .gb {{ fill: {GOLD}; }}
  /* 主轮廓垫一层极淡的墨，线稿才有体块感，不然整张图像没上色的草图 */
  .kf {{ fill: {INK}; fill-opacity: .07; }}
</style>
"""


# ---------------------------------------------------------------- 图元库
# 每个图元在自己的局部坐标里画，左上角 (0,0)，返回 (宽, 高, svg)

ART: dict[str, tuple[int, int, str]] = {
    # 奶粉罐：罐身 + 罐盖 + 腰线 + 标签块
    "can": (200, 250, """
  <rect class="k kf" x="8" y="36" width="184" height="206" rx="14"/>
  <ellipse class="k" cx="100" cy="36" rx="92" ry="14"/>
  <ellipse class="ks" cx="100" cy="36" rx="64" ry="9"/>
  <line class="ks" x1="8" y1="106" x2="192" y2="106"/>
  <rect class="kd" x="42" y="126" width="116" height="62" rx="8"/>
  <line class="ks" x1="8" y1="216" x2="192" y2="216"/>"""),
    # 利乐砖：顶部斜脊是它和普通纸盒的区别
    "carton": (170, 240, """
  <path class="k kf" d="M18 66 L85 12 L152 66 V228 H18 Z"/>
  <line class="k" x1="18" y1="66" x2="152" y2="66"/>
  <line class="ks" x1="85" y1="12" x2="85" y2="66"/>
  <rect class="kd" x="44" y="112" width="82" height="56" rx="6"/>"""),
    # 酸奶杯：上宽下窄的杯身 + 杯口椭圆 + 盖膜
    "cup": (180, 220, """
  <path class="k kf" d="M32 58 L48 206 H132 L148 58"/>
  <ellipse class="k" cx="90" cy="58" rx="58" ry="12"/>
  <line class="ks" x1="24" y1="44" x2="156" y2="44"/>
  <rect class="kd" x="56" y="100" width="68" height="50" rx="6"/>"""),
    # 奶酪楔块：三角 + 两个孔
    "wedge": (220, 160, """
  <path class="k kf" d="M12 142 L118 16 L208 142 Z"/>
  <circle class="ks" cx="150" cy="112" r="9"/>
  <circle class="ks" cx="96" cy="120" r="6"/>
  <line class="ks" x1="12" y1="142" x2="208" y2="142"/>"""),
    # 奶瓶：奶嘴盖 + 瓶颈 + 瓶身 + 三条刻度
    "bottle": (140, 250, """
  <rect class="k kf" x="46" y="8" width="48" height="28" rx="7"/>
  <rect class="k" x="52" y="36" width="36" height="28"/>
  <path class="k" d="M34 64 H106 L112 224 Q112 242 94 242 H46 Q28 242 28 224 Z"/>
  <line class="ks" x1="36" y1="120" x2="60" y2="120"/>
  <line class="ks" x1="36" y1="150" x2="64" y2="150"/>
  <line class="ks" x1="36" y1="180" x2="60" y2="180"/>"""),
    # 营养瓶：短粗瓶 + 大标签
    "capsule": (160, 220, """
  <rect class="k kf" x="42" y="14" width="76" height="38" rx="8"/>
  <rect class="k" x="20" y="52" width="120" height="156" rx="16"/>
  <rect class="kd" x="36" y="104" width="88" height="62" rx="8"/>"""),
    # 折线柱图：坐标轴 + 四根柱 + 一条上升折线
    "chart": (260, 200, """
  <line class="k kf" x1="10" y1="12" x2="10" y2="188"/>
  <line class="k" x1="10" y1="188" x2="250" y2="188"/>
  <rect class="kb" x="38" y="140" width="28" height="48" rx="3"/>
  <rect class="kb" x="88" y="116" width="28" height="72" rx="3"/>
  <rect class="kb" x="138" y="128" width="28" height="60" rx="3"/>
  <rect class="kb" x="188" y="88" width="28" height="100" rx="3"/>
  <polyline class="k" points="52,120 102,96 152,104 202,58"/>
  <circle class="gb" cx="202" cy="58" r="8"/>"""),
    # 盾牌 + 对勾：质量安全的通用语义
    "shield": (200, 230, """
  <path class="k kf" d="M100 8 L190 48 V116 Q190 190 100 224 Q10 190 10 116 V48 Z"/>
  <path class="kd" d="M100 34 L166 64 V116 Q166 172 100 198 Q34 172 34 116 V64 Z"/>
  <path class="g" d="M60 112 L90 148 L146 78"/>"""),
    # 警示三角：召回/抽检不合格
    "warn": (200, 200, """
  <path class="k kf" d="M100 12 L192 182 H8 Z"/>
  <line class="k" x1="100" y1="70" x2="100" y2="132"/>
  <circle class="gb" cx="100" cy="158" r="7"/>"""),
    # 文件 + 印章：政策法规
    "doc": (190, 250, """
  <path class="k kf" d="M24 8 H132 L166 44 V242 H24 Z"/>
  <path class="ks" d="M132 8 V44 H166"/>
  <line class="ks" x1="48" y1="96" x2="142" y2="96"/>
  <line class="ks" x1="48" y1="126" x2="130" y2="126"/>
  <line class="ks" x1="48" y1="156" x2="142" y2="156"/>
  <circle class="g" cx="126" cy="198" r="26"/>
  <rect class="g" x="114" y="190" width="24" height="16" rx="3"/>"""),
    # 地球 + 经纬网：出海、跨境、海外
    "globe": (230, 230, """
  <circle class="k kf" cx="115" cy="115" r="110"/>
  <ellipse class="ks" cx="115" cy="115" rx="110" ry="42"/>
  <ellipse class="ks" cx="115" cy="115" rx="46" ry="110"/>
  <line class="kd" x1="5" y1="115" x2="225" y2="115"/>"""),
    # 集装箱货轮：船体 + 两层箱 + 驾驶舱 + 烟囱
    "ship": (300, 200, """
  <path class="k kf" d="M18 148 H282 L248 192 H52 Z"/>
  <rect class="k" x="192" y="86" width="44" height="62"/>
  <rect class="k" x="244" y="52" width="24" height="96"/>
  <line class="ks" x1="244" y1="70" x2="268" y2="70"/>
  <rect class="k" x="40" y="110" width="44" height="38" rx="3"/>
  <rect class="k" x="92" y="110" width="44" height="38" rx="3"/>
  <rect class="k" x="144" y="110" width="44" height="38" rx="3"/>
  <rect class="k" x="66" y="70" width="44" height="38" rx="3"/>
  <rect class="k" x="118" y="70" width="44" height="38" rx="3"/>
  <line class="ks" x1="8" y1="206" x2="292" y2="206"/>"""),
    # 厂房：锯齿屋顶 + 烟囱 + 卷帘门 + 两扇窗
    "factory": (270, 200, """
  <rect class="k kf" x="24" y="86" width="232" height="110"/>
  <polyline class="k" points="24,86 64,48 104,86 144,48 184,86 224,48 264,86"/>
  <rect class="k" x="238" y="16" width="22" height="72"/>
  <rect class="k" x="120" y="136" width="56" height="60"/>
  <rect class="ks" x="48" y="112" width="34" height="26" rx="2"/>
  <rect class="ks" x="196" y="112" width="34" height="26" rx="2"/>"""),
    # 购物车：车斗 + 把手 + 两轮
    "cart": (220, 180, """
  <path class="k kf" d="M34 26 L64 116 H182 L202 26 Z"/>
  <line class="k" x1="34" y1="26" x2="16" y2="6"/>
  <circle class="k" cx="76" cy="146" r="16"/>
  <circle class="k" cx="166" cy="146" r="16"/>
  <line class="ks" x1="52" y1="56" x2="188" y2="56"/>
  <line class="ks" x1="58" y1="86" x2="184" y2="86"/>"""),
    # 星标：新品、发布、签约
    "star": (180, 180, """
  <path class="k kf" d="M90 10 L110 67.5 L170.8 68.7 L122.3 105.5 L139.9 163.8 L90 129
    L40.1 163.8 L57.7 105.5 L9.2 68.7 L70 67.5 Z"/>"""),
    # 放大镜 + 检测条文：抽检、检出、处罚
    "scan": (200, 200, """
  <circle class="k kf" cx="86" cy="86" r="62"/>
  <line class="k" x1="131" y1="131" x2="178" y2="178"/>
  <line class="ks" x1="54" y1="68" x2="118" y2="68"/>
  <line class="ks" x1="54" y1="90" x2="104" y2="90"/>
  <line class="ks" x1="54" y1="112" x2="114" y2="112"/>"""),
    # 并购/融资：两个主体交叠，交叠处一支双向箭头，表示股权往来
    "deal": (230, 200, """
  <circle class="k kf" cx="78" cy="100" r="68"/>
  <circle class="k" cx="152" cy="100" r="68"/>
  <path class="g" d="M96 100 H134"/>
  <path class="g" d="M96 100 L108 90 M96 100 L108 110"/>
  <path class="g" d="M134 100 L122 90 M134 100 L122 110"/>"""),
}


# ---------------------------------------------------------------- 关键词规则
# 顺序即优先级：召回类新闻即使同时提到"增长"，也应该先画盾牌而不是折线图

TOPIC_RULES: list[tuple[str, list[str]]] = [
    # 质量问题拆成三档：出事的、被查的、合格的，画出来的东西不一样
    ("warn", ["召回", "下架", "销毁", "致敏", "致病", "污染", "投毒", "造假", "停售",
              "致死", "感染", "事故事件", "舆情", "质疑"]),
    ("scan", ["抽检", "检出", "超标", "不合格", "通报", "处罚", "违规", "菌落", "风险",
              "投诉", "约谈", "立案", "罚款", "整改"]),
    ("shield", ["合规", "认证", "达标", "通过", "安全", "质量", "检验", "注册证", "溯源"]),
    ("doc", ["政策", "法规", "标准", "国标", "征求意见", "备案", "注册", "许可", "监管",
             "规定", "条例", "办法", "通知", "海关", "关税", "立法", "指南",
             "实施细则", "补贴"]),
    ("chart", ["增长", "下滑", "上涨", "下跌", "营收", "利润", "销售额", "市场份额", "价格",
               "涨价", "降价", "成本", "数据", "财报", "业绩", "同比", "环比", "指数",
               "规模", "销量", "渗透率", "增速"]),
    ("deal", ["收购", "并购", "融资", "上市", "IPO", "投资", "入股", "估值", "募资",
              "交割", "股权", "合并", "重组"]),
    ("globe", ["海外", "全球", "国际", "出口", "进口", "跨境", "欧盟", "美国", "新西兰",
               "澳洲", "日本", "东南亚", "中东", "境外", "出海", "外资"]),
    ("ship", ["物流", "供应链", "原料", "奶源", "牧场", "运输", "仓储", "库存", "港口",
              "集装箱", "货运", "冷链", "断供", "采购"]),
    ("factory", ["工厂", "产能", "投产", "扩建", "生产线", "基地", "车间", "新建", "竣工",
                 "智能制造", "车间"]),
    ("cart", ["电商", "直播", "零售", "渠道", "商超", "门店", "双11", "黑五", "促销",
              "销售", "消费者", "订单", "终端", "铺货"]),
    ("star", ["新品", "上市", "发布", "推出", "首发", "签约", "合作", "战略", "升级",
              "创新", "获奖", "联名", "代言"]),
]

# 次元素：这条新闻涉及什么品类
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("can", ["奶粉", "配方粉", "婴配", "羊奶", "特配", "配方", "乳粉"]),
    ("carton", ["液态奶", "纯牛奶", "鲜奶", "巴氏", "白奶", "灭菌乳", "常温奶"]),
    ("cup", ["酸奶", "发酵乳", "乳酸菌", "饮品", "乳饮料"]),
    ("wedge", ["奶酪", "芝士", "黄油", "奶油", "稀奶油", "乳脂"]),
    ("bottle", ["母婴", "婴儿", "宝宝", "辅食", "孕产", "儿童"]),
    ("capsule", ["营养", "益生菌", "保健", "维生素", "功能", "特医"]),
]

# 没有命中任何主题词时，按分类兜底
FALLBACK_TOPIC = {
    "品牌动态": "star",
    "市场数据": "chart",
    "质量安全": "shield",
    "供应链": "ship",
    "跨境电商": "globe",
    "政策法规": "doc",
    "海外动态": "globe",
    "行业动态": "factory",
}

# 每个分类先看自己的偏好图元。不这么做的话，"供应链"里带个"成本"就被折线图抢走，
# 一条讲奶源断供的新闻最后画成一张财报。
CAT_PREF = {
    "品牌动态": ["star", "deal", "chart"],
    "市场数据": ["chart", "deal"],
    "质量安全": ["warn", "scan", "shield"],
    "供应链": ["ship", "factory", "chart"],
    "跨境电商": ["globe", "cart", "chart"],
    "政策法规": ["doc", "scan"],
    "海外动态": ["globe", "ship"],
    "行业动态": ["factory", "deal", "chart"],
}

TOPIC_DICT = {name: words for name, words in TOPIC_RULES}


def pick(rules, text: str, default: str) -> str:
    for name, words in rules:
        for w in words:
            if w in text:
                return name
    return default


def pick_topic(text: str, cat: str) -> str:
    """先按分类偏好找，再按全局优先级找，最后按分类兜底。"""
    for name in CAT_PREF.get(cat, []):
        if any(w in text for w in TOPIC_DICT.get(name, [])):
            return name
    for name, words in TOPIC_RULES:
        if any(w in text for w in words):
            return name
    return FALLBACK_TOPIC.get(cat, "factory")


def place(name: str, cx: float, cy: float, target_w: float, extra: str = "") -> str:
    """把图元按目标宽度缩放到 (cx, cy) 居中；non-scaling-stroke 保证线宽不变。"""
    w, h, body = ART[name]
    k = target_w / w
    x, y = cx - w * k / 2, cy - h * k / 2
    return (f'<g transform="translate({x:.1f},{y:.1f}) scale({k:.3f})">'
            f'{body}{extra}</g>')


def background(tone: str, rnd: random.Random, variant: int) -> str:
    """纸底 + 分类色光晕 + 一层随机的淡装饰 + 一条基线，让每张图都有"版心"感。

    装饰四种：散点 / 同心弧 / 斜线 / 规则点阵。它只负责让画面不空，
    强度压在 opacity .12 以内，远看仍是一张素纸。
    """
    s = f'<rect width="{W}" height="{H}" fill="{PAPER}"/>'
    s += f'<circle cx="612" cy="300" r="232" fill="{tone}" opacity=".05"/>'

    if variant == 0:
        for _ in range(11):
            s += (f'<circle cx="{rnd.randint(56, 908)}" cy="{rnd.randint(44, 566)}" '
                  f'r="{rnd.choice([2.0, 2.5, 3.0])}" fill="{INK}" opacity=".13"/>')
    elif variant == 1:
        # 同心弧挪到左下角：右上角留给主元素呼吸，不再有任何装饰线条
        for r in (150, 196, 242):
            s += (f'<path d="M{150 - r} 520 A {r} {r} 0 0 1 150 {520 + r}" fill="none" '
                  f'stroke="{INK}" stroke-width="1.1" opacity=".12" '
                  f'vector-effect="non-scaling-stroke"/>')
    elif variant == 2:
        # 右缘一列渐短刻度线，像版心外的标尺，不压主体
        for i in range(9):
            y = 176 + i * 30
            s += (f'<line x1="868" y1="{y}" x2="{868 + 34 - i * 3}" y2="{y}" stroke="{INK}" '
                  f'stroke-width="1.1" opacity=".16" vector-effect="non-scaling-stroke"/>')
    else:
        # 点阵同样挪到左下，避开右上角
        for gx in range(5):
            for gy in range(4):
                s += (f'<circle cx="{110 + gx * 42}" cy="{368 + gy * 42}" r="2.2" '
                      f'fill="{INK}" opacity=".14"/>')

    s += (f'<line x1="110" y1="524" x2="860" y2="524" stroke="{INK_SOFT}" '
          f'stroke-width="1.1" opacity=".35"/>')
    return s


def accents(tone: str, rnd: random.Random) -> str:
    """已停用：右上角的金色虚线弧和金点在缩略图里显得杂乱，按用户要求去掉。
    保留空函数是为了不动的调用点。"""
    return ""


def build_svg(item: dict) -> str:
    text = " ".join([
        str(item.get("title") or ""),
        str(item.get("summary") or ""),
        str(item.get("body") or "")[:600],
    ])
    tags = item.get("tags") or []
    cat = tags[0] if tags else "行业动态"
    tone = TONE.get(cat, TONE["行业动态"])

    seed = int(hashlib.md5(str(item.get("id") or text).encode("utf-8")).hexdigest()[:8], 16)
    rnd = random.Random(seed)

    topic = pick_topic(text, cat)
    # 次元素：能认出品类的就画对应包装；认不出的按 id 散开，避免满屏全是同一个罐子
    goods = pick(CATEGORY_RULES, text, "")
    if not goods:
        goods = ["can", "carton", "cup", "wedge", "capsule"][seed % 5]

    # 主元素绝不能和次元素是同一个图元，否则画面会重复
    if goods == topic:
        goods = "can" if topic != "can" else "carton"

    s = background(tone, rnd, seed % 4)

    # 六种版式：主体大小/位置 + 次元素落点都换，同分类的图也不会长得一样
    MAIN = [
        (592, 296, 340, 292, 430, 150),
        (612, 306, 320, 286, 194, 142),
        (570, 312, 352, 300, 436, 132),
        (600, 288, 360, 286, 438, 128),
        (560, 304, 344, 300, 182, 154),
        (620, 300, 328, 282, 302, 134),
    ]
    mx, my, mw, gx, gy, gw = MAIN[seed % len(MAIN)]
    s += place(topic, mx, my, mw)
    s += place(goods, gx, gy, gw)

    s += accents(tone, rnd)

    title = html.escape(f"主题配图：{cat}", quote=True)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'width="{W}" height="{H}" role="img"><title>{title}</title>'
            f'{STYLE}{s}</svg>\n')


# ---------------------------------------------------------------- 入口


def load_items() -> list[dict]:
    items, seen = [], set()
    for path in sorted(glob.glob(str(ROOT / "data" / "20*.jsonl"))):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                iid = obj.get("id")
                if not iid or iid in seen:
                    continue
                seen.add(iid)
                items.append(obj)
    return items


def generate_all(out_dir: pathlib.Path) -> tuple[int, int]:
    """返回 (成功数, 失败数)。单条失败只打印是哪条，不影响其余。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = bad = 0
    for item in load_items():
        iid = item["id"]
        try:
            svg = build_svg(item)
            path = out_dir / f"{iid}.svg"
            path.write_text(svg, encoding="utf-8")
            if path.stat().st_size > 12 * 1024:
                print(f"! {iid} 超过 12KB（{path.stat().st_size}B）", file=sys.stderr)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不该中断整批
            bad += 1
            print(f"! {iid} 生成失败：{exc}", file=sys.stderr)
    return ok, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "assets" / "items"))
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    ok, bad = generate_all(out)
    print(f"生成 {ok} 张，失败 {bad} 张 → {out}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

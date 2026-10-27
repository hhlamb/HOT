/* ============================================================
   首页：左侧栏 + 五个视图（精选 / 全部 / 热点榜 / 日报 / 主题）
   无框架、无构建。取数在 feed-data.js，详情页共用同一份。
   ============================================================ */

const FD = window.FeedData;
const PAGE_SIZE = 30;
/* 热点榜只在精不在多：20 条以外的事，读者从"全部动态"里翻更合适。
   榜单拉得太长会让第 40 名之后的内容显得也"很热"，反而模糊了重点。 */
const HOT_MAX = 20;

const VIEWS = {
  pick: { title: "精选" },
  all: { title: "全部动态" },
  hot: { title: "热点榜" },
  daily: { title: "日报" },
  topics: { title: "主题" },
};

/* 精选按"事件"读，不按"稿件"读。
   同一件事常有两三家媒体各写一遍：今天恒天然能源中断被写成 3 篇、蒙牛终止
   姆巴佩合作 2 篇、雀巢 Q3 财报 2 篇。它们排在时间流里就是三条独立新闻，
   读者会以为发生了三件事。精选先把这些归组，一个事件只占一格。
   全部动态仍然一篇不合并——那里要的是完整流水。 */
/* 精选不再按栏目分块。
   栏目（政策法规、品牌动态……）是抓取侧给稿件打的标签，粗且会漂：
   "蒙牛终止与姆巴佩合作"常被标成政策法规，"荷兰奶牛场用科技提产"会落进
   品牌动态。按标签摆，摆错的位置比漏掉的内容更伤——读者会直接不信任这一页。
   所以精选改成一个分数，只认数据里查得到的信号，新源、新标签接进来照样能算：
     ① 事件分 g.score = 热度 + "几家媒体写了同一件事"
     ② 硬新闻（监管、质量）+26：跟读者的钱和风险直接相关
     ③ 时效 +16 / +8 / -14：24 小时内、72 小时内、一周以上
     ④ 摘要够长 +8、有要点 +6：撑不起推荐位的稿子不加分
     ⑤ 泛稿 -10：标题里说不出"谁"和"多少"，也不是硬新闻
   取分数最高的一批，低于榜首 45% 的不凑数。精选宁可少几条。 */
const HARD_TAGS = ["质量安全", "政策法规"];
const PICK_MAX = 8; // 含头条
const PICK_PASS = 0.45; // 清单里最低要够榜首分数的几成

/* 标题里能识别出品方/机构名的词。用于判断"两条标题像不像同一件事"——
   纯字符相似度抓不住"恒天然"与"Fonterra"这类写法差异，得靠实体词兜一层。 */
const EVENT_ENTITIES = [
  "蒙牛", "伊利", "飞鹤", "君乐宝", "雀巢", "达能", "恒天然", "fonterra",
  "a2", "雅培", "美赞臣", "圣元", "贝因美", "光明", "新希望", "澳优", "健合",
  "菲仕兰", "雅士利", "完达山", "妙可蓝多", "山姆", "盒马", "拼多多", "京东",
  "淘宝", "天猫", "抖音", "快手", "认养一头牛", "westland", "威士兰", "萨普托",
  "兰特黎斯", "菲士兰", "明治", "森永", "卡夫", "星巴克", "三只松鼠",
];

const state = {
  items: [],
  meta: {},
  view: "pick",
  tag: "all",
  query: "",
  limit: PAGE_SIZE,
  tagCounts: {},
};

const el = {
  view: document.getElementById("view"),
  status: document.getElementById("status"),
  more: document.getElementById("more-btn"),
  cats: document.getElementById("cats"),
  catRail: document.getElementById("rail-cats"),
  search: document.getElementById("search"),
  title: document.getElementById("page-title"),
  live: document.getElementById("live"),
  board: document.getElementById("board"),
  railStat: document.getElementById("rail-stat"),
  theme: document.getElementById("theme-btn"),
  navItems: [...document.querySelectorAll(".nav-item")],
};

/* ------------------------------------------------------------ 载入 */

async function load() {
  el.view.innerHTML = "";
  for (let i = 0; i < 3; i++) {
    const sk = document.createElement("div");
    sk.className = "skeleton";
    el.view.appendChild(sk);
  }

  let data;
  try {
    data = await FD.loadAll();
  } catch (err) {
    /* 页面只说读者能处理的话，具体原因留控制台 */
    console.error("[数据加载失败]", err);
    el.view.innerHTML = "";
    el.status.textContent = "内容没能加载出来，刷新一下试试。";
    return;
  }

  state.items = data.items;
  /* 换了数据，噪声字组表就得重算，否则聚类还按上一批稿子的词频走 */
  _stopGrams = null;
  _eventCache = null;
  state.meta = data.meta;

  /* 站点通用图统计：同一张图被 3 条以上复用，基本可以断定不是文章配图
     （栏目封面、分享卡片、站标）。后端的过滤只对新入库的条目生效，
     老数据得在这里兜一层，否则大图区永远是那张蓝色横幅。 */
  for (const it of state.items) {
    if (!FD.hasOwnImage(it)) continue;
    imageFreq.set(it.image, (imageFreq.get(it.image) || 0) + 1);
  }

  if (!state.items.length) {
    el.view.innerHTML = "";
    el.status.textContent = "暂时还没有内容，稍后再来看看。";
    return;
  }

  for (const it of state.items) {
    // 只统计主分类：侧栏数字相加必须等于总条数，否则读者会怀疑数据错了
    const t = FD.primaryTag(it);
    state.tagCounts[t] = (state.tagCounts[t] || 0) + 1;
  }
  // 预先把热度算好，排序、榜单、日报都复用，避免每处各算一遍
  FD.rankByHeat(state.items);

  renderRail();
  renderFoot();
  renderLive();
  applyRoute(true);
}

/* ------------------------------------------------------------ 状态层 */

/* 统计口径用滚动 24 小时，不用自然日。
   自然日在凌晨和清早一定是 0 条（数据还没抓到今天的），
   四个数字全挂 0 会让读者以为站点停了。24 小时窗口任何时候都有意义。 */
function recentItems(hours = 24) {
  const now = Date.now();
  return state.items.filter((i) => {
    const t = Date.parse(i.published_at || i.fetched_at || "");
    return !Number.isNaN(t) && now - t <= hours * 36e5;
  });
}

/* 页头下面那行小字：这一屏是什么时候的。
   口号（"每一次变动都在这里"）在这里没有信息量，换成三个事实。 */
function renderLive() {
  const recent = recentItems();
  const sources = new Set(state.items.map((i) => i.source_id)).size;
  const stamp = state.meta.last_updated || (state.items[0] && state.items[0].fetched_at) || "";

  el.live.innerHTML = "";
  const dot = document.createElement("i");
  dot.className = "live";
  dot.setAttribute("aria-hidden", "true");
  el.live.appendChild(dot);

  const facts = [];
  if (stamp) facts.push(`最近更新 ${FD.fmtStamp(stamp)}`);
  facts.push(`近 24 小时 ${recent.length} 条`);
  facts.push(`${sources} 个信源`);
  facts.forEach((text, i) => {
    if (i) el.live.appendChild(dotSpan());
    el.live.appendChild(document.createTextNode(text));
  });

  /* 源健康度只留给排障：读者看到"5 个源抓取失败"不会知道该怎么办，
     只会怀疑站点是不是缺数据。运维口径走控制台，页面上一个数字都不出现。 */
  const total = Number(state.meta.sources_total || 0);
  const ok = Number(state.meta.sources_ok || 0);
  if (total && ok && total > ok) {
    console.info(`[源健康度] 本轮 ${ok}/${total} 个源抓取成功，失败 ${total - ok} 个`);
  }
}

function dotSpan() {
  const s = document.createElement("span");
  s.className = "d";
  s.setAttribute("aria-hidden", "true");
  s.textContent = "·";
  return s;
}

/* ------------------------------------------------------------ 侧栏 */

function renderRail() {
  /* 精选不给数字：它是"挑过的十几条"，写个"21"会让人以为点进去有 21 条。
     全部动态和日报的数是真能点开的量，留着。 */
  document.getElementById("n-pick").textContent = "";
  document.getElementById("n-all").textContent = state.items.length;
  document.getElementById("n-hot").textContent = "";
  const days = new Set(state.items.map((i) => FD.dayKey(i.published_at || i.fetched_at))).size;
  document.getElementById("n-daily").textContent = days;

  /* 分类：名字 + 计数 + 一条按占比伸缩的细条。
     原来是"色点 + 数字"，八个数字没有共同的量纲，读完不知道今天哪类变多。
     细条把同一个总数画了八次，扫一眼就能看出重心在哪。 */
  const sorted = Object.entries(state.tagCounts).sort((a, b) => b[1] - a[1]);
  const maxN = sorted.length ? sorted[0][1] : 1;
  el.cats.innerHTML = "";
  for (const [tag, n] of sorted) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cat-item";
    btn.dataset.tag = tag;
    btn.setAttribute("aria-current", String(state.tag === tag));

    const row = document.createElement("span");
    row.className = "cat-row";
    const dot = document.createElement("i");
    dot.className = "cat-dot";
    dot.setAttribute("aria-hidden", "true");
    row.appendChild(dot);
    row.appendChild(document.createTextNode(tag));
    const num = document.createElement("span");
    num.className = "n";
    num.textContent = n;
    row.appendChild(num);
    btn.appendChild(row);

    const bar = document.createElement("span");
    bar.className = "cat-bar";
    const fill = document.createElement("i");
    // 最小 6%：只有一条的分类也留一道可见的短线，否则像没渲染
    fill.style.width = Math.max(6, Math.round((n / maxN) * 100)) + "%";
    bar.appendChild(fill);
    btn.appendChild(bar);

    btn.addEventListener("click", () => {
      state.tag = state.tag === tag ? "all" : tag;
      state.limit = PAGE_SIZE;
      go(state.view === "daily" || state.view === "topics" ? "all" : state.view);
    });
    el.cats.appendChild(btn);
  }
}

function renderFoot() {
  const sources = new Set(state.items.map((i) => i.source_id)).size;
  const stamp = state.meta.last_updated || (state.items[0] && state.items[0].fetched_at) || "";
  el.railStat.innerHTML =
    `已收录 <b>${state.items.length}</b> 条 · <b>${sources}</b> 个信源<br>` +
    (stamp ? `更新于 ${FD.fmtStamp(stamp)}` : "");

  /* 页脚不再出现"源健康度 x/y"、保留天数、数据源个数：
     这些是站长的运维口径，读者只需要知道今天有几条可看。
     收录量与信源数在状态行和侧栏已经说过一次，不必再说第二遍。 */
}

/* ------------------------------------------------------------ 路由 */

function readRoute() {
  const q = new URLSearchParams(location.search);
  const v = q.get("view");
  if (v && VIEWS[v]) state.view = v;
  const t = q.get("tag");
  if (t) state.tag = t;
}

function writeRoute() {
  const q = new URLSearchParams();
  if (state.view !== "pick") q.set("view", state.view);
  if (state.tag !== "all") q.set("tag", state.tag);
  const s = q.toString();
  const url = location.pathname + (s ? "?" + s : "");
  try {
    history.replaceState(null, "", url);
  } catch (e) {
    /* file:// 下可能被拒，忽略即可 */
  }
}

function go(view, keepScroll) {
  state.view = view;
  state.limit = PAGE_SIZE;
  writeRoute();
  applyRoute(false, keepScroll);
}

function applyRoute(firstLoad, keepScroll) {
  const meta = VIEWS[state.view];
  el.title.textContent = state.tag !== "all" ? `${meta.title} · ${state.tag}` : meta.title;

  /* 精选是按分数挑出来的几件事，不按栏目分，所以侧栏的分类整块收起来——
     摆在那却点不动最糟。全部动态、热点榜、日报这些要筛的时候再出现。
     已经选了分类（精选退成时间流）时也留着，否则没法切回去。 */
  el.catRail.hidden = state.view === "pick" && state.tag === "all";

  for (const btn of el.navItems) {
    btn.setAttribute("aria-current", String(btn.dataset.view === state.view));
  }
  for (const btn of el.cats.querySelectorAll(".cat-item")) {
    btn.setAttribute("aria-current", String(btn.dataset.tag === state.tag));
  }

  el.view.innerHTML = "";
  el.status.textContent = "";

  const rows = filtered();

  if (state.view === "pick") {
    /* 筛了分类或搜了关键词，精选就退回时间流：这时候读者是在找东西，
       推荐位只会挡住结果。 */
    if (state.tag !== "all" || state.query.trim()) {
      el.board.hidden = true;
      el.board.innerHTML = "";
      renderFeed(rows);
    } else {
      renderPick(rows);
    }
  } else if (state.view === "all") {
    el.board.hidden = true;
    el.board.innerHTML = "";
    renderFeed(rows);
  } else if (state.view === "hot") {
    el.board.hidden = true;
    el.board.innerHTML = "";
    renderHot(rows);
  } else if (state.view === "daily") {
    el.board.hidden = true;
    el.board.innerHTML = "";
    renderDaily(rows);
  } else if (state.view === "topics") {
    el.board.hidden = true;
    el.board.innerHTML = "";
    renderTopics(rows);
  }

  if (!keepScroll) window.scrollTo({ top: 0, behavior: firstLoad ? "auto" : "smooth" });
}

/* ------------------------------------------------------------ 过滤 */

/* 时间倒序比较。不能比字符串：ISO 串带时区偏移，`…T21:30+08:00` 字典序
   排在 `…T14:00+00:00` 之后，可它其实更早（13:30Z）。源里 +08:00 / Z / 空
   混着来，只能解析成毫秒数比。解析失败的一律沉底，不让坏数据顶到第一条。 */
function pubTs(item) {
  const t = Date.parse(item.published_at || item.fetched_at || "");
  return Number.isNaN(t) ? 0 : t;
}

function newestFirst(a, b) {
  return pubTs(b) - pubTs(a);
}

function filtered() {
  const q = state.query.trim().toLowerCase();
  let rows = state.items;
  if (state.tag !== "all") rows = rows.filter((i) => FD.primaryTag(i) === state.tag);
  if (q) {
    rows = rows.filter((i) => {
      const hay = `${i.title_zh || ""} ${i.title || ""} ${i.summary || ""} ${i.source_name || ""}`.toLowerCase();
      return hay.includes(q);
    });
  }
  /* 这里只做筛选与排序，不在过滤层去重。
     原因：主题页的"每类多少条"、侧栏分类计数、全部动态的总数，三处必须
     用同一个分母，否则读者一加就发现少了二三十条。转载稿的标题去重
     交给各个视图自己做（时间轴、榜单、日报列表、主题预览各去一次）。 */
  return rows.slice().sort(newestFirst);
}

/* ------------------------------------------------------------ 精选：判断层 + 记录层 */

/* 判断层：一个头条 + 四条要闻。
   原来这里是"当前热点 Top5"的带框列表——只说了什么热，没说他为什么值得看。
   现在头条给出摘要与要点，剩下四条压成一行一条，三秒能扫完。 */
/* 只给日期、没给时刻的稿子（管线会把它规整成当天 00:00）。
   这些短行里宁可整段省掉，也别印一个看起来像真时间的 00:00——
   头条摘要下面那一行本来就很挤，多一个日期反而更堵。 */
function stampShort(iso) {
  if (!iso || !FD.hasTime(iso)) return "";
  return FD.hm(iso);
}

/* 头条要的是"一件事"，不是"一份目录"。
   "今日导读 / 早报 / 一周要闻"这类稿子标题里就写着自己是汇总，
   放进头条位等于用最贵的位置放一个目录页。精选页各处都用这条判断。 */
const DIGEST_RE = /今日导读|早报|晚报|周报|日报|汇总|盘点|速览|一周要闻|要闻回顾/;
const isDigestTitle = (i) => DIGEST_RE.test(i.title_zh || i.title || "");

/* ------------------------------------------------------------ 事件归组 */

/* 标题切成二元字组算相似度。中文短标题用整词表切不准，两字一组足够
   分辨"同一件事换个说法"和"同一家公司两件事"。 */
function eventGrams(text) {
  /* 注意不能写 \W：JS 的 \W 在没有 u 标志时等价于 [^A-Za-z0-9_]，
     中文会被整段剥掉，集合永远是空的（这个坑踩过一次）。
     这里显式列出"CJK + 拉丁字母 + 数字"，其余当分隔符。 */
  const t = String(text || "").toLowerCase().replace(/[^0-9a-z\u4e00-\u9fff]+/g, "");
  const set = new Set();
  for (let i = 0; i < t.length - 1; i++) set.add(t.slice(i, i + 2));
  return set;
}

function eventTitle(item) {
  return item.title_zh || item.title || "";
}

function eventEntities(text) {
  const t = String(text || "").toLowerCase();
  return EVENT_ENTITIES.filter((e) => t.includes(e));
}

function eventStamp(item) {
  const t = Date.parse(item.published_at || item.fetched_at || "");
  return Number.isNaN(t) ? 0 : t;
}

/* 领域内的高频字组（"婴儿配方奶粉""召回""支付"）几乎每条标题都有，
   拿它们算相似度，任意两篇奶粉新闻都能凑到 0.4。
   先统计一遍文档频率，出现超过一成半的字组直接当噪声剔掉，
   剩下的才是"雅培""3.85 亿""飞鹤""印度"这类有分辨力的词。 */
let _stopGrams = null;

function stopGrams(rows) {
  if (_stopGrams) return _stopGrams;
  const df = new Map();
  for (const item of rows) {
    for (const g of eventGrams(eventTitle(item))) df.set(g, (df.get(g) || 0) + 1);
  }
  const limit = Math.max(3, rows.length * 0.15);
  const stop = new Set();
  for (const [g, n] of df) if (n > limit) stop.add(g);
  _stopGrams = stop;
  return stop;
}

/* 剔掉噪声字组后的标题指纹。剔完剩不到三个字组就退回完整集合——
   太短的标题（"FDA 将增加检测"）素得太狠反而认不出自己。 */
function eventPrint(item, stop) {
  const all = item._gram || eventGrams(eventTitle(item));
  if (!stop) return all;
  const keep = new Set();
  for (const g of all) if (!stop.has(g)) keep.add(g);
  return keep.size >= 3 ? keep : all;
}

/* 纯拉丁字母标题（英文稿）。用来分辨"同一篇稿的中英文两版"。 */
function isLatinTitle(text) {
  const t = String(text || "");
  return !/[\u4e00-\u9fff]/.test(t) && /[a-z]/i.test(t);
}

/* 标题里的金额和数量。同事件的两篇稿几乎一定会写同一个数字——
   雅培那件"3.85 亿美元和解"被 15 家媒体写过，标题措辞各异，
   但"3.85 亿"这个词没变过。数字比字组稳，比实体具体。 */
function eventNumbers(item) {
  const t = String(eventTitle(item) || "").replace(/,/g, "");
  const out = new Set();
  const re = /(\d+(?:\.\d+)?)\s*(亿|万|%|美元|元|单|吨|家)/g;
  let m;
  while ((m = re.exec(t))) {
    // 2022、2034 这类年份不是数量
    if (/^(19|20)\d{2}$/.test(m[1])) continue;
    out.add(m[1] + m[2]);
  }
  return out;
}

/* 两条稿子是不是同一件事。三个条件都不苛刻，但要同时看：
   ① 时间在三天内——半年前那份财报和今天的财报不是一回事；
   ② 标题字面重合 ≥0.45——转载、加副标题、改动词都落在这里；
   ③ 或者共享同一个机构名，且字面重合 ≥0.22。
      只共享机构名远远不够："伊利收购 Westland"和"伊利与奇安信合作"
      都提到伊利，说的却是两件事，所以字面还得兜住。 */
function sameEvent(a, b, stop) {
  const ta = eventStamp(a);
  const tb = eventStamp(b);
  if (ta && tb && Math.abs(ta - tb) > 3 * 864e5) return false;

  /* 同一篇稿的中英文两版：同源、同一分钟、一条纯拉丁一条中文。
     "Technology drives Dutch dairy farm productivity" 与
     "荷兰奶牛场通过科技提升生产力" 字面重合为零、实体也对不上，
     只有这个组合能认出来。
     注意不能只判"同源同时刻"——Google News 这种聚合源同一分钟会吐多条
     互不相干的新闻，那会把整批稿子粘成一坨（踩过）。
     也必须两边都有真实时刻：只给日期的源统一落在 00:00。 */
  if (
    a.source_id &&
    a.source_id === b.source_id &&
    FD.hasTime(a.published_at) &&
    FD.hasTime(b.published_at) &&
    a.published_at === b.published_at &&
    !/google news/i.test(a.source_name || "") &&
    isLatinTitle(eventTitle(a)) !== isLatinTitle(eventTitle(b)) &&
    isLatinTitle(eventTitle(a)) + isLatinTitle(eventTitle(b)) === 1
  ) {
    return true;
  }

  if (!a._gram) a._gram = eventGrams(eventTitle(a));
  if (!b._gram) b._gram = eventGrams(eventTitle(b));
  const ga = eventPrint(a, stop);
  const gb = eventPrint(b, stop);
  if (!ga.size || !gb.size) return false;

  let inter = 0;
  for (const g of ga) if (gb.has(g)) inter++;
  const jac = inter / (ga.size + gb.size - inter);
  /* 字面重合够高就直接算同一件事。阈值偏严是有意的：合并错了的代价是
     "读者根本没看到那条新闻"，比多出一条重复严重得多——
     "进口海胆镉超标"和"进口红皮刀汞超标"字面 0.39，绝不能并成一条。 */
  if (jac >= 0.45) return true;

  /* 其余情形要求共享机构名，再看两层：
     ① 字面还有 0.25 以上重合——只是换个动词、加了副标题；
     ② 或者写着同一个金额数量——"雅培 3.85 亿美元和解"被 15 家媒体写过，
        措辞完全不同，但那个数字没变。 */
  const ea = eventEntities(eventTitle(a));
  const eb = eventEntities(eventTitle(b));
  const shared = ea.filter((e) => eb.includes(e));
  if (!shared.length) return false;
  if (jac >= 0.25) return true;

  const na = eventNumbers(a);
  if (!na.size) return false;
  for (const n of eventNumbers(b)) if (na.has(n)) return true;
  return false;
}

/* 稿件归组成事件，返回 [{ items, main, heat, tag }]，按热度降序。
   主稿取事件内热度最高的一篇；热度相同取摘要更长的那篇，
   头条位和列表都要有摘要才立得住。 */
let _eventCache = null;

function clusterEvents(rows) {
  /* 切视图会重新渲染，每次都做一遍两两比较太浪费——全量那条结果缓存住。
     数据换了 loadAll 会清空。 */
  if (rows === state.items && _eventCache) return _eventCache;

  const stop = stopGrams(state.items); // 用全量语料算噪声词，口径不受当前筛选影响
  const groups = [];
  for (const item of rows) {
    /* 导读、早报、一周回顾这类稿子自己就是目录，不参与归组——
       它们会把好几天的事装进一条，跟谁合并都是错的 */
    if (isDigestTitle(item)) continue;
    if (!eventTitle(item)) continue;

    let hit = null;
    for (const g of groups) {
      if (sameEvent(g.main, item, stop)) {
        hit = g;
        break;
      }
    }
    if (hit) {
      hit.items.push(item);
      hit.heat = Math.max(hit.heat, item._heat || 0);
    } else {
      groups.push({ items: [item], main: item, heat: item._heat || 0 });
    }
  }

  for (const g of groups) {
    g.items.sort(
      (x, y) =>
        (y._heat || 0) - (x._heat || 0) ||
        (y.summary || "").length - (x.summary || "").length,
    );
    g.main = g.items[0];
    g.tag = FD.primaryTag(g.main);
    /* 一家媒体写是新闻，十五家媒体同时写就是大事。归组之后这个信号才看得见，
       所以按篇数给一点加权——上限 +24，免得三天前那组 15 篇的转载
       压过两小时前的独家。 */
    g.score = (g.heat || 0) + Math.min(24, 6 * (g.items.length - 1));
  }
  groups.sort((a, b) => b.score - a.score);
  if (rows === state.items) _eventCache = groups;
  return groups;
}

/* 同一件事被几家媒体写过，就在 meta 里标一句"3 篇报道"。
   不把另外几篇的标题也铺出来——那是全部动态该干的事。 */
function eventNote(group) {
  return group.items.length > 1 ? `${group.items.length} 篇报道` : "";
}

/* 标题里有没有"文件味"。只用来判断一条稿子是不是泛泛的通稿，
   不再用它给稿件分栏目。 */
const POLICY_HINT = /新规|标准|监管|办法|通知|征求意见|条例|公告|法规|政策|备案|注册|许可|抽检|通报|执法/;

/* 事件分。算一次存住，排序时会被反复问。 */
function pickScore(group) {
  if (group._pick != null) return group._pick;

  const item = group.main;
  const title = eventTitle(item);
  let s = group.score || 0;

  if (HARD_TAGS.includes(group.tag)) s += 26;

  const stamp = eventStamp(item);
  const hrs = stamp ? (Date.now() - stamp) / 36e5 : Infinity;
  if (hrs <= 24) s += 16;
  else if (hrs <= 72) s += 8;
  else if (hrs > 168) s -= 14;

  const len = (item.summary || "").length;
  if (len >= 60) s += 8;
  else if (len >= 30) s += 4;
  if ((item.points || []).length >= 2) s += 6;

  /* 泛稿：说不出"谁"、说不出"多少"，也不是监管或质量类。
     多是"某机构发布一份报告""行业进入新阶段"这种没有落点的稿子。 */
  if (
    !HARD_TAGS.includes(group.tag) &&
    !eventEntities(title).length &&
    !eventNumbers(title).size &&
    !POLICY_HINT.test(title)
  ) {
    s -= 10;
  }

  group._pick = s;
  return s;
}

const byPick = (a, b) => pickScore(b) - pickScore(a);

/* 清单里那句话截到一句，不做全文搬运。 */
function pickBrief(item, max) {
  const s = String(item.summary || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  const stop = Math.max(cut.lastIndexOf("。"), cut.lastIndexOf("；"), cut.lastIndexOf("，"));
  return (stop > max * 0.55 ? cut.slice(0, stop) : cut) + "…";
}

/* 头条：一件事的摘要加要点。
   不放配图——抓来的 image 里活动海报、栏目封面、分享卡片占比很高，
   挑错一次最显眼的位置就成了一块和全站纸色打架的彩色牌子。 */
function renderLead(group) {
  const lead = group.main;
  el.board.innerHTML = "";

  const card = document.createElement("article");
  card.className = "lead";

  const txt = document.createElement("div");
  txt.className = "lead-txt";

  const kick = document.createElement("div");
  kick.className = "kick";
  kick.textContent = "今日头条";
  txt.appendChild(kick);

  const h2 = document.createElement("h2");
  const link = document.createElement("a");
  link.href = FD.articleHref(lead.id);
  link.textContent = eventTitle(lead) || "（无标题）";
  h2.appendChild(link);
  txt.appendChild(h2);

  if (lead.summary) {
    const p = document.createElement("p");
    p.textContent = lead.summary;
    txt.appendChild(p);
  }

  if ((lead.points || []).length) {
    const ul = document.createElement("ul");
    for (const pt of lead.points.slice(0, 3)) {
      const li = document.createElement("li");
      li.textContent = pt;
      ul.appendChild(li);
    }
    txt.appendChild(ul);
  }

  const foot = document.createElement("div");
  foot.className = "lead-f";
  const leadStamp = lead.published_at || lead.fetched_at;
  [
    FD.primaryTag(lead),
    lead.source_name,
    stampShort(leadStamp),
    eventNote(group),
  ]
    .filter(Boolean)
    .forEach((text, i) => {
      if (i) foot.appendChild(dotSpan());
      foot.appendChild(document.createTextNode(text));
    });
  txt.appendChild(foot);
  card.appendChild(txt);

  el.board.appendChild(card);
  el.board.hidden = false;
}

/* 精选：今天值得知道的几件事。
   和"全部动态"的分工不是"挑几条"和"全部"——那是同一个列表切两刀。
   这里换了一层：全部动态是 300 多篇报道的标题流水，精选是先归组、
   再按 pickScore 打分留下的几件事，每条带一句摘要，能直接读。
   所以两边点开看到的东西不一样：一边是"媒体都在说什么"，
   一边是"今天最要紧的是哪几件"。 */
function renderPick(rows) {
  const events = clusterEvents(rows);
  if (!events.length) {
    el.board.hidden = true;
    return;
  }

  /* 头条要撑得住最贵的位置：有真实时刻、摘要够长、有要点。
     只满足"有摘要"还不够——"XX 局公布不合格名单"这类稿件也有摘要，
     但那是一份目录，放在头条位等于用整屏推荐一个索引页。 */
  const freshEnough = (g) => {
    const t = eventStamp(g.main);
    return t > 0 && Date.now() - t <= 72 * 36e5;
  };
  const strong = events.filter(
    (g) =>
      freshEnough(g) &&
      FD.hasTime(g.main.published_at) &&
      (g.main.summary || "").length >= 60 &&
      (g.main.points || []).length >= 2,
  );
  const withSum = events.filter((g) => (g.main.summary || "").length >= 40);
  const leadGroup = strong.slice().sort(byPick)[0] || withSum.slice().sort(byPick)[0] || events[0];

  /* 概览：一行说清"多少篇报道归成几件事"。这是全页唯一一个能把
     "重复"讲明白的地方——读者看到 21 篇变成 13 件，就知道下面是梳理过的。 */
  const recentRows = recentItems();
  const recentEvents = recentRows.length ? clusterEvents(recentRows).length : 0;

  renderLead(leadGroup);

  /* 概览要放在头条之上：先告诉读者"这些是归并过的"，再读具体那条。
     放到头条下面就成了卡片的一条注脚，读者会以为那句话只解释头条。 */
  if (recentEvents) {
    const sum = document.createElement("p");
    sum.className = "pick-sum";
    /* 不写"近 24 小时"：页头那行已经说过一次，重复一遍像两个口径在打架。
       这句话只为交代一件事——下面是一格一件事，不是一篇稿一格。 */
    sum.textContent =
      `${recentRows.length} 篇报道归成 ${recentEvents} 件事，同一件事的多家报道只占一格。`;
    el.board.insertBefore(sum, el.board.firstChild);
  }

  /* 头条之外，同一个分数继续往下取，不再分组。
     每组都要凑两条的日子，会把"某地一家乳企拿到许可"这种稿子也摆上来；
     按分数取，够格的进，不够的就空着。 */
  const rest = events.filter((g) => g !== leadGroup).sort(byPick);
  const floor = pickScore(leadGroup) * PICK_PASS;
  const list = rest.filter((g) => pickScore(g) >= floor).slice(0, PICK_MAX - 1);
  const used = new Set([leadGroup, ...list]);

  if (list.length) {
    const ul = document.createElement("ul");
    ul.className = "pick-list";
    for (const g of list) {
      const item = g.main;
      const li = document.createElement("li");
      li.className = "pk";

      const h3 = document.createElement("h3");
      const a = document.createElement("a");
      a.href = FD.articleHref(item.id);
      a.textContent = eventTitle(item) || "（无标题）";
      h3.appendChild(a);
      li.appendChild(h3);

      /* 一句摘要：精选要能直接读，全部动态那边是标题流水。
         两边点开看到的东西因此不是同一份。 */
      const brief = pickBrief(item, 76);
      if (brief) {
        const p = document.createElement("p");
        p.className = "pk-sum";
        p.textContent = brief;
        li.appendChild(p);
      }

      const meta = document.createElement("div");
      meta.className = "pm";
      [item.source_name, stampShort(item.published_at || item.fetched_at), eventNote(g)]
        .filter(Boolean)
        .forEach((text, i) => {
          if (i) meta.appendChild(dotSpan());
          meta.appendChild(document.createTextNode(text));
        });
      li.appendChild(meta);

      ul.appendChild(li);
    }

    const box = document.createElement("section");
    box.className = "picks picks--flat";
    box.appendChild(ul);
    el.view.appendChild(box);
  }

  /* 出口：精选是节选，想翻完的人必须有明确的一条路 */
  const toAll = document.createElement("button");
  toAll.type = "button";
  toAll.className = "to-all";
  toAll.textContent = `全部 ${state.items.length} 条动态 →`;
  toAll.addEventListener("click", () => go("all"));
  el.view.appendChild(toAll);

  el.more.hidden = true;
  /* 事件是组，稿件是条：这里要减掉的是"已经展示过的稿件数"，
     不是事件数，否则 8 件事可能只覆盖 11 篇，减出来就成了 316。 */
  const covered = [...used].reduce((n, g) => n + g.items.length, 0);
  el.status.textContent = `以上 ${used.size} 件事，由 ${covered} 篇报道归并；全部 ${state.items.length} 篇在「全部动态」。`;
}

function tagChip(item) {
  const name = FD.primaryTag(item);
  const span = document.createElement("span");
  span.className = "tag";
  span.dataset.tag = name;
  /* 全站只给"质量安全"留着底色。风险必须第一眼被看到，
     其余的彩色标签撤掉底色，视线才不会被平均分配。 */
  if (name === "质量安全") span.dataset.c = "warn";
  span.textContent = name;
  return span;
}

/* 缩略图：只放原文真配图。抓来的封面九成是栏目图或分享卡片，
   拿分类插画顶上，筛到单个分类时一屏全是同一张放大镜——比没有图更糟。
   没有真图就不放图，让标题自己撑住版面（.it--txt 会把字号提一档）。 */
function thumbFor(item) {
  const realImg = usableImage(item);
  if (!realImg) return null;
  if ((usedImages.get(realImg) || 0) >= 1) return null;
  usedImages.set(realImg, (usedImages.get(realImg) || 0) + 1);

  const box = document.createElement("div");
  box.className = "thumb";
  const img = document.createElement("img");
  img.src = realImg;
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  img.alt = "";
  /* 图挂了就整块撤掉，不留一个空框 */
  img.addEventListener("error", () => box.remove());
  box.appendChild(img);
  return box;
}

/* 记录层：时间脊。
   左侧 60px 是页边注，不是"时间列"——日期数字挂在上面，时刻挂在每一行，
   轴心线只负责提示顺序。只有当同一分钟涌进多条时，脊上才点一个实心节点：
   那个点是"事件"，不是装饰。 */
function renderFeed(rows) {
  usedImages.clear();
  if (!rows.length) {
    el.status.textContent = state.query
      ? `没有匹配「${state.query}」的内容，换个关键词试试。`
      : "这个分类下暂时没有内容。";
    el.more.hidden = true;
    return;
  }

  // 转载稿标题完全相同，这一屏里只留一条（计数也用去重后的，
  // 否则"还有 N 条"会把重复稿算进去，点到底却是空的）
  const uniq = dedupeByTitle(rows);
  const shown = uniq.slice(0, state.limit);
  const stampOf = (it) => it.published_at || it.fetched_at || "";
  /* 有些源只给日期不给时刻，解析后统一落在 00:00。这类条目既不该在
     脊上印一个假的 00:00，也不该跟"真的零点那一批"并成一个节点。
     同一天里"只有日期"的条目归到同一个节点上：既不必每条重复印日期，
     也不至于把时间位空成一片。 */
  const hasTimeOf = (it) => FD.hasTime(stampOf(it));
  const minuteOf = (it) =>
    hasTimeOf(it)
      ? FD.dayKey(stampOf(it)) + "|" + FD.hm(stampOf(it))
      : "dayonly|" + FD.dayKey(stampOf(it));

  // 先数一遍每分钟几条，才知道哪些行要合并成一批
  const perMinute = new Map();
  for (const it of shown) {
    const k = minuteOf(it);
    perMinute.set(k, (perMinute.get(k) || 0) + 1);
  }

  let lastDay = null;
  let prevMinute = null;

  for (const item of shown) {
    const stamp = stampOf(item);
    const day = FD.dayKey(stamp);
    if (day !== lastDay) {
      el.view.appendChild(dayHead(stamp, rows, day));
      lastDay = day;
      prevMinute = null;
    }

    const mk = minuteOf(item);
    const same = perMinute.get(mk) || 1;
    const isIn = same > 1 && mk === prevMinute; // 同一批里的后续条目
    const isBatch = same > 1 && !isIn;          // 这一批的第一条，脊上画节点

    const row = document.createElement("article");
    row.className = "it" + (isBatch ? " it--batch" : "") + (isIn ? " it--in" : "");

    const time = document.createElement("div");
    time.className = "it-t";
    if (!isIn) {
      const known = hasTimeOf(item);
      if (known) {
        time.appendChild(document.createTextNode(FD.hm(stamp)));
        /* 时刻下面那行小字：这一批几条 > 多久以前。
           超过 8 天，timeAgo 会退回"10-19 12:05"，跟上面那个时刻重复，
           这时干脆不印——日期已在日头上写着。 */
        const rel = item.published_at ? FD.timeAgo(item.published_at) : "";
        const small = document.createElement("small");
        small.textContent = isBatch ? `${same} 条` : /\d-\d/.test(rel) ? "" : rel;
        if (small.textContent) time.appendChild(small);
      } else {
        /* 这条源只给日期、没给时刻。时间位写这一天的日期（"10-27"），
           同一天后面的同类条目共用这个节点，不再重复印。
           不写"仅日期"这种解释，也不留一格空白——日期本身就是答案。 */
        time.className = "it-t it-t--day";
        time.appendChild(document.createTextNode(FD.dayShort(stamp)));
        if (stamp) time.title = FD.mdDate(stamp);
      }
      if (known && stamp) time.title = FD.fmtFull(stamp);
    }
    row.appendChild(time);

    const body = document.createElement("div");
    body.className = "it-b";
    const head = document.createElement("div");
    head.className = "it-h";

    const main = document.createElement("div");
    main.className = "it-x";

    const meta = document.createElement("div");
    meta.className = "it-m";
    meta.appendChild(tagChip(item));
    const src = document.createElement("span");
    src.className = "src";
    src.textContent = item.source_name || "未知来源";
    meta.appendChild(src);
    main.appendChild(meta);

    const h3 = document.createElement("h3");
    const link = document.createElement("a");
    link.href = FD.articleHref(item.id);
    link.textContent = item.title_zh || item.title || "（无标题）";
    h3.appendChild(link);
    main.appendChild(h3);

    if (item.summary) {
      const p = document.createElement("p");
      p.className = "it-s";
      p.textContent = item.summary;
      main.appendChild(p);
    }
    head.appendChild(main);

    const thumb = thumbFor(item);
    if (thumb) head.appendChild(thumb);
    else row.classList.add("it--txt");

    body.appendChild(head);
    row.appendChild(body);
    el.view.appendChild(row);

    prevMinute = mk;
  }

  el.more.hidden = uniq.length <= state.limit;
  if (!el.more.hidden) el.more.textContent = `加载更多（还有 ${uniq.length - state.limit} 条）`;
}

/* 章节头：日期数字挂在左侧页边注上，右边是星期 + 条数 + 节奏。
   原来"10 月 26 日"整串在右侧、左栏空着，才出现 74px 的错位；
   现在左栏从上到下没有空白，那条 60px 就成了"悬挂缩进"，不再是对不齐。 */
function dayHead(iso, rows, key) {
  const wrap = document.createElement("div");
  wrap.className = "day";

  const inDay = rows.filter((i) => FD.dayKey(i.published_at || i.fetched_at) === key);
  const d = new Date(iso);
  const ok = !Number.isNaN(d.getTime());

  const num = document.createElement("div");
  num.className = "day-n";
  const mon = document.createElement("em");
  mon.textContent = ok ? d.getMonth() + 1 + " 月" : "日期";
  const day = document.createElement("b");
  day.textContent = ok ? d.getDate() : "?";
  num.appendChild(mon);
  num.appendChild(day);

  const title = document.createElement("div");
  title.className = "day-t";
  const week = document.createElement("b");
  week.textContent = ok ? FD.weekday(iso) : "时间未知";
  title.appendChild(week);

  /* 时间轴上最值钱的是"密度"，删掉圆点阵之后要靠这行字把它换回来：
     平均 47 分钟一条是平淡，平均 3 分钟一条说明那天出事了。 */
  const parts = [`${inDay.length} 条`];
  // 只拿"确实有时刻"的条目算节奏，否则一堆 00:00 会把平均间隔压成几分钟
  const ts = inDay
    .filter((i) => FD.hasTime(i.published_at || i.fetched_at))
    .map(pubTs)
    .filter((t) => t)
    .sort((a, b) => a - b);
  if (ts.length >= 2) {
    const gapMin = Math.round((ts[ts.length - 1] - ts[0]) / 60e3 / (ts.length - 1));
    if (gapMin >= 1) {
      parts.push(
        gapMin >= 60 ? `平均间隔 ${(gapMin / 60).toFixed(1)} 小时` : `平均间隔 ${gapMin} 分钟`
      );
    }
  }
  const detail = document.createElement("span");
  detail.textContent = parts.join(" · ");
  title.appendChild(detail);

  wrap.appendChild(num);
  wrap.appendChild(title);
  return wrap;
}

/* ------------------------------------------------------------ 渲染工具 */

/* 同一篇稿被几家媒体转载时标题一字不差（Google News 抓到的尤其常见），
   榜单里出现两次很刺眼。这里按"剥掉站点后缀 + 去标点"的标题做一次过滤。 */
function titleKeyOf(text) {
  return String(text || "")
    .replace(/\s*[-_|｜]\s*(https?:\/\/\S+|[^-_|｜]{2,12})$/, "")
    .replace(/[^0-9a-zA-Z\u4e00-\u9fff]+/g, "")
    .slice(0, 40);
}

function dedupeByTitle(rows) {
  const seen = new Set();
  return rows.filter((it) => {
    const key = titleKeyOf(it.title_zh || it.title);
    if (!key) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/* 图片复用统计：同一个 URL 出现太多次 = 站点通用图 */
const imageFreq = new Map();

/* 某些站点的 RSS 给所有稿子同一张分享图（雨果跨境那张 2021 年的蓝色横幅），
   连着几条一模一样比没有图更难看。已经用过的图不再重复显示。
   分类主题图是共用的，允许用两次，再多就整列雷同了。 */
const usedImages = new Map();

/* 图 URL 里常带上传日期（…/upload/202107/19/202107191800522486.png）。
   配图比文章早一年以上，只可能是站点固定封面，不会是这篇稿的图。 */
function staleImage(url, item) {
  const m = /(20\d{2})(\d{2})\d{2}|\/(20\d{2})[\/\-](\d{2})\//.exec(String(url || ""));
  if (!m) return false;
  const y = Number(m[1] || m[3]);
  const mo = Number(m[2] || m[4]);
  if (!y || !mo || mo > 12) return false;
  const t = Date.parse(item.published_at || item.fetched_at || "");
  if (Number.isNaN(t)) return false;
  const d = new Date(t);
  return d.getFullYear() * 12 + d.getMonth() + 1 - (y * 12 + mo) >= 12;
}

/* 这张图能不能当配图：不是站点通用图、也不是一年前的固定封面 */
function usableImage(item) {
  const url = FD.hasOwnImage(item) ? item.image : "";
  if (!url) return "";
  if ((imageFreq.get(url) || 0) >= 2) return ""; // 同图复用两次以上 = 通用图
  if (staleImage(url, item)) return "";
  return url;
}

/* ------------------------------------------------------------ 热点榜 */

function renderHot(rows) {
  if (!rows.length) {
    el.status.textContent = "没有可排行的内容。";
    el.more.hidden = true;
    return;
  }
  const all = dedupeByTitle(rows).sort((a, b) => (b._heat || 0) - (a._heat || 0));
  const sorted = all.slice(0, HOT_MAX);
  const list = document.createElement("div");
  list.className = "ranklist";

  sorted.forEach((item, i) => {
    const a = document.createElement("a");
    a.className = "rank-item";
    a.href = FD.articleHref(item.id);

    const n = document.createElement("span");
    n.className = "rank-n";
    n.textContent = i + 1;

    const main = document.createElement("div");
    main.className = "rank-main";
    const t = document.createElement("div");
    t.className = "rank-t";
    t.textContent = item.title_zh || item.title || "";
    const meta = document.createElement("div");
    meta.className = "rank-meta";
    /* 没有发布时间就写抓到的时刻，不加"收录于"——读者要看的是时间本身 */
    const when = item.published_at ? FD.timeAgo(item.published_at) : FD.hm(item.fetched_at);
    meta.textContent = [item.source_name, (item.tags || [])[0], when].filter(Boolean).join(" · ");
    main.appendChild(t);
    main.appendChild(meta);

    const heat = document.createElement("span");
    heat.className = "rank-heat";
    heat.textContent = item._heat || 0;

    a.appendChild(n);
    a.appendChild(main);
    a.appendChild(heat);
    list.appendChild(a);
  });

  el.view.appendChild(list);
  el.more.hidden = true;
  const formula = "热度 = 新鲜度 + 分类权重（召回、监管优先）+ 信息完整度，由规则算分，不涉及模型生成";
  el.status.textContent = all.length > HOT_MAX
    ? `Top ${sorted.length}（候选 ${all.length} 条，其余见「全部动态」）。${formula}。`
    : `共 ${sorted.length} 条，按热度排序。${formula}。`;
}

/* ------------------------------------------------------------ 日报 */

/* 中文不做分词，靠一张行业词表去数标题里的高频实体，够用了 */
const DAILY_WORDS = [
  "飞鹤", "伊利", "蒙牛", "达能", "雀巢", "君乐宝", "澳优", "贝因美", "合生元", "惠氏",
  "雅培", "爱他美", "美素佳儿", "佳贝艾特", "a2", "圣元", "完达山", "光明", "三元", "新乳业",
  "配方注册", "国标", "召回", "抽检", "不合格", "跨境电商", "保税", "海关", "关税",
  "进口", "出口", "奶源", "原奶", "乳清", "乳铁蛋白", "涨价", "降价", "财报", "营收",
  "市占率", "新品", "收购", "IPO", "产能", "牧场", "HMO", "益生菌", "羊奶粉", "特医食品",
  "辅食", "出生率", "母婴店", "直播", "渠道", "库存",
];

function renderDaily(rows) {
  if (!rows.length) {
    el.status.textContent = "没有可汇总的内容。";
    return;
  }

  const groups = new Map();
  for (const item of dedupeByTitle(rows)) {
    const k = FD.dayKey(item.published_at || item.fetched_at);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(item);
  }
  const keys = [...groups.keys()].sort().reverse().slice(0, 14);

  for (const key of keys) {
    const dayRows = groups.get(key).sort(newestFirst);

    /* 关键词 */
    const counts = new Map();
    for (const it of dayRows) {
      const text = (it.title_zh || "") + " " + (it.title || "");
      for (const w of DAILY_WORDS) {
        if (text.indexOf(w) >= 0) counts.set(w, (counts.get(w) || 0) + 1);
      }
    }
    const words = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);

    /* 分类分布 */
    const cats = new Map();
    for (const it of dayRows) for (const t of it.tags || []) cats.set(t, (cats.get(t) || 0) + 1);
    const catList = [...cats.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);

    const box = document.createElement("section");
    box.className = "daily";

    const head = document.createElement("div");
    head.className = "daily-head";
    head.innerHTML =
      `<span class="daily-date">${FD.mdDate(dayRows[0].published_at || dayRows[0].fetched_at) || "时间未知"}</span>` +
      `<span class="daily-week">${FD.weekday(dayRows[0].published_at) || FD.weekday(key + "T12:00:00")}</span>` +
      `<span class="daily-cnt">${dayRows.length} 条</span>`;

    const body = document.createElement("div");
    body.className = "daily-body";

    if (words.length) {
      const kw = document.createElement("div");
      kw.className = "daily-kw";
      for (const [w, n] of words) {
        const span = document.createElement("span");
        span.className = "kw";
        span.innerHTML = `${FD.escapeHtml(w)}<b>${n}</b>`;
        kw.appendChild(span);
      }
      body.appendChild(kw);
    }

    if (catList.length) {
      const cat = document.createElement("div");
      cat.className = "daily-cat";
      for (const [t, n] of catList) {
        const span = document.createElement("span");
        span.dataset.tag = t; // 借分类色，日报里一眼看出各栏目的构成
        span.innerHTML = `${FD.escapeHtml(t)} <b>${n}</b>`;
        cat.appendChild(span);
      }
      body.appendChild(cat);
    }

    const list = document.createElement("ul");
    list.className = "daily-list";
    const important = dayRows.slice().sort((a, b) => (b._heat || 0) - (a._heat || 0)).slice(0, 8);
    for (const it of important) {
      const li = document.createElement("li");
      const at = document.createElement("span");
      at.className = "at";
      at.textContent = stampShort(it.published_at || it.fetched_at);
      const a = document.createElement("a");
      a.href = FD.articleHref(it.id);
      a.textContent = it.title_zh || it.title || "";
      li.appendChild(at);
      li.appendChild(a);
      list.appendChild(li);
    }
    body.appendChild(list);

    if (dayRows.length > important.length) {
      const more = document.createElement("div");
      more.className = "daily-more";
      const a = document.createElement("a");
      a.href = "?view=all";
      a.textContent = `当天还有 ${dayRows.length - important.length} 条 →`;
      more.appendChild(a);
      body.appendChild(more);
    }

    box.appendChild(head);
    box.appendChild(body);
    el.view.appendChild(box);
  }

  el.more.hidden = true;
  el.status.textContent = `近 ${keys.length} 天，共 ${rows.length} 条。关键词从标题里统计，用来快速判断当天报道重心。`;
}

/* ------------------------------------------------------------ 主题 */

const TOPIC_DESC = {
  质量安全: "召回、抽检不合格、污染物与微生物事件",
  政策法规: "配方注册、国标、海关与跨境监管口径",
  跨境电商: "保税、正面清单、关税与渠道变化",
  供应链: "奶源、原奶价格、乳清与乳铁蛋白等原料",
  市场数据: "财报、市占率、价格与销量走势",
  品牌动态: "新品、并购、人事与渠道动作",
  海外动态: "海外市场、监管机构与跨国乳企",
  行业动态: "其余与行业相关的内容",
};

function renderTopics(rows) {
  const groups = new Map();
  for (const item of rows) {
    // 只按主分类归组：一条稿只出现在一个主题卡片里，卡片上的条数之和
    // 等于总条数。原来遍历全部 tags，同一条会同时挂在两三个主题下。
    const t = FD.primaryTag(item);
    if (!groups.has(t)) groups.set(t, []);
    groups.get(t).push(item);
  }
  const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);
  if (!sorted.length) {
    el.status.textContent = "还没有可归类的主题。";
    return;
  }

  const grid = document.createElement("div");
  grid.className = "topic-grid";

  for (const [tag, list] of sorted) {
    const box = document.createElement("div");
    box.className = "topic";

    const head = document.createElement("div");
    head.className = "topic-head";
    head.innerHTML =
      `<span class="topic-name">${FD.escapeHtml(tag)}</span>` +
      `<span class="topic-n">${list.length} 条</span>`;
    const go = document.createElement("a");
    go.className = "topic-go";
    go.href = "?view=all&tag=" + encodeURIComponent(tag);
    go.textContent = "查看全部 →";
    head.appendChild(go);
    box.appendChild(head);

    if (TOPIC_DESC[tag]) {
      const desc = document.createElement("div");
      desc.className = "topic-desc";
      desc.textContent = TOPIC_DESC[tag];
      box.appendChild(desc);
    }

    const ul = document.createElement("ul");
    const latest = dedupeByTitle(list.slice().sort(newestFirst)).slice(0, 4);
    for (const it of latest) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = FD.articleHref(it.id);
      a.textContent = it.title_zh || it.title || "";
      li.appendChild(a);
      ul.appendChild(li);
    }
    box.appendChild(ul);
    grid.appendChild(box);
  }

  el.view.appendChild(grid);
  el.more.hidden = true;
  el.status.textContent = `共 ${sorted.length} 个主题。点右上角进入该主题的全部条目。`;
}

/* ------------------------------------------------------------ 交互 */

let searchTimer = null;
el.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = el.search.value;
    state.limit = PAGE_SIZE;
    applyRoute(false, true);
  }, 160);
});

/* 手机端：点放大镜才展开搜索框。再点一次收起，并把已输入的关键词清掉，
   否则"收起"之后列表还停在筛选结果里，读者会以为内容少了。 */
const searchMini = document.getElementById("search-mini");
if (searchMini) {
  searchMini.addEventListener("click", () => {
    const hd = searchMini.closest(".hd");
    const open = hd.classList.toggle("is-searching");
    searchMini.setAttribute("aria-expanded", String(open));
    if (open) {
      el.search.focus();
    } else if (el.search.value) {
      el.search.value = "";
      state.query = "";
      state.limit = PAGE_SIZE;
      applyRoute(false, true);
    }
  });
}

el.more.addEventListener("click", () => {
  state.limit += PAGE_SIZE;
  applyRoute(false, true);
});

for (const btn of el.navItems) {
  btn.addEventListener("click", () => {
    if (btn.dataset.view === state.view) {
      // 再点一次当前栏目 = 重置筛选，避免"卡在某个分类里出不来"
      if (state.tag !== "all") {
        state.tag = "all";
        state.limit = PAGE_SIZE;
        applyRoute(false, true);
      }
      return;
    }
    go(btn.dataset.view);
  });
}

window.addEventListener("popstate", () => {
  readRoute();
  applyRoute(true, true);
});

FD.initTheme(el.theme);
readRoute();
load();

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
  pick: { title: "精选", sub: "今天值得看的几条，加上最新动态" },
  all: { title: "全部动态", sub: "按发布时间排，最新的在最上面" },
  hot: { title: "热点榜", sub: "按热度排序：新鲜度、影响面、信息完整度" },
  daily: { title: "日报", sub: "每天一屏看完整天发生了什么" },
  topics: { title: "主题", sub: "按赛道归类，找细分类目更方便" },
};

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
  search: document.getElementById("search"),
  title: document.getElementById("page-title"),
  sub: document.getElementById("page-sub"),
  hotbox: document.getElementById("hotbox"),
  hotboxList: document.getElementById("hotbox-list"),
  railStat: document.getElementById("rail-stat"),
  theme: document.getElementById("theme-btn"),
  footMeta: document.getElementById("foot-meta"),
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
    el.view.innerHTML = "";
    el.status.innerHTML =
      "数据加载失败：" + FD.escapeHtml(err.message) + "<br>" +
      "如果是双击打开的，请确认 <code>data/latest.js</code> 存在；" +
      "要看全量数据，在该目录执行 <code>python -m http.server 8000</code> 后访问 localhost:8000。";
    return;
  }

  state.items = data.items;
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
    el.status.textContent = "还没有任何数据。先跑一次 python scripts/run.py 生成 data/ 目录。";
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
  applyRoute(true);
}

/* ------------------------------------------------------------ 侧栏 */

function renderRail() {
  const today = state.items.filter((i) => withinHours(i, 24)).length;
  document.getElementById("n-pick").textContent = today || "";
  document.getElementById("n-all").textContent = state.items.length;
  document.getElementById("n-hot").textContent = "";
  const days = new Set(state.items.map((i) => FD.dayKey(i.published_at || i.fetched_at))).size;
  document.getElementById("n-daily").textContent = days;

  // 分类：按条数降序
  const sorted = Object.entries(state.tagCounts).sort((a, b) => b[1] - a[1]);
  el.cats.innerHTML = "";
  for (const [tag, n] of sorted) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cat-item";
    btn.dataset.tag = tag;
    btn.setAttribute("aria-current", String(state.tag === tag));
    btn.appendChild(document.createTextNode(tag));
    const num = document.createElement("span");
    num.className = "n";
    num.textContent = n;
    btn.appendChild(num);
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

  const parts = [`共 ${state.items.length} 条`, `保留最近 ${state.meta.retention_days || 90} 天`];
  if (state.meta.sources_total) parts.push(`本轮源健康度 ${state.meta.sources_ok}/${state.meta.sources_total}`);
  parts.push(`数据源 ${sources} 个`);
  el.footMeta.textContent = parts.join(" · ");
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
  el.sub.textContent = meta.sub;

  for (const btn of el.navItems) {
    btn.setAttribute("aria-current", String(btn.dataset.view === state.view));
  }
  for (const btn of el.cats.querySelectorAll(".cat-item")) {
    btn.setAttribute("aria-current", String(btn.dataset.tag === state.tag));
  }

  el.view.innerHTML = "";
  el.hotbox.hidden = true;
  el.status.textContent = "";

  const rows = filtered();

  if (state.view === "pick") {
    renderHotbox(rows);
    renderTimeline(rows, true);
  } else if (state.view === "all") {
    renderTimeline(rows, false);
  } else if (state.view === "hot") {
    renderHot(rows);
  } else if (state.view === "daily") {
    renderDaily(rows);
  } else if (state.view === "topics") {
    renderTopics(rows);
  }

  if (!keepScroll) window.scrollTo({ top: 0, behavior: firstLoad ? "auto" : "smooth" });
}

/* ------------------------------------------------------------ 过滤 */

function withinHours(item, hours) {
  const t = Date.parse(item.published_at || item.fetched_at || "");
  return !Number.isNaN(t) && Date.now() - t <= hours * 36e5;
}

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

/* ------------------------------------------------------------ 精选：当前热点 + 时间轴 */

function renderHotbox(rows) {
  // 只在没有搜索、没有筛分类时出现：此时"当前热点"才代表全局
  if (state.query.trim() || state.tag !== "all" || !rows.length) return;

  const top = rows.slice().sort((a, b) => (b._heat || 0) - (a._heat || 0)).slice(0, 5);
  el.hotboxList.innerHTML = "";

  top.forEach((item, i) => {
    const row = document.createElement("div");
    row.className = "hot-row";

    const rank = document.createElement("span");
    rank.className = "hot-rank";
    rank.textContent = i + 1;
    row.appendChild(rank);

    const a = document.createElement("a");
    a.className = "hot-title";
    a.href = FD.articleHref(item.id);
    a.textContent = item.title_zh || item.title || "（无标题）";
    a.title = a.textContent;
    row.appendChild(a);

    const tags = document.createElement("span");
    tags.className = "hot-tags";
    const src = document.createElement("span");
    src.className = "srcbadge";
    src.style.background = FD.badgeColor(item.source_id);
    src.textContent = FD.initial(item.source_name);
    src.title = item.source_name || "";
    tags.appendChild(src);
    row.appendChild(tags);

    const heat = document.createElement("span");
    heat.className = "hot-heat";
    heat.innerHTML = `${item._heat || 0}<small>热度</small>`;
    row.appendChild(heat);

    el.hotboxList.appendChild(row);
  });
  el.hotbox.hidden = top.length === 0;
}

function renderTimeline(rows, isPick) {
  usedImages.clear();
  if (!rows.length) {
    el.status.textContent = state.query
      ? `没有匹配「${state.query}」的内容，换个关键词试试。`
      : "这个分类下暂时没有内容。";
    el.more.hidden = true;
    return;
  }

  // 转载稿标题完全相同，这一屏里只留一条（列表视图在这里去重，
  // 主题页的计数才不会被削减）。总数也用去重后的，否则"还有 N 条"
  // 会把重复稿算进去，点到底却是空的。
  const uniq = dedupeByTitle(rows);
  const shown = uniq.slice(0, state.limit);
  // 精选视图里，最新一条若有摘要就放大成头条，让页面有视觉重心
  const leadId =
    isPick && !state.query && shown[0] && (shown[0].summary || "").length >= 40 ? shown[0].id : null;

  let lastDay = null;
  for (const item of shown) {
    const key = FD.dayKey(item.published_at || item.fetched_at);
    if (key !== lastDay) {
      el.view.appendChild(dayBar(item.published_at || item.fetched_at, rows, key));
      lastDay = key;
    }
    const row = document.createElement("div");
    row.className = "row";
    const time = document.createElement("div");
    time.className = "row-time";
    time.textContent = FD.hm(item.published_at || item.fetched_at);
    row.appendChild(time);
    const slot = document.createElement("div");
    slot.className = "card-slot";
    slot.appendChild(makeCard(item, { lead: item.id === leadId }));
    row.appendChild(slot);
    el.view.appendChild(row);
  }

  el.more.hidden = uniq.length <= state.limit;
  if (!el.more.hidden) el.more.textContent = `加载更多（还有 ${uniq.length - state.limit} 条）`;
}

function dayBar(iso, rows, key) {
  const d = document.createElement("div");
  d.className = "daybar";
  const n = rows.filter((i) => FD.dayKey(i.published_at || i.fetched_at) === key).length;
  d.innerHTML =
    `<span>${FD.mdDate(iso) || "时间未知"}</span>` +
    `<span class="week">${FD.weekday(iso)}</span>` +
    `<span class="cnt">· ${n} 条</span>`;
  return d;
}

/* ------------------------------------------------------------ 卡片 */

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

function makeCard(item, opts = {}) {
  const card = document.createElement("a");
  card.className = "card" + (opts.lead ? " card--lead" : "");
  card.href = FD.articleHref(item.id);
  card.dataset.tag = FD.primaryTag(item); // 无图行靠它取分类色

  const body = document.createElement("div");
  body.className = "card-body";

  /* 缩略图：有原文配图用原文的，没有就用分类卡通插画。
     每张图全页只用一次（quota = 1）——同一张插画在信息流里重复出现，
     读者第一反应是"这几条是不是同一篇"，而且一屏里连着几张一样的画很难看。
     用完的图不再复用，这条卡片就退成纯文字行（左侧一条分类色竖线做区分，
     不是把图放大凑数）。 */
  const realImg = usableImage(item);
  const imgKey = realImg || "topic:" + FD.topicCover(item);
  if ((usedImages.get(imgKey) || 0) < 1) {
    usedImages.set(imgKey, (usedImages.get(imgKey) || 0) + 1);
    const thumb = document.createElement("div");
    thumb.className = "thumb";
    const img = document.createElement("img");
    img.src = imgKey.startsWith("topic:") ? FD.topicCover(item) : realImg;
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.alt = "";
    if (!realImg) {
      thumb.classList.add("thumb--topic");
      img.classList.add("is-art");     // 卡通位图，暗色下不能走反相
    }
    /* 原文图加载失败 → 退回分类插画，而不是退回逐条线稿。
       列表和详情页都用同一套卡通插画，混两套风格会出现"首页线稿、
       点进去变卡通"的割裂；实在退不了就直接隐藏缩略图。 */
    const fallback = () => {
      if (!img.dataset.fb) {
        img.dataset.fb = "1";
        img.src = FD.topicCover(item);
        thumb.classList.add("thumb--topic");
        img.classList.add("is-art");
      } else {
        thumb.classList.add("is-broken");
      }
    };
    img.addEventListener("error", fallback);
    thumb.appendChild(img);
    body.appendChild(thumb);
    card.classList.add("card--pic");
  } else {
    card.classList.add("card--text"); // 无图行：靠左侧分类色竖线保持节奏
  }

  const main = document.createElement("div");
  main.className = "card-main";

  /* 顶行：分类 + 来源 */
  const top = document.createElement("div");
  top.className = "card-top";
  const tag = (item.tags || [])[0];
  if (tag) {
    const span = document.createElement("span");
    span.className = "card-tag";
    span.dataset.tag = tag;
    span.textContent = tag;
    top.appendChild(span);
  }
  const src = document.createElement("span");
  src.className = "card-source";
  src.textContent = item.source_name || "未知来源";
  top.appendChild(src);
  main.appendChild(top);

  /* 标题 */
  const h2 = document.createElement("h2");
  h2.textContent = item.title_zh || item.title || "（无标题）";
  main.appendChild(h2);

  /* 摘要 */
  if (item.summary) {
    const p = document.createElement("p");
    p.className = "card-summary";
    p.textContent = item.summary;
    main.appendChild(p);
  }

  /* 要点：头条位展开前两条，让首屏信息量足一些 */
  if (opts.lead && (item.points || []).length) {
    const ul = document.createElement("ul");
    ul.className = "card-points";
    for (const pt of item.points.slice(0, 2)) {
      const li = document.createElement("li");
      li.textContent = pt;
      ul.appendChild(li);
    }
    main.appendChild(ul);
  }

  /* 脚注 */
  const foot = document.createElement("div");
  foot.className = "card-foot";
  const time = document.createElement("span");
  time.className = "time";
  const stamp = item.published_at || item.fetched_at;
  // 源没给发布时间的老稿不少，写"时间未知"观感很差；用抓取时间并标明是收录时间
  time.textContent = item.published_at ? FD.timeAgo(item.published_at) : "收录于 " + FD.hm(stamp);
  if (stamp) time.title = FD.fmtFull(stamp);
  foot.appendChild(time);

  if (item.language === "en") {
    foot.appendChild(sepDot());
    const lang = document.createElement("span");
    lang.textContent = "外媒";
    foot.appendChild(lang);
  }
  if (item._heat) {
    foot.appendChild(sepDot());
    const heat = document.createElement("span");
    heat.className = "heat";
    heat.textContent = "热度 " + item._heat;
    foot.appendChild(heat);
  }

  const more = document.createElement("span");
  more.className = "more";
  more.textContent = "阅读全文 →";
  foot.appendChild(more);
  main.appendChild(foot);

  body.appendChild(main);
  card.appendChild(body);
  return card;
}

function sepDot() {
  const s = document.createElement("span");
  s.className = "card-sep";
  s.textContent = "·";
  return s;
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
    const when = item.published_at ? FD.timeAgo(item.published_at) : "收录于 " + FD.hm(item.fetched_at);
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
      at.textContent = FD.hm(it.published_at || it.fetched_at);
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

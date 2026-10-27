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

/* 判断层的体量：一个头条 + 四条要闻。
   再多的"热点"就不再是判断，而是把记录层复制一遍。 */
const BRIEF_COUNT = 4;

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
  live: document.getElementById("live"),
  stats: document.getElementById("stats"),
  board: document.getElementById("board"),
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
  renderLive();
  renderStats();
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

  /* 抓取失败数只有后端给了口径才显示。没有这组字段时宁可不提，
     也不编一个数字——这是全站唯一会让人怀疑"数据是不是缺了"的地方。 */
  const total = Number(state.meta.sources_total || 0);
  const ok = Number(state.meta.sources_ok || 0);
  if (total && ok && total > ok) {
    el.live.appendChild(dotSpan());
    const warn = document.createElement("span");
    warn.className = "warn";
    warn.textContent = `${total - ok} 个源抓取失败`;
    el.live.appendChild(warn);
  }
}

function dotSpan() {
  const s = document.createElement("span");
  s.className = "d";
  s.setAttribute("aria-hidden", "true");
  s.textContent = "·";
  return s;
}

/* 四个数：读者在第一屏要判断"今天量多不多、有没有事"，
   这四个数比一句口号有用得多。 */
function renderStats() {
  const recent = recentItems();
  const risk = recent.filter((i) => FD.primaryTag(i) === "质量安全").length;
  const policy = recent.filter((i) => FD.primaryTag(i) === "政策法规").length;
  const sources = new Set(state.items.map((i) => i.source_id)).size;

  const cells = [
    { v: recent.length, label: "近 24 小时" },
    { v: risk, label: "风险提示", warn: risk > 0 },
    { v: policy, label: "政策法规" },
    { v: sources, label: "覆盖信源" },
  ];

  el.stats.innerHTML = "";
  for (const c of cells) {
    const box = document.createElement("div");
    box.className = "st" + (c.warn ? " st--warn" : "");
    const b = document.createElement("b");
    b.textContent = c.v;
    const em = document.createElement("em");
    em.textContent = c.label;
    box.appendChild(b);
    box.appendChild(em);
    el.stats.appendChild(box);
  }
}

/* ------------------------------------------------------------ 侧栏 */

function renderRail() {
  const today = state.items.filter((i) => withinHours(i, 24)).length;
  document.getElementById("n-pick").textContent = today || "";
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

  for (const btn of el.navItems) {
    btn.setAttribute("aria-current", String(btn.dataset.view === state.view));
  }
  for (const btn of el.cats.querySelectorAll(".cat-item")) {
    btn.setAttribute("aria-current", String(btn.dataset.tag === state.tag));
  }

  el.view.innerHTML = "";
  el.status.textContent = "";

  const rows = filtered();

  /* 只有"按时间读"的两个视图才配得上状态层和判断层。
     热点榜、日报、主题各自有明确的任务，再在顶上压四个数和一个头条，
     等于三套导航同时抢注意力。 */
  const isFeed = state.view === "pick" || state.view === "all";
  el.stats.hidden = !isFeed;

  if (state.view === "pick") {
    renderBoard(rows);
    renderFeed(rows);
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

/* ------------------------------------------------------------ 精选：判断层 + 记录层 */

/* 判断层：一个头条 + 四条要闻。
   原来这里是"当前热点 Top5"的带框列表——只说了什么热，没说他为什么值得看。
   现在头条给出摘要与要点，剩下四条压成一行一条，三秒能扫完。 */
function renderBoard(rows) {
  el.board.innerHTML = "";
  if (state.query.trim() || state.tag !== "all" || !rows.length) {
    el.board.hidden = true;
    return;
  }

  const byHeat = rows.slice().sort((a, b) => (b._heat || 0) - (a._heat || 0));

  /* 头条要的是"一件事"，不是"一份目录"。
     "今日导读 / 早报 / 一周要闻"这类稿子标题里就写着自己是汇总，
     放进头条位等于用最贵的位置放一个目录页。 */
  const DIGEST = /今日导读|早报|晚报|周报|日报|汇总|盘点|速览|一周要闻|要闻回顾/;
  const isDigest = (i) => DIGEST.test(i.title_zh || i.title || "");

  const solid = byHeat.filter((i) => !isDigest(i));
  const pool = solid.length >= 3 ? solid : byHeat;
  // 有摘要才撑得起头条位：只有标题的一条放在这里，下面是空的
  const withSum = pool.filter((i) => (i.summary || "").length >= 40);
  const lead = withSum[0] || pool[0] || null;
  if (!lead) {
    el.board.hidden = true;
    return;
  }

  /* 头条不放配图。抓来的 image 里活动海报、栏目封面、分享卡片占比很高，
     靠宽高比分辨不出来；一旦挑错，最显眼的位置就成了一块和全站灰绿纸色
     打架的彩色牌子。宁可纯排版，也不赌图源质量。 */
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
  link.textContent = lead.title_zh || lead.title || "（无标题）";
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
  const bits = [FD.primaryTag(lead), lead.source_name, FD.hm(leadStamp)];
  if (lead._heat) bits.push("热度 " + lead._heat);
  bits.filter(Boolean).forEach((text, i) => {
    if (i) foot.appendChild(dotSpan());
    foot.appendChild(document.createTextNode(text));
  });
  txt.appendChild(foot);
  card.appendChild(txt);

  el.board.appendChild(card);

  const briefs = pool.filter((i) => i !== lead).slice(0, BRIEF_COUNT);
  if (briefs.length) {
    const ol = document.createElement("ol");
    ol.className = "briefs";
    briefs.forEach((item, i) => {
      const li = document.createElement("li");
      li.className = "br";

      const no = document.createElement("span");
      no.className = "no";
      no.textContent = String(i + 1).padStart(2, "0");
      li.appendChild(no);

      const box = document.createElement("div");
      box.className = "br-x";
      const h3 = document.createElement("h3");
      const a = document.createElement("a");
      a.href = FD.articleHref(item.id);
      a.textContent = item.title_zh || item.title || "（无标题）";
      h3.appendChild(a);
      box.appendChild(h3);

      const bm = document.createElement("div");
      bm.className = "bm";
      const when = item.published_at || item.fetched_at;
      [item.source_name, FD.primaryTag(item), FD.hm(when)]
        .filter(Boolean)
        .forEach((text, k) => {
          if (k) bm.appendChild(dotSpan());
          bm.appendChild(document.createTextNode(text));
        });
      box.appendChild(bm);
      li.appendChild(box);

      const ht = document.createElement("span");
      ht.className = "ht";
      ht.textContent = item._heat || 0;
      li.appendChild(ht);

      ol.appendChild(li);
    });
    el.board.appendChild(ol);
  }

  el.board.hidden = false;
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

/* 缩略图：有原文配图用原文的，没有就用分类卡通图，每张全页只用一次。
   用完不复用——同一张插画连着出现，读者会怀疑这几条是不是同一篇。 */
function thumbFor(item) {
  const realImg = usableImage(item);
  const key = realImg || "topic:" + FD.topicCover(item);
  if ((usedImages.get(key) || 0) >= 1) return null;
  usedImages.set(key, (usedImages.get(key) || 0) + 1);

  const box = document.createElement("div");
  box.className = "thumb" + (realImg ? "" : " thumb--topic");
  const img = document.createElement("img");
  img.src = realImg || FD.topicCover(item);
  img.loading = "lazy";
  img.referrerPolicy = "no-referrer";
  img.alt = "";
  if (!realImg) img.classList.add("is-art");

  const fallback = () => {
    if (!img.dataset.fb) {
      img.dataset.fb = "1";
      img.src = FD.topicCover(item);
      box.classList.add("thumb--topic");
      img.classList.add("is-art");
    } else {
      box.classList.add("is-broken");
    }
  };
  img.addEventListener("error", fallback);
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
  const minuteOf = (it) => FD.dayKey(stampOf(it)) + "|" + FD.hm(stampOf(it));

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
      time.appendChild(document.createTextNode(FD.hm(stamp)));
      const small = document.createElement("small");
      // 批次头把"这一批几条"放在时刻下面，比"3 小时前"更有用
      small.textContent = isBatch
        ? `${same} 条`
        : item.published_at
          ? FD.timeAgo(item.published_at)
          : "收录";
      time.appendChild(small);
      if (stamp) time.title = FD.fmtFull(stamp);
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
  const ts = inDay.map(pubTs).filter((t) => t).sort((a, b) => a - b);
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

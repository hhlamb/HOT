/* ============================================================
   首页：渲染信息流。无框架、无构建。
   取数逻辑在 feed-data.js，详情页共用同一份。
   ============================================================ */

const FD = window.FeedData;
const PAGE_SIZE = 40;

const state = {
  items: [],
  filtered: [],
  tag: "all",
  query: "",
  limit: PAGE_SIZE,
  meta: {},
};

const el = {
  feed: document.getElementById("feed"),
  status: document.getElementById("status"),
  tags: document.getElementById("tags"),
  search: document.getElementById("search"),
  count: document.getElementById("count"),
  more: document.getElementById("more-btn"),
  theme: document.getElementById("theme-btn"),
  footMeta: document.getElementById("foot-meta"),
  topbar: document.getElementById("topbar"),
  sToday: document.getElementById("s-today"),
  sTotal: document.getElementById("s-total"),
  sSources: document.getElementById("s-sources"),
  sUpdated: document.getElementById("s-updated"),
};

/* ---------------- 载入 ---------------- */

async function load() {
  el.status.textContent = "";
  el.feed.innerHTML = "";
  for (let i = 0; i < 4; i++) {
    const sk = document.createElement("div");
    sk.className = "skeleton";
    el.feed.appendChild(sk);
  }

  let data;
  try {
    data = await FD.loadAll();
  } catch (err) {
    el.feed.innerHTML = "";
    el.status.innerHTML =
      "数据加载失败：" + FD.escapeHtml(err.message) + "<br>" +
      "如果是双击打开的，请确认 <code>data/latest.js</code> 存在；" +
      "要看全量数据，在该目录执行 <code>python -m http.server 8000</code> 后访问 localhost:8000。";
    return;
  }

  state.items = data.items;
  state.meta = data.meta;
  el.feed.innerHTML = "";

  if (!state.items.length) {
    el.status.textContent = "还没有任何数据。先跑一次 python scripts/run.py 生成 data/ 目录。";
    return;
  }

  renderStats(data.partial);
  buildTags();
  applyFilter();
}

function renderStats(partial) {
  const since = Date.now() - 24 * 3600 * 1000;
  const today = state.items.filter((i) => {
    const t = Date.parse(i.fetched_at || i.published_at || "");
    return !Number.isNaN(t) && t >= since;
  }).length;
  const sources = new Set(state.items.map((i) => i.source_id)).size;
  el.sToday.textContent = today;
  el.sTotal.textContent = state.items.length;
  el.sSources.textContent = sources;

  const stamp = state.meta.last_updated || (state.items[0] && state.items[0].fetched_at) || "";
  el.sUpdated.textContent = stamp ? FD.fmtStamp(stamp) : "–";
  if (partial) el.sUpdated.title = "本地预览：仅最近 100 条";

  const parts = [`共 ${state.items.length} 条`, `保留最近 ${state.meta.retention_days || 90} 天`];
  if (state.meta.sources_total) {
    parts.push(`本轮源健康度 ${state.meta.sources_ok}/${state.meta.sources_total}`);
  }
  parts.push(`数据源 ${sources} 个`);
  el.footMeta.textContent = parts.join(" · ");
}

/* ---------------- 分类 ---------------- */

function buildTags() {
  const counts = new Map();
  for (const item of state.items) {
    for (const tag of item.tags || []) counts.set(tag, (counts.get(tag) || 0) + 1);
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);

  el.tags.innerHTML = "";
  el.tags.appendChild(makeTagChip("all", "全部", state.items.length));
  for (const [tag, n] of sorted) el.tags.appendChild(makeTagChip(tag, tag, n));
}

function makeTagChip(value, label, n) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tag";
  btn.dataset.value = value;
  btn.setAttribute("aria-pressed", String(state.tag === value));
  btn.appendChild(document.createTextNode(label));
  const num = document.createElement("span");
  num.className = "n";
  num.textContent = n;
  btn.appendChild(num);
  btn.addEventListener("click", () => {
    state.tag = value;
    state.limit = PAGE_SIZE;
    for (const chip of el.tags.querySelectorAll(".tag")) {
      chip.setAttribute("aria-pressed", String(chip.dataset.value === value));
    }
    applyFilter();
  });
  return btn;
}

function applyFilter() {
  const q = state.query.trim().toLowerCase();
  state.filtered = state.items.filter((item) => {
    if (state.tag !== "all" && !(item.tags || []).includes(state.tag)) return false;
    if (!q) return true;
    const hay = `${item.title_zh || ""} ${item.title || ""} ${item.summary || ""} ${item.source_name || ""}`.toLowerCase();
    return hay.includes(q);
  });
  render();
}

/* ---------------- 渲染 ---------------- */

function render() {
  el.feed.innerHTML = "";
  const rows = state.filtered.slice(0, state.limit);

  if (!rows.length) {
    el.status.textContent = state.query
      ? `没有匹配「${state.query}」的内容，换个关键词试试。`
      : "这个分类下暂时没有内容。";
    el.count.textContent = "0 条";
    el.more.hidden = true;
    return;
  }

  // 默认视图下，最新一条做头条，页面才有视觉重心
  const leadMode = !state.query && state.tag === "all";
  // 只有当最新一条自带摘要时才放大成头条：否则会出现"第二张卡比第一张还大"的错位感
  const leadId = leadMode && ((rows[0].summary || "").length >= 20) ? rows[0].id : null;

  let lastDay = null;
  rows.forEach((item, idx) => {
    const label = FD.dayLabel(item.published_at);
    if (label !== lastDay) {
      const d = document.createElement("div");
      d.className = "day";
      d.textContent = label;
      el.feed.appendChild(d);
      lastDay = label;
    }
    el.feed.appendChild(makeCard(item, leadMode && item.id === leadId));
  });

  el.count.textContent = `${state.filtered.length} 条`;
  el.more.hidden = state.filtered.length <= state.limit;
  el.status.textContent =
    el.more.hidden && state.filtered.length > PAGE_SIZE ? `已显示全部 ${state.filtered.length} 条` : "";
}

function makeCard(item, isLead) {
  const card = document.createElement("a");
  card.className = "card" + (isLead ? " card--lead" : "");
  card.href = FD.articleHref(item.id);

  const top = document.createElement("div");
  top.className = "card-top";
  const tag = (item.tags || [])[0];
  if (tag) {
    const span = document.createElement("span");
    span.className = "card-tag";
    span.textContent = tag;
    top.appendChild(span);
  }
  const src = document.createElement("span");
  src.className = "card-source";
  src.textContent = item.source_name || "未知来源";
  top.appendChild(src);
  card.appendChild(top);

  const h2 = document.createElement("h2");
  h2.textContent = item.title_zh || item.title || "（无标题）";
  card.appendChild(h2);

  if (item.summary) {
    const p = document.createElement("p");
    p.className = "card-summary";
    p.textContent = item.summary;
    card.appendChild(p);
  }

  const foot = document.createElement("div");
  foot.className = "card-foot";
  const time = document.createElement("span");
  time.textContent = FD.timeAgo(item.published_at);
  if (item.published_at) time.title = FD.fmtFull(item.published_at);
  foot.appendChild(time);

  if (item.language === "en") {
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.textContent = "·";
    foot.appendChild(dot);
    const lang = document.createElement("span");
    lang.textContent = "外媒";
    foot.appendChild(lang);
  }

  const more = document.createElement("span");
  more.className = "more";
  more.textContent = "阅读全文 →";
  foot.appendChild(more);

  card.appendChild(foot);
  return card;
}

/* ---------------- 交互 ---------------- */

let searchTimer = null;
el.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = el.search.value;
    state.limit = PAGE_SIZE;
    applyFilter();
  }, 160);
});

el.more.addEventListener("click", () => {
  state.limit += PAGE_SIZE;
  render();
});

/* 顶栏滚动时补一条分隔线；深浅色交给共用层 */
FD.initTopbar(el.topbar);
FD.initTheme(el.theme);

load();

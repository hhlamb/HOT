/* ============================================================
   前端逻辑：取数 → 归并 → 筛选 → 渲染。无框架、无构建。
   ------------------------------------------------------------
   两种取数路径：
     1) http(s) 打开：读 data/index.json 拿到月份分片清单，再逐片 fetch，
        分片是 JSONL（每行一条），比整包 JSON 小得多，也只增不改。
     2) file:// 双击：浏览器禁止 file 页面 fetch 本地文件（CORS 硬限制），
        改用 <script src="data/latest.js"> 注入，里面是最近 100 条。
        所以双击只能看到最近约 1 天的数据，要看全量请起本地 http 服务。
   ============================================================ */

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
  updated: document.getElementById("updated"),
  more: document.getElementById("more-btn"),
  reload: document.getElementById("reload-btn"),
  theme: document.getElementById("theme-btn"),
  footMeta: document.getElementById("foot-meta"),
};

/* ---------------- 工具 ---------------- */

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error("无法加载 " + src));
    document.head.appendChild(s);
  });
}

function parseJsonl(text) {
  const rows = [];
  for (const line of text.split("\n")) {
    const s = line.trim();
    if (!s) continue;
    try {
      rows.push(JSON.parse(s));
    } catch (e) {
      /* 单行损坏跳过，不影响整份数据 */
    }
  }
  return rows;
}

function mergeById(rows) {
  const map = new Map();
  for (const row of rows) {
    if (row && row.id) map.set(row.id, row); // 重复 id 取最后一条
  }
  return [...map.values()].sort((a, b) => {
    const ta = a.published_at || "";
    const tb = b.published_at || "";
    if (ta === tb) return 0;
    return ta < tb ? 1 : -1; // 时间未知的（空串）自然沉底
  });
}

function timeAgo(iso) {
  if (!iso) return "时间未知";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "时间未知";
  const diff = Date.now() - t;
  if (diff < 60e3) return "刚刚";
  const m = Math.floor(diff / 60e3);
  if (m < 60) return m + " 分钟前";
  const h = Math.floor(m / 60);
  if (h < 24) return h + " 小时前";
  const d = Math.floor(h / 24);
  if (d < 8) return d + " 天前";
  return fmtDay(iso);
}

function fmtDay(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  const mm = d.getMonth() + 1;
  const dd = d.getDate();
  return sameYear ? `${mm} 月 ${dd} 日` : `${d.getFullYear()} 年 ${mm} 月 ${dd} 日`;
}

function dayLabel(iso) {
  if (!iso) return "时间未知";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "时间未知";
  const today = new Date();
  const yest = new Date(today.getTime() - 864e5);
  const md = `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
  if (d.toDateString() === today.toDateString()) return "今天 · " + md;
  if (d.toDateString() === yest.toDateString()) return "昨天 · " + md;
  return md;
}

function fmtStamp(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ---------------- 取数 ---------------- */

async function fetchHttp() {
  const indexRes = await fetch("data/index.json", { cache: "no-cache" });
  if (!indexRes.ok) throw new Error("index.json 读取失败（HTTP " + indexRes.status + "）");
  const index = await indexRes.json();
  const months = (index.months || []).filter((m) => m.count > 0);

  const shards = await Promise.all(
    months.map(async (m) => {
      const res = await fetch("data/" + m.file, { cache: "no-cache" });
      if (!res.ok) return [];
      return parseJsonl(await res.text());
    })
  );
  return { rows: shards.flat(), meta: index, partial: false };
}

async function fetchFileProtocol() {
  await loadScript("data/latest.js");
  const payload = window.__FEED__;
  if (!payload) throw new Error("data/latest.js 里没有数据");
  return {
    rows: payload.items || [],
    meta: { last_updated: payload.last_updated, total: payload.total },
    partial: true,
  };
}

async function load() {
  setStatus("");
  el.feed.innerHTML = "";
  for (let i = 0; i < 4; i++) {
    const sk = document.createElement("div");
    sk.className = "skeleton";
    el.feed.appendChild(sk);
  }

  let data;
  try {
    data = location.protocol === "file:" ? await fetchFileProtocol() : await fetchHttp();
  } catch (err) {
    el.feed.innerHTML = "";
    setStatus(
      "数据加载失败：" + err.message + "<br>" +
      "如果是双击打开的，请确认 <code>data/latest.js</code> 存在；" +
      "要看全量数据，在该目录执行 <code>python -m http.server 8000</code> 后访问 localhost:8000。"
    );
    el.updated.textContent = "数据不可用";
    return;
  }

  state.items = mergeById(data.rows);
  state.meta = data.meta || {};
  el.feed.innerHTML = "";

  if (!state.items.length) {
    setStatus("还没有任何数据。先跑一次 <code>python scripts/run.py</code> 生成 data/ 目录。");
    el.updated.textContent = "暂无数据";
    return;
  }

  const stamp = state.meta.last_updated || state.items[0].fetched_at || "";
  el.updated.textContent = stamp ? `${fmtStamp(stamp)} 更新` : "更新时间未知";
  if (data.partial) {
    el.updated.textContent += "（本地预览，仅最近 100 条）";
  }

  const parts = [
    `共 ${state.items.length} 条`,
    `保留最近 ${state.meta.retention_days || 90} 天`,
    `数据来自 ${countSources(state.items)} 个信息源`,
  ];
  if (state.meta.sources_total) {
    parts.push(`本轮源健康度 ${state.meta.sources_ok}/${state.meta.sources_total}`);
  }
  el.footMeta.textContent = parts.join(" · ");

  buildTags();
  applyFilter();
}

function countSources(items) {
  return new Set(items.map((i) => i.source_id)).size;
}

/* ---------------- 渲染 ---------------- */

function buildTags() {
  const counts = new Map();
  for (const item of state.items) {
    for (const tag of item.tags || []) {
      counts.set(tag, (counts.get(tag) || 0) + 1);
    }
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);

  el.tags.innerHTML = "";
  el.tags.appendChild(makeTagChip("all", "全部", state.items.length));
  for (const [tag, n] of sorted) {
    el.tags.appendChild(makeTagChip(tag, tag, n));
  }
}

function makeTagChip(value, label, n) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tag";
  btn.dataset.value = value;
  btn.setAttribute("aria-pressed", String(state.tag === value));
  btn.textContent = label;
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

function render() {
  el.feed.innerHTML = "";
  const rows = state.filtered.slice(0, state.limit);

  if (!rows.length) {
    setStatus(
      state.query
        ? `没有匹配「${escapeText(state.query)}」的内容，换个关键词试试。`
        : "这个分类下暂时没有内容。"
    );
    el.count.textContent = "0 条";
    el.more.hidden = true;
    return;
  }

  let lastDay = null;
  for (const item of rows) {
    const label = dayLabel(item.published_at);
    if (label !== lastDay) {
      const d = document.createElement("div");
      d.className = "day";
      d.textContent = label;
      el.feed.appendChild(d);
      lastDay = label;
    }
    el.feed.appendChild(makeCard(item));
  }

  el.count.textContent = `${state.filtered.length} 条`;
  el.more.hidden = state.filtered.length <= state.limit;
  if (el.more.hidden && state.filtered.length > PAGE_SIZE) {
    setStatus(`已显示全部 ${state.filtered.length} 条`);
  } else {
    setStatus("");
  }
}

function makeCard(item) {
  const card = document.createElement("article");
  card.className = "card";

  const top = document.createElement("div");
  top.className = "card-top";

  for (const tag of (item.tags || []).slice(0, 2)) {
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
  const link = document.createElement("a");
  link.href = item.original_url || "#";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = item.title_zh || item.title || "（无标题）";
  h2.appendChild(link);
  card.appendChild(h2);

  if (item.summary) {
    const p = document.createElement("p");
    p.className = "summary";
    p.textContent = item.summary;
    card.appendChild(p);
  }

  const foot = document.createElement("div");
  foot.className = "card-foot";

  const left = document.createElement("span");
  left.className = "time";
  left.textContent = timeAgo(item.published_at) + (item.language === "en" ? " · 原文为英文" : "");
  if (item.published_at) left.title = fmtStamp(item.published_at);
  foot.appendChild(left);

  const out = document.createElement("a");
  out.className = "out-link";
  out.href = item.original_url || "#";
  out.target = "_blank";
  out.rel = "noopener noreferrer";
  out.textContent = "查看原文";
  foot.appendChild(out);

  card.appendChild(foot);
  return card;
}

function setStatus(html) {
  el.status.innerHTML = html || "";
}

function escapeText(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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

el.reload.addEventListener("click", () => {
  const url = new URL(location.href);
  url.searchParams.set("t", Date.now());
  location.href = url.toString();
});

/* 深浅色：跟随系统，按钮可覆盖并记住 */
const THEME_KEY = "milkfeed-theme";
function applyTheme(mode) {
  if (mode === "light" || mode === "dark") {
    document.documentElement.dataset.theme = mode;
  } else {
    delete document.documentElement.dataset.theme;
  }
  el.theme.textContent = mode === "dark" ? "☀" : mode === "light" ? "☾" : "◐";
}
try {
  applyTheme(localStorage.getItem(THEME_KEY) || "auto");
} catch (e) {
  applyTheme("auto");
}
el.theme.addEventListener("click", () => {
  let cur = "auto";
  try {
    cur = localStorage.getItem(THEME_KEY) || "auto";
  } catch (e) {}
  const next = cur === "auto" ? "dark" : cur === "dark" ? "light" : "auto";
  try {
    if (next === "auto") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, next);
  } catch (e) {}
  applyTheme(next);
});

load();

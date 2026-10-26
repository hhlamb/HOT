/* ============================================================
   共用取数层：首页和详情页都走这里，避免两份重复代码。
   ------------------------------------------------------------
   两条路径：
     1) http(s)：读 data/index.json 拿月份清单，再逐片 fetch JSONL
     2) file://：浏览器禁止 fetch 本地文件，改用 data/latest.js（仅最近 100 条）
   ============================================================ */

(function () {
  const FEED = {};

  function parseJsonl(text) {
    const rows = [];
    for (const line of text.split("\n")) {
      const s = line.trim();
      if (!s) continue;
      try {
        rows.push(JSON.parse(s));
      } catch (e) {
        /* 单行损坏跳过 */
      }
    }
    return rows;
  }

  function mergeById(rows) {
    const map = new Map();
    for (const row of rows) {
      if (row && row.id) map.set(row.id, row); // 同 id 取最后一条
    }
    return [...map.values()].sort((a, b) => {
      const ta = a.published_at || "";
      const tb = b.published_at || "";
      if (ta === tb) return 0;
      return ta < tb ? 1 : -1; // 时间未知的自然沉底
    });
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error("无法加载 " + src));
      document.head.appendChild(s);
    });
  }

  async function fetchHttp() {
    const res = await fetch("data/index.json", { cache: "no-cache" });
    if (!res.ok) throw new Error("index.json 读取失败（HTTP " + res.status + "）");
    const index = await res.json();
    const months = (index.months || []).filter((m) => m.count > 0);
    const shards = await Promise.all(
      months.map(async (m) => {
        const r = await fetch("data/" + m.file, { cache: "no-cache" });
        return r.ok ? parseJsonl(await r.text()) : [];
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

  FEED.loadAll = async function () {
    const data = location.protocol === "file:" ? await fetchFileProtocol() : await fetchHttp();
    return { items: mergeById(data.rows), meta: data.meta || {}, partial: !!data.partial };
  };

  /* ---------------- 时间格式 ---------------- */

  FEED.fmtStamp = function (iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  FEED.fmtFull = function (iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "时间未知";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()} 年 ${d.getMonth() + 1} 月 ${d.getDate()} 日 ${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  FEED.timeAgo = function (iso) {
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
    return FEED.fmtStamp(iso);
  };

  FEED.dayLabel = function (iso) {
    if (!iso) return "时间未知";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "时间未知";
    const md = `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
    const today = new Date();
    const yest = new Date(today.getTime() - 864e5);
    if (d.toDateString() === today.toDateString()) return "今天 · " + md;
    if (d.toDateString() === yest.toDateString()) return "昨天 · " + md;
    return md;
  };

  FEED.isToday = function (iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return false;
    return d.toDateString() === new Date().toDateString();
  };

  FEED.articleHref = function (id) {
    return "article.html?id=" + encodeURIComponent(id);
  };

  /* ---------------- 热度 ---------------- */

  const HEAT_TAGS = {
    "质量安全": 26,
    "政策法规": 22,
    "跨境电商": 12,
    "供应链": 10,
    "市场数据": 8,
    "品牌动态": 6,
  };

  /** 稳定抖动：同一条内容每次算出来的分要一样，否则刷新就换榜，很怪 */
  function hash(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 997;
    return h;
  }

  /**
   * 热度分：新鲜度为主，分类权重次之，再看信息完整度。
   * 目的不是"科学排序"，而是让当天真正要紧的事浮在前面——
   * 召回/监管这类错过代价高的内容，比一条品牌软文更值得占据榜首。
   */
  FEED.heat = function (item, tagCounts) {
    const t = Date.parse(item.published_at || item.fetched_at || "");
    const known = !Number.isNaN(t);
    const hours = known ? Math.max(0, (Date.now() - t) / 36e5) : 0;
    let h = 38;
    if (!known) h -= 12; // 时间未知的条目不能靠"分类权重"挤到榜首，读者会以为是新稿
    else if (hours <= 6) h += 44;
    else if (hours <= 24) h += 30;
    else if (hours <= 72) h += 15;
    else if (hours <= 168) h += 6;
    else h -= 20; // 一周以上的老稿（常被翻出来转载）不该跟当天的稿子抢榜

    const tags = item.tags || [];
    for (const tag of tags) h += HEAT_TAGS[tag] || 0;
    if (tags[0] && tagCounts) h += Math.min(10, (tagCounts[tags[0]] || 0) * 0.6);

    if (item.image) h += 7;
    const s = (item.summary || "").length;
    if (s >= 150) h += 11;
    else if (s >= 80) h += 6;
    if ((item.points || []).length) h += 4;
    if (item.priority === "P0") h += 6;
    if (item.body && item.body.length > 600) h += 4;
    // 还没翻成中文的外文条目在榜单上观感很差，降权，等下一轮模型补译文
    if (item.language === "en" && !item.title_zh) h -= 14;

    h += hash(String(item.id || item.title || "")) % 9 - 4;
    return Math.round(h);
  };

  /** 给一批条目算好热度并挂到 _heat 上，返回按热度降序的副本 */
  FEED.rankByHeat = function (items) {
    const tagCounts = {};
    for (const it of items) for (const t of it.tags || []) tagCounts[t] = (tagCounts[t] || 0) + 1;
    const scored = items.map((it) => ({ item: it, heat: FEED.heat(it, tagCounts) }));
    const byHeat = (a, b) => {
      if (b.heat !== a.heat) return b.heat - a.heat;
      return (b.item.published_at || "") > (a.item.published_at || "") ? 1 : -1;
    };
    scored.sort(byHeat);

    // 同源限流：一个站连发五条不该占满榜单。第 n 条起逐步扣分后重排，
    // 这样既保住"热度优先"，又让榜单看起来像多家媒体在说话。
    const seen = {};
    for (const s of scored) {
      const k = s.item.source_id || "?";
      seen[k] = (seen[k] || 0) + 1;
      if (seen[k] > 1) s.heat = Math.round(s.heat - (seen[k] - 1) * 7);
      s.item._heat = s.heat;
    }
    scored.sort(byHeat);
    return scored;
  };

  /* ---------------- 来源徽章 ---------------- */

  const BADGE_COLORS = [
    "#0e5b4f", "#1f5f8b", "#8a5a2b", "#6b4a86", "#a4402f",
    "#2f6b4f", "#7a6330", "#3c5a7a", "#8b3f5e", "#4a5a2f",
  ];

  FEED.badgeColor = function (key) {
    return BADGE_COLORS[hash(String(key || "")) % BADGE_COLORS.length];
  };

  FEED.initial = function (name) {
    const s = String(name || "?").trim();
    // 英文名取首字母，中文取首字
    if (/^[A-Za-z]/.test(s)) return s.slice(0, 1).toUpperCase();
    return s.slice(0, 1);
  };

  /* ---------------- 时间小工具 ---------------- */

  FEED.hm = function (iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  FEED.mdDate = function (iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
  };

  FEED.weekday = function (iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return "星期" + "日一二三四五六".charAt(d.getDay());
  };

  /** 日期键：YYYY-MM-DD，用于按天分组 */
  FEED.dayKey = function (iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "unknown";
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  };

  FEED.escapeHtml = function (s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  };

  window.FeedData = FEED;

  /* ---------------- 两个页面共用的页面行为 ---------------- */

  const THEME_KEY = "milkradar-theme";

  FEED.initTheme = function (btn) {
    function apply(mode) {
      if (mode === "dark") document.documentElement.dataset.theme = "dark";
      else delete document.documentElement.dataset.theme;
      if (btn) {
        btn.textContent = mode === "dark" ? "☀" : "☾";
        btn.title = mode === "dark" ? "切换到浅色" : "切换到深色";
      }
    }
    let mode = "light"; // 默认浅色：读长文更舒服
    try {
      mode = localStorage.getItem(THEME_KEY) || "light";
    } catch (e) {}
    apply(mode);
    if (!btn) return;
    btn.addEventListener("click", () => {
      mode = mode === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(THEME_KEY, mode);
      } catch (e) {}
      apply(mode);
    });
  };

  FEED.initTopbar = function (bar) {
    if (!bar) return;
    const onScroll = () => bar.classList.toggle("is-stuck", window.scrollY > 6);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  };
})();

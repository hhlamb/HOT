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

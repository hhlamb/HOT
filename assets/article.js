/* ============================================================
   站内详情页：按 ?id= 取一条，渲染大图 + AI 速览 + 要点
   + 原文摘录 + 原文信息 + 延伸阅读。不跳外站，英文/长文都在这里读完。
   ============================================================ */

const FD = window.FeedData;

const el = {
  cats: document.getElementById("cats"),
  theme: document.getElementById("theme-btn"),
  article: document.getElementById("article"),
  meta: document.getElementById("a-meta"),
  title: document.getElementById("a-title"),
  titleEn: document.getElementById("a-title-en"),
  hero: document.getElementById("a-hero"),
  heroImg: document.getElementById("a-hero-img"),
  heroCap: document.getElementById("a-hero-cap"),
  ai: document.getElementById("a-ai"),
  summary: document.getElementById("a-summary"),
  points: document.getElementById("a-points"),
  bodyWrap: document.getElementById("a-body-wrap"),
  body: document.getElementById("a-body"),
  bodyNote: document.getElementById("a-body-note"),
  factsWrap: document.getElementById("a-facts-wrap"),
  facts: document.getElementById("a-facts"),
  originText: document.getElementById("a-origin-text"),
  origin: document.getElementById("a-origin"),
  originBox: document.getElementById("a-origin-box"),
  status: document.getElementById("status"),
  related: document.getElementById("related"),
  relatedList: document.getElementById("related-list"),
  extend: document.getElementById("extend"),
  extendLabel: document.getElementById("extend-label"),
  extendList: document.getElementById("extend-list"),
};

FD.initTheme(el.theme);

const SITE = "奶链雷达";

/** 把整段正文切成好读的段落（每段 110~150 字） */
function splitParagraphs(text) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (!clean) return [];
  const sents = clean.match(/[^。！？!?；;]+[。！？!?；;]?/g) || [clean];
  const out = [];
  let cur = "";
  for (const s of sents) {
    if (cur && (cur + s).length > 140) {
      out.push(cur.trim());
      cur = s;
    } else {
      cur += s;
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

const timeOf = (item) => item.published_at || item.fetched_at || "";

async function main() {
  const id = new URLSearchParams(location.search).get("id");
  if (!id) {
    fail("缺少文章编号，请从列表页点进来。");
    return;
  }

  let data;
  try {
    data = await FD.loadAll();
  } catch (err) {
    fail("数据加载失败：" + err.message);
    return;
  }

  const item = data.items.find((i) => String(i.id) === id);
  if (!item) {
    fail("这篇文章不在最近的数据里——可能已超过 90 天保留期，或者链接被截断了。");
    return;
  }

  FD.rankByHeat(data.items); // 让侧栏和延伸阅读也能显示热度
  renderRail(data.items, item);
  render(item, data.items);
}

function fail(msg) {
  el.status.textContent = msg;
  el.article.hidden = true;
}

function renderRail(items, current) {
  const counts = {};
  for (const it of items) for (const t of it.tags || []) counts[t] = (counts[t] || 0) + 1;
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  el.cats.innerHTML = "";
  for (const [tag, n] of sorted) {
    const a = document.createElement("a");
    a.className = "cat-item";
    a.href = "?view=all&tag=" + encodeURIComponent(tag);
    a.setAttribute("aria-current", String((current.tags || []).includes(tag)));
    a.appendChild(document.createTextNode(tag));
    const num = document.createElement("span");
    num.className = "n";
    num.textContent = n;
    a.appendChild(num);
    el.cats.appendChild(a);
  }
}

function render(item, all) {
  const title = item.title_zh || item.title || "（无标题）";
  document.title = title + " · " + SITE;

  /* 顶部元信息 */
  el.meta.innerHTML = "";
  const tag = (item.tags || [])[0];
  if (tag) {
    const span = document.createElement("span");
    span.className = "card-tag" + (tag === "质量安全" ? " card-tag--warn" : "");
    span.textContent = tag;
    el.meta.appendChild(span);
  }
  el.meta.appendChild(text(item.source_name || "未知来源"));
  el.meta.appendChild(sepDot());
  el.meta.appendChild(text(timeOf(item) ? FD.fmtFull(timeOf(item)) : "时间未知"));
  if (item.language === "en") {
    el.meta.appendChild(sepDot());
    el.meta.appendChild(text("原文为英文"));
  }

  el.title.textContent = title;

  const rawTitle = (item.title || "").trim();
  if (rawTitle && rawTitle !== title) {
    el.titleEn.textContent = rawTitle;
    el.titleEn.hidden = false;
  }

  /* 大图 */
  if (item.image && /^https?:/.test(item.image)) {
    el.heroImg.src = item.image;
    el.heroImg.alt = title;
    el.heroImg.addEventListener("error", () => {
      el.hero.hidden = true;
    });
    el.heroCap.textContent = `配图来自 ${item.source_name || "原媒体"}`;
    el.hero.hidden = false;
  }

  /* AI 速览 */
  const norm = (t) => String(t || "").replace(/\s+/g, "").replace(/[|｜\-—:：,，。.]/g, "");
  const summaryText = (item.summary || "").trim();
  const redundant =
    summaryText && (norm(title).includes(norm(summaryText)) || norm(summaryText) === norm(title));
  if (summaryText && !redundant) {
    el.summary.textContent = summaryText;
    el.ai.hidden = false;
  }

  /* 关键要点 */
  const points = (item.points || []).filter((p) => String(p || "").trim());
  if (points.length) {
    for (const p of points) {
      const li = document.createElement("li");
      li.textContent = p;
      el.points.appendChild(li);
    }
    el.points.hidden = false;
    el.ai.hidden = false;
  }

  /* 原文摘录 */
  const paras = splitParagraphs(item.body);
  if (paras.length) {
    for (const p of paras) {
      const node = document.createElement("p");
      node.textContent = p;
      el.body.appendChild(node);
    }
    el.bodyWrap.hidden = false;
    const n = (item.body || "").length;
    el.bodyNote.textContent = `以上为原文关键段落摘录，约 ${n} 字，用于判断是否值得深读；完整内容请点下方原文按钮。`;
  } else {
    // 检索类来源（Google News 跳转、openFDA 接口）拿不到网页正文，说明白免得读者以为页面坏了
    const hint = document.createElement("p");
    hint.className = "body-note";
    hint.textContent = summaryText
      ? "这条来源给的是检索跳转或数据接口，抓不到网页正文。上面的速览 + 下方原文链接足够判断内容。"
      : "这篇还没抓到正文，可以直接查看原文。";
    el.originBox.parentNode.insertBefore(hint, el.originBox);
  }

  /* 原文信息表 */
  const facts = [
    ["来源", item.source_name || "未知"],
    ["发布于", timeOf(item) ? FD.fmtFull(timeOf(item)) : "时间未知"],
    ["收录于", item.fetched_at ? FD.fmtFull(item.fetched_at) : "—"],
    ["分类", (item.tags || []).join(" / ") || "未分类"],
  ];
  if (item.published_guessed) facts.push(["时间说明", "源未给准确时间，按抓取日估算"]);
  if (item.author) facts.push(["作者", item.author]);
  for (const [k, v] of facts) {
    const wrap = document.createElement("div");
    wrap.className = "fact";
    const dt = document.createElement("dt");
    dt.textContent = k;
    const dd = document.createElement("dd");
    dd.textContent = v;
    wrap.appendChild(dt);
    wrap.appendChild(dd);
    el.facts.appendChild(wrap);
  }
  el.factsWrap.hidden = false;

  /* 原文出口 */
  const url = item.original_url || "";
  el.originText.textContent = `原文出处：${item.source_name || "原媒体"}。本站只做聚合与摘要，版权归原媒体所有。`;
  if (url.startsWith("http")) el.origin.href = url;
  else el.origin.hidden = true;

  el.article.hidden = false;
  el.status.textContent = "";

  renderRelated(item, all);
  renderExtend(item, all);
  window.scrollTo(0, 0);
}

/* ------------------------------------------------------------ 相关阅读（卡片） */

function renderRelated(item, all) {
  const tags = item.tags || [];
  const rest = all.filter((i) => i.id !== item.id);
  const byTime = (a, b) => ((b.published_at || "") > (a.published_at || "") ? 1 : -1);

  const sameTag = rest.filter((i) => (i.tags || []).some((t) => tags.includes(t))).sort(byTime);
  const used = new Set(sameTag.slice(0, 3).map((i) => i.id));
  const sameSrc = rest.filter((i) => i.source_id === item.source_id && !used.has(i.id)).sort(byTime);
  const picked = sameTag.slice(0, 3).concat(sameSrc).slice(0, 3);
  if (!picked.length) return;

  for (const rel of picked) {
    const card = document.createElement("a");
    card.className = "card";
    card.href = FD.articleHref(rel.id);
    card.style.marginBottom = "9px";

    const body = document.createElement("div");
    body.className = "card-body";

    if (rel.image) {
      const thumb = document.createElement("div");
      thumb.className = "thumb";
      const img = document.createElement("img");
      img.src = rel.image;
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.alt = "";
      img.addEventListener("error", () => thumb.classList.add("is-broken"));
      thumb.appendChild(img);
      body.appendChild(thumb);
    }

    const main = document.createElement("div");
    main.className = "card-main";

    const top = document.createElement("div");
    top.className = "card-top";
    top.appendChild(text(rel.source_name || ""));
    top.appendChild(sepDot());
    top.appendChild(text(FD.timeAgo(timeOf(rel))));
    main.appendChild(top);

    const h2 = document.createElement("h2");
    h2.textContent = rel.title_zh || rel.title || "";
    main.appendChild(h2);

    body.appendChild(main);
    card.appendChild(body);
    el.relatedList.appendChild(card);
  }
  el.related.hidden = false;
}

/* ------------------------------------------------------------ 延伸阅读（纯列表） */

function renderExtend(item, all) {
  const tags = item.tags || [];
  const seen = new Set([item.id]);
  const rows = [];

  const push = (list, label) => {
    for (const it of list) {
      if (rows.length >= 8) return;
      if (seen.has(it.id)) continue;
      seen.add(it.id);
      rows.push({ item: it, label });
    }
  };

  const byHeat = (a, b) => (b._heat || 0) - (a._heat || 0);

  // 同分类里热度最高的几条：读者真正的"顺着看下去"
  push(
    all
      .filter((i) => (i.tags || []).some((t) => tags.includes(t)))
      .sort(byHeat),
    tags[0] || "同主题"
  );
  // 再用同来源补位，方便"这家媒体最近还说了什么"
  push(all.filter((i) => i.source_id === item.source_id).sort(byHeat), item.source_name || "同源");
  // 最后用其他分类的热点兜底，避免延伸区空着
  push(all.slice().sort(byHeat), "热门");

  if (!rows.length) return;

  el.extendLabel.textContent = `延伸阅读 · 顺着往下看`;
  for (const { item: it, label } of rows) {
    const a = document.createElement("a");
    a.className = "extend-item";
    a.href = FD.articleHref(it.id);

    const src = document.createElement("span");
    src.className = "x-src";
    src.textContent = label;
    src.title = label;

    const t = document.createElement("span");
    t.className = "x-t";
    t.appendChild(document.createTextNode(it.title_zh || it.title || ""));
    const small = document.createElement("small");
    small.textContent = [it.source_name, FD.timeAgo(timeOf(it)), `${it._heat || 0} 热度`]
      .filter(Boolean)
      .join(" · ");
    t.appendChild(small);

    a.appendChild(src);
    a.appendChild(t);
    el.extendList.appendChild(a);
  }
  el.extend.hidden = false;
}

/* ------------------------------------------------------------ 小工具 */

function text(s) {
  const span = document.createElement("span");
  span.textContent = s;
  return span;
}

function sepDot() {
  const s = document.createElement("span");
  s.style.opacity = ".4";
  s.textContent = "·";
  return s;
}

main();

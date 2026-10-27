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
  aiTag: document.getElementById("a-ai-tag"),
  summary: document.getElementById("a-summary"),
  points: document.getElementById("a-points"),
  timeline: document.getElementById("a-timeline"),
  timelineList: document.getElementById("a-timeline-list"),
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

const SITE = "白鲸";

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
/* 原站只给日期时，管线会把它规整成当天 00:00。这里只印日期，
   不印一个假的"00:00"——列表页的时间脊用的是同一套判断。 */
const pubLabel = (iso) => (FD.hasTime(iso) ? FD.fmtFull(iso) : FD.mdDate(iso));

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
  // 与首页同一口径：只统计主分类，各栏相加必须等于总条数。
  // 原来遍历全部 tags，同一篇稿在三个栏目里各计一次，合计 447 > 实际 289 条。
  const counts = {};
  for (const it of items) {
    const t = FD.primaryTag(it);
    counts[t] = (counts[t] || 0) + 1;
  }
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  el.cats.innerHTML = "";
  for (const [tag, n] of sorted) {
    const a = document.createElement("a");
    a.className = "cat-item";
    a.href = "?view=all&tag=" + encodeURIComponent(tag);
    a.dataset.tag = tag;
    // 只标主分类：一篇文章常挂三个标签，全高亮会让人以为同时选中了三个栏目
    a.setAttribute("aria-current", String((current.tags || [])[0] === tag));
    a.appendChild(document.createTextNode(tag));
    const num = document.createElement("span");
    num.className = "n";
    num.textContent = n;
    a.appendChild(num);
    el.cats.appendChild(a);
  }
}

function render(item, all) {
  shownIds.clear();
  shownIds.add(item.id);
  const title = item.title_zh || item.title || "（无标题）";
  document.title = title + " · " + SITE;

  /* 顶部元信息：主分类实色，辅分类描边。
     一条稿常挂两个标签，全都做成一样的淡底小字，读者分不清哪个才是
     它所属的栏目——实色只给主分类，辅分类退成描边，层级一眼就出来。 */
  el.meta.innerHTML = "";
  const tags = item.tags || [];
  if (tags.length) {
    const main = document.createElement("span");
    main.className = "card-tag card-tag--main";
    main.dataset.tag = tags[0];
    main.textContent = tags[0];
    el.meta.appendChild(main);

    for (const t of tags.slice(1)) {
      const sub = document.createElement("span");
      sub.className = "card-tag card-tag--sub";
      sub.dataset.tag = t;
      sub.textContent = t;
      el.meta.appendChild(sub);
    }
  }
  el.meta.appendChild(text(item.source_name || "未知来源"));
  el.meta.appendChild(sepDot());
  el.meta.appendChild(text(timeOf(item) ? pubLabel(timeOf(item)) : "时间未知"));
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

  /* 大图：原文有配图就用原文的；没有就用分类主题插画，但要标明不是原图。
     同一张图在多条里复用 = 站点通用封面（栏目图、分享卡片），不能当配图。 */
  let ownImg = FD.hasOwnImage(item);
  if (ownImg) {
    const reuse = all.filter((i) => i.image === item.image).length;
    if (reuse >= 2) ownImg = false;
    else {
      // 图 URL 里带的上传日期比发布日早一年以上 → 站点固定封面
      const m = /(20\d{2})(\d{2})\d{2}|\/(20\d{2})[\/\-](\d{2})\//.exec(item.image);
      const t = Date.parse(item.published_at || item.fetched_at || "");
      if (m && !Number.isNaN(t)) {
        const d = new Date(t);
        const gap = d.getFullYear() * 12 + d.getMonth() + 1 - (Number(m[1] || m[3]) * 12 + Number(m[2] || m[4]));
        if (gap >= 12) ownImg = false;
      }
    }
  }
  if (ownImg || (item.tags || []).length) {
    /* 详情页大图用分类卡通插画（8 张一套，构图完整、撑得住 660px）。
       原文配图存在就先用原文的，加载失败再退回同一套卡通——
       不退回"逐条线稿"，否则列表是插画、点进来变线稿，两套风格打架。 */
    const art = FD.topicCover(item);
    el.heroImg.src = ownImg ? item.image : art;
    el.heroImg.alt = title;
    el.hero.classList.toggle("hero-img--topic", !ownImg);
    el.hero.classList.toggle("hero-img--art", !ownImg);
    let tried = false;
    el.heroImg.addEventListener("error", () => {
      if (!tried && el.heroImg.src !== art) {
        tried = true;
        el.heroImg.src = art;
        el.hero.classList.add("hero-img--topic", "hero-img--art");
        el.heroCap.textContent = "主题配图 · 按分类绘制的示意图，非原文配图";
        return;
      }
      el.hero.hidden = true;
    });
    el.heroCap.textContent = ownImg
      ? `配图来自 ${item.source_name || "原媒体"}`
      : "主题配图 · 按分类绘制的示意图，非原文配图";
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

  /* 原文抓不到时，摘要就是唯一信息源，必须说清楚它是 AI 归纳 */
  const bodyLen = (item.body || "").trim().length;
  const noBody = bodyLen < 200;
  if (noBody && summaryText) {
    el.aiTag.textContent = "AI 归纳";
    el.aiTag.hidden = false;
  } else {
    el.aiTag.hidden = true;
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
    // 检索类来源（Google News 跳转、数据接口）和需要登录/纯图片的站拿不到网页正文。
    // 与其空着，不如把这一条在"事件脉络"里的位置摆出来，让人顺着看前后进展。
    const hint = document.createElement("p");
    hint.className = "body-note body-note--muted";
    hint.textContent = summaryText
      ? "原文站点没有可直接抓取的正文（可能是图片长图、需登录或触发了反爬），上方速览是根据标题与可获得的信息做的 AI 归纳，下方可查看事件脉络与原文。"
      : "这篇还没抓到正文，可以直接查看原文。";
    el.originBox.parentNode.insertBefore(hint, el.originBox);
  }

  /* 原文信息表 */
  const facts = [
    ["来源", item.source_name || "未知"],
    ["发布于", timeOf(item) ? pubLabel(timeOf(item)) : "时间未知"],
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
  renderTimeline(item, all);
  window.scrollTo(0, 0);
}

/* 同一篇稿不该在"相关阅读""延伸阅读""事件脉络"里重复出现三次 */
const shownIds = new Set();

/* ------------------------------------------------------------ 事件脉络 */

/* 标题里出现、但完全没有区分度的词：只靠分类凑数会让"脉络"变成"随机推荐"。 */
const TL_STOP = new Set(
  (
    "奶粉 婴配粉 婴幼儿 婴儿 配方 市场 行业 企业 公司 品牌 增长 发展 中国 母婴 营养 产品 " +
    "消费 全球 今年 去年 季度 数据 报告 显示 表示 已经 正在 可以 如何 什么 为什么 我们 他们 " +
    "一个 一家 这些 那些 以及 但是 因为 所以 目前 未来 继续 开始 进行 通过 相关 主要 重要"
  ).split(" ")
);

/* 两字滑窗取实体词。中文标题没有空格，靠停用词表过滤掉通用词之后，
   "蒙牛""特医""雀巢""羊奶"这类才是能串起一条脉络的词。 */
function tlEntities(text) {
  const set = new Set();
  const s = String(text || "").replace(/[^\u4e00-\u9fffA-Za-z0-9]+/g, " ");
  for (const seg of s.split(" ")) {
    if (/^[A-Za-z0-9]+$/.test(seg)) {
      if (seg.length >= 2) set.add(seg.toLowerCase());
      continue;
    }
    for (let i = 0; i + 2 <= seg.length; i++) {
      const w = seg.slice(i, i + 2);
      if (!TL_STOP.has(w)) set.add(w);
    }
  }
  return set;
}

function tlShare(a, b) {
  let n = 0;
  for (const w of a) if (b.has(w)) n++;
  return n;
}

/* 标题归一化：去掉空白与标点后取前 24 个字。
   转载稿的标题常常只差一个"- 某网"后缀或几个空格，按 id 去重拦不住，
   同一件事会在"事件脉络"里连出三条一样的标题，看着像 bug。 */
function tlTitleKey(item) {
  return String(item.title_zh || item.title || "")
    .replace(/[\s\u3000·—\-–|｜:：,，。.、!！?？"'“”‘’()（）\[\]【】]/g, "")
    .slice(0, 24);
}

/* 同一分类 + 标题有共同实体词，才算"同一件事的进展"。
   原文抓不到时，这是读者唯一能获得上下文的途径；能抓到正文时也有用——
   一件事从发酵到落地往往跨好几天、好几家媒体。 */
function renderTimeline(item, all) {
  const tags = item.tags || [];
  if (!tags.length || !all || all.length < 4) {
    el.timeline.hidden = true;
    return;
  }

  const cur = Date.parse(item.published_at || item.fetched_at || "") || 0;
  const mine = tlEntities(item.title_zh || item.title);
  const mineKey = tlTitleKey(item);
  const titleKeys = new Set([mineKey]);

  const pool = all
    .filter((i) => i.id !== item.id && !shownIds.has(i.id))
    .filter((i) => (i.tags || []).some((t) => tags.includes(t)))
    .map((i) => ({
      item: i,
      t: Date.parse(i.published_at || i.fetched_at || "") || 0,
      key: tlTitleKey(i),
      share: tlShare(mine, tlEntities(i.title_zh || i.title)),
    }))
    // 两个共同二字组才算一件事：只重合一个（"乳业""奶粉"）会把无关的稿子也串进来，
    // 脉络就退化成"同分类随机推荐"了。
    .filter((x) => x.t && x.share >= 2)
    // 标题级去重：同一件事的不同转载只留一条
    .filter((x) => {
      if (titleKeys.has(x.key)) return false;
      titleKeys.add(x.key);
      return true;
    })
    .sort((a, b) => b.share - a.share || Math.abs(a.t - cur) - Math.abs(b.t - cur))
    .slice(0, 6)
    .sort((a, b) => a.t - b.t); // 头部按时间正序摆，读起来才像脉络

  if (pool.length < 2) {
    el.timeline.hidden = true;
    return;
  }

  el.timelineList.innerHTML = "";
  for (const { item: other, t } of pool) {
    shownIds.add(other.id);
    const li = document.createElement("li");
    li.className = "tl-item";

    const when = document.createElement("span");
    when.className = "tl-date";
    when.textContent = FD.mdDate(new Date(t).toISOString());
    li.appendChild(when);

    const a = document.createElement("a");
    a.className = "tl-title";
    a.href = FD.articleHref(other.id);
    a.textContent = other.title_zh || other.title || "（无标题）";
    li.appendChild(a);

    const src = document.createElement("span");
    src.className = "tl-src";
    src.textContent = other.source_name || "";
    li.appendChild(src);

    el.timelineList.appendChild(li);
  }
  el.timeline.hidden = false;
}

/* ------------------------------------------------------------ 相关阅读（卡片） */

function renderRelated(item, all) {
  const tags = item.tags || [];
  const rest = all.filter((i) => i.id !== item.id);
  const byTime = (a, b) =>
    (Date.parse(b.published_at || b.fetched_at || "") || 0) -
    (Date.parse(a.published_at || a.fetched_at || "") || 0);

  const sameTag = rest.filter((i) => (i.tags || []).some((t) => tags.includes(t))).sort(byTime);
  const used = new Set(sameTag.slice(0, 3).map((i) => i.id));
  const sameSrc = rest.filter((i) => i.source_id === item.source_id && !used.has(i.id)).sort(byTime);
  const picked = sameTag.slice(0, 3).concat(sameSrc).slice(0, 3);
  if (!picked.length) return;

  for (const rel of picked) {
    shownIds.add(rel.id);
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

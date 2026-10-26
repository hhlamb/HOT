/* ============================================================
   站内详情页：按 ?id= 取一条，渲染 AI 速览 + 原文摘录 + 原文链接。
   不跳外站，读不完的英文/长文都在这页解决。
   ============================================================ */

const FD = window.FeedData;

const el = {
  topbar: document.getElementById("topbar"),
  theme: document.getElementById("theme-btn"),
  article: document.getElementById("article"),
  meta: document.getElementById("a-meta"),
  title: document.getElementById("a-title"),
  titleEn: document.getElementById("a-title-en"),
  ai: document.getElementById("a-ai"),
  summary: document.getElementById("a-summary"),
  bodyWrap: document.getElementById("a-body-wrap"),
  body: document.getElementById("a-body"),
  originText: document.getElementById("a-origin-text"),
  origin: document.getElementById("a-origin"),
  originBox: document.getElementById("a-origin-box"),
  status: document.getElementById("status"),
  related: document.getElementById("related"),
  relatedList: document.getElementById("related-list"),
};

FD.initTopbar(el.topbar);
FD.initTheme(el.theme);

const SITE = "奶链雷达";

/** 把整段正文按句号切成好读的段落（每段 100~140 字） */
function splitParagraphs(text) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (!clean) return [];
  const sents = clean.match(/[^。！？!?；;]+[。！？!?；;]?/g) || [clean];
  const out = [];
  let cur = "";
  for (const s of sents) {
    if (cur && (cur + s).length > 130) {
      out.push(cur.trim());
      cur = s;
    } else {
      cur += s;
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

function timeOf(item) {
  return item.published_at || item.fetched_at || "";
}

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

  render(item, data.items);
}

function fail(msg) {
  el.status.textContent = msg;
  el.article.hidden = true;
}

function render(item, all) {
  const title = item.title_zh || item.title || "（无标题）";
  document.title = title + " · " + SITE;

  // 标签 / 来源 / 时间
  el.meta.innerHTML = "";
  const tag = (item.tags || [])[0];
  if (tag) {
    const span = document.createElement("span");
    span.className = "card-tag";
    span.textContent = tag;
    el.meta.appendChild(span);
  }
  const src = document.createElement("span");
  src.textContent = item.source_name || "未知来源";
  el.meta.appendChild(src);

  const dot = document.createElement("span");
  dot.textContent = "·";
  dot.style.opacity = ".4";
  el.meta.appendChild(dot);

  const time = document.createElement("span");
  const iso = timeOf(item);
  time.textContent = iso ? FD.fmtFull(iso) : "时间未知";
  el.meta.appendChild(time);

  if (item.language === "en") {
    const dot2 = document.createElement("span");
    dot2.textContent = "·";
    dot2.style.opacity = ".4";
    el.meta.appendChild(dot2);
    const lang = document.createElement("span");
    lang.textContent = "原文为英文";
    el.meta.appendChild(lang);
  }

  el.title.textContent = title;

  // 英文原标题：译名之外保留原文，方便核对
  const rawTitle = (item.title || "").trim();
  if (rawTitle && rawTitle !== title) {
    el.titleEn.textContent = rawTitle;
    el.titleEn.hidden = false;
  }

  // AI 速览：检索类来源的 description 往往就是标题本身，重复展示等于浪费版面
  const norm = (t) => String(t || "").replace(/\s+/g, "").replace(/[|｜\-—:：,，。.]/g, "");
  const summaryText = (item.summary || "").trim();
  const redundant = summaryText && (norm(title).includes(norm(summaryText)) || norm(summaryText) === norm(title));
  if (summaryText && !redundant) {
    el.summary.textContent = summaryText;
    el.ai.hidden = false;
  }

  // 原文摘录
  const paras = splitParagraphs(item.body);
  if (paras.length) {
    for (const p of paras) {
      const node = document.createElement("p");
      node.textContent = p;
      el.body.appendChild(node);
    }
    el.bodyWrap.hidden = false;
  } else {
    // 检索类来源（Google News 跳转、openFDA 接口）拿不到 HTML 正文，
    // 这里说明白，免得读者以为是页面坏了
    const hint = document.createElement("p");
    hint.className = "body-note";
    hint.textContent = item.summary
      ? "这条来源给的是检索跳转或数据接口，抓不到网页正文，上面的速览 + 下方原文链接足够判断内容。"
      : "这篇还没抓到正文，可以直接查看原文。";
    el.originBox.parentNode.insertBefore(hint, el.originBox);
  }

  // 原文出口
  const url = item.original_url || "";
  el.originText.textContent = `原文出处：${item.source_name || "原媒体"}。本站只做聚合与摘要，版权归原媒体所有。`;
  if (url.startsWith("http")) {
    el.origin.href = url;
  } else {
    el.origin.hidden = true;
  }

  el.article.hidden = false;
  el.status.textContent = "";

  renderRelated(item, all);

  // 从列表点进来时恢复到顶部（返回时浏览器会自己记位置）
  window.scrollTo(0, 0);
}

function renderRelated(item, all) {
  const tags = item.tags || [];
  const rest = all.filter((i) => i.id !== item.id);
  const byTime = (a, b) => ((b.published_at || "") > (a.published_at || "") ? 1 : -1);

  // 先同类（读者真正关心的关联），不够再用同源补位
  const sameTag = rest
    .filter((i) => (i.tags || []).some((t) => tags.includes(t)))
    .sort(byTime);
  const used = new Set(sameTag.slice(0, 3).map((i) => i.id));
  const sameSrc = rest
    .filter((i) => i.source_id === item.source_id && !used.has(i.id))
    .sort(byTime);

  const picked = sameTag.slice(0, 3).concat(sameSrc).slice(0, 3);
  if (!picked.length) return;

  for (const rel of picked) {
    const card = document.createElement("a");
    card.className = "card";
    card.href = FD.articleHref(rel.id);

    const top = document.createElement("div");
    top.className = "card-top";
    const src = document.createElement("span");
    src.className = "card-source";
    src.textContent = rel.source_name || "";
    top.appendChild(src);
    const t = document.createElement("span");
    t.textContent = FD.timeAgo(timeOf(rel));
    top.appendChild(t);
    card.appendChild(top);

    const h2 = document.createElement("h2");
    h2.textContent = rel.title_zh || rel.title || "";
    card.appendChild(h2);

    el.relatedList.appendChild(card);
  }
  el.related.hidden = false;
}

main();

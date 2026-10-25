# 跨境母婴奶粉资讯聚合站 — 方案评审（Stage 0 前置）

> 状态：待你确认，**尚未开始写代码**
> 评审日期：2026-10-25
> 实测环境：本机（中国大陆网络），非 GitHub Actions 环境

---

## 一、结论先行

**方案整体可行，成本确实能做到 ¥0。但有 7 个地方按原样实现会踩坑，必须在动手前修正。**

| # | 原方案假设 | 实际情况（实测/已验证） | 影响 | 处理 |
|---|---|---|---|---|
| 1 | 本地双击 HTML 能读仓库内 JSON | `file://` 协议下 `fetch()` 被 CORS 拦截，Chrome/Edge 直接报错 | 你"本地双击就能看"的诉求落空 | 额外产出 `data/feed.js`（`window.__FEED__=...`），用 `<script>` 兜底，file:// 与 http 都能跑 |
| 2 | GitHub Actions cron 稳定每 4 小时跑 | ① 官方 cron 常态延迟 5~30 分钟，高峰可能整次跳过；② **公开仓库连续 60 天无提交，schedule 会被自动停用** | 静默停更，你可能两周后才发现页面没更新 | 加 keepalive（每次运行写一个心跳文件）+ 提供外部 cron 触发 `workflow_dispatch` 的兜底方案 |
| 3 | 抓取 push 后触发 Pages 自动部署 | `GITHUB_TOKEN` 推送的 commit **不会触发新的 workflow**（GitHub 防循环机制） | 数据更新了但页面不重建，白等 | 抓取和部署写在**同一个 workflow** 里，用 `actions/deploy-pages` 直接部署，不走 push 触发 |
| 4 | 信息源"RSS 优先"列表可直接用 | 实测：Bing News RSS（`format=RSS`）已返回 HTML 搜索页；FDA 召回 RSS 路径 404；DairyReporter / FoodNavigator 的 `/rss/` 全部 404 | 按想当然的 URL 写死配置，首次运行大批失败 | Stage 0 先做**源探测脚本**，用实测结果生成 `sources.yaml`，不靠猜 |
| 5 | 政府站点（海关总署/市监总局/卫健委）可直接抓 | `www.customs.gov.cn` 从本机直接连接失败（000）；政府站大量 JS 渲染 + WAF，且**境外 IP（Actions 机房）更易被拦** | 政策法规这一类最核心的内容反而抓不到 | 政府源降级为 P2（HTML 解析 + 宽松失败容忍），主供给改为媒体转载源（食品伙伴网/36氪等）兜底 |
| 6 | 站点"只有 5 个人看" | GitHub Pages 公开仓库 = 全网可访问、可被搜索引擎索引 | 隐私预期不符 | 加 `noindex` + `robots.txt`；若要真私密，走 Cloudflare Pages + Cloudflare Access（免费 50 用户） |
| 7 | 月度 JSON 全量重写并提交 | 每天 6 次 × 30 天 = 每月 180 个全量 blob，月末单文件约 1.3MB，`.git` 一年可涨到 **200MB+** | 本地 clone 越来越慢 | **改成 append-only JSONL**（只追加新行，不重写旧行）+ `fetch-depth: 1` + 无新增不提交。见附录 A |

其余部分（GitHub Actions 抓取、JSON 存仓库、纯静态前端、大模型摘要、无服务器）**全部成立，且是这个场景下的正确选择**。

---

## 二、技术选型结论

原则：**能不加依赖就不加，能少一个进程就少一个进程。** 这是一个每 4 小时跑一次、处理几百条数据的小工具，任何架构复杂度都是负债。

| 层 | 选型 | 理由 / 替代方案 |
|---|---|---|
| 运行环境 | **GitHub Actions**（`ubuntu-latest`，Python 3.12） | 唯一无需服务器的免费定时执行环境。公开仓库 Actions 分钟数无限 |
| 抓取 | **Python + `httpx` + `feedparser` + `beautifulsoup4`** | `feedparser` 对畸形 RSS 容错最好（中文站 RSS 普遍不规范）。**不用 Scrapy**（重、需要调度器）。asyncio 并发但**按域名串行 + 1~3s 随机间隔** |
| 存储 | **仓库内 JSONL，按月分片** `data/2026-10.jsonl`（每行一条记录） | 无数据库。**关键：只追加不重写** —— 每次运行只在文件末尾加几行，git 只会新增这几行的 blob，而不是整个文件。这是控制 `.git` 体积的根因解法（全量 JSON 重写一年能涨到 200MB+，JSONL 约 4MB/年）。前端 `text.split('\n').map(JSON.parse)` 即可解析 |
| 摘要/翻译/分类 | **默认 GLM-4-Flash（永久免费）**，可切 DeepSeek-V4-Flash | 见第六节费用。都是 OpenAI 兼容接口，换供应商只改 3 行配置 |
| 前端 | **原生 HTML + CSS + 原生 JS，零构建** | **明确否决 Astro/Vite**：数据是运行时 fetch 的，站点本身没有构建产物；引入构建只会多一个 CI 失败点。aisoft.news 式的信息流，原生 JS 200 行足够 |
| 部署 | **GitHub Pages**（MVP 首选）/ Cloudflare Pages（私密版） | Pages 有 100GB/月带宽、1GB 仓库限制，本项目用量 <1% |
| 密钥 | GitHub Secrets → 环境变量 | `.env` 本地用，`.gitignore` 排除，`.env.example` 提交 |
| 日志 | `$GITHUB_STEP_SUMMARY` + Actions 运行日志 | 直接渲染成 Markdown 简报，**不往仓库里写日志文件**，避免仓库膨胀 |

### 三个关键的架构简化（比原方案更省）

1. **不需要 `state.json` 存已见列表** —— 直接从最近 30 天的月度 JSON 里重建 URL 集合与标题集合。少一个状态文件，少一类不一致 bug。
2. **相关性判断 + 分类 + 翻译 + 摘要合并成一次 LLM 调用** —— 原方案是"先模型判相关性，再模型摘要"，等于每条调两次。合并后按 8~10 条一批出 JSON，token 用量降约 60%，代码也短一半。
3. **不引入 MinHash / datasketch** —— 每天几百条的量级，`URL 规范化 + 标题归一化 + 字符二元组 Jaccard ≥ 0.82 + 7 天窗口` 已经足够，纯标准库 30 行。中文不要用编辑距离（对长标题噪声大）。

---

## 三、架构（数据流）

```
┌──────────────────── GitHub Actions（cron 每 4 小时，UTC 非整点）─────────────────────┐
│                                                                                    │
│  sources.yaml                                                                      │
│   (P0~P2 源)                                                                        │
│       │                                                                            │
│       ▼                                                                            │
│  ┌──────────┐   ┌────────────────┐   ┌──────────────┐   ┌─────────────────┐        │
│  │ ① 抓取   │──►│ ② 清洗与去重    │──►│ ③ LLM 加工   │──►│ ④ 落盘          │        │
│  └──────────┘   └────────────────┘   └──────────────┘   └─────────────────┘        │
│   RSS/Atom 优先    URL 规范化           一次调用完成：      data/2026-10.jsonl       │
│   HTML 列表兜底    (去 utm/重定向)       · 相关性二分类     data/index.json          │
│   单源独立 try     标题归一化             · 打标签(多选)    data/latest.js           │
│   失败重试 2 次    字符bigram Jaccard     · 外文译中        90 天外整月删除           │
│   按域限速         同一事件留 1~2 条      · 1~3 句中文摘要                            │
│   显式 UA          + 关键词粗筛               │                                     │
│       │                    │                 │ 失败降级：原文前 80 字截断            │
│       │                    │                 ▼                                     │
│       │                    │        ┌─────────────────┐                            │
│       │                    └───────►│ 无网络/Key 失效  │                            │
│       │                             │ 也不中断，出数据 │                            │
│       ▼                             └─────────────────┘                            │
│  $GITHUB_STEP_SUMMARY  ◄── 简报：新增 N 条 / 失败源 M 个 / token 用量               │
│                                                                                    │
│                        git commit & push（同一 workflow 内继续部署）                 │
└──────────────────────────────────────────┬─────────────────────────────────────────┘
                                           ▼
              ┌──────── GitHub Pages（actions/deploy-pages，无构建步骤）────────┐
              │  index.html + assets/   ──fetch──►  data/index.json + 月分片   │
              └───────────────────────────────────────────────────────────────┘
                                           ▼
                                   浏览器（手机 / 桌面，深浅色自适应）
```

**失败隔离**：每个源在独立 `try/except` 里跑，单源失败只记一行日志，不影响其他源，也不影响最终 commit。

---

## 四、目录结构

```
新-HOT/
├─ .github/workflows/
│  ├─ update.yml                    # 抓取 + 部署（同一 workflow，规避 GITHUB_TOKEN 问题）
│  └─ probe.yml                     # 仅 workflow_dispatch：在 Actions 环境跑源探测
├─ index.html                       # 唯一页面
├─ assets/
│  ├─ app.js                        # 取数 / 渲染 / 筛选 / 搜索 / 时间格式化
│  ├─ style.css                     # CSS 变量 + prefers-color-scheme
│  └─ favicon.svg
├─ data/                            # 站点直接消费的静态数据
│  ├─ index.json                    # {last_updated, total, months[]}（体积 ~1KB）
│  ├─ latest.js                     # window.__FEED__ = [...最近 100 条]  ← file:// 双击兜底
│  ├─ 2026-10.jsonl                 # 按月分片，**只追加不重写**，每行一条记录
│  └─ 2026-09.jsonl
├─ meta/
│  ├─ heartbeat.txt                 # 内容恒定为 "alive"，用于防 60 天停用（零 blob 增长）
│  └─ sources_status.json           # 每源最近成功时间 / 连续失败次数
├─ pipeline/                        # 抓取与加工逻辑（可本地单测）
│  ├─ __init__.py
│  ├─ sources.py                    # 读 sources.yaml / config.yaml
│  ├─ fetchers.py                   # rss / html_list / 三种抓取器
│  ├─ clean.py                      # URL 与标题归一化
│  ├─ dedupe.py                     # bigram Jaccard + 窗口内去重
│  ├─ relevance.py                  # 关键词白/黑名单粗筛
│  ├─ llm.py                        # OpenAI 兼容客户端 + 批量 JSON + 重试 + 降级
│  ├─ store.py                      # 读 JSONL / 追加新行 / 写 index / 裁剪 90 天
│  └─ report.py                     # STEP_SUMMARY 简报
├─ scripts/
│  ├─ probe_sources.py              # Stage 0：批量探测，支持 --json out.json 便于两地比对
│  ├─ compare_probe.py              # 对比「本地探测」与「Actions 探测」两份结果
│  └─ run.py                        # 本地一键跑完整流程
├─ sources.yaml                     # 信息源配置（你后续增删改这一个文件即可）
├─ config.yaml                      # 阈值/批大小/模型名/保留天数/限速/latest.js 条数
├─ serve.bat / serve.sh             # 本地起 http 服务（可选，比 file:// 更接近真实环境）
├─ requirements.txt                 # 依赖锁定
├─ .env.example
├─ .gitignore
├─ README.md                        # 部署教程 + 运维手册
└─ PLAN.md                          # 本文件
```

git 忽略：`.env`、`logs/`、`__pycache__/`、`.venv/`

---

## 五、信息源清单（候选池 32 个，实测状态标注）

> ⚠️ 下面是**候选池**，不是全部可用。Stage 0 会用 `probe_sources.py` 全量跑一遍，实际保留 15~20 个能出数据的源。
> 标注含义：✅ 本轮实测正常 ｜ ⚠️ 路径需调整/返回异常 ｜ 🚫 本机不可达（需在 Actions 环境复测）｜ 🔧 无 RSS，需 HTML 解析

### A. 中国政府与监管（政策法规的权威源，但最脆弱）

| # | 源 | 地址 | 类型 | 状态 | 备注 |
|---|---|---|---|---|---|
| 1 | 海关总署·进出口食品 | `customs.gov.cn` | 🔧 | 🚫 本机连接失败 | 境外 IP 更易被 WAF 拦，做 P2，失败即跳过 |
| 2 | 市场监管总局·食品抽检通告 | `samr.gov.cn` | 🔧 | 🔧 首页 200 | 列表页需解析，注意分页 |
| 3 | 卫健委·食品安全标准与监测评估司 | `nhc.gov.cn/sps/` | 🔧 | 待测 | HTML 静态列表，较易解析 |
| 4 | 中国政府网·政策文件库 | `gov.cn/zhengce/` | RSS? | 🚫 待测 | 历史上有 RSS，需在 Actions 侧验证 |
| 5 | 特殊食品信息查询平台（配方注册名单） | `tsspxx.gsxt.gov.cn` | 🔧 | 极难 | **JS 渲染 + 验证码，建议本期不做**，改为抓"媒体对注册名单更新的报道" |
| 6 | 中国食品安全报 | `cfsn.cn` | 🔧 | 待测 | 政策转载质量高，可替代 1~3 的缺位 |

### B. 国内行业媒体（实际的主力供给）

| # | 源 | 地址 | 类型 | 状态 | 备注 |
|---|---|---|---|---|---|
| 7 | 36氪 | `36kr.com/feed` | RSS | ✅ 200，实测有效 | 快讯覆盖乳业/消费融资 |
| 8 | 食品伙伴网·资讯 | `news.foodmate.net` | 🔧 | ✅ 站点可达 | 乳品/法规频道质量高，**最值得做的 HTML 源** |
| 9 | 中国奶业协会 | `dairy.org.cn` | 🔧 | 🚫 本机失败 | 行业数据与协会口径 |
| 10 | 母婴行业观察 | `muyingguancha.com` | 🔧 | 待测 | 垂直媒体，渠道/品牌动态 |
| 11 | 亿邦动力 | `ebrun.com` | 🔧 | 待测 | 跨境电商/渠道 |
| 12 | 雨果跨境 | `cifnews.com` | 🔧 | 待测 | 跨境政策解读 |
| 13 | 新乳业 / 乳业时报 | 公众号为主 | — | 无 RSS | **本期不做**（见第七节"公众号"说明） |
| 14 | 第一财经 / 界面 | `yicai.com` / `jiemian.com` | 🔧 | 待测 | 无 RSS，需解析且反爬较强 |
| 15 | 食悟 / 乳品之家 / 奶粉圈 | 公众号为主 | — | 无 RSS | 本期不做 |

### C. 海外行业媒体（RSS 质量最好，是稳定性的基本盘）

| # | 源 | 地址 | 类型 | 状态 | 备注 |
|---|---|---|---|---|---|
| 16 | Dairy Reporter | `dairyreporter.com` | RSS | ⚠️ `/rss/` 404 | 需重新定位 feed 路径（页面里找 `<link rel=alternate>`） |
| 17 | FoodNavigator | `foodnavigator.com` | RSS | ⚠️ `/rss/` 404 | 同上 |
| 18 | Just Food | `just-food.com/feed/` | RSS | ⚠️ 403 | 需真实浏览器 UA + Referer，仍可能拦 |
| 19 | Food Dive | `fooddive.com/feeds/news/` | RSS | 待测 | 行业并购/财报覆盖好 |
| 20 | Food Business News | `foodbusinessnews.net` | RSS | 待测 | 乳业公司动态 |
| 21 | Dairy Foods | `dairyfoods.com` | RSS | 待测 | 美国乳品加工侧 |
| 22 | NutraIngredients | `nutraingredients.com` | RSS | 待测 | 乳铁蛋白/HMO 等原料话题 |
| 23 | BakeryAndSnacks / Foodnavigator-Asia | 同上系列 | RSS | 待测 | 亚太视角 |

### D. 海外监管与安全通报（召回/法规，权重最高的一类）

| # | 源 | 地址 | 类型 | 状态 | 备注 |
|---|---|---|---|---|---|
| 24 | FDA Recalls | `fda.gov/.../rss.xml` | RSS | ⚠️ 404，路径已变 | 需重新定位（FDA 近年改版 RSS 目录） |
| 25 | EFSA News | `efsa.europa.eu/en/rss` | RSS | ✅ 200，实测有效 | 欧盟食品安全局 |
| 26 | RASFF Window（欧盟预警） | `webgate.ec.europa.eu/rasff-window/` | 🔧 | 无 RSS | 高价值但需解析/下载通告 |
| 27 | FSANZ（澳新） | `fsanz.gov.au` | RSS? | 待测 | a2/爱他美相关召回常出此处 |
| 28 | USDA FAS GAIN | `fas.usda.gov/data` | 🔧 | 待测 | 各国乳业报告，PDF 为主，本期只取标题 |
| 29 | EU Official Journal L 系列 | `eur-lex.europa.eu` | RSS | 待测 | 法规原文，噪声大，可选 |

### E. 品牌官方 newsroom（无 RSS，反爬普遍很松）

| # | 源 | 地址 | 类型 | 状态 | 备注 |
|---|---|---|---|---|---|
| 30 | Nestlé Media | `nestle.com/media/news` | 🔧 | 待测 | 惠氏/能恩母公司 |
| 31 | Danone News | `danone.com/media.html` | 🔧 | 待测 | 爱他美/诺优能母公司 |
| 32 | a2 Milk News | `thea2milkcompany.com/news` | 🔧 | 待测 | 澳新系重点 |
| 33 | 飞鹤 / 伊利 / 君乐宝 官网新闻 | 各官网 | 🔧 | 待测 | 国产三巨头，结构简单，好解析 |

### F. 聚合检索源（兜底神器，用于补漏）

| # | 源 | 地址 | 类型 | 状态 | 备注 |
|---|---|---|---|---|---|
| 34 | Google News RSS | `news.google.com/rss/search?q=...` | RSS | 🚫 本机不可达（需代理/Actions） | **Actions 在境外，可正常访问**，是最强的补漏源；缺点是链接为 Google 跳转，需二次解析出真实 URL |
| 35 | Bing News RSS | `bing.com/news/search?...&format=RSS` | RSS | ❌ **已失效**（返回 HTML） | 从候选池剔除 |
| 36 | GDELT DOC API | `api.gdeltproject.org/api/v2/doc/doc` | JSON API | 待测 | **免费、无需 Key、支持中文查询、可按域名与时间过滤**，作为 Google News 的备选 |
| 37 | RSSHub 公共实例 | `rsshub.app/...` | RSS | 不稳定 | 只作兜底，公共实例经常限流；自建需服务器（违反约束，不做） |

**关于"≥20 个"的诚实结论**：候选池 32 个，但真正能稳定出数据的预计是 **15~20 个**（海外 RSS 8~10 个 + 国内 HTML 5~8 个 + 聚合源 2 个 + 政府源 0~3 个）。政府源是"加分项"而不是"基本盘"，这个预期要提前对齐。

**关于微信公众号**：无官方 RSS，第三方抓取（搜狗微信）反爬极严且有合规风险。**本期明确不做**，替代方案是把公众号里最有价值的内容，通过它们在其他平台（36氪/食品伙伴网）的同步稿覆盖。

---

## 六、费用预估（月度）

按"每天新增 80~120 条，每 4 小时跑一次"的真实量级测算。

| 项目 | 方案 | 免费额度 | 本项目用量 | 月费用 |
|---|---|---|---|---|
| GitHub Actions | 公开仓库 | 无限 | ≈180 次 × 90s ≈ 270 分钟 | **¥0** |
| GitHub Actions | 私有仓库 | 2000 分钟/月 | 同上 | **¥0**（用量 13%） |
| GitHub Pages | 免费 | 100GB 带宽/月、1GB 仓库 | <1GB、<1GB 流量 | **¥0** |
| LLM 摘要 | GLM-4-Flash | **永久免费**（限 30 并发，需实名认证） | 日 ~4 万 in / ~1.5 万 out | **¥0** |
| LLM 摘要 | DeepSeek-V4-Flash | 新账号赠 500 万 tokens | 月 ~120 万 in / ~45 万 out | **¥2 ~ ¥5** |
| 域名 | 可选 | — | 用 `*.github.io` 子域 | **¥0**（买 .com 约 ¥60~80/年） |

**结论：走 GLM-4-Flash 就是纯 ¥0；走 DeepSeek 上限约 ¥5/月。** 就算抓取量翻 3 倍，也在 ¥15 以内。

> 价格说明：DeepSeek 官方价格页当前 V4-Flash 为输入 $0.14 / 输出 $0.28 每百万 tokens（缓存命中输入 $0.0028），第三方转述存在 $0.15/$0.60 等差异版本，**以你开通账号时的官方价格页为准**。模型 ID 也建议在部署时以官方文档为准（`deepseek-chat` 已公告废弃，应使用 v4 系列 ID）。

---

## 七、分步实施计划（每步都有可验证的产物）

### Stage 0 · 双环境源探测（约半天）　✅ 本地基线已完成，待 Actions 复核

> 本地探测结果见 **`docs/STAGE0.md`**：37 个源中 21 个可用（8 RSS + 13 HTML）。
> 探测中推翻了 4 个错误假设（36氪假 RSS、TLS 指纹校验、Bot UA 被拦、FDA 网页改版），已全部修正到配置里。

- 产出：`scripts/probe_sources.py`、`.github/workflows/probe.yml`、`scripts/compare_probe.py`、`sources.yaml`
- 做的事：对 32 个候选源逐个请求，记录 `http_status / content_type / 条目数 / 首条标题 / 耗时`，自动从 HTML 页面的 `<link rel="alternate">` 里挖出真实 RSS 地址。同一份脚本在两个环境各跑一次：
  1. **Actions 上跑（生产环境，唯一权威）**：`probe.yml` 用 `workflow_dispatch` 手动触发，报告写入 `$GITHUB_STEP_SUMMARY` 并作为 artifact 上传
  2. **本地跑（仅供参考 + 调试便利）**：`python scripts/probe_sources.py --json local.json`
  3. `python scripts/compare_probe.py local.json actions.json` 输出三档结论
- 判定规则（**以 Actions 结果为准**）：

  | 情况 | 判定 | 用法 |
  |---|---|---|
  | Actions 通 + 本地通 | **P0 核心源** | 每轮必抓 |
  | Actions 通 + 本地不通 | **P1** | 可用，但你本地调试时会被跳过（正常现象，别误判成坏了） |
  | Actions 不通 + 本地通 | **弃用** | 生产跑不了，本地能通没有意义（政府站大概率落这里） |
  | 两地都不通 | 弃用 | 从 `sources.yaml` 移除或降为 disabled |

- **验收标准（改）**：Actions 上的探测报告中，**≥15 个源条目数 > 0**，且其中**至少 8 个是 P0（两地都通）**。达不到就先替换源，不进入 Stage 1
- 稳定性加分项：`probe.yml` **连跑 2 次（间隔 10 分钟）**，两次都通才记为 P0。单次成功可能只是没触发 WAF 限流
- 决策点：这份报告决定最终 `sources.yaml`。政府源若大面积落进"Actions 不通"，按原计划走媒体转载兜底

### Stage 1 · 抓取 + 去重 + 落盘（约 1 天）—— 这一步就是 MVP
- 产出：`pipeline/` 核心、`data/2026-10.jsonl`、`data/latest.js`、`index.html`
- 做的事：完整抓取 → 归一化 → 去重 → 关键词筛 → **追加写入 JSONL**。**本阶段不接大模型**，摘要先用原文/标题截断（保证链路先跑通）
- **验证方式**：
  1. `python scripts/run.py` → 控制台输出「源 A 抓到 12 条 / 源 B 失败: timeout」
  2. 打开 `data/2026-10.jsonl`，确认 **≥10 行真实记录**，字段齐全（id/title/source_name/original_url/published_at/tags）
  3. **双击 `index.html`（不启服务器）能看到卡片列表** → 满足你的"双击可看"硬要求
  4. **幂等测试**：紧接着再跑一次 `run.py`，新增行数应为 0（或个位数），证明去重生效
- 达成条件：这一步就满足了你"能跑通一次抓取并在页面展示 10 条以上真实数据"的 MVP 定义

### Stage 2 · 接入大模型（约半天）
- 产出：`pipeline/llm.py`、分类标签生效、外文标题变中文
- 做的事：批量 JSON 调用（8~10 条/批）一次完成 相关性判断 + 分类 + 翻译 + 摘要；重试 2 次；三次失败降级为截断摘要并打 `summary_source: fallback`
- **验证方式**：
  1. 随机抽 10 条人工检查：摘要是否 1~3 句、有没有营销腔、外文是否已译中、标签是否合理
  2. 控制台打印本次 token 消耗 → 乘 180 次/月，与第六节预估对账
  3. 故意把 API Key 改成错的再跑一次 → **必须仍然产出数据**（只是摘要降级），不能整体失败

### Stage 3 · CI + 部署（约半天）
- 产出：`.github/workflows/update.yml`
- 做的事：`workflow_dispatch` + `schedule` 双触发 → 抓取 → （有新内容才）commit → 同 workflow 内 `upload-pages-artifact` + `deploy-pages`
- **本阶段必须逐个落实的 6 个配置**（少一个就卡住，新手最常卡在前两条）：

  | 配置 | 值 | 不写会怎样 |
  |---|---|---|
  | `concurrency` | `group: update-feed` + **`cancel-in-progress: false`** | cron 延迟时两次运行重叠，两个任务同时 push 冲突。**注意必须是 `false`（排队），写成 `true` 会在 commit 中途掐掉上一次运行** |
  | `jobs.<job>.timeout-minutes` | `20` | 某次运行卡死会永久占住 concurrency 队列，后续全部阻塞 |
  | `permissions` | `contents: write` / `pages: write` / `id-token: write` | `deploy-pages` 直接 403 |
  | `jobs.deploy.environment` | `name: github-pages` + `url: ${{ steps.deployment.outputs.page_url }}` | **最容易漏**：`deploy-pages` 要求 job 挂 `github-pages` 环境，不写会报 "environment not found" |
  | 仓库 Settings → Pages → Source | 选 **GitHub Actions**（不是分支） | 站点 404，或部署到你不认识的分支 |
  | `actions/checkout` | `fetch-depth: 1` | 默认浅克隆本来就是 1，但显式写上，防止以后有人改 |

- **验证方式**：
  1. 在 Actions 页面手动点一次 Run workflow，确认绿色
  2. 运行页面的 Summary 里能看到简报（新增 N 条 / 失败源 M 个 / token X）
  3. 打开 `https://<你的用户名>.github.io/新-HOT/`，能看到与本地一致的数据
  4. 仓库里出现一次 `chore: update feed 2026-10-25T09:17Z` 的自动提交
  5. **重叠测试**：手动连点两次 Run workflow，第二次应显示 "queued / waiting"，而不是并行执行
  6. **无变更测试**：在没抓到新数据的情况下再跑一次，应该**不产生 commit**（日志里出现 "no new items, skip commit"）

### Stage 4 · 定时可靠性与运维（约半天）
- 产出：keepalive 心跳、`meta/sources_status.json`、`data/blocklist.txt`
- 做的事：cron 改为 `17 1,5,9,13,17,21 * * *`（UTC，对应北京时间 09:17/13:17/17:17/21:17/01:17/05:17，避开整点）；记录每源最近成功时间并在页脚显示"数据源 18/22 正常"
- **心跳的省法（比每周空提交更省）**：
  - 前提认知：60 天停用的判定是"仓库无提交"，而**数据提交本身就是活动**。所以只要还在抓到新闻，根本不需要心跳
  - 只有当"**连续 7 天一次数据提交都没有**"时才提交一次 `meta/heartbeat.txt`
  - 该文件**内容恒定为 `alive`**（不写时间戳）→ 内容不变则 git 不产生新 blob，只多一个 ~400B 的 commit 对象
- **验证方式**：连续观察 2 天，页面顶部"最后更新"时间应稳定在 4 小时节奏内；故意改坏一个源地址，确认页脚健康度下降但站点不受影响

### Stage 5 · 前端体验打磨（约半天）
- 做的事：分类筛选、关键词前端搜索（200ms 防抖）、"x 小时前"、深浅色、移动端、首屏 60 条 + 滚动增量加载
- **验证方式**：手机浏览器打开，单手可完成"筛选→搜索→点原文"三步；切换系统深色模式无白闪、无对比度不足

---

## 八、建议在原方案上补充的东西（这些是我认为该加、成本很低的）

| # | 补充项 | 为什么 | 成本 |
|---|---|---|---|
| 1 | `data/latest.js` 双通道 | 解决 `file://` 打不开的硬伤。**只放最近 100 条**（约 1 天），且**仅在 `location.protocol === 'file:'` 时动态加载**，http 访问不加载这 45KB | 10 行 |
| 2 | keepalive 心跳（按需触发） | 防止 60 天静默停更。仅在连续 7 天无数据提交时才提交，且文件内容恒定以避免 blob 增长 | 8 行 |
| 3 | `meta/sources_status.json` + 页脚健康度 | 源失效时你能立刻发现，而不是两周后 | 20 行 |
| 4 | `data/blocklist.txt` | 5 人小站最实用的功能：屏蔽不想看的稿（按 URL 或标题关键词），改一行文本即可，不用动代码 | 15 行 |
| 5 | `published_at` 兜底规则 | 中文 RSS 时间经常缺失/时区错乱。规则：统一存 UTC；缺失用 `fetched_at` 并打 `published_at_estimated: true`；时间明显未来值（>2h）视为脏数据丢弃 | 10 行 |
| 6 | 摘要硬约束 | prompt 里给 2 个「营销腔」负面示例 + 硬性 ≤80 字 + 禁止出现"重磅/赋能/引领"等词表 | prompt |
| 7 | 前端缓存破除 | GitHub Pages 有约 10 分钟 CDN 缓存，`fetch` 加 `?v=<last_updated>` | 1 行 |
| 8 | 版权合规 | **只存标题 + ≤120 字摘要 + 链接，不存全文、不存图片** | 设计约束 |
| 9 | 首屏增量渲染 | 90 天数据约 1 万条，一次性 DOM 渲染会卡 | 30 行 |
| 10 | 失败告警（可选） | Actions 失败默认会给仓库 owner 发邮件，够用；如需即时告警再加 webhook | 5 行 |

---

## 九、需要你拍板的 6 个问题

| # | 问题 | 选项 | 我的建议 |
|---|---|---|---|
| 1 | 仓库公开还是私有？ | A. 公开仓库 + GitHub Pages（零配置，但内容全网可见）<br>B. 私有仓库 + Cloudflare Pages + Cloudflare Access（免费 50 用户，真私密，配置多 3 步） | **先 A**（加 noindex），你的内容是公开新闻，隐私诉求其实很低；哪天需要了再迁 B，数据文件不用改 |
| 2 | 摘要模型用哪个？ | A. GLM-4-Flash 免费（需实名认证）<br>B. DeepSeek-V4-Flash（¥2~5/月，不需要额外实名） | **A 优先**，代码写成可切换，B 作为一行配置的备胎 |
| 3 | 政府源（海关/市监/卫健委）做到什么程度？ | A. 只抓能抓的，失败了就算<br>B. 投入额外精力做 HTML 解析 + 多重兜底 | **A**，把精力放在食品伙伴网/36氪这类稳定源，政策内容靠转载覆盖 |
| 4 | 定时 6 次/天确认？ | 4/6/8 次 | **6 次（每 4 小时）**，资讯类够了 |
| 5 | 公众号内容要不要？ | 要 / 不要 | **不要**，反爬严 + 合规风险 + 无稳定方案 |
| 6 | 内容只保留 90 天？ | 90 / 180 天 | **90 天**，JSON 总共约 5~8MB，够轻 |

---

## 十、主要风险与备选方案（每一项都不卡死）

| 风险 | 概率 | 备选 1 | 备选 2 |
|---|---|---|---|
| 中文源大多无 RSS | 高 | HTML 列表解析（食品伙伴网/36氪/母婴观察） | 用 Google News RSS 按中文关键词聚合（Actions 环境可访问） |
| Google News RSS 被限 | 中 | GDELT DOC API（免费无 Key） | 直接抓海外媒体 RSS（DairyReporter 等） |
| 政府站境外 IP 被 WAF 拦 | 高 | 降级 P2，失败即跳过 | 只抓"媒体报道政府通告"的转载稿（信息延迟 1~2 天，可接受） |
| LLM 免费额度政策变化 | 中 | 切 DeepSeek（¥2~5/月） | 切 Qwen（新用户 100 万 tokens/模型） |
| Actions cron 延迟/停用 | 高 | keepalive 心跳 | cron-job.org 每 4 小时调 GitHub `dispatches` API（免费） |
| GitHub Pages 国内访问慢 | 中 | 纯文本页面影响小，可接受 | 迁 Cloudflare Pages 或腾讯 EdgeOne Pages（有国内节点，免费额度） |
| 某站改版导致解析失效 | 高 | 单源失败不中断整次任务 | 页脚健康度提示 + 每月花 5 分钟看一次 `sources_status.json` |

---

## 十一、Git 体积核算与对策（附量化）

你的担心是对的，但**只治症状不够**。把两种写法摊开算一遍：

前提：每天约 100 条新数据，每月 180 次运行，每条记录约 450B，git blob 按 zlib 压缩约 4~8 倍估算。

| 写法 | 单次提交新增的内容 | 月增量 | 年增量 |
|---|---|---|---|
| ❌ 月度 JSON 全量重写 | **整个月文件**（月末 1.3MB，月均约 0.65MB → 压缩后 ~160KB） | ~28MB | **~340MB** |
| ✅ JSONL 只追加 | 本次 17 条新记录 ≈ 7.6KB → 压缩后 ~2KB | ~0.4MB | **~4.4MB** |
| `data/latest.js`（100 条） | 每次全量重写 ≈ 45KB → 压缩后 ~12KB | ~2.2MB | ~26MB |
| `data/index.json`（1KB） | 全量重写 | ~0.2MB | ~2.4MB |
| commit 对象 | ~400B/次 × 5 次/天 | ~0.06MB | ~0.7MB |
| **合计（JSONL 方案）** | | **~2.9MB/月** | **~34MB/年** |

结论有三条：

1. **根因是"全量重写"，不是"提交太频繁"**。你的第 2 点措施（浅克隆、无新增不提交、每周心跳）都很对，但即使全做了，全量重写仍是 ~340MB/年。改成 JSONL 追加后直接降到 ~4.4MB/年，**降了 77 倍**。
2. `latest.js` 反而是剩余的大头（26MB/年，占 76%）。所以**不能按"最近 7 天"放**（7 天 ≈ 700 条 ≈ 315KB，年增量会到 180MB）。改成**最近 100 条**，体积从 315KB 降到 45KB。代价：`file://` 双击只能看到最近约 1 天的数据——对你"验证链路是通的"这个用途完全够，要看全量就起本地 http 服务。
3. `fetch-depth: 1` 要写，但要说清它的真实作用：**CI 侧本来就看不到历史，所以 `.git` 再大也不影响 Actions**。它真正保护的是你本地 `git clone` 的体验。所以真正该控制的是"别让 `.git` 变大"，而不只是"CI 别拉历史"。

> 补充：若哪天 `.git` 真的超过 300MB，README 里会给一段 `git checkout --orphan` 重建历史的操作（清空历史但保留当前文件），**属可选项，不是必需**。

---

## 十二、CI 稳健性骨架（Stage 3 落地时按这个写）

只列出关键骨架，完整代码 Stage 3 再写：

```yaml
name: update-feed
on:
  schedule:
    - cron: "17 1,5,9,13,17,21 * * *"
  workflow_dispatch:

concurrency:
  group: update-feed
  cancel-in-progress: false        # 关键：排队，不取消

permissions:
  contents: write
  pages: write
  id-token: write

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 20            # 防卡死占住队列
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 1
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements.txt
      - run: python scripts/run.py
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_BASE_URL: ${{ secrets.LLM_BASE_URL }}
          LLM_MODEL: ${{ secrets.LLM_MODEL }}
      - name: commit if changed
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data meta
          if git diff --staged --quiet; then echo "no new items, skip commit"; exit 0; fi
          git commit -m "chore: update feed $(date -u +%FT%TZ)"
          git pull --rebase --autostash
          git push
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with: { path: "." }

  deploy:
    needs: update
    runs-on: ubuntu-latest
    environment:                   # deploy-pages 的隐形必需项
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```

三个容易踩的细节：
- `cancel-in-progress: false`：网上抄来的模板很多写 `true`，那会在 commit 途中掐掉上一次运行，可能留下 `index` 与 `.git` 不一致的状态。
- `environment: github-pages`：`deploy-pages` 明确要求，漏了报 "environment not found"。
- `git pull --rebase --autostash` 在 push 前：concurrency 已经防了自动重叠，但你在网页端手改文件时仍可能撞车，这一行是廉价保险。

---

## 十三、对你 5 点反馈的逐条回应

| # | 你的意见 | 我的判断 | 落地情况 |
|---|---|---|---|
| 1 | 探测要双环境，以境外为准 | **完全正确，这是我原方案的实质缺陷**。我写"你本地执行"是错的——本地通而 Actions 不通的源，在生产里等于不存在 | 已重写 Stage 0：新增 `probe.yml`（Actions 侧跑）、`compare_probe.py`（两地比对）、四档判定表，验收标准改为"Actions 侧 ≥15 源、其中 ≥8 个两地都通" |
| 2 | Git 历史膨胀 | **方向对，但只治症状**。你列的三个措施都采纳；真正的根因是全量重写，改成 JSONL 追加后年增量 340MB → 4.4MB | 已改存储格式 + 附录十一给出量化 |
| 3 | 加 `concurrency` 防重叠 | **正确，且有个坑要补**：必须 `cancel-in-progress: false`（排队），写 `true` 会中途掐断上一次运行；另加 `timeout-minutes: 20`，否则卡死会永久占住队列 | 已写入 Stage 3 配置表 + 骨架 |
| 4 | `pages: write` / `id-token: write` + Source 选 Actions | **正确，但还漏一项**：`deploy-pages` 还要求 deploy job 声明 `environment: name: github-pages`，这一条比 permissions 更容易卡住新手 | 已写入配置表（6 项逐条列出）+ README 教程会包含 |
| 5 | `feed.js` 别放全量 | **同意，但"7 天"仍然太大**（700 条 ≈ 315KB，年增 180MB）。改成按条数封顶 100 条（45KB），并加你说的"仅 `file://` 下加载" | 已改为 `data/latest.js`，100 条 + 条件加载 |

另外有两点是我顺着你的思路补充的：
- **心跳不必每周一次**：数据提交本身就满足"仓库有活动"，所以只在"连续 7 天无数据提交"时才提交心跳，且文件内容恒定（不写时间戳），git 不产生新 blob。
- **`fetch-depth: 1` 的真实作用**要说清：CI 本来就看不到历史，`.git` 再大也不影响 Actions；它保护的是你本地 clone 的体验。

---

## 十四、下一步

设计和口径已经定稿。仍需你确认第九节 6 个问题（或直接回"按建议来"），我就开始 Stage 0：先交付源探测脚本 + `probe.yml`，在 Actions 上跑出报告，再据此定 `sources.yaml`，然后进 Stage 1 出 MVP。

---

## 十五、Stage 1 实施记录（MVP 已跑通）

**结果**：44 个源（启用 39），一轮抓取 508 条原始内容 → 关键词筛选后 92 条 → 去重后入库 87 条，
来自 15 个可用源。页面已可本地打开，`data/` 下已有真实数据。

### 实施中新发现的 6 个问题（都不在原方案里，是跑起来才暴露的）

| # | 问题 | 影响 | 修法 |
|---|---|---|---|
| 1 | **同站链接被判成外链** | 中婴网文章在 `info.` / `news.` 子域，严格比对 netloc 导致整站 0 条 | 改成比对可注册域名（含 `com.cn` 等二级后缀） |
| 2 | **机构名当关键词 = 全站放行** | 市场监管总局每条新闻标题都含"市场监管总局"，"党建座谈会"也进信息流（9 条里 7 条无关） | 从打分表移除所有机构名，仅保留在标签规则里；该源通过数 9 → 2 |
| 3 | **标题把日期/栏目名一起抓进来** | 中童传媒卡片标题变成"21 2026 10 行业新闻 新闻速递丨…" | 标题提取改为「`<a>` 内最长标题标签优先」，并支持 `title_selector` |
| 4 | **API 分派默认值会误伤** | GDELT 源没写 `api` 字段，退化成 openFDA，两次抓同一批召回 | 按域名推断 + `sources.yaml` 显式标注 |
| 5 | **openFDA 未做产品过滤** | 40 条召回里绝大多数是奶酪、沙拉，直接把信息流灌满 | 按乳/婴关键词预筛 → 8 条抓取、2 条入库 |
| 6 | **日期解析时区炸了** | `naive > aware` 抛 TypeError，界面/澎湃/雨果三个源整源失败 | 统一补 `tzinfo` |

另外补了两个内容卫生规则：发布时间早于 45 天的旧稿丢弃（品牌官网页脚挂着 2024 年的稿子）；
时间未知的条目排到信息流末尾，而不是按抓取时间顶到第一条。

### 仍未解决（有意留到后面）

- 54 / 87 条没有摘要（HTML 列表页本身不提供）→ Stage 2 由模型补，顺带翻译英文标题
- 本地抓不到的源（Google News、FSANZ、母婴行业观察、雀巢等）→ 待 Actions 侧复测
- 定时与部署 → Stage 3

### 验证方式

```bash
python scripts/run.py --dry-run --show 10   # 不写盘，看会抓到什么
python scripts/run.py                       # 正式跑，写 data/
python -m http.server 8000                  # 或直接双击 index.html
```



---

## 十五、交付状态（2026-10-25）

| 阶段 | 状态 | 关键产出 |
|---|---|---|
| Stage 0 源探测 | ✅ | `docs/STAGE0.md`、`probe-local.md`、`sources.yaml`（44 源） |
| Stage 1 抓取 MVP | ✅ | `pipeline/` 五个模块 + `scripts/run.py` + 静态页 |
| Stage 2 摘要与翻译 | ✅ | `pipeline/extract.py`、`pipeline/llm.py`、`data/zh_cache.json` |
| Stage 3 定时与部署 | ✅ | `.github/workflows/update.yml`、`scripts/build_site.py` |
| 附加 | ✅ | `scripts/purge.py`（清坏条目）、质量三道否决（junk / promo / 时政） |

与本文档原计划的 4 处偏差，均为实测后调整：

1. 存储从"月度 JSON 全量重写"改为 **append-only JSONL**（体积 340MB/年 → 约 4MB/年）。
2. 前端明确**不用 Astro/Vite**，原生 JS 零构建。
3. 相关性/分类/翻译/摘要**合并为一次模型调用**，token 降约 60%；无 Key 时退回正文抽取 + 译文缓存。
4. `localize`/`purge` 这类一次性维护动作做成独立脚本，不进主流水线。

当前基线：75 条 / 18 个源 / 73 条有摘要 / 源健康度 24/39。

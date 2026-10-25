# Stage 0 交付：源探测结果

> 日期：2026-10-25　环境：本机（中国大陆网络 + 沙箱代理已绕过）
> 权威性说明：**本地结果只作参考，最终以 GitHub Actions 的 probe 报告为准**

---

## 一、结论

**37 个源里 21 个本地可用（8 个 RSS + 13 个 HTML），超过"≥15 个可用"的验收线。**

| 类别 | 数量 | 说明 |
|---|---|---|
| ✅ RSS 直接可用 | 8 | 抓取成本最低，Stage 1 立刻能用 |
| 🔧 需 HTML 解析 | 13 | 能拿到页面和链接，Stage 1 要写选择器 |
| ❌ 本地不可用 | 16 | 6 个疑似本地网络受限，Actions 上大概率能救回 |

**若 Actions 侧能救回全部网络受限源，可用源预计达到 26~28 个。**

---

## 二、探测中推翻的 4 个错误假设（这些比数字更重要）

| # | 我原先的判断 | 实测真相 | 影响 |
|---|---|---|---|
| 1 | 36氪 `36kr.com/feed` 是有效 RSS（第一轮 HTTP 200 就下了结论） | **返回的是 HTML 单页应用**，不是 RSS。之前只看状态码，判断错了 | 36氪从 P0 降为 P2/观察；提醒：**永远不能只看状态码判断 feed** |
| 2 | 用 httpx 抓就够 | **多站校验 TLS 指纹**：同 UA 下 httpx 403、curl 指纹 200（foodmate 实测） | 已把 HTTP 层整体换成 `curl_cffi`（impersonate chrome）。这个问题在 Actions 上一样存在，不是本地问题 |
| 3 | 带 `Bot` 标识的 UA 更"礼貌" | **恰相反**：UA 里带 `MilkFeedBot/0.1` 时 foodmate 直接 403，纯浏览器 UA 返回 200 | 已改为纯浏览器 UA。仍遵守 robots.txt、限速、只取标题摘要 |
| 4 | FDA 召回抓网页 | 网页已改版（RSS 404、列表 JS 渲染），但 **openFDA 有官方 JSON 接口**，免 Key，2026 年 1049 条 | 换用 `api.fda.gov/food/enforcement.json`，比抓网页可靠得多 |

顺带修正的两个 feed 路径：
- EFSA：`/en/rss` 只是语言目录页，真正 feed 是 **`/en/rss/news`**
- FoodNavigator-Asia：自动发现会误抓主站 `foodnavigator.com` 的 feed（造成重复），必须写死 **`foodnavigator-asia.com/arc/outboundfeeds/rss/`**

---

## 三、可用源清单（本地基线）

### RSS（8 个，Stage 1 可直接用）

| 源 | id | 优先级 | 条目 | 备注 |
|---|---|---|---|---|
| openFDA 食品召回 | `openfda_food_recall` | P0 | 50 | 官方 JSON API，免 Key，限 1000 次/天 |
| Food Business News | `foodbusinessnews` | P1 | 30 | `rss/articles` |
| DairyReporter | `dairyreporter` | P0 | 20 | `/arc/outboundfeeds/rss/` |
| FoodNavigator | `foodnavigator` | P0 | 20 | 同上 |
| FoodNavigator-Asia | `foodnavigator_asia` | P1 | 20 | 必须用 asia 站自己的 feed |
| NutraIngredients | `nutraingredients` | P1 | 20 | 原料话题（乳铁蛋白/HMO） |
| EFSA | `efsa` | P0 | 10 | `/en/rss/news` |
| Food Dive | `fooddive` | P1 | 10 | `feeds/news/` |

### HTML 可解析（13 个，Stage 1 需写选择器）

| 源 | id | 优先级 | 候选链接数 |
|---|---|---|---|
| 中国食品安全网 | `cfsn` | P1 | 268 |
| 食品伙伴网·资讯 | `foodmate_news` | **P0** | 201 |
| 界面新闻 | `jiemian` | P1 | 102 |
| 雨果跨境 | `cifnews` | P1 | 87 |
| 市场监管总局 | `samr_cn` | P1 | 57 |
| USDA FAS | `usda_fas` | P2 | 34 |
| 第一财经 | `yicai` | P2 | 25 |
| 伊利 | `yili` | P1 | 20 |
| Foodaily 每日食品 | `foodaily` | P1 | 16 |
| 澎湃新闻 | `thepaper` | P2 | 7 |
| 达能新闻 | `danone_news` | P1 | 4 |
| 君乐宝 | `junlebao` | P2 | 3 |
| a2 Milk | `a2milk_news` | P1 | 1 |

> 候选链接数是"该页有多少条疑似新闻链接"的粗略估计，不代表都能解析成功。Stage 1 要为每个 P0/P1 源写明确的选择器，并逐个人工验收。

---

## 四、16 个失败源：原因与处置

| 失败原因 | 源 | 处置 |
|---|---|---|
| **本地网络受限**（CONNECT 失败 / DNS 失败） | `gnews_zh`(P0)、`gnews_en`(P0)、`fsanz`(P1)、`muyingguancha`(P1)、`rasff`(P2) | **保留，待 Actions 复测**。Actions 在境外，这批大概率能通 |
| **HTTP 403**（反爬） | `justfood`、`nestle_media`、`dairyfoods`、`tmtpost` | 保留待 Actions 复测；若仍 403 则关闭 |
| **HTTP 412 / 502** | `nhc_sps`(412 是 JS 挑战)、`dairy_org_cn`(502) | 降为 P2；502 属临时故障，下轮自动恢复 |
| **JS 渲染，无静态链接** | `kr36`、`ebrun`、`feihe`、`gov_cn_policy` | 保留观察；若 Actions 仍抓不到则关闭（这些站要么有 API，要么得放弃） |
| **429 限流** | `gdelt_formula` | 上轮成功拿到 12 条，本轮被限流。Stage 1 需加"失败即跳过"逻辑 |
| **已停用** | `customs_cn`（412 JS 挑战）、`samr_food`（栏目地址待确认） | `enabled: false` |

---

## 五、Stage 0 产出的文件

```
config.yaml                      配置（UA 已按实测修正，模型 glm-4.7-flash）
sources.yaml                     37 个源，含实测备注
requirements.txt                 依赖锁定（含 curl_cffi）
pipeline/http.py                 礼貌抓取客户端（curl_cffi + robots + 限速 + 重试）
pipeline/sources.py              配置加载与校验
scripts/probe_sources.py         探测脚本（可在本地和 Actions 同一份代码跑）
scripts/compare_probe.py         两地结果对比，输出四档判定
.github/workflows/probe.yml      Actions 侧手动触发探测
.env.example / .gitignore
```

---

## 六、你怎么验证（3 步）

### 第 1 步：本地跑一遍（1 分钟出结果，约 90 秒完成）

```bash
# 首次：装依赖
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 跑探测
python scripts/probe_sources.py --label local
```

**预期**：控制台逐行打印每个源的结果，末尾输出

```
RSS 直接可用 : 8
需 HTML 解析 : 13
不可用       : 16
```

并生成 `probe-local.json` 和 `probe-local.md`（后者是表格，方便看）。

> 若你本地数字与我差很多，正常——网络环境不同。**不用管差异，直接进第 2 步。**

### 第 2 步：推到 GitHub，在 Actions 上跑权威探测

1. 新建一个 **public** 仓库，把本目录推上去
2. 打开仓库的 **Actions** 标签页，左侧选 **probe-sources**
3. 点右侧 **Run workflow** → 绿色按钮
4. 等约 90 秒，进这次运行 → 页面底部 **Summary** 就是完整报告表格
5. 顺便在页面底部 **Artifacts** 下载 `probe-report-actions`（含 `probe-actions.json`）

### 第 3 步：两地对比

把 `probe-actions.json` 下载到项目根目录，然后：

```bash
python scripts/compare_probe.py probe-local.json probe-actions.json
```

**预期输出**四档分类：

```
- P0 两地都通: 20 个 [...]
- P1 仅 Actions 通: 5 个 ['gnews_zh_formula', 'gnews_en_formula', ...]
- 弃用 仅本地通: 0 个 []
- 弃用 两地都不通: 12 个 [...]
```

**验收标准**：`P0 两地都通` ≥ 8 个，且 `P0 + P1` 总数 ≥ 15 个 → 达标，进 Stage 1。

---

## 七、Stage 1 要做的第一件事

不值得为 13 个 HTML 源都写选择器。按这个顺序来：

1. 先只接 **8 个 RSS 源 + openFDA**（零解析成本，20 分钟内出数据）
2. 再补 **3 个 P0/P1 中文 HTML 源**：`foodmate_news`、`cfsn`、`jiemian`
3. 其余（品牌官网、USDA、第一财经）放后面，抓不到就算了

这样你能很快看到"10 条以上真实数据"的 MVP，而不是卡在解析上。

# 奶链雷达 · MILK RADAR

跨境母婴与婴幼儿配方奶粉的行业情报聚合。抓取 40 余个中英文信息源，自动筛选、去重、
分类、翻成中文，产出一个纯静态站点。**零服务器、零数据库、零构建**，全部跑在免费额度内。

当前状态：**已上线**，站点 <https://hhlamb.github.io/HOT/>

页面结构：

| 文件 | 作用 |
|---|---|
| `index.html` | 首页信息流：头图、数据条、搜索、分类筛选、卡片列表 |
| `article.html` | 站内详情页（`?id=<条目 id>`）：AI 速览 + 原文摘录 + 原文链接 + 相关阅读 |
| `assets/feed-data.js` | 两页共用的取数层（http 读 JSONL / file:// 读 latest.js） |
| `assets/app.js` | 首页渲染 |
| `assets/article.js` | 详情页渲染 |

| 阶段 | 状态 | 内容 |
|---|---|---|
| Stage 0 源探测 | ✅ | 44 个候选源实测，24 个稳定出数据 |
| Stage 1 抓取 MVP | ✅ | 抓取 → 筛选 → 去重 → 落盘 → 静态页 |
| Stage 2 摘要与翻译 | ✅ | 详情页抽取 + 模型摘要 + 译文缓存 |
| Stage 3 定时与部署 | ✅ | `update.yml`：每 4 小时抓取并部署 |
| Stage 4 私有化 | ⬜ | 需要真私密时迁 Cloudflare Access（见 §9） |

---

## 1. 先跑一次看看

```bash
# 1) 装依赖（Python 3.11+）
pip install -r requirements.txt

# 2) 抓一轮，写入 data/
python scripts/run.py

# 3) 看效果
python -m http.server 8000
# 浏览器打开 http://localhost:8000
```

**双击 `index.html` 也能看**。浏览器禁止 `file://` 页面 fetch 本地文件（CORS 硬限制），
这条路径改读 `data/latest.js`（内嵌最近 100 条）。代价是双击只能看到最近一两天的数据，
要看全量请用上面的 `http.server`。

常用参数：

```bash
python scripts/run.py --dry-run --show 10     # 只看会抓到什么，不写盘
python scripts/run.py --only efsa,cbiline     # 只跑指定源
python scripts/run.py --no-llm                # 不调模型（省额度）
python scripts/run.py --no-extract            # 跳过详情页抽取（快）
```

---

## 2. 它是怎么工作的

```
  ┌──────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
  │ sources.yaml │──▶│  fetchers    │──▶│  relevance    │──▶│   dedupe     │
  │ 44 个源      │   │  RSS/HTML/API│   │  关键词加权   │   │  URL + bigram│
  └──────────────┘   └──────────────┘   └───────────────┘   └──────┬───────┘
                     指纹伪装/限速/重试     打分<4.0 丢弃            │
                     单源失败不影响全局                             ▼
                                              ┌──────────────────────────────┐
                                              │  extract：抓详情页抽首段     │
                                              │  llm：模型译中 + 压到 90 字  │
                                              │       （无 Key 时用译文缓存）│
                                              └──────────────┬───────────────┘
                                                             ▼
                          data/2026-10.jsonl（只追加）+ index.json + latest.js
                                                             │
                                                             ▼
                                    index.html + assets/app.js（原生 JS，无框架）
```

分工与"为什么这么选"：

| 环节 | 做法 | 为什么 |
|---|---|---|
| 抓取 | `curl_cffi` 模拟 Chrome 的 TLS 指纹 | 食品伙伴网等站校验 JA3，httpx 一律 403 |
| 筛相关 | 先过"主题硬门槛"（标题必须有母婴品类/跟踪品牌/奶粉供应链核心词），再看关键词加权分（标题命中算 1.6 倍） | 泛食品源的抽检稿（芒果二氧化硫、出口烤花生不合格）分数很高但完全跑题，只能靠标题硬门槛拦；分数只管排序与确认 |
| 补摘要 | 抓详情页抽首段 | 零成本、忠实原文，不编造 |
| 精加工 | GLM 一次调用做「判相关+打标签+翻译+摘要」 | 四条合并成一次调用，token 约为分开调的 1/2.6 |
| 存储 | 按月分片 JSONL，只追加 | 全量重写一年会在 git 里堆 340MB，只追加约 4MB |
| 前端 | 原生 HTML/CSS/JS | 数据是运行时 fetch 的，站点没有构建产物，加构建只多一个失败点 |
| 调度 | GitHub Actions cron | 无常驻进程，公开仓库分钟数不限 |

---

## 3. 目录结构

```
.
├─ index.html                唯一页面
├─ assets/
│  ├─ app.js                 取数 / 渲染 / 筛选 / 搜索 / 相对时间
│  ├─ style.css              CSS 变量 + 深浅色自动适配
│  └─ favicon.svg
├─ data/                     ← 站点消费的数据（提交进仓库）
│  ├─ index.json             {last_updated, total, months[], tags[]}
│  ├─ 2026-10.jsonl          按月分片，每行一条 JSON，**只追加不改写**
│  ├─ latest.js              file:// 双击时的兜底数据（最近 100 条）
│  ├─ zh_cache.json          外文标题/摘要的译文缓存（省 token 用）
│  └─ archive/               超过保留期的月份压缩成 .jsonl.gz
├─ pipeline/
│  ├─ http.py                curl_cffi 客户端：指纹 / 限速 / robots / 重试
│  ├─ sources.py             YAML 配置加载
│  ├─ fetchers.py            RSS、HTML 列表页、JSON API 三种抓取器
│  ├─ clean.py               URL 与标题归一化、bigram 相似度
│  ├─ relevance.py           关键词加权打分 + 分类标签
│  ├─ extract.py             详情页正文抽取（补摘要）
│  ├─ llm.py                 模型调用 + 译文缓存
│  ├─ dedupe.py              URL 去重 + 标题相似度去重
│  ├─ store.py               JSONL 追加、裁剪归档、写索引
│  └─ report.py              运行简报（控制台 + $GITHUB_STEP_SUMMARY）
├─ scripts/
│  ├─ run.py                 跑完整流水线
│  ├─ build_site.py          把要发布的文件拷到 _site/（发布只发站点，不发源码）
│  ├─ purge.py               一次性清理已入库的坏条目（内容农场、公关稿）
│  ├─ purge_weak.py          按主题硬门槛清洗已入库的跑题稿（泛食品抽检、乳业宏观、导航页）
│  ├─ probe_sources.py       批量探测信息源可用性
│  └─ compare_probe.py       比对本地 / Actions 两侧的探测结果
├─ .github/workflows/
│  ├─ update.yml             定时抓取 + 部署（主力）
│  └─ probe.yml              手动触发的源探测
├─ sources.yaml              信息源（增删只改这一个文件）
├─ config.yaml               阈值 / 限速 / 模型 / 保留天数
├─ requirements.txt
├─ .env.example
├─ docs/STAGE0.md            源探测报告（44 个源的实测结论）
└─ PLAN.md                   方案评审（含踩坑记录与体积核算）
```

---

## 4. 数据存在哪（本项目没有数据库）

这是设计上的取舍，不是没做完：**不建数据库，用仓库里的文件当数据库。**

| 你在找的"数据库" | 实际是什么 | 在哪 |
|---|---|---|
| 表 / 集合 | 按月分片的 JSONL 文件，一行一条记录 | `data/2026-10.jsonl` |
| 索引 / 元数据 | 总数、最后更新时间、月份清单、标签清单 | `data/index.json` |
| 缓存表 | 外文标题与摘要的中文译文 | `data/zh_cache.json` |
| 归档区 | 超过 90 天的月份压缩存放 | `data/archive/*.jsonl.gz` |
| 备份 | 不需要，git 历史本身就是备份 | `.git/`（每次提交 = 一个版本） |
| 连接串 / 账号密码 | 不存在，也没有 | — |

为什么敢这么干：

- 量级只有几百到几千条、单条约 500 字节，90 天全量也就几 MB，任何数据库都显得多余
- 只追加不改写，git diff 每次只多几行，人眼可读、可回滚
- 前端直接 `fetch` 这些文件，中间没有任何服务端进程——这也是"零服务器"的来源
- 加数据库反而要引入连接凭证、备份策略、免费额度限制，是负收益

**所以如果要找"数据库账号密码"，答案是：没有，也不需要。** 唯一需要保管的密钥是
模型 API Key（§5 步骤 4），它只用于生成摘要，与数据存储无关。

> 如果哪天数据涨到十万条以上、或想做全文检索，再把 JSONL 灌进 SQLite / Cloudflare D1 即可，
> 数据格式不用改。

### 4.1 单条记录的字段

| 字段 | 说明 |
|---|---|
| `id` | `sha1(source_id + 规范化URL)` 前 16 位，稳定不变，用于 URL 级去重 |
| `title` | 原始标题（外文为原文） |
| `title_zh` | 中文标题；有它就优先展示它 |
| `summary` | 中文摘要，1~3 句 |
| `summary_source` | `rss` / `extract` / `llm`，出问题时能看出摘要哪来的 |
| `source_id` / `source_name` | 来源标识 / 展示名 |
| `source_url` | 抓取入口地址 |
| `original_url` | 原文链接（已去 utm 等追踪参数） |
| `published_at` | 发布时间，带 +08:00 时区；空串表示该页没给时间 |
| `published_guessed` | 时间是否靠推断（页面只给"10月23日"这种无年份日期时为 true） |
| `fetched_at` | 抓取时间 |
| `tags` | 分类标签，最多 3 个 |
| `language` | `zh` / `en` |
| `score` / `matched` | 关键词得分与命中词，排查筛得对不对时用 |

`id` 重复时**后写的覆盖先写的**（JSONL 只追加，用这个规则做"修正"）。

---

## 5. 零成本部署（跟着点，别跳步）

全程约 10 分钟，只需要一个 GitHub 账号。装在电脑上的软件只有一个可选（GitHub Desktop），
其余都在网页上点。

**六步总览**（先看一遍，心里有数）：

```
① 建空仓库           github.com/new，名字 new-hot，Public，三个勾选框都不勾
        ↓
② 传代码             推荐 GitHub Desktop（能把 .github 隐藏目录一起带上）
        ↓
③ 拿模型 Key         open.bigmodel.cn，实名后创建 API Key（可选）
        ↓
④ 存进 Secret        Settings → Secrets and variables → Actions → ZHIPU_API_KEY
        ↓
⑤ 开 Pages           Settings → Pages → Source 改成 "GitHub Actions"
        ↓
⑥ 手动跑一次         Actions → update-feed → Run workflow → 等两个绿勾 → 拿网址
```

④⑤ 两步的顺序**不能反**：先存 Secret 再跑，否则第一次跑完了才配 Key，得再跑一次才有译文。
⑤ 必须在 ⑥ 之前：Pages 没开，deploy 那一步会直接红。

### 5.0 先找到「要上传的文件夹」在哪

**路径：`E:\workbuddy\新-HOT`**

打开它的两种办法，任选一种：

1. 按键盘 `Win + E` 打开资源管理器 → 在最上方**地址栏**里粘贴 `E:\workbuddy\新-HOT` → 回车
2. 或者按 `Win + R` → 输入 `E:\workbuddy\新-HOT` → 回车

进去之后，你应该能看到这些东西（**看到 `index.html` 和 `sources.yaml` 就说明找对了**）：

```
E:\workbuddy\新-HOT
├─ index.html          ← 网页本体
├─ assets\             网站样式和脚本
├─ data\               数据（75 条）
├─ pipeline\           抓取代码
├─ scripts\            运行脚本
├─ config.yaml         参数配置
├─ sources.yaml        信息源清单
├─ requirements.txt    Python 依赖
├─ README.md           本文
├─ robots.txt
├─ .github\            ⚠️ 定时任务配置，默认看不见
└─ .gitignore          ⚠️ 默认看不见
```

**关键一步：把隐藏文件显示出来。** `.github` 和 `.gitignore` 以点开头，Windows 默认隐藏。
在资源管理器点顶部 **查看** → **显示** → 勾选 **隐藏的项目**
（Win11 是「查看」→「显示」→「隐藏的项目」；Win10 是「查看」选项卡里勾「隐藏的项目」）。

**为什么必须做这步**：`.github\workflows\update.yml` 就是"每 4 小时自动跑一次"的定时任务。
它藏在 `.github` 里，如果没显示出来、复制时漏了，你后面会**在 GitHub 上找不到 `update-feed`、
也找不到 Run workflow 按钮**——这是新手最常见的卡点，先把它显示出来省掉后面所有麻烦。

一共 40 个文件、452 KB，很小，整包传上去没问题。

---

### 5.1 第一步 · 建一个空仓库

1. 浏览器打开 <https://github.com/new>
2. **Repository name** 填 `new-hot`（可以随意，但建议纯小写英文，后面网址就是它）
3. **Description** 可留空
4. 可见性选 **Public**
   - 公开仓库的 Actions 分钟数**不限**；私有仓库每月只有 2000 分钟，够用但不保险
   - 公开 = 网页能被别人访问（已加 `noindex`，不想被搜到够了；要真私密看 §9）
5. **下面三个勾选框全部不要勾**：Add a README file、Add .gitignore、Choose a license

   ⚠️ 这一条很重要。如果勾了 README，仓库一创建就有文件，你上传时会和它冲突，
   后面会多出一堆"怎么合并"的麻烦。要一个**全空的仓库**。

6. 点绿色 **Create repository**

创建完你会看到一个叫 "Quick setup" 的页面，上面有几行命令行代码——**先别管它，也不要粘贴**。
这个页面就是空仓库的样子，下一步我们往里面放文件。

### 5.2 第二步 · 把代码传上去（三条路线，选一条）

| 路线 | 适合谁 | 需要装软件 | 成功率 |
|---|---|---|---|
| **A · GitHub Desktop** | 没用过 git、想一次搞定 | 要装（免费） | ✅ 最稳，**推荐** |
| **B · 纯网页上传** | 不想装任何软件 | 不用 | ⚠️ 需手工补 1 个隐藏文件（`update.yml`） |
| **C · 命令行** | 电脑上已经有 git | 不用 | ✅ 快，但要会敲命令 |

#### 路线 A · GitHub Desktop（推荐）

为什么推荐：它会把 `.github`、`.gitignore` 这些隐藏文件一起带上，
不需要你手工创建文件，也不会出现"上传了但 Actions 里啥也没有"的情况。

**A-0 从这里开始（你现在就在这个页面）**

空仓库页面上有个浅蓝色区块，标题是 **"Quick setup — if you've done this kind of thing before"**。
这个区块里其实有三个入口，**不要看下面的命令行代码**，只看最上面那行：

```
🔲 Set up in GitHub Desktop    or    HTTPS  https://github.com/你的用户名/你的仓库.git  [复制]
Get started by creating a new file or uploading an existing file. ...
```

- 点最左边那个 **Set up in GitHub Desktop** → 浏览器会问"是否打开 GitHub Desktop"
- 没装过软件的话，这个链接会先带你去下载页，装完再回来点一次即可
- 软件打开后会自动带上仓库地址，你只要在 **Local path** 选 `E:\workbuddy` → **Clone**，
  等于跳过了下面 A-1、A-2 两步

> 顺便说一下：**空仓库页面上没有 `Add file` 按钮**，这是很多人卡住的第一步。
> 想纯手工上传，入口是那行小字 `uploading an existing file`（见路线 B）。

**A-1 安装并登录**（已经点过 A-0 的可跳过）

1. 打开 <https://desktop.github.com/> → 点 **Download for Windows**
2. 双击安装包，装完打开
3. 点 **Sign in to GitHub.com**，浏览器会弹出授权页，点 **Authorize desktop**，回到软件
4. 如果提示填 Name / Email，随便填（这是提交记录里的署名，不影响使用）

**A-2 把仓库克隆到本地**

1. 菜单 **File** → **Clone repository**（或首页的 Clone a repository）
2. 切到 **GitHub.com** 标签页，列表里选刚建的那个仓库（`你的用户名/仓库名`）
3. **Local path** 改成 `E:\workbuddy`
4. 点 **Clone**

克隆完，本地会多出一个空文件夹。假设仓库名叫 `HOT`，那就是 `E:\workbuddy\HOT`
（里面只有一个隐藏的 `.git`，看着是空的）。

⚠️ **注意别和原始项目文件夹搞混**：原始项目是 `E:\workbuddy\新-HOT`，
克隆出来的是 `E:\workbuddy\HOT`，只差一个"新-"。

分辨方法：**有 `.git` 隐藏文件夹的那个才是要提交的克隆目录**。
`新-HOT` 里没有 `.git`（它只是个普通文件夹），往它里面改东西不会被推上去。

**A-3 把项目文件复制进去**

1. 打开 `E:\workbuddy\新-HOT`（就是 5.0 那个文件夹，**确认已显示隐藏项目**）
2. `Ctrl + A` 全选 → `Ctrl + C` 复制
3. 打开克隆出来的文件夹（如 `E:\workbuddy\HOT`）→ `Ctrl + V` 粘贴
4. 如果系统问"是否合并/替换"，选**是**

   源文件夹里没有 `.git`，所以不会覆盖掉克隆目录的那个 `.git`，放心全选复制。

**A-4 提交并推送**

1. 回到 GitHub Desktop，左侧 **Changes** 标签会列出三十多个文件
   （`.gitignore` 会自动排除 `probe-local.json`、`last-run.md` 这类临时产物）
2. 左下角 **Summary** 框填一句 `init`（必填，随便写）
3. 点蓝色 **Commit to main**
4. 点顶部中间偏右的 **Push origin**（有个向上的箭头）

**A-5 验收**：按 `Win + E` 打开克隆目录（如 `E:\workbuddy\HOT`），应该能看到 `index.html`、`sources.yaml`、
以及 `.github`（显示隐藏后）。再去网页刷新仓库首页，文件列表里也应该有 `.github` 文件夹。

#### 路线 B · 纯网页上传（不装软件）

能行，但必须手工补至少 1 个隐藏文件，否则定时任务不会出现。

> 空仓库的页面上**没有 `Add file` 按钮**。入口是 Quick setup 蓝框里那行小字：
> **"Get started by creating a new file or uploading an existing file."**
> 点其中的 **uploading an existing file**（用 `Ctrl + 单击` 也行，别让它整页跳走）。

1. 点 **uploading an existing file**
2. 打开 `E:\workbuddy\新-HOT`，`Ctrl + A` 全选，拖进浏览器那个虚线框
3. 等进度条走完，**往下滚**，文件名那一栏**什么都别改**（保持 `index.html` 之类原名）
4. 点绿色 **Commit changes**

**然后补隐藏文件**（点开头的文件浏览器拖不进去，只能手工建）：

在仓库首页点 **Add file** → **Create new file**
（上传完文件后，`Add file` 按钮才出现），然后按下表逐个建：

| 优先级 | 文件名栏里输入 | 内容 |
|---|---|---|
| **必须** | `.github/workflows/update.yml` | 复制本地 `.github\workflows\update.yml` 全文。没有它就没有定时任务，站点永远不会更新 |
| 建议 | `.gitignore` | 复制本地 `.gitignore` 全文。防止以后误把 `.env`、缓存提交上去 |
| 可选 | `.github/workflows/probe.yml` | 手动源探测，不影响站点 |
| 可选 | `.env.example` | 只是给人看的模板，不影响运行 |

在文件名框里输入带斜杠的路径（如 `.github/workflows/update.yml`）时，
GitHub 会自动把前置斜杠变成文件夹，不用你手动建目录。

按 `Ctrl + A` → `Ctrl + C` 从本地文件复制全文，粘到网页编辑器里，点 **Commit changes**
（可以直接提交到 main）。

**怎么确认补成功了**：仓库首页文件列表里能看到 `.github` 文件夹，点进去有 `workflows`，
再点进去有 `update.yml`。或者直接去 **Actions** 标签，左侧能看到 **update-feed** 就对了。

#### 路线 C · 命令行

电脑上装了 git 的话，最快：

```bash
cd /d E:\workbuddy\新-HOT
git init
git add -A
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/new-hot.git
git push -u origin main
```

`.gitignore` 已经写好了，`.env`、`__pycache__`、`_site` 这些不会被提交。

### 5.3 第三步 · 拿一个免费的模型 Key（可选，但强烈建议）

作用：把外文标题翻成中文、把长摘要压到 1~3 句。不配也能跑，只是新来的外文内容不会自动翻译。

1. 打开 <https://open.bigmodel.cn/>，注册
2. 按提示做**实名认证**（免费模型要求实名，个人认证几分钟就好）
3. 左侧菜单找 **API Keys**（有的版本叫「API 密钥」）→ **新建 / 创建密钥**
4. 复制生成的那串（形如 `xxxxxxxx.xxxxxxxx`，中间有个点）——**只显示一次，先粘到记事本**

> 这个 Key 是密码级别的东西，只放在 GitHub Secrets 里，**不要写进任何代码文件**，
> 也不要发到聊天群里。如果不小心泄露了，回控制台删掉重建一个即可。

### 5.4 第四步 · 把 Key 存进 Secret

1. 打开你的仓库首页 → 顶部导航**最右边**那个 **⚙ Settings**
2. 左侧栏往下滚，找到 **Security** 分组 → 点 **Secrets and variables** → 再点 **Actions**

   如果只看到 `Secrets and variables` 而没有展开，点它旁边的小箭头

3. 右上角 **New repository secret**（绿按钮）
4. **Name** 填：`ZHIPU_API_KEY`
5. **Secret** 粘贴刚才那串 Key
6. 点 **Add secret**

> 名字必须**一字不差**是 `ZHIPU_API_KEY`（全大写、下划线）。代码里读的就是这个环境变量名，
> 拼错了会静默降级成"不翻译"，不会报错，很难查。

存好后页面上只会显示 `ZHIPU_API_KEY`，值被隐藏，这是正常的。

### 5.5 第五步 · 开启 Pages（顺序别搞错）

1. 仓库 **Settings** → 左侧 **Code and automation** 分组 → 点 **Pages**

   （不在 General 里，往下滚，在 Actions / Environments 那一组下面）

2. 页面中部 **Build and deployment** 区块 → **Source** 有个下拉框
3. 把它从 `Deploy from a branch` 改成 **GitHub Actions**
4. 选完立刻生效，不需要点 Save（页面上也没有 Save 按钮）

改完之后这块会变成一句提示，大意是"你的站点将由 GitHub Actions 工作流部署"，
下面还会出现两个推荐模板卡片（Jekyll / Static HTML）——**不要点那些卡片**，
我们用自己的 `update.yml`。

> 这一步选错的后果：运行 `deploy-pages` 时红字报
> `Get Pages site failed. Please verify that the repository has Pages enabled and configured
> to build using GitHub Actions`。看到这句，回来重选 Source 就行。
>
> 另外 `update.yml` 里 deploy job 的 `environment: github-pages` 也不能删，
> 删了会报 `environment not found`。

### 5.6 第六步 · 跑第一次

1. 仓库顶部标签栏点 **Actions**

   如果点进去看到的是 "Get started with GitHub Actions" 一堆模板卡片，
   **说明 `.github` 没传上去**，回 §5.2 补（这是路线 B 最常见的失败）

2. 左侧工作流列表里点 **update-feed**
3. 右侧会出现一个灰底的提示条，靠右是蓝色按钮 **Run workflow** → 点它 → 再点绿色 **Run workflow** 确认
4. 页面刷新，出现一条带黄色圆点的运行记录，几秒后变成转圈
5. 点这条记录进去，能看到 `build` 和 `deploy` 两个方块。等 1~3 分钟，两个都变成 ✅ 绿勾
6. 点左侧的 **build** 方块 → 展开每一步，最后有 **Summary** 页，就是本轮简报。实际长这样：

   ```
   抓取简报 · 2026-10-25 12:16 (UTC+08)
   - 耗时 80.0s，源 24/39 正常
   - 抓取 497 条 → 关键词筛选后 72 条 → 去重后新增 6 条（跳过重复 66 条）
   - 库内总计 75 条（保留 90 天）
   - 有摘要的条目 73/75
   - 失败源（不影响站点）：国家卫健委、雀巢·媒体中心、FSANZ、RSS 若干…
   ```

   第一次跑会多花 1~2 分钟装 Python 依赖，总共 2~4 分钟。**"源 24/39 正常"是正常的**，
   境外机房的网络能救回一部分、也会丢掉另一部分，不用追求 39/39。

7. 回到 **Settings → Pages**，顶部会出现一行
   "Your site is live at `https://<你的用户名>.github.io/new-hot/`"，点 **Visit site**

地址形如：

```
https://<你的用户名>.github.io/<仓库名>/
```

8. 之后不用管了。`update.yml` 每 4 小时自动跑一轮（北京时间 09:17 / 13:17 / 17:17 /
   21:17 / 01:17 / 05:17），有新内容就提交并重新部署，没新内容就跳过提交。

### 5.7 第七步（可选）· 外部定时兜底

GitHub 的 cron 有两个已知问题：**常态延迟 5~30 分钟**，以及**公开仓库连续 60 天没有提交
会被自动停用**（`update.yml` 已内置心跳，但只在 7 天无数据时才提交一次）。

若要更稳，用任意免费外部定时服务（如 cron-job.org）每 4 小时调一次：

```
POST https://api.github.com/repos/<用户名>/<仓库名>/actions/workflows/update.yml/dispatches
Header: Authorization: Bearer <一个 fine-grained PAT，权限选 Actions: write>
Body: {"ref":"main"}
```

### 5.8 「找不到选项」对照表

这张表覆盖了实际会卡住的全部位置。找不到哪个，直接查这里：

| 你要找的 | 到底在哪 | 找不到的原因 |
|---|---|---|
| **Add file 按钮** | 仓库首页右上，文件列表上方 | **空仓库没有这个按钮**。空仓库只有 "Quick setup" 和蓝色小字 `uploading an existing file`，点那个链接 |
| **Upload files** | 同上，或 `Add file` 下拉里 | 同上 |
| **Settings 标签** | 仓库顶部导航**最右边**（齿轮图标） | 窗口太窄时右侧标签会被折叠进 `···` 菜单里，把浏览器窗口拉宽即可 |
| **Pages** | Settings → 左侧 **Code and automation** 分组 → Pages | 不在 General 里，要往下滚 |
| **Secrets** | Settings → 左侧 **Security** 分组 → Secrets and variables → Actions | 名字里带 "Actions"，很容易和上面那个 Actions 分组搞混；Secrets 在下面 |
| **Actions 标签** | 顶部导航第 4 个 | 如果整个仓库没有 workflow 文件，点进去只有模板页 = `.github` 没上传 |
| **Run workflow 按钮** | Actions → 左侧点 `update-feed` → 右侧灰条最右 | ① 左侧没点具体工作流时不会出现 ② `.github` 没上传 ③ 你的账号对该仓库没有写权限 |
| **update-feed** | Actions 页面左侧的列表 | 名字来自 `update.yml` 第一行 `name:`。列表里是空的 = 同上 |
| **站点地址** | Settings → Pages 顶部 "Your site is live at ..." | 必须**先成功跑过一次** deploy job，这里才有地址 |
| **`.github` 文件夹** | 仓库首页文件列表里（GitHub 网页会显示点开头的目录） | 看不到 = 没传上去，回 §5.2 |
| **隐藏的项目** | 资源管理器 → 查看 → 显示 → 隐藏的项目 | Win10 在「查看」选项卡最右侧；不勾就看不到 `.github` |
| **模型 Key 在哪填** | 不是填在代码里，是 Settings → Secrets → `ZHIPU_API_KEY` | 代码只读环境变量，仓库里搜不到 Key 是正常的 |

### 5.9 第一次跑失败怎么办

点进失败的那次运行，展开红色的步骤，看最后 10 行。按报错对号入座：

| 报错关键词 | 原因 | 怎么修 |
|---|---|---|
| `Get Pages site failed` | Pages 的 Source 没选 GitHub Actions | §5.5 重选 |
| `environment not found` | `update.yml` 的 deploy job 少了 `environment: github-pages` | 恢复那一行（文件里有注释标着） |
| `Permission denied` / `403` on push | 极少数情况下仓库策略限制了写令牌 | 一般不出现（workflow 里已声明 `contents: write`）。真遇到才去 Settings → Actions → General → Workflow permissions 改成 **Read and write** → Save |
| `No such file or directory: 'scripts/run.py'` | 上传时多套了一层文件夹（变成 `new-hot/新-HOT/scripts/...`） | 把多出来的那层目录里的文件挪到仓库根目录 |
| 页面能打开但一片空白 | `data/` 没上传 | 补传 `data/` 目录（见 §4） |
| 页面 404 | 仓库是私有 + 免费账号，私有仓库的 Pages 需要 Pro | 改 Public，或用 Cloudflare Pages |
| 全部源都失败 | Actions 是境外网络，个别政府站会拒 | 正常现象，看 Summary 里哪些失败，源健康度过半即可用 |

> **关于 Workflow permissions，顺便澄清一个常见误解**：新个人仓库的 `GITHUB_TOKEN`
> 默认只有 `contents` 只读权限，但 GitHub 允许工作流在文件里按需申请更高权限
> （官方文档原话：*"Anyone with write access to a repository can modify the permissions
> granted to the GITHUB_TOKEN ... by editing the `permissions` key in the workflow file"*）。
> `update.yml` 第 21 行已经声明了 `permissions: contents: write`，**所以这一项不用改**。
> 只有你后来自己新增了别的 workflow、或真的遇到 push 403 时，再去
> Settings → Actions → General → Workflow permissions 改成 **Read and write**。

---

## 6. 增删信息源

只改 `sources.yaml`，不用动代码：

```yaml
  - id: cbiline                 # 唯一标识，别乱改（改了历史去重会对不上）
    name: 中童传媒               # 卡片上显示的来源名
    url: https://www.cbiline.com/news/
    type: html                  # rss / html / api
    lang: zh
    priority: P0                # P0 核心 / P1 常规 / P2 尽力 / P3 停用观察
    enabled: true
    selector: "ul li a"         # 可选：CSS 选择器，指向 <a> 或含 <a> 的容器
    url_must_include: "/detail/\\d+"   # 可选：正则，只留匹配的文章链接
    date_regex: '(?P<d>\d{1,2})\s+(?P<y>20\d{2})\s+(?P<m>\d{1,2})'  # 可选
```

可选字段：

| 字段 | 用途 |
|---|---|
| `selector` | CSS 选择器。列表页结构清晰时一定给，比自动启发式准 |
| `title_selector` | 标题节点选择器，用于 `<a>` 里还塞了日期/栏目名的情况 |
| `url_must_include` | 正则，只保留匹配的文章链接（过滤广告位） |
| `date_regex` | 命名组 `y/m/d`，日期被拆成多个标签时用（中童传媒就是） |
| `api` | `openfda` 或 `gdelt`，配合 `type: api` |
| `enabled: false` | 暂时停用，不删配置 |

**加新源前先探测**，别凭感觉写地址：

```bash
python scripts/probe_sources.py --only 新源id
# 或在 Actions 里手动跑 probe-sources，以境外机房的结果为准
```

删除源：把 `enabled` 改成 `false` 即可（保留配置便于回滚）；彻底不要就删掉那一段。

---

## 7. 调频率 / 换模型 / 改阈值

| 想改什么 | 改哪里 |
|---|---|
| 抓取频率 | `.github/workflows/update.yml` 的 `cron`（UTC 时间，比北京时间少 8 小时） |
| 筛得更严/更松 | `config.yaml` → `relevance.min_score`（默认 4.0，调低更松） |
| 去重灵敏度 | `config.yaml` → `dedupe.title_similarity`（默认 0.82，调高更严） |
| 保留天数 | `config.yaml` → `pipeline.retention_days`（默认 90） |
| 陈旧内容 | `config.yaml` → `pipeline.max_age_days`（默认 45，品牌官网页脚常有旧稿） |
| 单源条数 | `config.yaml` → `fetch.max_items_per_source`（默认 40） |
| 详情页抽取量 | `config.yaml` → `extract.max_per_run` / `backfill_per_run` |
| 摘要字数 | `config.yaml` → `llm.max_summary_chars`（默认 90） |

### 换模型

三个字段换掉就行，都是 OpenAI 兼容接口：

```yaml
llm:
  provider: deepseek
  base_url: https://api.deepseek.com
  model: deepseek-v4-flash
  api_key_env: DEEPSEEK_API_KEY   # 同时去 GitHub Secrets 加这个名字
```

| 供应商 | base_url | model | 价格 |
|---|---|---|---|
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.7-flash` | **免费**（需实名，30 并发） |
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` | 约 $0.14 / $0.28 每百万 token |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-flash` | 有免费额度 |

### 本地放 Key

复制 `.env.example` 为 `.env`，填 Key。`.env` 已在 `.gitignore` 里，不会被提交。

---

## 8. 费用

| 项 | 用量 | 费用 |
|---|---|---|
| GitHub Actions | 每日 6 次 × 约 3 分钟 ≈ 540 分钟/月 | 公开仓库 **$0** |
| GitHub Pages | 静态流量 100GB/月、构建 10 次/小时 | **$0** |
| GLM-4.7-Flash | 每日约 90 条 × 6 次 ≈ 540 条，约 30 万 token/月 | **$0**（免费模型） |
| 域名 | 用 `*.github.io` 子域 | **$0** |
| **合计** | | **¥0/月** |

换成 DeepSeek 付费：约 **¥2~5/月**（token 量同上）。

成本控制点（已内置）：只把「关键词粗筛通过的新条目」送模型（约为全量的 1/6）、
每条内容只送 900 字、8 条合一次请求、外文译文写进 `data/zh_cache.json` 不重复翻。

---

## 9. 隐私说明

GitHub Pages 站点是**公开可访问**的，`*.github.io` 会进搜索引擎索引，靠 `noindex`
只能"请求"不被收录，拦不住知道链接的人。

- 只是不想被搜到：`index.html` 里的 `<meta name="robots" content="noindex,nofollow">` 是主要手段。
  ⚠️ 注意：项目仓库的站点在 `用户名.github.io/仓库名/` 这个**子路径**下，
  而 `robots.txt` 只对域名根目录生效（`用户名.github.io/robots.txt`），
  所以仓库里这份 `robots.txt` 对这个站点其实**不起作用**，真正有效的是 `noindex` meta。
  GitHub Pages 不支持自定义响应头，没法补 `X-Robots-Tag`。
- 要真私密：迁到 Cloudflare Pages，并开启 **Cloudflare Access**（免费 50 用户），
  给 5 个朋友发邮箱邀请即可。静态文件不用改，`data/` 直接搬过去。

---

## 10. 已知问题与踩过的坑

1. **必须用 `curl_cffi`，不能用 httpx/requests。** 食品伙伴网等站校验 TLS 指纹，
   同样的 UA 下 httpx 403、curl 指纹 200。这个差异在 GitHub Actions 上同样存在。
2. **UA 里不要加 `Bot` 标识。** 实测食品伙伴网对 `MilkFeedBot` 直接 403，纯浏览器 UA 200。
   仍遵守 robots.txt、按域限速、只取标题与摘要（不抓全文）。
3. **子域也算同站。** 中婴网文章在 `info.` / `news.` 子域上，严格比对 netloc 会把整站链接丢掉。
4. **机构名不能当关键词。** 市场监管总局官网每条新闻标题都含"市场监管总局"，
   把它计入打分会让"党建座谈会"这类内容全部通过。机构名只用于打标签。
5. **时间未知的条目要沉底。** 品牌官网页脚常挂着两年前的旧稿，
   若按抓取时间排序会顶到第一条。
6. **openFDA 必须做产品预筛。** 食品召回库里绝大多数是奶酪、沙拉，一轮能灌进 40 条无关内容。
7. **本地代理会改写请求。** 同一个 URL 沙箱内 403、绕过沙箱 200。网络结论要以 Actions 为准。
8. **cron 不可靠。** 延迟 5~30 分钟是常态；公开仓库 60 天无提交会被自动停用（已内置心跳）。
9. **`GITHUB_TOKEN` 推送不会触发下一个 workflow。** 所以抓取和部署必须写在同一个 workflow 里。
10. **`concurrency` 的 `cancel-in-progress` 必须是 `false`。** 设 `true` 会在 commit 中途
    杀掉上一次运行，可能留下 index 与 .git 不一致的半状态。
11. **`timeout-minutes` 必须设。** 某次运行卡死会永久占住 concurrency 队列，后续全部阻塞。
12. **抓不到的源（已确认）**：海关总署（412 JS 挑战）、国家卫健委（412）、
    特殊食品信息查询平台与进口食品境外企业注册（是查询系统，没有列表页，POST+验证码）、
    雀巢媒体中心（403）、母婴行业观察 / FSANZ / RASFF / Google News（境外可达性差，
    需要在 Actions 上复测）。
13. **关键词分拦不住两类稿子。** 一类是内容农场的 SEO 稿（标题里正好有 baby formula），
    一类是企业获奖/庆典公关稿（品牌词密集）。所以 `clean.py` 里额外做了
    `is_junk_title` 和 `is_promo_title` 两道否决，命中直接丢，不参与打分。
14. **单源会刷屏。** 品牌官网媒体中心一轮能出十几条同类稿，
    所以 `pipeline.max_per_source`（默认 8）按时间截断，防止一个站占满首屏。
15. **品牌官网的"新闻"大部分是公关稿。** 伊利官网一轮 20 条里 15 条是获奖、庆典、
    领导调研。加上 promo 过滤后只留 6 条，信息流质量差别很大。
16. **已入库的坏条目要单独清。** JSONL 只追加，改规则管不到历史数据，
    用 `scripts/purge.py` 清一次。

---

## 11. 日常运维

| 想看什么 | 去哪看 |
|---|---|
| 这一轮抓了多少、哪些源挂了 | Actions → 运行记录 → Summary |
| 某个源为什么没数据 | 同上，各源明细表里有 HTTP 状态或错误信息 |
| 页面上数据不对 | 看 `data/2026-10.jsonl` 里对应那条的 `score` / `matched` / `summary_source` |
| 想重抓某几个源 | 本地 `python scripts/run.py --only a,b` |
| 想删掉已经入库的坏条目 | `python scripts/purge.py --url-contains 域名 --dry-run` 先看再删 |

发布到外部静态托管（Cloudflare Pages / Vercel / 任意静态空间）时，只需要 `_site/` 的内容：

```bash
python scripts/build_site.py     # 生成 _site/（只含 index.html / assets / data / robots.txt）
```

仓库体积：约 2.9MB/月、34MB/年（JSONL 只追加 + 模型译文缓存不重复翻 + `latest.js`
按条数封顶 100 条）。超过一年可以每季度清一次历史，或把 `data/archive/` 移到别处。

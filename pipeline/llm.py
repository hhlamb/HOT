"""大模型加工层：一次调用同时完成「相关性精判 + 打标签 + 外文译中 + 写摘要」。

为什么合并成一次调用：
    原方案是一条内容调两次（先判相关性、再写摘要），实测 token 消耗约为合并后的 2.6 倍。
    合并做法是把一批（默认 8 条）打包成一次请求，让模型返回 JSON 数组，逐条给结论。

没有 API Key 时的行为：
    `enrich()` 直接返回 enabled=False，流水线退回「正文抽取 + 本地译文缓存」，
    页面依旧可用。这样本地开发、以及 Key 额度耗尽时都不会阻塞出数据。

支持的供应商（都是 OpenAI 兼容接口，改 base_url + model 即可）：
    - 智谱 GLM：https://open.bigmodel.cn/api/paas/v4   glm-4.7-flash（当前免费）
    - DeepSeek：https://api.deepseek.com               deepseek-v4-flash
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .clean import clip, is_cjk_text
from .sources import ROOT

CACHE_FILE = ROOT / "data" / "zh_cache.json"

# 这几个错误码说明"这个模型本身用不了"，换模型比等下去有效：
#   1305 该模型当前访问量过大（免费池拥塞，实测高峰期连 1 条小请求都进不去）
#   1220 无权访问该模型    1211 模型不存在（下线/改名）
SWITCH_CODES = {"1305", "1220", "1211"}


def _error_code(resp) -> str:
    """从智谱的 {"error":{"code":"1305",...}} 里取错误码，取不到就返回空串。"""
    try:
        err = (resp.json() or {}).get("error") or {}
        return str(err.get("code") or "")
    except Exception:
        return ""

SYSTEM_PROMPT = """你是跨境母婴与婴幼儿奶粉行业的资讯编辑，服务对象是跨境电商与母婴行业的从业者。
用户会给你一个 JSON 数组，每个元素是一篇待处理的文章。请逐条处理并只输出 JSON。

输出格式（不要 Markdown 代码块，不要任何解释）：
{"items":[{"id":"输入里的 id","relevant":1,"title":"中文标题","summary":"中文摘要","points":["要点1","要点2"],"tags":["标签"]}]}

规则：
1. relevant：与「婴幼儿奶粉 / 母婴喂养 / 乳业与食品安全政策 / 原料供应链 / 跨境渠道」强相关填 1；
   泛消费、纯企业形象稿、与行业无关的社会新闻、招聘与活动通知填 0。
2. title：外文标题翻译成中文（品牌名、产品名保留原文或通用译名）；已是中文就原样返回，不要改写。
3. summary：3~5 句中文，120~200 字。要把「谁、做了什么、涉及什么品类/市场/数字、对从业者意味着什么」讲清楚，
   信息足够时可以补一句背景或影响判断；信息不足时只写确凿事实，绝不编造数字、时间、金额、机构表态。
   - 禁止营销话术，禁止「本文」「据悉」「值得一提」「总而言之」这类填充词，禁止复述标题
   - 外文内容直接用中文写，不要保留英文原句
4. points：2~4 条要点，每条不超过 32 字，只放摘要里没展开、但值得单列的关键事实
   （具体数字、生效时间、涉及品类、监管动作、渠道变化）。信息不足就返回空数组 []。
5. tags：从下面列表里选 1~3 个最贴切的，不要自造：
   品牌动态 / 政策法规 / 质量安全 / 市场数据 / 供应链 / 跨境电商 / 海外动态
6. 所有字段一律用简体中文，除品牌与产品专有名词外不出现英文单词。
"""

TAG_LIST = ["品牌动态", "政策法规", "质量安全", "市场数据", "供应链", "跨境电商", "海外动态"]
TAG_FALLBACK = "行业动态"


# ---------------------------------------------------------------- 译文缓存


def _cache_key(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def apply_cache(items: list[dict], cache: dict | None = None) -> int:
    """把缓存里的中文标题/摘要套到条目上。命中就不必再花钱翻一遍。"""
    cache = load_cache() if cache is None else cache
    hit = 0
    for item in items:
        if is_cjk_text(item.get("title", "")):
            continue
        entry = cache.get(_cache_key(item["title"]))
        if not entry:
            continue
        if entry.get("title"):
            item["title_zh"] = entry["title"]
        # 有 "summary" 这个键就以它为准（哪怕是空串）：
        # 缓存里的中文摘要不存在时，宁可留空，也不要展示英文原文
        if "summary" in entry:
            item["summary"] = entry["summary"] or ""
        item["translated"] = True
        hit += 1
    return hit


# ---------------------------------------------------------------- HTTP 调用


def _api_key(cfg: dict) -> str:
    for env in (cfg.get("api_key_env") or "ZHIPU_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(env, "").strip()
        if value:
            return value
    # 本地开发允许放 .env（.env 已在 .gitignore 里）
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() in (cfg.get("api_key_env") or "ZHIPU_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"):
                return v.strip().strip('"').strip("'")
    return ""


def available(cfg: dict) -> bool:
    return bool(_api_key(cfg))


def _post(url: str, headers: dict, payload: dict, timeout: int):
    from curl_cffi import requests as cffi

    return cffi.post(url, headers=headers, json=payload, timeout=timeout)


def _parse_json_loose(text: str) -> dict:
    """模型偶尔会带 ```json 包裹或前后废话，这里做一次兜底提取。"""
    text = (text or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def _batch_payload(items: list[dict], cfg: dict) -> list[dict]:
    payload = []
    for item in items:
        body = (item.get("_body") or "").strip()
        payload.append(
            {
                "id": item["id"],
                "title": item["title"],
                "source": item.get("source_name", ""),
                "language": item.get("language", "zh"),
                "content": clip(body or item.get("summary") or "", 1800),
            }
        )
    return payload


def enrich(items: list[dict], cfg: dict, log=print, caller=None) -> dict:
    """批量为条目生成中文标题与摘要，原地修改 items。

    返回 {"enabled", "calls", "tokens_in", "tokens_out", "done", "failed", "dropped"}
    """
    stats = {
        "enabled": False,
        "calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "done": 0,
        "failed": 0,
        "dropped": 0,
        "skipped": 0,
    }
    if not items:
        return stats
    key = _api_key(cfg)
    if not key and caller is None:
        return stats

    stats["enabled"] = True
    base = (cfg.get("base_url") or "").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    # 模型降级链：主模型在免费池高峰会整池返回 1305（该模型访问量过大），
    # 这时换一个模型立刻就能过，比在同一模型上退避有效得多。
    models = [cfg.get("model") or ""] + [m for m in (cfg.get("model_fallbacks") or []) if m]
    models = [m for m in models if m]
    mi = 0
    batch_size = max(1, int(cfg.get("batch_size", 8)))
    retries = int(cfg.get("retries", 3))
    budget = float(cfg.get("max_wall_seconds", 720))
    deadline = time.monotonic() + budget
    # 免费池的限流阈值不公开，用「自适应节奏」代替猜：撞过 429 就放慢，顺利就慢慢加速。
    # 固定小间隙对付不了"每分钟只放几个请求"这种配额。
    base_pace = float(cfg.get("batch_gap_seconds", 1.5))
    pace = base_pace
    cache = load_cache()
    cache_dirty = False
    fatal_fails = 0
    rl_streak = 0  # 连续限流批次数

    for i in range(0, len(items), batch_size):
        # 免费模型高峰期限流严重，攒够时间预算就收工，剩下的下一轮接着补，别把 job 拖超时
        if time.monotonic() > deadline:
            left = len(items) - i
            stats["skipped"] += left
            log(f"  · 模型时间预算 {int(budget)}s 用尽，{left} 条留到下一轮")
            break
        if i:
            time.sleep(pace)
        batch = items[i : i + batch_size]
        user_content = json.dumps({"articles": _batch_payload(batch, cfg)}, ensure_ascii=False)
        payload = {
            "model": models[mi],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": cfg.get("temperature", 0.2),
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if cfg.get("disable_thinking") and re.search(r"glm-(4\.[5-9]|z)", payload["model"] or ""):
            payload["thinking"] = {"type": "disabled"}

        data = None
        relaxed = False
        fatal = False
        rate_limited = False
        tries = 0
        for attempt in range(retries + 1):
            tries += 1
            try:
                resp = (caller or _post)(url, headers, payload, int(cfg.get("timeout_seconds", 90)))
                if resp.status_code >= 400:
                    # 不同 GLM 版本对 thinking / response_format 的支持不一致，报 400 时
                    # 先去掉这两个可选参数重试一次（_parse_json_loose 本来就能兜住非 JSON 输出），
                    # 免得因为一个可选字段让整轮一个摘要都出不来。
                    if resp.status_code == 400 and not relaxed and ("thinking" in payload or "response_format" in payload):
                        payload.pop("thinking", None)
                        payload.pop("response_format", None)
                        relaxed = True
                        log(f"  · 接口拒绝可选参数（{resp.text[:60]}），去掉后重试")
                        continue
                    # 这个模型本身不可用（免费池拥塞 1305 / 无权限 1220 / 名字不对 1211）：
                    # 换降级链里的下一个模型，比在同一模型上退避有效得多。
                    code = _error_code(resp)
                    if code in SWITCH_CODES and mi + 1 < len(models):
                        log(f"  · {models[mi]} 不可用（{code}），切到 {models[mi + 1]}")
                        mi += 1
                        payload["model"] = models[mi]
                        continue
                    fatal = resp.status_code in (401, 403, 404)
                    rate_limited = resp.status_code == 429
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
                data = resp.json()
                break
            except Exception as exc:
                if fatal or attempt >= retries:
                    log(f"  ! 模型调用失败（第 {i // batch_size + 1} 批）：{str(exc)[:120]}")
                    stats["failed"] += len(batch)
                    break
                # 429 是"整池拥塞"，慢慢退避；其它错误按普通重试
                wait = min(90, 12 * (2**attempt)) if rate_limited else 2 * (attempt + 1)
                if rate_limited and attempt == 0:
                    log(f"  · 免费模型限流（429），退避 {wait}s 后重试")
                time.sleep(wait)

        if not data:
            if fatal:
                # 连续两批鉴权/权限失败 = Key 无效或模型名写错，继续打只会耗光 job 超时
                fatal_fails += 1
                if fatal_fails >= 2:
                    rest = len(items) - (i + len(batch))
                    stats["failed"] += rest
                    log(f"  ! 连续 {fatal_fails} 批鉴权失败，跳过剩余 {rest} 条（查 Key / 余额 / 模型名）")
                    break
            elif rate_limited:
                rl_streak += 1
                stats["skipped"] += len(batch)
                if rl_streak >= 4:
                    # 连着四批都撞墙，说明这会儿整池都满，继续打只是耗时间预算
                    rest = len(items) - (i + len(batch))
                    stats["skipped"] += rest
                    stats["rate_limited"] = True
                    log(f"  · 连续 {rl_streak} 批限流，本轮先处理已成功的，剩余 {rest} 条下一轮继续")
                    break
                # 单批限流 ≠ 全池满：跳过这批、放慢节奏继续试后面的，
                # 实测免费池是"抖动式放行"，隔几分钟往往又能挤进去几条。
                pace = min(90.0, pace * 2 + 5)
                log(f"  · 第 {i // batch_size + 1} 批限流，放慢到 {int(pace)}s 后继续")
                continue
            time.sleep(pace)
            continue
        fatal_fails = 0
        rl_streak = 0
        # 自适应节奏：退避后才成功 → 说明这个速度太快，放慢；一次就过 → 慢慢加速回来
        if tries > 1:
            pace = min(60.0, pace * 2 + 4)
        else:
            pace = max(base_pace, pace * 0.8)
        stats["calls"] += 1
        usage = data.get("usage") or {}
        stats["tokens_in"] += int(usage.get("prompt_tokens") or 0)
        stats["tokens_out"] += int(usage.get("completion_tokens") or 0)

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            stats["failed"] += len(batch)
            continue

        parsed = _parse_json_loose(content)
        rows = parsed.get("items") or parsed.get("data") or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict)}

        for item in batch:
            row = by_id.get(str(item["id"]))
            if not row:
                stats["failed"] += 1
                continue
            if str(row.get("relevant", "1")).strip() in ("0", "false", "False"):
                item["_drop"] = "模型判定不相关"
                stats["dropped"] += 1
                continue
            title_zh = (row.get("title") or "").strip()
            summary = (row.get("summary") or "").strip()
            if title_zh and is_cjk_text(title_zh):
                item["title_zh"] = clip(title_zh, 120)
            if summary:
                item["summary"] = clip(re.sub(r"\s+", " ", summary), int(cfg.get("max_summary_chars", 220)))
                item["summary_source"] = "llm"
            points = [clip(re.sub(r"\s+", " ", str(x)).strip(), 60) for x in (row.get("points") or []) if str(x).strip()]
            if points:
                item["points"] = points[:4]
                # 模型的要点比抽取式的好，标记来源让落盘时能覆盖前者
                item["points_source"] = "llm"
            tags = [t for t in (row.get("tags") or []) if t in TAG_LIST][:3]
            if tags:
                item["tags"] = tags
            item["translated"] = True
            stats["done"] += 1

            # 外文条目顺手写进译文缓存，下次同标题不再花钱
            if not is_cjk_text(item["title"]):
                cache[_cache_key(item["title"])] = {"title": item.get("title_zh", ""), "summary": item.get("summary", "")}
                cache_dirty = True

    if cache_dirty:
        try:
            save_cache(cache)
        except Exception:
            pass
    return stats

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

SYSTEM_PROMPT = """你是跨境母婴与婴幼儿奶粉行业的资讯编辑，服务对象是从业者。
用户会给你一个 JSON 数组，每个元素是一篇待处理的文章。请逐条处理并只输出 JSON。

输出格式（不要 Markdown 代码块，不要任何解释）：
{"items":[{"id":"输入里的 id","relevant":1,"title":"中文标题","summary":"中文摘要","tags":["标签"]}]}

规则：
1. relevant：与「婴幼儿奶粉 / 母婴喂养 / 乳业与食品安全政策 / 原料供应链 / 跨境渠道」强相关填 1；
   泛消费、纯企业形象稿、与行业无关的社会新闻、招聘与活动通知填 0。
2. title：外文标题翻译成中文（品牌名、产品名保留原文或通用译名）；已是中文就原样返回，不要改写。
3. summary：1~3 句中文，不超过 90 字，只陈述事实（谁、做了什么、有什么影响）。
   - 禁止营销话术、禁止「本文」「据悉」「值得一提」这类填充词，禁止复述标题
   - 外文内容直接用中文写摘要，不要保留英文原句
   - 信息不足时，宁可写一句短的，也不要编造数字、时间、金额
4. tags：从下面列表里选 1~3 个最贴切的，不要自造：
   品牌动态 / 政策法规 / 质量安全 / 市场数据 / 供应链 / 跨境电商 / 海外动态
5. 所有字段一律用简体中文，除品牌与产品专有名词外不出现英文单词。
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
                "content": clip(body or item.get("summary") or "", 900),
            }
        )
    return payload


def enrich(items: list[dict], cfg: dict, log=print, caller=None) -> dict:
    """批量为条目生成中文标题与摘要，原地修改 items。

    返回 {"enabled", "calls", "tokens_in", "tokens_out", "done", "failed", "dropped"}
    """
    stats = {"enabled": False, "calls": 0, "tokens_in": 0, "tokens_out": 0, "done": 0, "failed": 0, "dropped": 0}
    if not items:
        return stats
    key = _api_key(cfg)
    if not key and caller is None:
        return stats

    stats["enabled"] = True
    base = (cfg.get("base_url") or "").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    batch_size = max(1, int(cfg.get("batch_size", 8)))
    cache = load_cache()
    cache_dirty = False

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        user_content = json.dumps({"articles": _batch_payload(batch, cfg)}, ensure_ascii=False)
        payload = {
            "model": cfg.get("model"),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": cfg.get("temperature", 0.2),
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if cfg.get("disable_thinking") and "glm" in (cfg.get("model") or ""):
            payload["thinking"] = {"type": "disabled"}

        data = None
        for attempt in range(int(cfg.get("retries", 2)) + 1):
            try:
                resp = (caller or _post)(url, headers, payload, int(cfg.get("timeout_seconds", 90)))
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")
                data = resp.json()
                break
            except Exception as exc:
                if attempt >= int(cfg.get("retries", 2)):
                    log(f"  ! 模型调用失败（第 {i // batch_size + 1} 批）：{str(exc)[:120]}")
                    stats["failed"] += len(batch)
                    break
                time.sleep(2 * (attempt + 1))

        if not data:
            continue
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
                item["summary"] = clip(re.sub(r"\s+", " ", summary), int(cfg.get("max_summary_chars", 90)))
                item["summary_source"] = "llm"
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

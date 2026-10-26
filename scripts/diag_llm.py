"""免费模型限流诊断。

网上免费池的 429 有三种，治法完全相反：
  · QPS/并发限制   → 拉长间隔重试有效
  · 每分钟 token 配额(TPM) → 要把单次请求做小，重试必须等满一分钟窗口
  · 每日调用次数上限 → 再怎么退避都没用，只能减少调用次数
所以先打三枪，看响应头和报错文本判断是哪一种。

只在 CI 里跑，Key 从仓库 Secret 注入。
"""

import json
import os
import sys
import time

import requests

BASE = "https://open.bigmodel.cn/api/paas/v4"
MODEL = "glm-4.7-flash"
KEY = os.environ.get("ZHIPU_API_KEY", "")

FILLER = (
    "据行业监测数据，本月婴配粉线上渠道销售额环比增长百分之七，其中三段产品占比最高。"
    "多家乳企在投资者交流中表示，新国标切换带来的配方调整已基本完成，接下来重心转向渠道下沉。"
)


def call(tag: str, n_articles: int, body_chars: int, max_tokens_hint: int = 220) -> bool:
    arts = [
        {
            "id": i,
            "title": f"测试标题 {i}：婴配粉渠道与配方注册动态",
            "source": "测试源",
            "tags": ["行业动态"],
            "content": (FILLER * 40)[:body_chars],
        }
        for i in range(1, n_articles + 1)
    ]
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "你是母婴行业资讯编辑，返回 JSON。"},
            {"role": "user", "content": json.dumps({"articles": arts}, ensure_ascii=False)},
        ],
        "temperature": 0.2,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    req_bytes = len(json.dumps(payload, ensure_ascii=False).encode())
    t0 = time.monotonic()
    try:
        r = requests.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
    except Exception as exc:
        print(f"[{tag}] 请求异常 {type(exc).__name__}: {exc}")
        return False
    dt = int((time.monotonic() - t0) * 1000)
    rl_headers = {k: v for k, v in r.headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
    print(f"[{tag}] {n_articles} 条 / 请求 {req_bytes}B → {r.status_code} 耗时 {dt}ms")
    if rl_headers:
        print(f"       限流头：{json.dumps(rl_headers, ensure_ascii=False)}")
    if r.status_code >= 400:
        print(f"       报错：{r.text[:400]}")
        return False
    usage = (r.json() or {}).get("usage") or {}
    print(f"       token {usage.get('prompt_tokens')}+{usage.get('completion_tokens')}")
    print(f"       输出前 120 字：{json.dumps(r.json(), ensure_ascii=False)[:120]}")
    return True


def main() -> int:
    if not KEY:
        print("没有 ZHIPU_API_KEY，跳过")
        return 1
    print(f"模型 {MODEL}\n")
    # 1) 小请求：能不能通
    ok1 = call("小请求 1条×600字", 1, 600)
    # 2) 立刻再来一发同样的小请求：看是不是 QPS 限制
    time.sleep(1)
    call("连发 1条×600字", 1, 600)
    # 3) 真实负载规模：5 条×1800 字
    time.sleep(1)
    call("真实规模 5条×1800字", 5, 1800)
    # 4) 等满一分钟窗口再打一发大请求，验证是不是 TPM
    print("\n等 65s 让配额窗口翻页…")
    time.sleep(65)
    ok4 = call("等满窗口 5条×1800字", 5, 1800)
    print(f"\n小请求={'通' if ok1 else '挂'} / 等满窗口后={'通' if ok4 else '挂'}")
    if not ok1:
        print("→ 连小请求都过不去：每日额度或账号权限问题，减少调用次数才有用")
    elif ok1 and not ok4:
        print("→ 小请求能过、大请求过不去：TPM 配额，要把 content 切短、批次调小")
    else:
        print("→ 都能过：限流是抖动的，保持退避重试即可")
    return 0


if __name__ == "__main__":
    sys.exit(main())

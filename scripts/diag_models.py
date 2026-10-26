"""探测同账号下哪些模型还能用。

glm-4.7-flash 免费池高峰期会整体返回 1305（模型访问量过大），跟我们的配额无关，
再怎么退避也进不去。所以先摸一遍候选模型，挑一个能稳定出结果的。

错误码含义：
  1302 = 本账号速率限制（我们自己太快）
  1305 = 该模型当前访问量过大（服务端拥塞，免费池常见）
  1113 = 余额/额度不足
  1211 = 模型不存在
"""

import json
import os
import sys
import time

import requests

BASE = "https://open.bigmodel.cn/api/paas/v4"
KEY = os.environ.get("ZHIPU_API_KEY", "")

CANDIDATES = [
    "glm-4.7-flash",
    "glm-4.6-flash",
    "glm-4.5-flash",
    "glm-4-flash",
    "glm-4-flash-250414",
    "glm-z1-flash",
    "glm-4-air",
    "glm-4-air-250414",
    "glm-4.7",
    "glm-4.6",
]


def probe(model: str) -> tuple[str, str]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "只回 JSON。"},
            {"role": "user", "content": '把这句话概括成一句：婴配粉行业本月线上销量环比增长。输出 {"summary":"..."}'},
        ],
        "temperature": 0.2,
        "max_tokens": 60,
        "stream": False,
    }
    try:
        r = requests.post(
            f"{BASE}/chat/completions",
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
    except Exception as exc:
        return "EXC", f"{type(exc).__name__}: {exc}"
    if r.status_code == 200:
        try:
            txt = r.json()["choices"][0]["message"]["content"]
        except Exception:
            txt = r.text[:80]
        return "OK", txt.replace("\n", " ")[:90]
    try:
        err = r.json().get("error") or {}
        return str(err.get("code") or r.status_code), str(err.get("message") or r.text)[:80]
    except Exception:
        return str(r.status_code), r.text[:80]


def main() -> int:
    if not KEY:
        print("没有 ZHIPU_API_KEY")
        return 1
    # 先看有没有模型列表端点
    try:
        r = requests.get(f"{BASE}/models", headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
        print(f"GET /models → {r.status_code} {r.text[:300]}")
    except Exception as exc:
        print(f"GET /models 异常：{exc}")

    alive = []
    for m in CANDIDATES:
        code, detail = probe(m)
        mark = "✅" if code == "OK" else "✗"
        print(f"{mark} {m:<22} → {code} {detail}")
        if code == "OK":
            alive.append(m)
        time.sleep(2)

    # 对能用的再连打 3 次，看稳定性（免费池可能只是恰好放行）
    for m in alive:
        ok = 0
        for _ in range(3):
            time.sleep(2)
            if probe(m)[0] == "OK":
                ok += 1
        print(f"稳定性 {m}: {ok}/3")

    print(f"\n可用模型：{alive or '无'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

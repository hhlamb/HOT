"""Google News 跳转链解码诊断。

中转页是纯 JS 壳，必须拿 data-n-a-sg / data-n-a-ts 调 batchexecute 换原文地址。
接口的请求体格式改过几次，这里一次把所有候选格式都打一遍，看哪个能拿到地址，
并把失败时的原始响应打出来，方便按现场改代码。

只在 CI 里跑（本地访问不到 news.google.com）。
"""

import json
import re
import sys
from urllib.parse import quote

from curl_cffi import requests as creq

ENDPOINT = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

SAMPLES = [
    "https://news.google.com/rss/articles/CBMikgFBVV95cUxNdmpldlRuajByRE1hMEpYcEExUmRicUMyRW5abk5QckdIRUhCUzhLNXZCemhLS1dNZDFBZWFLc1puYVdvcVVJWm8zb0tvQTQ4ZVdWNWwyTXRldkxMR0ticDJHVlhRVVBEQVpVU1lpZ1diSlZ2aXNMU2g3eFhQNjM3OUl1ckRmMkZMU1Z3cGl2WXh3UQ",
    "https://news.google.com/rss/articles/CBMiVkFVX3lxTFAxSVczSTFBTVNUUGdZWVlBaXppWlRMb1JHSlRBcUdJOTVSbkdYU2VhY3FJM0FsaFJvR1RfaExvSU5ZQ3BiUzcyaV9heUwwZTQxMnJQNVlB",
]

HDRS = {
    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}


def gartreq(art_id: str, ts: str, sig: str, locale: str = "US:en") -> str:
    return (
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,'
        f'"{locale}",null,1,null,null,null,null,null,0,1],'
        f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{art_id}",{ts},"{sig}"]'
    )


def parse_reply(text: str) -> str:
    """从 batchexecute 的多段响应里抠出原文地址。"""
    for chunk in text.split("\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith(")]}'"):
            chunk = chunk[chunk.find("\n") + 1 :] if "\n" in chunk else chunk[4:]
        for candidate in (chunk,):
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            for row in parsed if isinstance(parsed, list) else []:
                if not (isinstance(row, list) and len(row) > 2):
                    continue
                payload = row[2]
                if not (isinstance(payload, str) and "garturlres" in payload):
                    continue
                try:
                    inner = json.loads(payload)
                except Exception:
                    continue
                if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], str):
                    if inner[1].startswith("http"):
                        return inner[1]
    return ""


def main() -> int:
    client = creq.Session(impersonate="chrome")
    ok = 0
    for idx, url in enumerate(SAMPLES, 1):
        print(f"\n=== 样本 {idx} ===")
        art_id = url.split("/articles/")[-1].split("?")[0]
        try:
            page = client.get(url, headers=HDRS, timeout=25, allow_redirects=True)
        except Exception as exc:
            print(f"  取中转页失败：{type(exc).__name__}: {exc}")
            continue
        html = page.text or ""
        final = str(getattr(page, "url", "") or "")
        sig = re.search(r'data-n-a-sg="([^"]+)"', html)
        ts = re.search(r'data-n-a-ts="([^"]+)"', html)
        nau = re.search(r'data-n-au="([^"]+)"', html)
        print(f"  status={page.status_code} html={len(html)}B final={final[:80]}")
        print(f"  签名 {'有' if sig else '无'} / 时间戳 {'有' if ts else '无'} / data-n-au {'有' if nau else '无'}")
        if nau:
            print(f"  data-n-au = {nau.group(1)[:110]}")
        if not (sig and ts):
            head = re.sub(r"\s+", " ", html)[:400]
            print(f"  页面开头：{head}")
            continue

        inner = gartreq(art_id, ts.group(1), sig.group(1))
        variants = {
            "A [[[Fbv4je,inner]]]": [[["Fbv4je", inner]]],
            "B [[Fbv4je,inner]]": [["Fbv4je", inner]],
            "C [[Fbv4je,inner,null,generic]]": [["Fbv4je", inner, None, "generic"]],
            "D [[[Fbv4je,inner,null,generic]]]": [[["Fbv4je", inner, None, "generic"]]],
        }
        for name, payload in variants.items():
            body = "f.req=" + quote(json.dumps(payload))
            try:
                r = client.post(ENDPOINT, data=body, headers=HDRS, timeout=25)
            except Exception as exc:
                print(f"  {name} → 请求异常 {type(exc).__name__}: {exc}")
                continue
            text = r.text or ""
            real = parse_reply(text)
            print(f"  {name} → {r.status_code} {len(text)}B 结果={real[:110] or '(空)'}")
            if real:
                ok += 1
                break
            if r.status_code != 200 or idx == 1:
                print(f"     原始响应：{re.sub(chr(10), ' | ', text)[:400]}")
    print(f"\n成功解出 {ok}/{len(SAMPLES)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

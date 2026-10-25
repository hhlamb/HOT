"""统一的 HTTP 客户端：User-Agent、超时、重试、robots.txt、按域名限速。

为什么用 curl_cffi 而不是 httpx / requests：
    很多站点（foodmate、雀巢、部分政务站）会对 TLS 指纹做校验，
    同样一个 Chrome 的 User-Agent，httpx 请求被 403，curl 指纹却是 200。
    curl_cffi 能模拟 Chrome 的 TLS/JA3 指纹，实测把 foodmate 从 403 救回 200。
    这个差异在 GitHub Actions 上同样存在，所以不是本地环境问题。

礼貌抓取约束：
- 全局并发有上限，但**同一域名严格串行**，两次请求之间随机间隔
- 失败重试最多 2 次，只对网络异常 / 5xx / 429 重试，其他 4xx 直接放弃
- robots.txt 按域名缓存，取不到视为"未知"，未知时放行但记录
"""

from __future__ import annotations

import random
import threading
import time
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from curl_cffi import requests as cffi


class PoliteClient:
    def __init__(self, settings: dict):
        self.user_agent = settings["user_agent"]
        self.timeout = settings["timeout_seconds"]
        self.retries = settings["retries"]
        self.min_delay = settings["min_delay_seconds"]
        self.max_delay = settings["max_delay_seconds"]
        self.respect_robots = settings.get("respect_robots", True)

        self._local = threading.local()  # 每线程一个 Session，避免 Session 并发问题
        self._host_locks: dict[str, threading.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._guard = threading.Lock()
        self._robots_cache: dict[str, Optional[RobotFileParser]] = {}

    # ---------- 内部工具 ----------

    def _session(self) -> cffi.Session:
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = cffi.Session(impersonate="chrome", timeout=self.timeout, allow_redirects=True)
            self._local.session = sess
        return sess

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            if host not in self._host_locks:
                self._host_locks[host] = threading.Lock()
            return self._host_locks[host]

    def _throttle(self, host: str) -> None:
        """同一域名两次请求之间至少间隔 min_delay~max_delay 秒。"""
        last = self._host_last.get(host)
        if last is not None:
            gap = random.uniform(self.min_delay, self.max_delay)
            wait = gap - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._host_last[host] = time.monotonic()

    def _robots_for(self, base: str) -> Optional[RobotFileParser]:
        with self._guard:
            if base in self._robots_cache:
                return self._robots_cache[base]
        parser: Optional[RobotFileParser] = None
        try:
            resp = self._session().get(
                urljoin(base, "/robots.txt"),
                timeout=10,
                headers={"User-Agent": self.user_agent},
            )
            if resp.status_code == 200 and resp.text.strip():
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
        except Exception:
            parser = None
        with self._guard:
            self._robots_cache[base] = parser
        return parser

    # ---------- 对外接口 ----------

    def allowed_by_robots(self, url: str) -> Optional[bool]:
        """True 允许 / False 禁止 / None 未知（拿不到 robots.txt）。"""
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots_for(base)
        if parser is None:
            return None
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:
            return None

    def get(self, url: str, *, retries: Optional[int] = None, timeout: Optional[float] = None):
        parts = urlparse(url)
        host = parts.netloc
        attempts = (self.retries if retries is None else retries) + 1
        last_exc: Optional[Exception] = None
        resp = None

        for i in range(attempts):
            with self._lock_for(host):
                self._throttle(host)
                try:
                    resp = self._session().get(
                        url,
                        timeout=timeout or self.timeout,
                        headers={
                            "User-Agent": self.user_agent,
                            "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        },
                    )
                except Exception as exc:
                    last_exc = exc
                    resp = None

            if resp is not None:
                if resp.status_code < 400:
                    return resp
                # 429 / 5xx 值得重试；其余 4xx（403/404/412）重试没意义
                if resp.status_code != 429 and resp.status_code < 500:
                    return resp

            if i < attempts - 1:
                time.sleep(1.5 * (i + 1) + random.uniform(0, 0.5))

        if resp is None and last_exc is not None:
            raise last_exc
        return resp

    def close(self) -> None:
        sess = getattr(self._local, "session", None)
        if sess is not None:
            sess.close()

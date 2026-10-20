from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque

from .store import Rejected


class BrowserAdmission:
    """Bounded single-process limits; browser identity is not task authentication."""

    cookie = "myl_browser"
    lifetime = 86400

    def __init__(self, secret: str, clock=time.monotonic, wall_clock=time.time):
        self.key = hmac.digest(secret.encode(), b"browser-rate-limit-v1", "sha256")
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.Lock()
        self.rates: dict[tuple[str, str], deque] = {}

    def _sign(self, value):
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    def identify(self, cookie: str):
        parts = cookie.split(".")
        if len(parts) == 3 and len(cookie) <= 160 and cookie.isascii():
            value = ".".join(parts[:2])
            try:
                age = self.wall_clock() - int(parts[1])
                if 0 <= age < self.lifetime and hmac.compare_digest(
                    parts[2], self._sign(value)
                ):
                    return cookie, False
            except ValueError:
                pass
        value = f"{secrets.token_hex(16)}.{int(self.wall_clock())}"
        return f"{value}.{self._sign(value)}", True

    def check(self, browser: str, ip: str):
        with self.lock:
            now = self.clock()
            for key in list(self.rates):
                entries = self.rates[key]
                while entries and entries[0] <= now - 60:
                    entries.popleft()
                if not entries:
                    del self.rates[key]
            keys = (("browser", browser, 6), ("ip", ip, 60))
            missing = sum((kind, value) not in self.rates for kind, value, _ in keys)
            if len(self.rates) + missing > 4096:
                raise Rejected("rate_limited", 429)
            for kind, value, limit in keys:
                if len(self.rates.get((kind, value), ())) >= limit:
                    raise Rejected(
                        "rate_limited" if kind == "browser" else "ip_rate_limited", 429
                    )
            for kind, value, _ in keys:
                self.rates.setdefault((kind, value), deque()).append(now)

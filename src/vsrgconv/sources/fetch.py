"""Polite HTTP fetching with an on-disk cache that mirrors URLs (`<cache>/<host>/<path>`).

The mirror layout is deliberate: files downloaded any other way (a browser on another machine, a Remote Control
session) can be dropped into the cache and every adapter will use them with `offline=True`. The cloud build machine
cannot reach sdvx.in, so this is how its data gets in (docs/sources/chunithm-sdvxin.md).
"""
from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class FetchError(RuntimeError):
    pass


class NotFound(FetchError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib API
        return None


class Fetcher:
    def __init__(self, cache_dir: str | Path = "data/cache", offline: bool = False, min_interval: float = 1.0,
                 user_agent: str = "vsrgconv/0.1 (personal chart converter)", timeout: float = 30.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.min_interval = min_interval
        self.user_agent = user_agent
        self.timeout = timeout
        self._last = 0.0
        self._lock = threading.Lock()   # the request interval is shared by threads (scrape --workers)
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._calls = threading.local()

    def calls_in_thread(self) -> int:
        """How many files this thread has asked for (from the cache or the network). `scrape` compares it before and
        after a chart to tell a chart some source was asked for from one no source knows."""
        return getattr(self._calls, "n", 0)

    def cache_path(self, url: str) -> Path:
        u = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(u.path).lstrip("/") or "index"
        if u.query:
            path += "@" + u.query.replace("/", "_")
        return self.cache_dir / u.netloc / path

    def get(self, url: str, refresh: bool = False) -> bytes:
        self._calls.n = self.calls_in_thread() + 1
        p = self.cache_path(url)
        if p.exists() and not refresh:
            return p.read_bytes()
        if self.offline:
            raise NotFound(f"offline and not cached: {url} (expected at {p})")
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last)
            self._last = time.monotonic() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308, 404):
                raise NotFound(f"{url}: HTTP {e.code}") from None
            raise FetchError(f"{url}: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise FetchError(f"{url}: {getattr(e, 'reason', e)}") from None
        p.parent.mkdir(parents=True, exist_ok=True)
        # whole or not at all: another thread (or process) may read this file the moment it exists, as when several
        # charts share one picture layer
        tmp = p.with_name(f"{p.name}.part-{os.getpid()}-{threading.get_ident()}")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        return data

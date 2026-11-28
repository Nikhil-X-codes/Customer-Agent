"""Bounded in-process HTTP controls; the gateway should also enforce limits."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from http.server import ThreadingHTTPServer


class RequestLimiter:
    def __init__(self, window_seconds: int = 60, max_keys: int = 10_000) -> None:
        self.window_seconds, self.max_keys = window_seconds, max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            self._hits.move_to_end(key)
            while hits and now - hits[0] >= self.window_seconds:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            if len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
            return True


class LimitedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, handler, max_workers: int = 32) -> None:
        self._slots = threading.BoundedSemaphore(max_workers)
        super().__init__(address, handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(15)
        return request, address

    def process_request(self, request, client_address) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                                b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

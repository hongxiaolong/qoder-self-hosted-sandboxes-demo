# SPDX-License-Identifier: Apache-2.0
"""Qoder HTTP 协议与分页。

模块定位：本地控制端和 Sandbox Worker 共用的轻量 HTTP 客户端，无第三方依赖。
主要逻辑：Bearer 认证、限时请求、事件分页和脱敏错误；写请求不自动重放。
支撑能力：直接使用 Qoder 国际站 API，完整保留工具调用与结果的关联 ID。
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class APIError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f"Qoder HTTP {status}: {message}")


class API:
    def __init__(self, base_url, token, timeout=15):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

        # API credentials must not follow an HTTP redirect to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, path, body=None, *, query=None, headers=None, raw=False):
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        h = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        h.update(headers or {})
        if isinstance(body, bytes):
            data = body
        elif body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        else:
            data = None
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    result = response.read(4 * 1024 * 1024 + 1)
                    if len(result) > 4 * 1024 * 1024:
                        raise ValueError("API response exceeded demo limit (4 MiB)")
                    if response.headers.get("Content-Encoding") == "gzip":
                        result = gzip.decompress(result)
                return result if raw else json.loads(result) if result else None
            except urllib.error.HTTPError as exc:
                message = (
                    exc.read(4096)
                    .decode(errors="replace")
                    .replace(self.token, "[REDACTED]")
                )
                exc.close()
                if exc.code in (429, 502, 503, 504) and attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise APIError(exc.code, message[:1200]) from None
            except (urllib.error.URLError, TimeoutError):
                if attempt + 1 < attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise RuntimeError(
                    "Qoder network request failed or timed out"
                ) from None

    def events(self, session_id, after_id=None):
        query = {"order": "asc", "limit": 100}
        if after_id:
            query["after_id"] = after_id
        seen_pages = set()
        while True:
            page = self.request("GET", f"/sessions/{session_id}/events", query=query)
            yield from page.get("data", [])
            cursor = page.get("next_page")
            if not cursor:
                if page.get("has_more"):
                    raise RuntimeError(
                        "Events page says has_more but omitted next_page"
                    )
                return
            if cursor in seen_pages:
                raise RuntimeError("Repeated events pagination cursor")
            seen_pages.add(cursor)
            query = {"order": "asc", "limit": 100, "page": cursor}

    def send(self, session_id, events, request_key):
        return self.request(
            "POST",
            f"/sessions/{session_id}/events",
            {"events": events},
            headers={"Idempotency-Key": request_key},
        )

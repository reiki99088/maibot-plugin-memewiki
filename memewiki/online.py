"""cnmeme.wiki 公开 API（只读、无需 Key）。"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any

import aiohttp

API_BASE = "https://cnmeme.wiki/api/v1/public/memes"
TIMEOUT = 12


class CnmemeClient:
    def __init__(self, user_agent: str, logger: logging.Logger, timeout: float = TIMEOUT) -> None:
        self.user_agent = user_agent
        self.logger = logger
        self.timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def get(self, url: str, params: dict[str, Any] | None = None) -> dict | None:
        session = await self._get_session()
        for attempt in range(2):
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    if resp.status == 429:
                        wait = float(resp.headers.get("Retry-After") or 3)
                        await asyncio.sleep(min(wait, 5))
                        continue
                    if resp.status == 404:
                        return None
                    self.logger.warning(f"HTTP {resp.status} {url}")
                    return None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"请求失败({type(exc).__name__}): {exc}")
                if attempt == 0:
                    await asyncio.sleep(1)
        return None

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        data = await self.get(API_BASE, {"q": query, "limit": limit})
        return list((data or {}).get("items") or [])

    async def detail(self, slug: str) -> dict | None:
        """返回 API 原始响应（含 item），由调用方决定是否落盘。"""
        return await self.get(API_BASE + "/" + urllib.parse.quote(slug, safe=""))

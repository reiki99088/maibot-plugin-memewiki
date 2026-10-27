"""查梗服务：把三层本地词源、在线回退、缓存和预查闸门串起来。不依赖任何宿主框架。

词源优先级：group_slang.json → 用户自定义黑话 → 内置 / 同步词库 → cnmeme.wiki 在线 API。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from . import gate, prompts
from .cache import TTLCache
from .online import CnmemeClient
from .store import MemeStore
from .terms import UserTerms, load_group_slang, lookup_slang
from .textify import entry_data, entry_text, user_term_data, user_term_text

CACHE_TTL = 6 * 3600
CACHE_MAX = 300
MISS_TTL = 600
FAST_TIMEOUT = 1.5          # 预查时在线查询的总超时，避免拖慢回复

SearchMode = Literal["model", "off"]


@dataclass
class ServiceOptions:
    online_lookup: bool = True
    online_timeout: float = 6.0
    search_mode: SearchMode = "model"
    search_max_chars: int = 10
    wake_words: list[str] = field(default_factory=list)


@dataclass
class PrefetchResult:
    query: str
    hint: str | None
    reason: str
    cost_ms: float = 0.0


class MemeWikiService:
    def __init__(
        self,
        data_dir: Path,
        bundled_db: Path,
        logger: logging.Logger,
        user_agent: str,
        options: ServiceOptions | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.logger = logger
        self.options = options or ServiceOptions()
        self.store = MemeStore(data_dir, bundled_db, logger)
        self.terms = UserTerms(data_dir / "user_terms.json", logger)
        self.client = CnmemeClient(user_agent, logger, timeout=self.options.online_timeout)
        self.slang: dict[str, str] = {}
        self.terms_lock = asyncio.Lock()
        self._cache: TTLCache[str] = TTLCache(CACHE_TTL, CACHE_MAX)
        self._miss_cache: TTLCache[bool] = TTLCache(MISS_TTL, 1000)

    def load(self) -> dict[str, Any]:
        """读取本地数据；可重复调用（热重载、同步完换库）。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.slang = load_group_slang(self.data_dir / "group_slang.json", self.logger)
        self.terms.load()
        info = self.store.reload()
        self.invalidate_cache()
        return info

    def apply_options(self, options: ServiceOptions) -> None:
        self.options = options
        self.client.timeout = options.online_timeout

    async def close(self) -> None:
        await self.client.close()

    def invalidate_cache(self) -> None:
        """黑话增删改、换库后清掉缓存。

        缓存的键是查询原文，一条黑话可能出现在任意句子里，只删同名键清不干净；
        未命中缓存不清的话，刚加的黑话最长 10 分钟内仍然查不到。
        """
        self._cache.clear()
        self._miss_cache.clear()

    # ---------------- 查词 ----------------

    async def _local_lookup(self, query: str) -> dict | None:
        """本地词库命中则秒回；没有 db、只有 index.json 时才读 details/ 或补一次 API。"""
        title = self.store.match(query)
        if not title:
            return None
        note = "" if title.strip() == query.strip() else f"（没有同名条目，这条标题是「{title}」）\n"
        if self.store.has_db():
            data = self.store.row(title)
            if data:
                data["note"] = note
            return data
        slug = self.store.slug_for(title)
        if not slug:
            return None
        item = await self._detail_cached(slug)
        return entry_data(item, query, note) if item else None

    async def _detail_cached(self, slug: str) -> dict | None:
        """先读磁盘；没有再走 API 并落盘。"""
        item = self.store.local_detail(slug)
        if item:
            return item
        if not self.options.online_lookup:
            return None
        detail = await self.client.detail(slug)
        item = (detail or {}).get("item")
        if item:
            self.store.save_detail(slug, detail)
        return item

    async def _search_online(self, query: str, strict: bool = False) -> dict | None:
        if not self.options.online_lookup:
            return None
        items = await self.client.search(query, limit=5)
        if not items:
            return None

        q = query.strip()
        exact = [it for it in items if (it.get("title") or "").strip() == q]
        picked = exact[0] if exact else None
        if picked is None:
            # 同样只认「完整标题出现在查询里」，不做前缀式模糊匹配
            for it in items:
                title = (it.get("title") or "").strip()
                if len(title) >= 2 and title in q:
                    picked = it
                    break

        # 预查模式更严格：只接受标题与查询词有明确包含关系的条目，
        # 否则会把「我真的特别爱你」这种普通短句当成梗，注入无关内容。
        if picked is None and strict:
            return None

        note = ""
        if picked is None:
            picked = items[0]
            note = f"（词库里没有完全同名的条目，最接近的是「{picked.get('title')}」）\n"
        elif not exact:
            note = f"（没有同名条目，这条标题是「{picked.get('title')}」）\n"

        item = (await self._detail_cached(picked["slug"]) if picked.get("slug") else None) or picked
        extra = None
        if not exact:
            others = [it.get("title") for it in items if it.get("slug") != picked.get("slug")]
            extra = [t for t in others if t][:3] or None
        return entry_data(item, q, note, extra)

    async def find(self, query: str, strict: bool = False) -> dict | None:
        """返回结构化词条（/梗 卡片用）。群梗不进卡片，和上游一致。"""
        hit = self.terms.find(query)
        if hit:
            return user_term_data(*hit)
        local = await self._local_lookup(query)
        if local:
            return local
        return await self._search_online(query, strict=strict)

    async def lookup(self, query: str, strict: bool = False) -> str:
        """返回给模型看的文本（search_meme_wiki 工具用）。"""
        query = (query or "").strip()
        if not query:
            return "查询词为空。"

        cached = self._cache.get(query)
        if cached:
            return cached

        slang = lookup_slang(self.slang, query)
        if slang:
            text = f"【群内梗】{query}\n{slang}"
            self._cache.put(query, text)
            return text

        # 自定义黑话优先级高于 wiki（用户自己编的，比 wiki 更贴近群）
        hit = self.terms.find(query)
        if hit:
            text = user_term_text(*hit)
            self._cache.put(query, text)
            return text

        data = await self._local_lookup(query) or await self._search_online(query, strict=strict)
        if data:
            text = entry_text(data, for_llm=True)
            self._cache.put(query, text)
            return text

        return prompts.not_found_text(query)

    async def lookup_fast(self, query: str) -> str:
        """只查本地：群内梗 / 自定义黑话 / 内置词典。毫秒级，不联网。"""
        query = (query or "").strip()
        if not query:
            return ""
        slang = lookup_slang(self.slang, query)
        if slang:
            return f"【群内梗】{query}\n{slang}"
        hit = self.terms.find(query)
        if hit:
            return user_term_text(*hit)
        if self.store.has_db():
            data = await self._local_lookup(query)
            if data:
                return entry_text(data, for_llm=True)
        return ""

    # ---------------- 预查 ----------------

    def _miss_hint(self, query: str) -> str:
        if self.options.search_mode == "off":
            return prompts.no_search_hint(query)
        return prompts.miss_hint(query)

    async def prefetch(self, raw: str) -> PrefetchResult | None:
        """消息像梗查询时先查好，返回要注入给模型的提示；不像梗返回 None。"""
        query = gate.extract_meme_query(raw, self.options.wake_words)
        if not query:
            return None
        query = gate.normalize_query(query)
        now = time.time()

        # ① 先查本地：毫秒级，命中就注入，不管联网开关
        t0 = time.perf_counter()
        fast_text = await self.lookup_fast(query)
        fast_ms = (time.perf_counter() - t0) * 1000
        if fast_text:
            return PrefetchResult(query, prompts.dict_hint(query, fast_text), "本地命中", fast_ms)

        # ② 本地没收录：不像梗就别联网，也不注入
        if not gate.worth_search(raw, query, self.options.search_max_chars):
            self._miss_cache.put(query, True, now)
            return PrefetchResult(query, None, "未命中且不像梗，跳过联网", fast_ms)

        # ③ 10 分钟内查过没结果：不再联网，直接给未命中提示
        if self._miss_cache.get(query, now):
            return PrefetchResult(query, self._miss_hint(query), "10 分钟内查过没结果，跳过重复联网", fast_ms)

        if not self.options.online_lookup:
            self._miss_cache.put(query, True, now)
            return PrefetchResult(query, self._miss_hint(query), "本地未命中（在线词库已关闭）", fast_ms)

        t1 = time.perf_counter()
        try:
            text = await asyncio.wait_for(self.lookup(query, strict=True), timeout=FAST_TIMEOUT)
        except asyncio.TimeoutError:
            self._miss_cache.put(query, True, now)
            return PrefetchResult(query, None, f"在线预查超时 {FAST_TIMEOUT}s，跳过", FAST_TIMEOUT * 1000)
        cost_ms = (time.perf_counter() - t1) * 1000
        if not text or text.startswith(prompts.NOT_FOUND_PREFIX):
            self._miss_cache.put(query, True, now)
            return PrefetchResult(query, self._miss_hint(query), f"在线也没收录（模式={self.options.search_mode}）", cost_ms)
        return PrefetchResult(query, prompts.dict_hint(query, text), "在线命中", cost_ms)

"""梗百科 MaiBot 插件入口。

移植自 astrbot_plugin_memewiki v1.5.2（MIT，in1t1a1）。平台无关的逻辑都在 memewiki/ 包里，
这里只负责把它接到 MaiBot SDK：配置、Tool、Command、WebUI API、Hook 和每日同步。
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from maibot_sdk import API, Command, Field, HookHandler, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder, ToolParameterInfo, ToolParamType

# Runner 把 plugin.py 当成以插件目录为根的合成包加载，插件目录本身不在 sys.path 上，必须用相对导入
from .memewiki import prompts
from .memewiki.card import render_card
from .memewiki.service import MemeWikiService, ServiceOptions
from .memewiki.terms import validate_term
from .memewiki.textify import entry_text, who_text

VERSION = "0.1.0"
PLUGIN_DIR = Path(__file__).resolve().parent
# 内置词库放 assets/：插件目录下若有 data/，宿主每次加载都会提示「旧式数据目录，建议迁移」
BUNDLED_DB = PLUGIN_DIR / "assets" / "memes.db"
SYNC_SCRIPT = PLUGIN_DIR / "scripts" / "sync_memes.py"
USER_AGENT = f"MaiBot-memewiki/{VERSION} (+https://github.com/reiki99088/maibot-plugin-memewiki)"
PREFETCH_TTL = 90           # 预查结果在多久内注入给同一会话的规划请求
SYNC_CHECK_INTERVAL = 1500  # 每 25 分钟看一次是否到了同步时刻


# ---------------- 配置 ----------------


class PluginSection(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class LookupSection(PluginConfigBase):
    __ui_label__ = "查梗"
    __ui_icon__ = "book-open"
    __ui_order__ = 1

    reply_mode: Literal["image", "text"] = Field(default="image", description="/梗 的回复形式：图片卡片或纯文字")
    online_lookup: bool = Field(default=True, description="本地词库没收录时，是否查 cnmeme.wiki 在线词库")
    online_timeout: float = Field(default=6.0, description="cnmeme.wiki 在线查询超时（秒）")
    admin_users: list[str] = Field(
        default_factory=list,
        description="允许 /黑话 删除 的用户，格式 平台:账号，例如 qq:123456；本机控制台始终允许",
    )


class InjectSection(PluginConfigBase):
    __ui_label__ = "预查注入"
    __ui_icon__ = "sparkles"
    __ui_order__ = 2

    prefetch_mode: Literal["off", "hook"] = Field(
        default="off",
        description="off：只靠 search_meme_wiki 工具；hook：消息像梗时预查并在规划请求里临时注入（实验性）",
    )
    inject_guidance: bool = Field(default=True, description="预查注入时一并注入内置接梗规则")
    guidance_text: str = Field(default="", description="替换内置接梗规则；留空用内置")
    search_mode: Literal["model", "off"] = Field(
        default="model",
        description="词库没收录时：model 提示模型用 web_search 自己查；off 不联网，只提示别硬解释",
    )
    search_max_chars: int = Field(default=10, description="非明确问梗时，查询词不超过多少字才当成梗处理（2–20）")
    wake_words: list[str] = Field(
        default_factory=list,
        description="消息开头要去掉的唤醒词；机器人昵称和别名会自动加入",
    )


class SyncSection(PluginConfigBase):
    __ui_label__ = "词库同步"
    __ui_icon__ = "refresh-cw"
    __ui_order__ = 3

    auto_sync_enable: bool = Field(default=False, description="每天从 cnmeme.wiki 增量同步一次词库")
    auto_sync_hour: int = Field(default=4, description="每天几点同步（0–23）")


class MemeWikiConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    lookup: LookupSection = Field(default_factory=LookupSection)
    inject: InjectSection = Field(default_factory=InjectSection)
    sync: SyncSection = Field(default_factory=SyncSection)


# ---------------- 小工具 ----------------


def _sender_nickname(message: Any) -> str:
    """平台昵称（不是群名片）。消息字典里拿不到就返回空串。"""
    if not isinstance(message, dict):
        return ""
    user_info = (message.get("message_info") or {}).get("user_info") or {}
    return str(user_info.get("user_nickname") or "")


class MemeWikiPlugin(MaiBotPlugin):
    config_model = MemeWikiConfig

    def __init__(self) -> None:
        super().__init__()
        self._service: MemeWikiService | None = None
        self._sync_task: asyncio.Task | None = None
        self._last_sync_day = ""
        self._syncing = False
        self._bot_names: list[str] = []
        # 预查结果：session_id → (过期时间, 查询词, 提示)；_seq 防止慢的旧预查覆盖新消息
        self._pending: dict[str, tuple[float, str, str]] = {}
        self._seq: dict[str, int] = {}

    # ---------------- 生命周期 ----------------

    @property
    def service(self) -> MemeWikiService:
        if self._service is None:
            raise RuntimeError("梗百科尚未加载")
        return self._service

    def _options(self) -> ServiceOptions:
        cfg = self.config
        return ServiceOptions(
            online_lookup=cfg.lookup.online_lookup,
            online_timeout=max(1.0, float(cfg.lookup.online_timeout)),
            search_mode=cfg.inject.search_mode,
            search_max_chars=cfg.inject.search_max_chars,
            wake_words=[*self._bot_names, *(w.strip() for w in cfg.inject.wake_words if w.strip())],
        )

    async def _load_bot_names(self) -> None:
        names: list[str] = []
        try:
            nickname = await self.ctx.config.get("bot.nickname", "")
            aliases = await self.ctx.config.get("bot.alias_names", [])
            names = [str(n).strip() for n in [nickname, *(aliases or [])] if str(n or "").strip()]
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning(f"读取机器人昵称失败，唤醒词只用插件配置: {exc}")
        self._bot_names = names

    async def on_load(self) -> None:
        await self._load_bot_names()
        self._service = MemeWikiService(
            data_dir=Path(self.ctx.paths.data_dir),
            bundled_db=BUNDLED_DB,
            logger=self.ctx.logger,
            user_agent=USER_AGENT,
            options=self._options(),
        )
        self._service.load()
        if self.config.sync.auto_sync_enable:
            self.ctx.logger.info(f"自动同步已启用：每天 {self.config.sync.auto_sync_hour % 24:02d}:00")
        self._sync_task = asyncio.create_task(self._auto_sync_loop())

    async def on_unload(self) -> None:
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._sync_task = None
        if self._service is not None:
            await self._service.close()

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        del config_data, version
        if scope == "self" and self._service is not None:
            self._service.apply_options(self._options())
            self._pending.clear()
            self.ctx.logger.info(
                f"配置已更新：预查={self.config.inject.prefetch_mode}，联网={self.config.inject.search_mode}，"
                f"自动同步={'开' if self.config.sync.auto_sync_enable else '关'}"
            )

    # ---------------- LLM 工具 ----------------

    @Tool(
        "search_meme_wiki",
        description=prompts.TOOL_DESCRIPTION,
        parameters=[
            ToolParameterInfo(
                name="query",
                param_type=ToolParamType.STRING,
                description="要查的梗或词语，尽量简短，例如「大水理论」「寿来」「狗东」",
                required=True,
            ),
        ],
    )
    async def search_meme_wiki(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        del kwargs
        try:
            text = await self.service.lookup(query)
            return {"success": True, "content": text}
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error(f"查询异常: {exc}")
            return {"success": False, "content": "梗词库暂时查不了，先按字面理解或说不知道。"}

    # ---------------- /梗 ----------------

    @Command("meme", description="查梗：/梗 <词>", pattern=r"^/梗(?:\s+(?P<term>.+?))?\s*$")
    async def meme_cmd(self, stream_id: str = "", matched_groups: dict | None = None, **kwargs: Any):
        term = str((matched_groups or {}).get("term") or "").strip()
        if not term:
            await self.ctx.send.text("用法：/梗 <词>", stream_id)
            return True, "缺少查询词", True

        data = await self.service.find(term)
        if not data:
            await self.ctx.send.text(f"词库里没有收录「{term}」。", stream_id)
            return True, f"未收录: {term}", True

        if self.config.lookup.reply_mode == "image":
            try:
                png = await asyncio.to_thread(render_card, data)
                if await self.ctx.send.image(base64.b64encode(png).decode("ascii"), stream_id):
                    return True, f"已发送词条卡片: {data['title']}", True
                self.ctx.logger.warning("图片发送失败，退回文字")
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning(f"图片渲染失败，退回文字: {exc}")
        await self.ctx.send.text(entry_text(data, for_llm=False), stream_id)
        return True, f"已发送词条: {data['title']}", True

    # ---------------- /黑话 ----------------

    def _is_admin(self, platform: str, user_id: str, is_local_operator: bool) -> bool:
        if is_local_operator:
            return True
        scoped = f"{platform.strip().lower()}:{user_id.strip()}"
        return any(scoped == a.strip().lower() for a in self.config.lookup.admin_users)

    @Command(
        "slang",
        description="自定义黑话：/黑话 <名称> <含义> | /黑话 列表 | /黑话 删除 <名称>",
        pattern=r"^/黑话(?:\s+(?P<rest>.+?))?\s*$",
    )
    async def slang_cmd(
        self,
        stream_id: str = "",
        platform: str = "",
        user_id: str = "",
        is_local_operator: bool = False,
        matched_groups: dict | None = None,
        message: Any = None,
        **kwargs: Any,
    ):
        del kwargs
        svc = self.service
        raw = str((matched_groups or {}).get("rest") or "").strip()

        async def reply(text: str, note: str):
            await self.ctx.send.text(text, stream_id)
            return True, note, True

        if not raw:
            return await reply(
                "用法：\n"
                "/黑话 <名称> <含义>   记一条群内黑话\n"
                "/黑话 列表             看看都记了啥\n"
                "/黑话 删除 <名称>      删掉一条（管理员）",
                "显示用法",
            )

        if raw in ("列表", "list", "清单"):
            items = svc.terms.sorted_items()
            if not items:
                return await reply("还没人加过黑话。用 /黑话 <名称> <含义> 加一条。", "列表为空")
            lines = [f"共 {len(items)} 条自定义黑话："]
            for name, entry in items[:40]:
                lines.append(f"· {name}：{str(entry.get('meaning'))[:40]}（{who_text(entry)}）")
            if len(items) > 40:
                lines.append(f"…… 还有 {len(items) - 40} 条")
            return await reply("\n".join(lines), f"列出 {len(items)} 条")

        if raw.startswith("删除") or raw.startswith("删 "):
            if not self._is_admin(platform, user_id, is_local_operator):
                return await reply("删除黑话只有管理员能用（插件配置 lookup.admin_users）。", "无权删除")
            name = raw[2:].strip() if raw.startswith("删除") else raw[1:].strip()
            async with svc.terms_lock:
                removed = await asyncio.to_thread(svc.terms.remove, name)
            if not removed:
                return await reply(f"没有「{name}」这条黑话。", f"不存在: {name}")
            svc.invalidate_cache()
            self.ctx.logger.info(f"删除黑话: {name} by {platform}:{user_id}")
            return await reply(f"已删除「{name}」。", f"已删除: {name}")

        parts = raw.split(maxsplit=1)
        name = parts[0].strip().strip("：:，,")
        meaning = parts[1].strip() if len(parts) > 1 else ""
        if not meaning:
            return await reply("还差含义。格式：/黑话 <名称> <含义>\n例：/黑话 狗东 京东", "缺少含义")
        error = validate_term(name, meaning)
        if error:
            return await reply(error, "参数不合法")

        nick = _sender_nickname(message)
        async with svc.terms_lock:
            existed = await asyncio.to_thread(svc.terms.upsert, name, meaning, user_id, nick)
        svc.invalidate_cache()
        self.ctx.logger.info(f"{'更新' if existed else '新增'}黑话: {name} = {meaning[:40]} by {platform}:{user_id}({nick})")
        who = f"{user_id} / {nick}" if nick else user_id
        return await reply(
            f"已{'更新' if existed else '记下'}「{name}」：{meaning}\n"
            f"记录人：{who}｜共 {len(svc.terms.items)} 条自定义黑话。",
            f"{'更新' if existed else '新增'}: {name}",
        )

    # ---------------- WebUI 黑话编辑页 ----------------

    @API("terms_list", description="列出自定义黑话", version="1")
    async def api_terms_list(self) -> dict[str, Any]:
        svc = self.service
        items = [
            {
                "name": name,
                "meaning": str(entry.get("meaning") or ""),
                "author": who_text(entry),
                "ts": str(entry.get("ts") or ""),
            }
            for name, entry in svc.terms.sorted_items()
        ]
        return {"count": len(items), "items": items}

    @API("terms_save", description="新增或更新一条自定义黑话", version="1")
    async def api_terms_save(self, name: str = "", meaning: str = "") -> dict[str, Any]:
        name, meaning = str(name or "").strip(), str(meaning or "").strip()
        error = validate_term(name, meaning)
        if error:
            raise ValueError(error)
        svc = self.service
        async with svc.terms_lock:
            existed = await asyncio.to_thread(svc.terms.upsert, name, meaning, "webui", "WebUI")
        svc.invalidate_cache()
        self.ctx.logger.info(f"WebUI {'更新' if existed else '新增'}黑话: {name}")
        return {"existed": existed, "count": len(svc.terms.items)}

    @API("terms_delete", description="删除一条自定义黑话", version="1")
    async def api_terms_delete(self, name: str = "") -> dict[str, Any]:
        name = str(name or "").strip()
        svc = self.service
        async with svc.terms_lock:
            removed = await asyncio.to_thread(svc.terms.remove, name)
        if not removed:
            raise ValueError(f"没有「{name}」这条黑话，请刷新页面")
        svc.invalidate_cache()
        self.ctx.logger.info(f"WebUI 删除黑话: {name}")
        return {"removed": name, "count": len(svc.terms.items)}

    # ---------------- 预查注入（实验性，prefetch_mode=hook） ----------------

    @HookHandler(
        "chat.receive.after_process",
        name="memewiki_prefetch_observer",
        description="入站消息像梗时先查好，留给下一次规划请求注入",
        mode=HookMode.OBSERVE,
        error_policy=ErrorPolicy.SKIP,
    )
    async def observe_inbound(self, message: Any = None, **kwargs: Any) -> None:
        del kwargs
        if self.config.inject.prefetch_mode != "hook" or not isinstance(message, dict):
            return None
        session_id = str(message.get("session_id") or "")
        if not session_id or message.get("is_command") or message.get("is_notify"):
            return None
        text = str(message.get("processed_plain_text") or "")
        seq = self._seq.get(session_id, 0) + 1
        self._seq[session_id] = seq

        result = await self.service.prefetch(text)
        if self._seq.get(session_id) != seq:
            return None     # 预查期间同一会话又来了新消息，这条结果已经过时
        if result is None or not result.hint:
            self._pending.pop(session_id, None)
            if result is not None:
                self.ctx.logger.info(f"预查 {result.query}：{result.reason}")
            return None
        self._pending[session_id] = (time.time() + PREFETCH_TTL, result.query, result.hint)
        self.ctx.logger.info(f"预查 {result.query}：{result.reason}（{len(result.hint)} 字，{result.cost_ms:.0f}ms）")
        return None

    def _guidance(self) -> str:
        return self.config.inject.guidance_text.strip() or prompts.DEFAULT_GUIDANCE

    @staticmethod
    def _user_item(text: str) -> dict[str, Any]:
        """一条只活在本次请求里的 UserMessageItem 快照（不写回聊天历史）。"""
        return {
            "item_type": "UserMessageItem",
            "meta": {
                "item_id": f"memewiki-{uuid.uuid4().hex}",
                "logical_turn_id": None,
                "timestamp": datetime.now().isoformat(),
            },
            "parts": [{"type": "text", "text": text}],
        }

    @HookHandler(
        "maisaka.planner.before_request",
        name="memewiki_planner_inject",
        description="把预查到的梗资料临时追加到本次规划请求",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        timeout_ms=2500,
        error_policy=ErrorPolicy.SKIP,
    )
    async def inject_planner(self, **kwargs: Any) -> dict[str, Any]:
        items = kwargs.get("items")
        session_id = str(kwargs.get("session_id") or "")
        if self.config.inject.prefetch_mode != "hook" or not isinstance(items, list):
            return {"action": "continue"}
        pending = self._pending.get(session_id)
        if pending is None or pending[0] <= time.time():
            self._pending.pop(session_id, None)
            return {"action": "continue"}
        _expire, query, hint = pending
        text = f"{self._guidance()}\n\n{hint}" if self.config.inject.inject_guidance else hint
        self.ctx.logger.info(f"规划请求注入梗资料: {query}（{len(text)} 字）")
        # 宿主用 modified_kwargs 整体替换原 kwargs（hook_dispatcher），只回 items 会丢掉
        # item_schema_version，宿主随即判定版本不符并忽略注入，所以必须带上其余参数
        return {"action": "continue", "modified_kwargs": {**kwargs, "items": [*items, self._user_item(text)]}}

    # ---------------- 每日自动同步 ----------------

    async def _auto_sync_loop(self) -> None:
        while True:
            try:
                await self._maybe_sync()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning(f"自动同步检查异常: {exc}")
            await asyncio.sleep(SYNC_CHECK_INTERVAL)

    async def _maybe_sync(self) -> None:
        cfg = self.config.sync
        if not cfg.auto_sync_enable or self._syncing:
            return
        if time.localtime().tm_hour != cfg.auto_sync_hour % 24:
            return
        today = time.strftime("%Y-%m-%d")
        if self._last_sync_day == today:
            return
        self._last_sync_day = today
        await self.run_sync()

    async def run_sync(self, *extra_args: str) -> int | None:
        """跑一次 scripts/sync_memes.py，结束后换库。返回子进程退出码。"""
        if not SYNC_SCRIPT.exists():
            self.ctx.logger.warning(f"同步脚本不存在: {SYNC_SCRIPT}")
            return None
        data_dir = Path(self.ctx.paths.data_dir)
        self._syncing = True
        self.ctx.logger.info(f"开始同步词库 → {data_dir}")
        started = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-X", "utf8", str(SYNC_SCRIPT), "--data-dir", str(data_dir), *extra_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            tail = (out or b"").decode("utf-8", "ignore").strip().splitlines()[-3:]
            self.ctx.logger.info(f"同步结束 rc={proc.returncode} 用时 {int(time.time() - started)}s | " + " / ".join(tail))
            self.service.store.reload()
            self.service.invalidate_cache()
            return proc.returncode
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning(f"同步失败: {exc}")
            return None
        finally:
            self._syncing = False


def create_plugin() -> MemeWikiPlugin:
    return MemeWikiPlugin()

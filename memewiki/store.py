"""本地词库：SQLite 只读快照优先，没有 db 才退回 index.json + details/。"""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from .textify import db_row_data


class MemeStore:
    """只认完整梗名：消息里必须出现完整标题，反过来（消息是标题前缀）不算。

    故意不缓存连接：Windows 上常驻句柄会锁住 memes.db。MaiBot 热重载会先拉起新 Runner、
    旧 Runner 继续服务，更新插件目录时旧句柄没放就会 WinError 32。所以每次查询开一个
    只读连接，用完立刻 close。
    """

    def __init__(self, data_dir: Path, bundled_db: Path, logger: logging.Logger) -> None:
        self.data_dir = data_dir
        self.bundled_db = bundled_db
        self.logger = logger
        self.db_path = data_dir / "memes.db"          # 同步脚本产出的库
        self.index_path = data_dir / "index.json"
        self.detail_dir = data_dir / "details"
        self._titles: dict[str, str] = {}

    def readable_db_path(self) -> Path | None:
        """优先数据目录里同步过的 db，其次插件自带的只读快照。"""
        if self.db_path.exists():
            return self.db_path
        if self.bundled_db.exists():
            return self.bundled_db
        return None

    def _connect(self) -> sqlite3.Connection | None:
        """开一个只读连接，**调用方用完必须 close()**。"""
        db_path = self.readable_db_path()
        if db_path is None:
            return None
        try:
            # as_uri() 会转义空格和中文路径，裸拼 file:{path} 遇到 # ? % 会被截断
            return sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"打开词库失败: {exc}")
            return None

    def reload(self) -> dict[str, Any]:
        """读一次词库信息（同步完换库时也调用）。连接不缓存，下次查询自然指向新文件。"""
        self._titles = {}
        db_path = self.readable_db_path()
        if db_path is not None:
            info: dict[str, Any] = {"source": str(db_path), "count": None, "built_at": None}
            conn = self._connect()
            if conn is not None:
                try:
                    count = conn.execute("SELECT value FROM meta WHERE key='count'").fetchone()
                    built = conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
                    info["count"] = count[0] if count else None
                    info["built_at"] = built[0] if built else None
                except Exception as exc:  # noqa: BLE001
                    self.logger.warning(f"读取词库信息失败: {exc}")
                finally:
                    conn.close()
            self.logger.info(f"词库 {info['count'] or '?'} 条（生成于 {info['built_at'] or '?'}，来自 {db_path}）")
            return info
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
            self._titles = data.get("titles") or {}
            self.logger.info(f"本地索引 {len(self._titles)} 条（生成于 {data.get('built_at', '?')}）")
            return {"source": str(self.index_path), "count": len(self._titles), "built_at": data.get("built_at")}
        except FileNotFoundError:
            self.logger.warning("没有可用的本地词库（memes.db / index.json 都不存在），只能在线查")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"读取本地索引失败: {exc}")
        return {"source": "", "count": 0, "built_at": None}

    def match(self, query: str) -> str | None:
        """例：「你是什么」不能命中「你是什么冠军」；「你说你是什么冠军」可以。"""
        q = (query or "").strip()
        if not q:
            return None

        conn = self._connect()
        if conn is not None:
            try:
                row = conn.execute("SELECT title FROM memes WHERE title = ?", (q,)).fetchone()
                if row:
                    return row[0]
                row = conn.execute(
                    "SELECT title FROM memes WHERE length(title) >= 2 AND instr(?, title) > 0 "
                    "ORDER BY length(title) DESC LIMIT 1",
                    (q,),
                ).fetchone()
                return row[0] if row else None
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"查词库失败: {exc}")
                return None
            finally:
                conn.close()

        if not self._titles:
            return None
        if q in self._titles:
            return q
        best = None
        for title in self._titles:
            if len(title) >= 2 and title in q and (best is None or len(title) > len(best)):
                best = title
        return best

    def has_db(self) -> bool:
        return self.readable_db_path() is not None

    def row(self, title: str) -> dict[str, Any] | None:
        conn = self._connect()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT title, slug, tags, usage, origin, other, summary FROM memes WHERE title = ?",
                (title,),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"读词库失败: {exc}")
            row = None
        finally:
            conn.close()
        return db_row_data(row) if row else None

    def slug_for(self, title: str) -> str | None:
        return self._titles.get(title)

    # ---------- details/ 详情缓存（index.json 回退链和在线查询共用） ----------

    def _detail_file(self, slug: str) -> Path:
        return self.detail_dir / (urllib.parse.quote(slug, safe="") + ".json")

    def local_detail(self, slug: str) -> dict | None:
        path = self._detail_file(slug)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("item")
        except Exception:  # noqa: BLE001
            return None

    def save_detail(self, slug: str, detail: dict) -> None:
        try:
            self.detail_dir.mkdir(parents=True, exist_ok=True)
            self._detail_file(slug).write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"写详情缓存失败: {exc}")

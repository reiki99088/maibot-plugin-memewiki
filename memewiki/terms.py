"""插件私有的本地词源：用户自定义黑话 user_terms.json 和可选的 group_slang.json。

与 MaiBot 内置黑话学习器（Jargon 表）分开存放，互不写入。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

NAME_MIN, NAME_MAX = 2, 16
MEANING_MAX = 300


def validate_term(name: str, meaning: str) -> str | None:
    """返回错误提示；合法时返回 None。"""
    if not (NAME_MIN <= len(name) <= NAME_MAX):
        return f"黑话名称要 {NAME_MIN}–{NAME_MAX} 个字。"
    if not meaning:
        return "含义不能为空。"
    if len(meaning) > MEANING_MAX:
        return f"含义太长了，控制在 {MEANING_MAX} 字以内。"
    return None


class UserTerms:
    def __init__(self, path: Path, logger: logging.Logger) -> None:
        self.path = path
        self.logger = logger
        self.items: dict[str, dict] = {}

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.items = {str(k): v for k, v in data.items() if isinstance(v, dict)}
                self.logger.info(f"载入自定义黑话 {len(self.items)} 条（{self.path}）")
                return
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"读取自定义黑话失败: {exc}")
        self.items = {}

    def save(self) -> None:
        # 留一份上一版备份，误删/覆盖时能捞回来
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.path.exists():
                backup = self.path.with_name(self.path.name + ".bak")
                backup.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"备份黑话文件失败: {exc}")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def find(self, query: str) -> tuple[str, dict] | None:
        """自定义黑话只认精确词，或「完整词出现在句子里」。"""
        q = (query or "").strip()
        if not q or not self.items:
            return None
        if q in self.items:
            return q, self.items[q]
        best = None
        for name in self.items:
            if len(name) >= 2 and name in q and (best is None or len(name) > len(best)):
                best = name
        return (best, self.items[best]) if best else None

    def upsert(self, name: str, meaning: str, author: str, author_name: str) -> bool:
        """写入并落盘，返回是否覆盖了已有条目。"""
        existed = name in self.items
        self.items[name] = {
            "meaning": meaning,
            "author": author,
            "author_name": author_name,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.save()
        return existed

    def remove(self, name: str) -> bool:
        if name not in self.items:
            return False
        self.items.pop(name, None)
        self.save()
        return True

    def sorted_items(self) -> list[tuple[str, dict]]:
        return sorted(self.items.items(), key=lambda kv: str(kv[1].get("ts") or ""), reverse=True)


def load_group_slang(path: Path, logger: logging.Logger) -> dict[str, str]:
    """可选的群内词表：放在插件数据目录里（不放插件包），没有就是空字典。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            logger.info(f"载入本地群梗 {len(data)} 条")
            return {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"载入群梗失败: {exc}")
    return {}


def lookup_slang(slang: dict[str, str], query: str) -> str | None:
    q = query.strip()
    if not q:
        return None
    if q in slang:
        return slang[q]
    for term, meaning in slang.items():
        if term and (term in q or q in term) and len(term) >= 2:
            return meaning
    return None

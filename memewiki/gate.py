"""预查闸门：从一条消息里抽出「像梗的查询词」，并判断本地没收录时值不值得联网。

全部是确定性规则，不依赖模型自觉。源自 astrbot_plugin_memewiki v1.5.2 的线上调参结果。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

ASK_PATTERN = re.compile(
    r"(?:是)?(?:什么梗|啥梗|什么来头|什么来历|什么出处|什么意思|啥意思|怎么用|怎么接|怎么玩|如何用)"
    r"(?:啊|呀|呢|的|啊这是)?[？?。！!~～]*$"
)
BARE_PATTERN = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9·]{2,10}$")
# 明确在问梗：命中这些词才无条件联网
INTENT_WORDS = (
    "什么梗", "啥梗", "梗是啥", "什么来头", "什么来历", "什么出处", "啥意思",
    "什么意思", "黑话", "谐音", "缩写", "怎么来的", "谁说的", "怎么用", "怎么接",
)
# 明显是闲聊/问状态的整句，别当梗去查（线上高频误报词）
CHAT_PHRASES = {
    "我是谁", "错了", "情绪", "接下一句", "你也学一个", "今天看什么", "到底多少了",
    "搜出来了吗", "来点贴吧评价", "都是同龄人", "你同事", "道德这么高",
    "这是谁的手臂", "比我能串", "宝宝你醒了", "中国人能不能飞", "你现在什么人格",
}
# 带这些成分的多半是整句想问句，不是词条
SENTENCE_WORDS = re.compile(r"怎么样|怎么|多少|为什么|能不能|是不是|什么|了吗|了没|一下|你说|要不要|有没有")
STOP_WORDS = {
    "在吗", "在不在", "哈哈", "哈哈哈", "笑死", "好的", "好吧", "晚安", "早安",
    "睡觉", "吃饭", "干嘛", "没事", "收到", "谢谢", "好耶", "草", "典", "乐",
}
# 带这些成分的多半是「你/我」开头的整句，不是词条
PRONOUN_WORDS = ("自己", "你们", "我们", "他们", "她们", "咱们", "大家", "别人")
_DEICTIC_HEADS = {
    "这", "那", "它", "他", "她", "这个", "那个", "你",
    "这句话", "那句话", "上面", "刚才", "下文", "前面",
}


def clean_text(text: str, wake_words: Iterable[str] = ()) -> str:
    t = (text or "").strip()
    t = re.sub(r"^(@\S+\s*)+", "", t)          # 去掉开头的 @某人
    # 长的唤醒词先试，避免「小鲸鱼」被「鲸鱼」截成「小」
    for w in sorted((w for w in wake_words if w), key=len, reverse=True):
        if t.lower().startswith(w.lower()):
            t = t[len(w):].strip(" ，,。.！!？?:：")
            break
    return t.strip()


def extract_meme_query(text: str, wake_words: Iterable[str] = ()) -> str | None:
    t = clean_text(text, wake_words)
    if not t:
        return None
    m = ASK_PATTERN.search(t)
    if m:
        head = t[: m.start()].strip(" ，,。.！!？?")
        head = head.strip("是").strip(" ，,。.！!？?")
        head = re.sub(r"(这个梗|那个梗|这梗|那梗|这个|那个)$", "", head).strip()
        head = re.sub(r"(到底|究竟)$", "", head).strip()
        if head in _DEICTIC_HEADS:
            return None
        if len(head) <= 2 and head[:1] in {"这", "那", "它", "他", "她"}:
            return None
        if 1 < len(head) <= 12:
            return head
        return None
    if BARE_PATTERN.match(t) and t not in STOP_WORDS:
        return t
    return None


def normalize_query(query: str) -> str:
    """把「杂鱼杂鱼」「666666」「已取餐已取餐」这类重复折叠成基词，便于命中缓存。"""
    q = (query or "").strip(" ，,。.！!？?、~～")
    n = len(q)
    if n >= 4:
        # 从长到短找重复块：「已取餐已取餐」取 3 字、「666666」取 3 字、「杂鱼杂鱼」取 2 字
        for size in range(min(4, n // 2), 1, -1):
            if n % size == 0 and q == q[:size] * (n // size):
                return q[:size]
    return q


def worth_search(raw: str, query: str, max_chars: int = 10) -> bool:
    """本地词库没收录时，判断这条查询值不值得联网查。

    上游线上实测：100 分钟触发 27 次搜索，其中 22 次是「我是谁」「情绪」
    「你对我的好感度怎么样」这类闲聊整句。这里只放行「明确在问梗」或「像词条的短词」。
    """
    text = (raw or "").strip()
    if any(k in text for k in INTENT_WORDS):
        return True
    q = (query or "").strip()
    max_n = max(2, min(int(max_chars), 20))
    if not q or len(q) > max_n:
        return False
    if q in CHAT_PHRASES:
        return False
    if SENTENCE_WORDS.search(q):
        return False
    if any(k in q for k in PRONOUN_WORDS):
        return False
    if len(q) >= 3 and q[:1] in "我你他她它这那":
        return False
    if len(q) >= 4 and q[:1] in "不没别请快都还就再又也":
        return False
    return True

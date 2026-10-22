"""IP 维度内存限频（滑动窗口）。

单进程 uvicorn 场景够用；多进程部署需换 Redis。
"""
import threading
import time
from collections import defaultdict, deque

_store: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()

# 测试可通过 monkeypatch 调整
REGISTER_LIMIT = 10
REGISTER_WINDOW_S = 3600   # 注册：10 次/小时/IP
LOGIN_LIMIT = 30
LOGIN_WINDOW_S = 900       # 登录：30 次/15分钟/IP


def hit(key: str, limit: int, window_s: int) -> bool:
    """记录一次访问。返回 True=放行，False=超出限额。"""
    now = time.monotonic()
    with _lock:
        q = _store[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset():
    """清空记录（测试用）。"""
    with _lock:
        _store.clear()

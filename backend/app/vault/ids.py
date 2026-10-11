"""ULID 生成，见 SCHEMA 2.2 的 `id` 字段。26 字符 Crockford base32。

48 位毫秒时间戳 + 80 位随机，字典序 == 生成顺序，所以 id 可以直接当排序键用。
不引第三方依赖。

同一毫秒内连续生成时递增随机段（ULID 规范的单调性要求），否则同毫秒内两个 id
的先后完全由随机数决定，字典序就不再等于生成顺序。状态用锁保护：写入层跑在
线程池里，new_ulid 会被并发调用。
"""

import os
import threading
import time

# Crockford base32：剔除了 I L O U，避免形近字和脏话
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_BITS = 48
_RANDOM_BITS = 80
_LENGTH = 26
_MAX_TIME = (1 << _TIME_BITS) - 1
_MAX_RANDOM = (1 << _RANDOM_BITS) - 1

_lock = threading.Lock()
_last_ms = -1
_last_random = 0


def _encode(value: int) -> str:
    chars = [""] * _LENGTH
    for i in range(_LENGTH - 1, -1, -1):
        chars[i] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_ulid() -> str:
    global _last_ms, _last_random
    with _lock:
        now = int(time.time() * 1000) & _MAX_TIME
        # <= 而不是 ==：系统时钟可能回拨，回拨时继续用上一次的毫秒数，
        # 宁可 id 里的时间戳滞后，也不能让字典序倒退
        if now <= _last_ms:
            now = _last_ms
            # 不掩码回绕：全 1 再 +1 会绕回 0，撞上本毫秒已发出的 id。
            # 重复 id 是最坏的一类 bug——静默、不可复现、污染索引，宁可这里炸掉
            if _last_random >= _MAX_RANDOM:
                raise RuntimeError(
                    "同一毫秒内 ULID 随机段耗尽（2^80 个 id），拒绝回绕以免产生重复 id"
                )
            _last_random += 1
        else:
            _last_ms = now
            _last_random = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
        return _encode((now << _RANDOM_BITS) | _last_random)

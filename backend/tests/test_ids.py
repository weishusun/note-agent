"""ids.py：ULID 的格式、单调性、唯一性、随机段溢出。"""

import time

import pytest

from app.vault import ids
from app.vault.ids import _ALPHABET, new_ulid


def test_format() -> None:
    ulid = new_ulid()
    assert len(ulid) == 26
    assert set(ulid) <= set(_ALPHABET)
    # 130 位编码空间只装 128 位，首字符最多用到 3 bit
    assert ulid[0] in "01234567"


def test_monotonic() -> None:
    """字典序 == 生成顺序，同一毫秒内也必须成立。"""
    ids = [new_ulid() for _ in range(1000)]
    for a, b in zip(ids, ids[1:]):
        assert a < b
    assert ids == sorted(ids)


def test_no_collision_in_10000() -> None:
    generated = [new_ulid() for _ in range(10000)]
    assert len(set(generated)) == 10000


def test_random_overflow_raises() -> None:
    """随机段全 1 时必须显式抛异常，不允许静默回绕成 0——回绕会产生重复 id。"""
    saved = (ids._last_ms, ids._last_random)
    try:
        # 直接把模块状态摆到边界：一个未来的毫秒数（保证走「同一毫秒」分支）+ 随机段全 1
        ids._last_ms = int(time.time() * 1000) + 10_000
        ids._last_random = (1 << 80) - 1
        with pytest.raises(RuntimeError, match="随机段耗尽"):
            new_ulid()
        # 抛异常后状态不变，没有偷偷回绕
        assert ids._last_random == (1 << 80) - 1
    finally:
        ids._last_ms, ids._last_random = saved

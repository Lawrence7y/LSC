"""`lsc/utils/coerce.py` 的取标量口径测试。

这个模块是"从异构字典里取一个标量，取不出来就回退"的唯一实现（债务报告 §11.22）。
它替换掉的是全仓 20+ 份手抄 `try: float(x) except (TypeError, ValueError)`，
其中三条判据是**手抄版本各自不一致**的地方，必须钉住：

1. `None` / 空串 / `"abc"` ⇒ 回退（所有实现一致）；
2. **NaN / Inf ⇒ 回退**（旧实现全部放行，`nan` 会静默扩散进时间轴与打分）；
3. **`bool` ⇒ 回退**（`int(True)==1` 会让一个误传的开关变成合法数值 1）。
"""
from __future__ import annotations

import math

import pytest

from lsc.utils.coerce import safe_float, safe_int, try_float, try_int


# ────────────────── try_float ──────────────────
def test_try_float_accepts_numbers_and_numeric_strings():
    assert try_float(12.5) == 12.5
    assert try_float(7) == 7.0
    assert try_float("12.5") == 12.5
    assert try_float(" -0.25 ") == -0.25
    assert try_float(0.0) == 0.0  # 0 是合法值，不能当成"缺失"


@pytest.mark.parametrize("bad", [None, "", "  ", "abc", {}, [], object()])
def test_try_float_returns_none_for_unusable_values(bad):
    assert try_float(bad) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "nan", "inf"])
def test_try_float_rejects_non_finite(bad):
    """NaN/Inf 能被 `float()` 解析成功，但对时间轴/打分不可用。"""
    assert try_float(bad) is None
    # 反证：确实存在"float() 成功但我们要拒绝"的输入
    assert not math.isfinite(float(bad))


@pytest.mark.parametrize("flag", [True, False])
def test_try_float_rejects_bool(flag):
    assert try_float(flag) is None


# ────────────────── safe_float ──────────────────
def test_safe_float_uses_default_only_when_unparseable():
    assert safe_float("2.5") == 2.5
    assert safe_float(None, -1.0) == -1.0
    assert safe_float("n/a", 7.0) == 7.0
    assert safe_float(True, 7.0) == 7.0
    assert safe_float(float("nan"), 7.0) == 7.0


def test_safe_float_keeps_zero_and_negative_values():
    assert safe_float(0, 5.0) == 0.0
    assert safe_float("0", 5.0) == 0.0
    assert safe_float(-3.5, 5.0) == -3.5


# ────────────────── try_int ──────────────────
def test_try_int_accepts_ints_integral_floats_and_digit_strings():
    assert try_int(3) == 3
    assert try_int(3.0) == 3
    assert try_int("4") == 4
    assert try_int(" 5 ") == 5
    assert try_int(0) == 0
    assert try_int(-2) == -2


@pytest.mark.parametrize("bad", [None, "abc", "2.7", 2.7, 1.5, "", True, False, {}, []])
def test_try_int_returns_none_for_values_it_cannot_take(bad):
    """带小数部分的一律回退：`sequence`/`index` 静默截断会造成索引错位。"""
    assert try_int(bad) is None


def test_try_int_matches_int_on_its_own_accepts():
    """它接受的东西必须和 `int()` 一致，否则替换会改变既有行为。"""
    for value in (3, 3.0, "4", " 5 ", -2, 0):
        assert try_int(value) == int(value)


def test_safe_int_uses_default_only_when_unparseable():
    assert safe_int("42") == 42
    assert safe_int(None, 7) == 7
    assert safe_int("x", 7) == 7
    assert safe_int(2.7, 7) == 7
    assert safe_int(0, 7) == 0

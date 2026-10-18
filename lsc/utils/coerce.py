"""异构字典（ffprobe JSON / 前端 payload / sidecar / settings）取标量的唯一口径。

修前的形状：同一个"取不出来就回退"的 3 行 `try/except (TypeError, ValueError)`
在全仓手抄了 20+ 份（`probe.py` 一份、`shared_ingest.py` 一份、`valorant_broadcast.py`
一份…），外加两个已经成形的副本 `handlers/room_utils.safe_float` 与
`handlers/export_handlers._safe_float`。后果有两层：

1. **口径分叉**：有的实现 `except Exception`、有的只接 `(TypeError, ValueError)`、
   有的把 `None` 当 0.0、有的当"跳过"，同一个坏字段在不同入口表现不同；
2. **静态检查打不开**：`float(dict.get(...))` 的参数类型是 `Any | None` / `object`，
   mypy 每条都报 `[arg-type]`（债务报告 §11.6 的 23 条噪声全部来自这个形状），
   于是只能整模块豁免 —— 豁免一挂，真正的类型错也一起看不见了。

这里给出两个形状：`try_*` 返回 `None` 表示"取不出来"（调用方决定跳过/报错/回退，
语义与原来的 `except: continue` 一致），`safe_*` 直接给回退值。
"""
from __future__ import annotations

import math


def try_float(value: object) -> float | None:
    """把 `value` 解析成 float；不可解析（含 `None`、NaN、Inf）时返回 `None`。

    NaN/Inf 一并当作不可用：它们能 `float()` 成功，但会把时间轴/打分算成
    `nan` 并静默扩散（`float('inf')` 进 `min/max` 还会悄悄改边界）。
    """
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def safe_float(value: object, default: float = 0.0) -> float:
    """`try_float` 的回退版：解析不出来时用 `default`。"""
    parsed = try_float(value)
    return default if parsed is None else parsed


def try_int(value: object) -> int | None:
    """把 `value` 解析成 int；`None`、`"abc"`、带小数部分的值一律返回 `None`。

    整数值的浮点（`3.0`）仍然接受 —— 这些字段来自 JSON，历史数据里存成 `3.0` 是合法的；
    但 `2.7` 不走截断：`sequence`/`index` 出现小数本身就该让调用方回退，
    静默截断会造成索引错位。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def safe_int(value: object, default: int = 0) -> int:
    """`try_int` 的回退版。"""
    parsed = try_int(value)
    return default if parsed is None else parsed

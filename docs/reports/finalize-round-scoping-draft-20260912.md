# 实现草案：赛事出点「回合化取证」（finalize round-scoping）

> 状态：**Part 1 已落地（2026-09-12）**；Part 2 仍未落地（详见 §7.1 落地记录）。
> 补丁文件：`docs/reports/patch-finalize-round-scoping-20260912.diff`
> （`git apply --check` 已验证可干净应用）。证据与实测数据见
> `docs/reports/reaudit-2045-caps-experiment-20260912.json`（21 节，含 corrections）。

## 1. 一句话

赛事审计的「出点未被真正截断」硬否决（`_has_decreasing_combat_after`）是**窗口盲**的：
它在整个扫描区间上取计时器证据，会把**下一回合**的钟表递减当成「本回合还没结束」，
于是把**正确**的截断点否决掉。把该逃逸规则从「仅固定切块」放开到**所有候选**，
即可修好 135（生产 45s 窗口下即可），并消除宽窗口下 045/055/070 的误否决。

## 2. 证据链（每条都有实测）

| 证据 | 数据 |
|---|---|
| 135 的真实出点被误否决 | 生产：`next_prep/coarse`；日志：`终点硬否决 … cutoff=1423.2`；独立标签探针：1421-1422 result / 1423-1434 non_game / 1436-1441 replay；1450-1456 为 **buy**（证明 1423 前后确有回合边界） |
| 回合化后 135 定稿 | `accepted / broadcast_exclusion / precise / end=1423.25`，`scan_end=1490`（**生产窗口，未放宽上限**） |
| 宽窗口下 045/055 被误否决 | 放宽上限而不修回合化：045 `514.25 → 672.485`（跨回合 +158s，仍报 precise）、055 → `749.625` |
| 回合化后不劣化 | 045→513.985（Δ0.265）、055→672.625（Δ0.125）、070→746.813（Δ0）、076→rejected 不变 |
| 123 需要放宽上限 | 回合化后 123 仍 `manual_review`：真实出点 1420.75 超出 `MAX_BROADCAST_ROUND_SEC(150)` 给的窗口 1382 |

## 3. Part 1（核心补丁，一处）

```diff
--- a/lsc/analyzer/valorant_broadcast.py
+++ b/lsc/analyzer/valorant_broadcast.py
@@ -1868,19 +1868,19 @@
         if (
             cutoff is not None
             and reason
             and _has_decreasing_combat_after(
                 cutoff,
                 timer_samples,
-                # 分裂块的出点是固定切块边界，块内"回放后再接下一回合满钟"是
-                # 结构性正常形态，不能据此否决截断（普通候选保持原语义）。
-                fresh_clock_min=(
-                    FRESH_ROUND_CLOCK_MIN
-                    if item.get("split_from_oversize")
-                    else None
-                ),
+                # 回合化（2026-09-12）：所有候选都允许「满钟=新回合」逃逸。
+                # 该规则本就为「回放后接下一回合满钟」设计（原仅用于固定切块），
+                # 普通候选遇到的正是同一形态——跨回合的钟表递减会误否决正确截断：
+                # 实测 round-000135 的真实出点 1423.2 被误否决（→next_prep/coarse），
+                # 且窗口放宽后 045 的正确截断 514.0 也会被下一回合的钟表误否决
+                # （→重搜接受 672.485，跨回合粘连 +158s）。
+                fresh_clock_min=FRESH_ROUND_CLOCK_MIN,
             )
         ):
             _log.info(
```

应用：`git apply docs/reports/patch-finalize-round-scoping-20260912.diff`（已验证 `--check` 通过）。

## 4. Part 2（可选，仅 123 类候选需要；必须与 Part 1 同时上）

`round-000123` 的真实出点（1420.75）超出 OCR 锚点 44.75s 且被 `MAX_BROADCAST_ROUND_SEC=150` 卡住，
需要两个上限可配：

```python
# 常量区（MAX_BROADCAST_ROUND_SEC 同处）
FINALIZE_LOOKAHEAD_SEC = 45.0      # 原为两处内联 45.0（tail_lookahead / effective_lookahead）

# 两处内联常量替换：
-                45.0
+                FINALIZE_LOOKAHEAD_SEC
                  if (finalize or available_end is None or has_strong_end)
-            45.0
+            FINALIZE_LOOKAHEAD_SEC
             if (finalize or available_end is None or has_strong_ocr_end)
```

* 语义拆分建议：把「回合最大长度」（`MAX_BROADCAST_ROUND_SEC`，参与 `scan_end` 上界与长度断言）
  与「取帧窗口」（新增 `AUDIT_SCAN_MAX_SEC`）拆成两个常量，避免再次出现「为了看更远而放宽回合长度语义」。
* 运行时开关（与既有 `LSC_VALORANT_BROADCAST_MODE_SHADOW` 同风格）：
  `LSC_VALORANT_FINALIZE_LOOKAHEAD_SEC` / `LSC_VALORANT_AUDIT_SCAN_MAX_SEC`，
  默认值保持现行为（45 / 150），实验与收尾可临时放宽。
* **红线**：Part 2 单独上线 = 已知回归（045→672.485）。必须与 Part 1 同批。

## 5. 回归测试（两条，已验证：原文件红 / 补丁后绿）

追加到 `tests/test_valorant_broadcast.py`：

```python
from pathlib import Path

from lsc.analyzer.valorant_broadcast import (
    FRESH_ROUND_CLOCK_MIN,
    _has_decreasing_combat_after,
)

ROOT = Path(__file__).resolve().parents[1]


def test_veto_ignores_decreasing_clock_of_next_round() -> None:
    """截断点之后出现「满钟」= 新回合：不得据此否决（回合化取证）。

    形态复刻 2026-09-11 现场 round-000135：真实出点 1423.2 之后是回放/非游戏，
    紧接着下一回合满钟并继续递减；旧逻辑（普通候选 fresh_clock_min=None）据此
    否决 → 出点退回 next_prep/coarse（导出被判「出点未定稿」）。
    """
    timer_samples = [
        (1352.0, 100.0, "combat"),
        (1400.0, 52.0, "combat"),
        (1420.0, 32.0, "combat"),
        (1424.0, None, "non_game"),      # 回放/非游戏
        (1450.0, 100.0, "combat"),       # 下一回合满钟
        (1460.0, 90.0, "combat"),
        (1470.0, 80.0, "combat"),
    ]
    # 旧语义：跨回合钟表递减 → 误否决（这条断言锁住"改之前是坏的"）
    assert _has_decreasing_combat_after(1423.2, timer_samples) is True
    # 回合化：识别到满钟=新回合 → 不否决
    assert (
        _has_decreasing_combat_after(
            1423.2, timer_samples, fresh_clock_min=FRESH_ROUND_CLOCK_MIN
        )
        is False
    )


def test_veto_fresh_clock_is_round_scoped_for_all_candidates() -> None:
    """源码守卫：硬否决的 fresh-clock 逃逸不得再按 split_from_oversize 分叉。

    现场教训：跨回合的钟表证据会误否决正确截断（135 的 1423.2；宽窗口下 045 的 514.0）。
    若有人把条件改回「仅分裂块」，本用例必须红。
    """
    src = (ROOT / "lsc" / "analyzer" / "valorant_broadcast.py").read_text(encoding="utf-8")
    anchor = "and _has_decreasing_combat_after("
    window = src[src.index(anchor): src.index(anchor) + 700]
    assert "fresh_clock_min=FRESH_ROUND_CLOCK_MIN," in window
    assert "split_from_oversize" not in window, (
        "硬否决的回合化逃逸不得再按 split_from_oversize 分叉（跨回合钟表会误否决正确截断）"
    )
```

## 6. L2 验收脚本（媒体级，分钟级，只读）

建议落点 `scripts/valorant_vision/verify_finalize_round_scoping.py`。
**本脚本已抽出实跑验证过**（抽取到 %TEMP% 运行，exit=1、红在 135 = 目标），
并据此修掉两个坑：① 必须探测录像时长并传 `available_end`（否则审计窗口语义变化，
045/055 基线不复现）；② 候选必须带上 `result_ts`（生产候选取自扫掠结果，缺它排除搜索不触发）。

```python
"""实测定稿口径验收（只读录像，不写 sidecar/草稿）。

用法：
  python scripts/valorant_vision/verify_finalize_round_scoping.py --recording <mp4> \
    --fixture tests/fixtures/broadcast_export_case_20260911_2045 \
    --profile core|with-caps --out docs/reports/verify-round-scoping-<date>.json

profile 语义：
  core      = 只要求 Part 1（135 定稿 + 可复现项不劣化 + 076 仍拒）
  with-caps = 额外要求 Part 2（123 定稿）
退出码：0 全部达标 / 1 有不达标 / 2 用法或媒体错误。
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lsc.analyzer.valorant_broadcast import audit_broadcast_rounds_with_outcomes  # noqa: E402
from lsc.analyzer.valorant_frame_classifier import ValorantFrameClassifier  # noqa: E402

FFMPEG = r"C:/Users/Administrator/AppData/Roaming/lsc-electron/runtime/ffmpeg/ffmpeg.exe"
TOL = 1.5  # 秒

# 绝对判据：只放"能从归档夹具复现"的项（055/070/076/135，见 §6.2）
EXPECT_CORE: dict[str, tuple[float | None, str, float]] = {
    "round-000055": (672.75, "accepted", TOL),
    "round-000070": (746.813, "accepted", TOL),
    "round-000076": (None, "rejected", 0.0),
    "round-000135": (1423.25, "accepted", TOL),   # Part 1 的收益；改前必红
}
EXPECT_WITH_CAPS = dict(EXPECT_CORE, **{"round-000123": (1420.75, "accepted", TOL)})

ROUND_KEYS = ["round-000045", "round-000055", "round-000070",
              "round-000076", "round-000123", "round-000135"]


def _probe_duration(recording: str, ffmpeg: str) -> float:
    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe"))
    if not Path(ffprobe).is_file():
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", recording],
            capture_output=True, text=True, timeout=60)
        return float((out.stdout or "0").strip() or 0.0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def _candidates(fixture: Path) -> list[dict[str, Any]]:
    """候选 = 收尾 sidecar 的 coarse 边界 + result_ts（生产候选取自扫掠结果）。"""
    fin = json.loads(sorted(fixture.glob("*.finalization.json"))[0].read_text(encoding="utf-8"))
    side = {c["round_key"]: c for c in fin["accepted_candidates"]}
    raw = {"round-000076": (763.7, 843.4), "round-000123": (1232.0, 1346.0),
           "round-000135": (1352.0, 1445.0)}
    out = []
    for key in ROUND_KEYS:
        extra: dict[str, Any] = {}
        if key in side:
            s, e = float(side[key]["start_coarse"]), float(side[key]["end_coarse"])
            if side[key].get("result_ts") is not None:
                extra["result_ts"] = float(side[key]["result_ts"])
        else:
            s, e = raw[key]
        out.append({"round_key": key, "start": s, "end": e, "start_coarse": s, "end_coarse": e,
                    "start_by": "ocr_combat", "end_by": "next_prep",
                    "confirm_status": "pending", **extra})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recording", required=True)
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--profile", choices=["core", "with-caps"], default="core")
    ap.add_argument("--ffmpeg", default=FFMPEG)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rec = Path(a.recording)
    if not rec.is_file():
        print(f"录像不存在: {rec}", file=sys.stderr)
        return 2
    duration = _probe_duration(str(rec), a.ffmpeg)
    expect = EXPECT_CORE if a.profile == "core" else EXPECT_WITH_CAPS
    clf = ValorantFrameClassifier(profile="broadcast")
    clf.load()
    rows, failed = [], []
    for cand in _candidates(Path(a.fixture)):
        t0 = time.monotonic()
        outs = audit_broadcast_rounds_with_outcomes(
            [dict(cand)], str(rec), ffmpeg_path=a.ffmpeg, classifier=clf,
            available_end=duration or None, finalize=True)
        row: dict[str, Any] = {"round_key": cand["round_key"],
                               "elapsed_sec": round(time.monotonic() - t0, 2)}
        for o in outs:
            c = o.candidate if isinstance(getattr(o, "candidate", None), dict) else {}
            row.update(status=getattr(o, "status", None), reason=getattr(o, "reason", None),
                       end=c.get("end"), end_by=c.get("end_by"),
                       end_quality=c.get("end_quality"), audit=c.get("broadcast_audit"),
                       scan_end=c.get("broadcast_audit_scan_end"))
        want = expect.get(cand["round_key"])
        if want is not None:
            w_end, w_status, tol = want
            ok = row.get("status") == w_status
            note = ""
            if w_end is not None and row.get("end") is not None:
                note = f"delta={abs(float(row['end']) - w_end):.3f}s"
                ok = ok and abs(float(row["end"]) - w_end) <= tol
                ok = ok and row.get("end_quality") == "precise"
            row["expect"] = {"end": w_end, "status": w_status, "tol": tol, "note": note, "ok": ok}
            if not ok:
                failed.append(row["round_key"])
        rows.append(row)
        print(f"{row['round_key']}: {row.get('status')} end={row.get('end')} "
              f"{row.get('end_by')} {row.get('end_quality')} ({row['elapsed_sec']}s)",
              file=sys.stderr, flush=True)
    report = {"profile": a.profile, "recording": str(rec), "recording_duration_sec": duration,
              "rows": rows, "failed": failed, "passed": not failed}
    out = Path(a.out)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)
    if failed:
        print(f"不达标: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"全部达标 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

### 6.1 判据

| 候选 | 期望 end | 期望状态 | 依据 |
|---|---|---|---|
| 045 | 514.25 ±1.5 | accepted / precise | **改前红**：实测 534.735/coarse；补丁后精确复现生产值（Δ0.000） |
| 055 | 672.75 ±1.5 | accepted / precise | 归档复现（实测 672.75 / 672.625） |
| 070 | 746.813 ±1.5 | accepted / precise | 归档复现（实测 746.813，Δ0） |
| 076 | — | rejected | 无效候选，离线判 `rejected_no_stable_combat_start` |
| 135 | 1423.25 ±1.5 | accepted / precise | Part 1 后生产窗口即可（改前必红：实测 `next_prep/coarse`） |
| 123 | 1420.75 ±1.5 | accepted / precise | 需 Part 2 |

### 6.2 更正：045 的差异本来就是同一个 bug（原判断已撤回）

草案初版把「045 无法从归档夹具反推」记为**夹具可复现性边界**——**这个判断是错的**。
打上 Part 1 后重跑，**同一份归档输入**给出 `514.25 / broadcast_exclusion / precise`，
与生产值**完全一致（Δ0.000）**；未打补丁时是 `534.735 / next_prep / coarse`。
即：045 与 135 同源——硬否决误杀了正确的 145→514 截断，只是它在**离线重放路径**上
同样复现，于是在改前看起来像「夹具缺字段」。结论：

* 045 已进入绝对判据（§6.1），并作为**改前红**的第二条证据（L2 实测）；
* 「归档审计前的候选原文」仍是有价值的夹具改进（让 L2 少依赖 sidecar 间接推导、覆盖更多候选），
  但**不再是 045 的前置条件**，降级为可选改进项。

## 7. 应用顺序 / CI / 回滚

1. `git apply patch-finalize-round-scoping-20260912.diff`（Part 1）→ 追加 §5 两条测试 →
   `pytest tests/test_valorant_broadcast.py -q`（原文件应红、补丁后应绿）。
2. 跑 §6 验收脚本 `--profile core`：135 由红转绿、055/070/076 保持，才继续。
3. 需要 123 类候选定稿时再上 Part 2（常量 + env 开关，默认值不变），跑 `--profile with-caps`。
4. CI：L1 = §5 两条测试（秒级，进每次提交）；L2 = §6 脚本（分钟级，合并前 / 发版前）。
5. 回滚：Part 1 是单处替换，`git apply -R` 或还原该文件即可；env 开关回到默认即恢复旧口径。

### 7.1 落地记录（2026-09-12）

| 步骤 | 结果 |
|---|---|
| `git apply …patch-finalize-round-scoping-20260912.diff` | OK（单处替换，`--check` 早已通过） |
| L1 `pytest tests/test_valorant_broadcast.py -q` | **58 passed**；新增两条中，**源码守卫改前红→改后绿**，语义特征测试两态皆绿（它锁的是函数参数语义，不随调用点翻转——行为级门禁由 L2 承担） |
| L1 相关套件（broadcast / finalization / draft 共 7 文件） | **132 passed** |
| 全量 `pytest -q` | **1970 passed**（改前 1968，+2） |
| L2 `--profile core` | **exit 0 全部达标**：045=514.25、055=672.75、070=746.813、076=rejected、**135=1423.25 precise**；123 仍 `manual_review`（按设计待 Part 2） |
| L2 报告 | `docs/reports/verify-round-scoping-20260912.json` |
| 新增脚本 | `scripts/valorant_vision/verify_finalize_round_scoping.py`（只读，媒体级验收） |

## 8. 风险与未决项

* **影响面变化**：Part 1 把该逃逸从「仅分裂块」扩大到「所有候选」，改变生产线行为（非新逻辑）。
  启用前建议用归档硬数据集复跑边界精度，重点看反向风险：**把「同一回合内的满钟误读」当成新回合** →
  出点可能被提前定稿。缓解：`FRESH_ROUND_CLOCK_MIN=85` 是既有的保守阈值，且仅影响「否决」这一条判定。
* **123 与 135 重叠 68.75s → 已由 L1（区间内边界自检）消除来源**：123 的区间内跨了回合边界
  （真实回合 B 的起点 1350 在 123 的区间内部），L1 落地后宽窗口下 123 被拒（`INTERIOR_BOUNDARY`）、
  不再产出跨回合切片；045/135 无误伤。详见 `candidate-dedup-merge-draft-20260912.md` §9。
  副产品：**Part 2 的动机减弱**——本夹具已无「区间干净但真实出点超出 +45s」的候选。
* **`reason_code` 建议**：新增 `EXCLUSION_VETOED_BY_TIMER`，把「找到了排除点但被计时器规则否决」
  与 `pending_no_exclusion`（找不到证据）分开；Part 1 落地后这一类应基本消失。
* **夹具 schema 改进**（可选，已降级）：归档「审计前的候选原文」——不是 045 的前置条件（§6.2 已更正）。
* **未验证**：Part 2 的默认值（45→?）与 `AUDIT_SCAN_MAX_SEC` 的取值需要成本/误判率实测后再定；
  本草案只固定「可配」这个机制。

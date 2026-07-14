# tests/test_ocr_accel.py
from lsc.analyzer.ocr_accel import (
    VALID_OCR_ACCEL,
    normalize_ocr_accel,
    list_accel_candidates,
    resolve_ocr_accel,
)


def test_normalize_ocr_accel_defaults_and_aliases() -> None:
    assert normalize_ocr_accel(None) == "auto"
    assert normalize_ocr_accel("") == "auto"
    assert normalize_ocr_accel("CPU") == "cpu"
    assert normalize_ocr_accel("DirectML") == "dml"
    assert normalize_ocr_accel("bogus") == "auto"
    assert set(VALID_OCR_ACCEL) == {"auto", "dml", "cuda", "cpu"}


def test_list_accel_candidates_cpu_always(monkeypatch) -> None:
    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel._onnx_providers",
        lambda: ["CPUExecutionProvider"],
    )
    monkeypatch.setattr("lsc.analyzer.ocr_accel._is_windows_dml_capable", lambda: False)
    assert list_accel_candidates() == ["cpu"]


def test_list_accel_candidates_includes_dml_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel._onnx_providers",
        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr("lsc.analyzer.ocr_accel._is_windows_dml_capable", lambda: True)
    assert list_accel_candidates() == ["dml", "cpu"]


def test_resolve_forced_dml_falls_back_when_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("lsc.analyzer.ocr_accel.list_accel_candidates", lambda: ["cpu"])
    assert resolve_ocr_accel("dml", probe_timings=None) == "cpu"


def test_resolve_auto_prefers_dml_then_cuda(monkeypatch) -> None:
    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel.list_accel_candidates",
        lambda: ["dml", "cpu"],
    )
    assert resolve_ocr_accel("auto", probe_timings=None) == "dml"

    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel.list_accel_candidates",
        lambda: ["cuda", "cpu"],
    )
    assert resolve_ocr_accel("auto", probe_timings=None) == "cuda"

    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel.list_accel_candidates",
        lambda: ["cpu"],
    )
    assert resolve_ocr_accel("auto", probe_timings=None) == "cpu"

    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel.list_accel_candidates",
        lambda: ["dml", "cpu"],
    )
    assert resolve_ocr_accel(
        "auto",
        probe_timings={"dml": 40, "cpu": 10},
    ) == "cpu"


def test_list_accel_candidates_excludes_dml_when_not_capable(monkeypatch) -> None:
    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel._onnx_providers",
        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr("lsc.analyzer.ocr_accel._is_windows_dml_capable", lambda: False)
    assert list_accel_candidates() == ["cpu"]


def test_list_accel_candidates_includes_cuda(monkeypatch) -> None:
    monkeypatch.setattr(
        "lsc.analyzer.ocr_accel._onnx_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setattr("lsc.analyzer.ocr_accel._is_windows_dml_capable", lambda: False)
    assert list_accel_candidates() == ["cuda", "cpu"]


from lsc.analyzer import ocr_accel as oa


def test_rapidocr_kwargs_for_backends() -> None:
    assert oa.rapidocr_kwargs_for("cpu") == {}
    dml = oa.rapidocr_kwargs_for("dml")
    assert dml.get("det_use_dml") is True
    assert dml.get("rec_use_dml") is True
    cuda = oa.rapidocr_kwargs_for("cuda")
    assert cuda.get("det_use_cuda") is True or cuda.get("use_cuda") is True


def test_probe_cache_roundtrip(tmp_path, monkeypatch) -> None:
    cache_file = tmp_path / "ocr_accel_probe.json"
    monkeypatch.setattr(oa, "_probe_cache_path", lambda: cache_file)
    oa.save_probe_cache({"dml": 10.0, "cpu": 40.0}, selected="dml", ort_version="1.0")
    loaded = oa.load_probe_cache()
    assert loaded is not None
    assert loaded["selected"] == "dml"
    assert loaded["timings"]["cpu"] == 40.0


def test_create_ocr_uses_resolved_backend(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(oa, "run_probe_if_needed", lambda mode: "dml")
    monkeypatch.setattr(
        "rapidocr_onnxruntime.RapidOCR",
        FakeOCR,
        raising=False,
    )
    import sys
    import types
    mod = types.ModuleType("rapidocr_onnxruntime")
    mod.RapidOCR = FakeOCR
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", mod)
    inst = oa.create_ocr("auto")
    assert isinstance(inst, FakeOCR)
    assert calls and calls[0].get("det_use_dml") is True


def test_invalidate_ocr_clears_singleton(monkeypatch) -> None:
    from lsc.analyzer import ocr_detector as od
    od._ocr_instance = object()
    od.invalidate_ocr()
    assert od._ocr_instance is None


def test_read_settings_ocr_accel(tmp_path, monkeypatch) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"ocr_accel": "cpu"}', encoding="utf-8")
    monkeypatch.setattr(oa, "_settings_path", lambda: settings)
    assert oa.read_settings_ocr_accel() == "cpu"


def test_ffmpeg_hwaccel_args() -> None:
    from lsc.analyzer.ocr_accel import ffmpeg_hwaccel_args
    import platform

    assert ffmpeg_hwaccel_args("cpu") == []
    assert ffmpeg_hwaccel_args("dml") == ["-hwaccel", "d3d11va"]
    assert ffmpeg_hwaccel_args("cuda") == ["-hwaccel", "cuda"]
    if platform.system() == "Windows":
        assert ffmpeg_hwaccel_args("auto") == ["-hwaccel", "d3d11va"]
    else:
        assert ffmpeg_hwaccel_args("auto") == []


def test_normalize_settings_ocr_accel() -> None:
    from handlers.room_handler import _normalize_settings_ocr_accel

    assert _normalize_settings_ocr_accel({"ocr_accel": "CPU"})["ocr_accel"] == "cpu"
    assert _normalize_settings_ocr_accel({"ocr_accel": "bogus"})["ocr_accel"] == "auto"
    assert _normalize_settings_ocr_accel({})["ocr_accel"] == "dml"


def test_load_settings_default_includes_ocr_accel(monkeypatch, tmp_path) -> None:
    from handlers import room_handler as rh

    settings_file = tmp_path / "missing_settings.json"
    monkeypatch.setattr(rh, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(rh, "_settings_cache", None)
    monkeypatch.setattr(rh, "_settings_cache_mtime", 0.0)
    monkeypatch.setattr(rh, "_settings_cache_time", 0.0)

    data = rh.load_settings()
    assert data.get("ocr_accel") == "dml"


def test_save_settings_normalizes_ocr_accel_and_invalidates(monkeypatch, tmp_path) -> None:
    from handlers import room_handler as rh

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(rh, "SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(rh, "_settings_cache", None)
    monkeypatch.setattr(rh, "_settings_cache_mtime", 0.0)
    monkeypatch.setattr(rh, "_settings_cache_time", 0.0)

    called = {"n": 0}

    def _invalidate() -> None:
        called["n"] += 1

    monkeypatch.setattr(
        "lsc.analyzer.ocr_detector.invalidate_ocr",
        _invalidate,
    )

    base = rh.load_settings()
    rh.save_settings({**base, "ocr_accel": "bogus"})
    data = rh.load_settings()
    assert data.get("ocr_accel") == "auto"
    # default is dml → bogus 归一化为 auto，属于变更，应 invalidate
    assert called["n"] == 1

    rh.save_settings({**data, "ocr_accel": "cpu"})
    assert rh.load_settings().get("ocr_accel") == "cpu"
    assert called["n"] == 2

    # 同值再存不应再次 invalidate（含大小写归一化）
    rh.save_settings({**rh.load_settings(), "ocr_accel": "cpu"})
    assert called["n"] == 2
    rh.save_settings({**rh.load_settings(), "ocr_accel": "CPU"})
    assert called["n"] == 2


def test_run_probe_if_needed_auto_writes_cache(tmp_path, monkeypatch) -> None:
    from lsc.analyzer import ocr_accel as oa

    cache_file = tmp_path / "ocr_accel_probe.json"
    monkeypatch.setattr(oa, "_probe_cache_path", lambda: cache_file)
    oa._process_probe = None
    monkeypatch.setattr(oa, "run_micro_benchmark", lambda: {"dml": 5.0, "cpu": 20.0})
    monkeypatch.setattr(oa, "list_accel_candidates", lambda: ["dml", "cpu"])
    monkeypatch.setattr(oa, "_ort_version", lambda: "test-ort")
    selected = oa.run_probe_if_needed("auto")
    assert selected == "dml"
    assert cache_file.is_file()
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        return {"cpu": 1.0}

    monkeypatch.setattr(oa, "run_micro_benchmark", boom)
    assert oa.run_probe_if_needed("auto") == "dml"
    assert calls["n"] == 0


def test_run_probe_if_needed_auto_selects_cpu_when_faster(tmp_path, monkeypatch) -> None:
    from lsc.analyzer import ocr_accel as oa

    cache_file = tmp_path / "ocr_accel_probe.json"
    monkeypatch.setattr(oa, "_probe_cache_path", lambda: cache_file)
    oa._process_probe = None
    monkeypatch.setattr(
        oa,
        "run_micro_benchmark",
        lambda: {"dml": 40.0, "cpu": 10.0},
    )
    monkeypatch.setattr(oa, "list_accel_candidates", lambda: ["dml", "cpu"])
    monkeypatch.setattr(oa, "_ort_version", lambda: "test-ort")
    assert oa.run_probe_if_needed("auto") == "cpu"


def test_run_ffmpeg_ocr_extract_retries_without_hwaccel(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))

        class R:
            returncode = 1 if "-hwaccel" in cmd else 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr("lsc.utils.process_launcher.run_hidden", fake_run)
    base = ["ffmpeg", "-y", "-i", "in.mp4", "out_%05d.jpg"]
    r = oa.run_ffmpeg_with_hwaccel_fallback(base, hwaccel_args=["-hwaccel", "d3d11va"])
    assert r.returncode == 0
    assert len(calls) == 2
    assert "-hwaccel" in calls[0]
    assert "-hwaccel" not in calls[1]


def test_run_hidden_sets_create_no_window_on_windows(monkeypatch) -> None:
    from lsc.utils import process_launcher as pl

    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr(pl.subprocess, "run", fake_run)
    monkeypatch.setattr(pl, "_IS_WINDOWS", True)
    monkeypatch.setattr(pl, "get_creation_flags", lambda: 0x08000000)
    pl.run_hidden(["ffmpeg", "-version"], capture_output=True)
    assert seen.get("creationflags") == 0x08000000

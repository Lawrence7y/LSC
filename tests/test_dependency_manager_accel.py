from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dependency_manager():
    module_path = ROOT / "python-backend" / "dependency_manager.py"
    spec = importlib.util.spec_from_file_location("lsc_dependency_manager_test", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_dependencies_only_repair_missing_gpu_provider(monkeypatch) -> None:
    dm = _load_dependency_manager()
    calls: list[str] = []

    monkeypatch.setattr(dm, "check_core_deps_ok", lambda: True)
    monkeypatch.setattr(dm, "_is_package_importable", lambda _name: True)
    monkeypatch.setattr(dm, "_has_windows_onnx_accel", lambda: False)
    monkeypatch.setattr(
        dm,
        "_pip_install_requirements",
        lambda *_args, **_kwargs: calls.append("requirements") or True,
    )
    monkeypatch.setattr(
        dm,
        "_install_windows_directml",
        lambda _phase: calls.append("directml") or True,
    )

    assert dm.install_python_deps(include_ai=True) is True
    assert calls == ["directml"]


def test_existing_gpu_provider_skips_all_reinstallation(monkeypatch) -> None:
    dm = _load_dependency_manager()
    calls: list[str] = []

    monkeypatch.setattr(dm, "check_core_deps_ok", lambda: True)
    monkeypatch.setattr(dm, "_is_package_importable", lambda _name: True)
    monkeypatch.setattr(dm, "_has_windows_onnx_accel", lambda: True)
    monkeypatch.setattr(
        dm,
        "_pip_install_requirements",
        lambda *_args, **_kwargs: calls.append("requirements") or True,
    )
    monkeypatch.setattr(
        dm,
        "_install_windows_directml",
        lambda _phase: calls.append("directml") or True,
    )

    assert dm.install_python_deps(include_ai=True) is True
    assert calls == []

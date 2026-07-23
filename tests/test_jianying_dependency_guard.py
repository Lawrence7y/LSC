from __future__ import annotations


def test_pyjianyingdraft_importable():
    import pyJianYingDraft  # noqa: F401


def test_requirements_pins_pyjianyingdraft():
    from pathlib import Path
    text = Path("requirements.txt").read_text(encoding="utf-8")
    assert "pyJianYingDraft==0.3.0" in text

from pathlib import Path
from unittest.mock import patch

import lsc.analyzer.ocr_accel as oa


def test_save_probe_cache_oserror_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(oa, "_probe_cache_path", lambda: tmp_path / "probe.json")

    def boom(*a, **k):
        raise OSError("read-only")

    with patch.object(Path, "write_text", boom):
        oa.save_probe_cache({"cpu": 1.0}, selected="cpu", ort_version="1")
    # 不抛即通过

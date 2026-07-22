from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "python-backend" / "handlers" / "room_handler.py").read_text(encoding="utf-8")


def test_analysis_jobs_lock_defined():
    assert "_analysis_jobs_lock" in SRC
    assert "threading.RLock()" in SRC or "RLock()" in SRC


def test_analysis_jobs_mutations_use_lock():
    # At least several with _analysis_jobs_lock usages
    assert SRC.count("with _analysis_jobs_lock") >= 5

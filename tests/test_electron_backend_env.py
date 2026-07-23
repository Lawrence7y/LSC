from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_electron_passes_valorant_runtime_env_to_backend() -> None:
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    safe_env = source.split("const safeEnv", 1)[1].split("backendProcess = spawn", 1)[0]
    assert "LSC_VALORANT_MODEL_DIR" in safe_env
    assert "LSC_VALORANT_VISION_SHADOW" in safe_env

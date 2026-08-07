from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_electron_passes_valorant_runtime_env_to_backend() -> None:
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    safe_env = source.split("const safeEnv", 1)[1].split("backendProcess = spawn", 1)[0]
    assert "LSC_VALORANT_MODEL_DIR" in safe_env
    assert "LSC_VALORANT_VISION_SHADOW" in safe_env


def test_development_mode_checks_runtime_dependencies() -> None:
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    startup = source.split(
        "async function ensureDependenciesThenStartBackend", 1
    )[1].split("app.whenReady()", 1)[0]

    assert "开发模式：跳过依赖检查" not in startup
    assert "hasValidDependencyMarker()" in startup
    assert "checkDependencies()" in startup
    assert "installDependencies(true)" in startup


def test_dependency_fast_path_requires_windows_gpu_provider_distribution() -> None:
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    guard = source.split("function hasRequiredRuntimeFiles", 1)[1].split(
        "function hasValidDependencyMarker", 1
    )[0]

    assert "onnxruntime_(directml|gpu)" in guard
    assert "inferenceProviderReady" in guard


def test_csp_allows_blob_audio_worklet() -> None:
    """预览对齐用 Blob URL 加载 AudioWorklet；CSP 必须放行 blob script/worker。"""
    source = (ROOT / "lsc-electron/electron/main.ts").read_text(encoding="utf-8")
    assert "worker-src 'self' blob:" in source
    assert "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:" in source
    assert "script-src 'self' file: blob:" in source

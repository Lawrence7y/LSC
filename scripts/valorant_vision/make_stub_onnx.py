"""生成测试用最小五分类 ONNX，并回填 sha256 到同目录 json。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


def main() -> None:
    try:
        import onnx
        from onnx import TensorProto, helper, numpy_helper
    except ImportError as exc:
        raise SystemExit("需要 onnx 包以生成 stub") from exc

    out_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "valorant_vision"
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / "valorant_phase_v1.onnx"
    meta_path = out_dir / "valorant_phase_v1.json"

    # input [N,3,224,224] -> Flatten -> Gemm -> Softmax
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, ["N", 3, 224, 224])
    y = helper.make_tensor_value_info("probs", TensorProto.FLOAT, ["N", 5])
    flat = helper.make_node("Flatten", ["input"], ["flat"], axis=1)
    w = numpy_helper.from_array(np.zeros((5, 3 * 224 * 224), dtype=np.float32), name="W")
    b = numpy_helper.from_array(
        np.array([0.0, 0.0, 10.0, 0.0, 0.0], dtype=np.float32), name="B"
    )  # bias toward combat
    gemm = helper.make_node("Gemm", ["flat", "W", "B"], ["logits"], alpha=1.0, beta=1.0, transB=1)
    sm = helper.make_node("Softmax", ["logits"], ["probs"], axis=1)
    graph = helper.make_graph([flat, gemm, sm], "valorant_stub", [x], [y], [w, b])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    onnx.checker.check_model(model)
    onnx.save(model, str(onnx_path))

    digest = hashlib.sha256(onnx_path.read_bytes()).hexdigest()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sha256"] = digest
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {onnx_path} sha256={digest}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Local keyboard label UI for Valorant phase frames.

  python scripts/valorant_vision/serve_label_ui.py
  open http://127.0.0.1:8765/
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.valorant_vision.round_gt import (
    build_preview_payload,
    load_draft_rounds,
    save_confirmed_rounds,
)

DEFAULT_ROOT = Path.home() / "LSC" / "datasets" / "valorant_phase" / "annotate"
PORT = 8765

ROOT = DEFAULT_ROOT
QUEUE = ROOT / "queue.json"
LABELS = ROOT / "labels.json"
MANIFEST = ROOT / "manifest_labeled.jsonl"


@dataclass(frozen=True)
class LabelPaths:
    root: Path
    queue: Path
    labels: Path
    manifest: Path


def default_root() -> Path:
    return DEFAULT_ROOT


def resolve_paths(root: Path | None = None) -> LabelPaths:
    root_path = (root or default_root()).resolve()
    return LabelPaths(
        root=root_path,
        queue=root_path / "queue.json",
        labels=root_path / "labels.json",
        manifest=root_path / "manifest_labeled.jsonl",
    )


def configure_paths(root: Path | None = None) -> LabelPaths:
    global ROOT, QUEUE, LABELS, MANIFEST
    paths = resolve_paths(root)
    ROOT = paths.root
    QUEUE = paths.queue
    LABELS = paths.labels
    MANIFEST = paths.manifest
    return paths


def _load_queue_frames() -> list[dict]:
    return json.loads(QUEUE.read_text(encoding="utf-8"))


def _infer_video_duration(video: dict, frames: list[dict]) -> float:
    duration = video.get("duration_sec")
    if duration is not None:
        try:
            value = float(duration)
            if value >= 0:
                return value
        except (TypeError, ValueError):
            pass
    ground_truth = video.get("ground_truth") or []
    max_round_end = max((float(row["end"]) for row in ground_truth), default=0.0)
    if frames:
        max_frame_ts = max(float(frame["timestamp_sec"]) for frame in frames)
        return max(max_round_end, max_frame_ts)
    return max_round_end


def _load_rounds_manifest() -> dict:
    confirmed_path = ROOT / "rounds_confirmed.json"
    if confirmed_path.is_file():
        data = json.loads(confirmed_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"invalid rounds_confirmed.json: {confirmed_path}")
        return data
    return load_draft_rounds(ROOT)


def _enrich_rounds_manifest(manifest: dict, frames: list[dict]) -> dict:
    videos = manifest.get("videos")
    if not isinstance(videos, list):
        return manifest
    enriched_videos: list[dict] = []
    for video in videos:
        if not isinstance(video, dict):
            enriched_videos.append(video)
            continue
        video_frames = [
            frame
            for frame in frames
            if frame.get("video_id") == video.get("video_id")
        ] or frames
        item = dict(video)
        item["duration_sec"] = _infer_video_duration(item, video_frames)
        enriched_videos.append(item)
    return {**manifest, "videos": enriched_videos}

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>无畏契约 · 帧标注</title>
<style>
  :root { color-scheme: dark; font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; }
  body { margin: 0; background: #111; color: #eee; }
  .wrap { display: grid; grid-template-columns: 1fr 320px; gap: 12px; height: 100vh; padding: 12px; box-sizing: border-box; }
  .main { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  img { max-width: 100%; max-height: calc(100vh - 160px); object-fit: contain; background: #000; border-radius: 8px; }
  .meta { font-size: 14px; line-height: 1.5; color: #bbb; }
  .keys button { margin: 4px 4px 0 0; padding: 10px 12px; font-size: 14px; cursor: pointer; border-radius: 8px; border: 1px solid #444; background: #222; color: #fff; }
  .keys button.active { outline: 2px solid #2e8dff; }
  .side { overflow: auto; border-left: 1px solid #333; padding-left: 12px; font-size: 13px; }
  .bar { display:flex; gap:8px; align-items:center; flex-wrap: wrap; }
  progress { width: 240px; }
  input[type=text] { width: 100%; padding: 8px; background:#1a1a1a; border:1px solid #444; color:#eee; border-radius:6px; }
  .tag { display:inline-block; padding:2px 6px; border-radius:4px; background:#333; margin-right:4px; }
  .side h3 { margin: 0 0 8px; font-size: 15px; }
  .help { margin-top: 12px; color: #999; line-height: 1.6; }
</style>
</head>
<body>
<div class="wrap">
  <div class="main">
    <div class="bar">
      <a href="/rounds" style="color:#2e8dff;text-decoration:none">确认回合</a>
      <strong id="progressText">0/0</strong>
      <progress id="prog" value="0" max="1"></progress>
      <span id="filterInfo"></span>
    </div>
    <img id="frame" alt="当前帧"/>
    <div class="meta" id="meta"></div>
    <div class="keys">
      <button data-l="non_game">1 非游戏</button>
      <button data-l="buy">2 买枪</button>
      <button data-l="combat">3 交战</button>
      <button data-l="result">4 结算</button>
      <button data-l="replay">5 回放</button>
      <button id="skip">S 跳过</button>
      <button id="undo">Z 撤销</button>
      <button id="prev">← 上一帧</button>
      <button id="next">→ 下一帧</button>
      <button id="export">导出清单</button>
      <button id="onlyUnlabeled">只看未标</button>
    </div>
    <input id="notes" type="text" placeholder="备注（可选）"/>
  </div>
  <div class="side">
    <h3>标注统计</h3>
    <pre id="stats"></pre>
    <div class="help">
      <div><b>快捷键</b></div>
      <div>1–5：标注并前进</div>
      <div>S：跳过　Z：撤销</div>
      <div>← / →：切换帧</div>
      <div style="margin-top:8px"><b>类别说明</b></div>
      <div>非游戏：舞台 / 广告 / 选人 / 设置</div>
      <div>买枪：准备 / 商店阶段</div>
      <div>交战：回合正赛画面</div>
      <div>结算：胜负结果画面</div>
      <div>回放：赛事 Replay / 死亡回放</div>
    </div>
  </div>
</div>
<script>
let queue = [];
let labels = {};
let idx = 0;
let onlyUnlabeled = false;
const order = ["non_game","buy","combat","result","replay"];
const LABEL_ZH = {
  non_game: "非游戏",
  buy: "买枪",
  combat: "交战",
  result: "结算",
  replay: "回放",
};
const SOURCE_ZH = { pov: "第一视角", broadcast: "赛事转播" };
const SPLIT_ZH = { train: "训练集", val: "验证集", test: "测试集" };

async function load() {
  queue = await (await fetch("/api/queue")).json();
  labels = await (await fetch("/api/labels")).json();
  idx = firstUnlabeled();
  render();
}
function firstUnlabeled() {
  for (let i=0;i<queue.length;i++) if (!labels[queue[i].id]) return i;
  return 0;
}
function visibleIndices() {
  if (!onlyUnlabeled) return queue.map((_,i)=>i);
  return queue.map((q,i)=>[q,i]).filter(([q])=>!labels[q.id]).map(([,i])=>i);
}
function current() { return queue[idx]; }
function render() {
  const q = current();
  if (!q) return;
  document.getElementById("frame").src = "/frame/" + encodeURIComponent(q.rel_path) + "?t=" + Date.now();
  const lab = labels[q.id];
  const labelText = lab ? (LABEL_ZH[lab.label] || lab.label) : "未标注";
  const hint = [];
  if (q.priority) hint.push(`复核:${q.priority}`);
  if (q.current_label) hint.push(`原标:${LABEL_ZH[q.current_label] || q.current_label}`);
  if (q.suggested_label) hint.push(`建议:${LABEL_ZH[q.suggested_label] || q.suggested_label}`);
  const reason = q.reason || q.notes || "";
  document.getElementById("meta").innerHTML =
    `<span class="tag">${SOURCE_ZH[q.source_type] || q.source_type}</span>` +
    `<span class="tag">${SPLIT_ZH[q.split] || q.split}</span>` +
    (q.priority ? `<span class="tag" style="background:#5a3d00">复核</span>` : "") +
    ` 时间 ${q.timestamp_sec.toFixed(1)} 秒<br/>` +
    `${q.video_id}<br/>当前标签：<b>${labelText}</b>` +
    (hint.length ? `<br/><span style="color:#ffb84d">${hint.join(" · ")}</span>` : "") +
    (reason ? `<br/><span style="color:#9ad">${reason}</span>` : "");
  document.getElementById("notes").value = lab?.notes || "";
  const labeled = Object.keys(labels).length;
  document.getElementById("progressText").textContent = `第 ${idx+1} / ${queue.length} 帧 · 已标注 ${labeled}`;
  document.getElementById("prog").max = queue.length;
  document.getElementById("prog").value = labeled;
  document.querySelectorAll(".keys button[data-l]").forEach(b => {
    b.classList.toggle("active", lab && lab.label === b.dataset.l);
  });
  const counts = Object.fromEntries(order.map(k=>[k,0]));
  let unlabeled = 0;
  for (const item of queue) {
    const L = labels[item.id];
    if (!L) unlabeled++;
    else counts[L.label] = (counts[L.label]||0)+1;
  }
  const lines = [
    `未标注：${unlabeled}`,
    ...order.map(k => `${LABEL_ZH[k]}：${counts[k] || 0}`),
  ];
  document.getElementById("stats").textContent = lines.join("\n");
  document.getElementById("filterInfo").textContent = onlyUnlabeled ? "筛选：仅未标注" : "筛选：全部";
}
async function saveLabel(label) {
  const q = current();
  const notes = document.getElementById("notes").value.trim();
  labels[q.id] = { label, notes, timestamp_sec: q.timestamp_sec, video_id: q.video_id };
  await fetch("/api/labels", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(labels) });
  go(1);
}
function go(delta) {
  const vis = visibleIndices();
  if (!vis.length) { render(); return; }
  let pos = vis.indexOf(idx);
  if (pos < 0) pos = 0;
  pos = Math.max(0, Math.min(vis.length-1, pos + delta));
  idx = vis[pos];
  render();
}
document.querySelectorAll(".keys button[data-l]").forEach(b => b.onclick = () => saveLabel(b.dataset.l));
document.getElementById("skip").onclick = () => go(1);
document.getElementById("undo").onclick = async () => {
  const q = current();
  delete labels[q.id];
  await fetch("/api/labels", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(labels) });
  render();
};
document.getElementById("prev").onclick = () => go(-1);
document.getElementById("next").onclick = () => go(1);
document.getElementById("onlyUnlabeled").onclick = () => { onlyUnlabeled = !onlyUnlabeled; idx = visibleIndices()[0] ?? idx; render(); };
document.getElementById("export").onclick = async () => {
  const r = await fetch("/api/export", { method:"POST" });
  const j = await r.json();
  alert(`已导出 ${j.count} 条\n保存至：${j.path}`);
};
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT") return;
  const map = { Digits:null, "1":"non_game","2":"buy","3":"combat","4":"result","5":"replay" };
  if (map[e.key]) { e.preventDefault(); saveLabel(map[e.key]); }
  else if (e.key === "s" || e.key === "S") go(1);
  else if (e.key === "z" || e.key === "Z") document.getElementById("undo").click();
  else if (e.key === "ArrowRight") go(1);
  else if (e.key === "ArrowLeft") go(-1);
});
load();
</script>
</body>
</html>
"""

ROUNDS_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>无畏契约 · 回合确认</title>
<style>
  :root { color-scheme: dark; font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; }
  body { margin: 0; background: #111; color: #eee; }
  .wrap { display: grid; grid-template-columns: 1fr 360px; gap: 12px; height: 100vh; padding: 12px; box-sizing: border-box; }
  .bar { display:flex; gap:12px; align-items:center; flex-wrap: wrap; margin-bottom: 12px; }
  a { color: #2e8dff; text-decoration: none; }
  button { padding: 8px 12px; font-size: 14px; cursor: pointer; border-radius: 8px; border: 1px solid #444; background: #222; color: #fff; }
  button.primary { background: #1a4f99; border-color: #2e8dff; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { border-bottom: 1px solid #333; padding: 8px; text-align: left; }
  tr.selected { background: #1a2a3a; }
  tr:hover { background: #1a1a1a; cursor: pointer; }
  .editor { display: grid; gap: 10px; }
  label { display:block; font-size: 12px; color: #aaa; margin-bottom: 4px; }
  input, select { width: 100%; padding: 8px; background:#1a1a1a; border:1px solid #444; color:#eee; border-radius:6px; box-sizing: border-box; }
  .preview { display: grid; gap: 12px; }
  .preview img { width: 100%; max-height: 32vh; object-fit: contain; background: #000; border-radius: 8px; }
  .side { overflow: auto; border-left: 1px solid #333; padding-left: 12px; }
  .status { color: #9ad; font-size: 13px; }
  .error { color: #ff6b6b; }
  .help { margin-top: 12px; color: #999; line-height: 1.6; font-size: 13px; }
</style>
</head>
<body>
<div class="wrap">
  <div>
    <div class="bar">
      <a href="/">← 帧标注</a>
      <strong>回合确认</strong>
      <button id="addRound">添加回合</button>
      <button id="save" class="primary">保存</button>
      <span id="status" class="status"></span>
    </div>
    <table>
      <thead>
        <tr><th>回合</th><th>起点</th><th>终点</th><th>结束原因</th><th></th></tr>
      </thead>
      <tbody id="roundRows"></tbody>
    </table>
  </div>
  <div class="side">
    <h3>编辑选中回合</h3>
    <div class="editor">
      <div>
        <label>round_key</label>
        <input id="roundKey" type="text"/>
      </div>
      <div>
        <label>起点 (秒)</label>
        <input id="startSec" type="number" step="0.1" min="0"/>
      </div>
      <div>
        <label>终点 (秒)</label>
        <input id="endSec" type="number" step="0.1" min="0"/>
      </div>
      <div>
        <label>结束原因</label>
        <select id="endReason">
          <option value="result">result · 结算画面</option>
          <option value="next_buy">next_buy · 进入买枪</option>
          <option value="score">score · 计分板</option>
          <option value="unknown">unknown · 未知</option>
        </select>
      </div>
    </div>
    <div class="preview" style="margin-top:16px">
      <div>
        <label>起点预览</label>
        <img id="startPreview" alt="起点预览"/>
      </div>
      <div>
        <label>终点预览</label>
        <img id="endPreview" alt="终点预览"/>
      </div>
    </div>
    <div class="help">
      <div>修改起止秒数后自动刷新预览。</div>
      <div>保存前会校验不重叠、不越界。</div>
      <div>时长：优先 draft 的 duration_sec，否则取帧时间戳与最后回合终点较大值。</div>
    </div>
  </div>
</div>
<script>
let manifest = { videos: [] };
let video = null;
let rounds = [];
let selected = 0;
let previewTimer = null;

function currentVideo() {
  return manifest.videos[0] || null;
}

function normalizeRounds(rows) {
  return rows.map((row, index) => ({
    round_key: row.round_key || `R${String(index + 1).padStart(2, "0")}`,
    start: Number(row.start),
    end: Number(row.end),
    end_reason: row.end_reason || "unknown",
  }));
}

async function load() {
  manifest = await (await fetch("/api/rounds")).json();
  video = currentVideo();
  if (!video) {
    document.getElementById("status").textContent = "未找到视频回合数据";
    return;
  }
  rounds = normalizeRounds(video.ground_truth || []);
  selected = 0;
  render();
}

function render() {
  const tbody = document.getElementById("roundRows");
  tbody.innerHTML = "";
  rounds.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.className = index === selected ? "selected" : "";
    tr.innerHTML =
      `<td>${row.round_key}</td>` +
      `<td>${row.start.toFixed(1)}</td>` +
      `<td>${row.end.toFixed(1)}</td>` +
      `<td>${row.end_reason}</td>` +
      `<td><button data-del="${index}">删除</button></td>`;
    tr.onclick = (e) => {
      if (e.target.tagName === "BUTTON") return;
      selected = index;
      render();
    };
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("button[data-del]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const index = Number(btn.dataset.del);
      rounds.splice(index, 1);
      selected = Math.min(selected, Math.max(0, rounds.length - 1));
      render();
    };
  });
  const row = rounds[selected];
  if (!row) {
    document.getElementById("roundKey").value = "";
    document.getElementById("startSec").value = "";
    document.getElementById("endSec").value = "";
    return;
  }
  document.getElementById("roundKey").value = row.round_key;
  document.getElementById("startSec").value = row.start;
  document.getElementById("endSec").value = row.end;
  document.getElementById("endReason").value = row.end_reason;
  schedulePreview();
}

function syncSelectedFromEditor() {
  const row = rounds[selected];
  if (!row) return;
  row.round_key = document.getElementById("roundKey").value.trim() || row.round_key;
  row.start = Number(document.getElementById("startSec").value);
  row.end = Number(document.getElementById("endSec").value);
  row.end_reason = document.getElementById("endReason").value;
  render();
}

async function refreshPreview() {
  const start = Number(document.getElementById("startSec").value);
  const end = Number(document.getElementById("endSec").value);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return;
  const payload = await (await fetch(`/api/rounds/preview?start=${start}&end=${end}`)).json();
  if (payload.error) return;
  const ts = Date.now();
  document.getElementById("startPreview").src = "/frame/" + encodeURIComponent(payload.start.rel_path) + "?t=" + ts;
  document.getElementById("endPreview").src = "/frame/" + encodeURIComponent(payload.end.rel_path) + "?t=" + ts;
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 200);
}

function nextRoundKey() {
  const nums = rounds.map((row) => Number((row.round_key || "").replace(/\D/g, "")) || 0);
  const next = (nums.length ? Math.max(...nums) : 0) + 1;
  return `R${String(next).padStart(2, "0")}`;
}

document.getElementById("addRound").onclick = () => {
  const duration = Number(video?.duration_sec || 0);
  const lastEnd = rounds.length ? rounds[rounds.length - 1].end : 0;
  const start = Math.min(lastEnd, duration);
  const end = Math.min(start + 30, duration || start + 30);
  rounds.push({ round_key: nextRoundKey(), start, end, end_reason: "unknown" });
  selected = rounds.length - 1;
  render();
};

["roundKey", "startSec", "endSec", "endReason"].forEach((id) => {
  document.getElementById(id).addEventListener("input", () => {
    syncSelectedFromEditor();
    schedulePreview();
  });
  document.getElementById(id).addEventListener("change", () => {
    syncSelectedFromEditor();
    schedulePreview();
  });
});

document.getElementById("save").onclick = async () => {
  syncSelectedFromEditor();
  const payload = {
    videos: [{
      ...video,
      ground_truth: rounds,
      duration_sec: Number(video.duration_sec),
    }],
  };
  const status = document.getElementById("status");
  status.className = "status";
  const resp = await fetch("/api/rounds", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await resp.json();
  if (!resp.ok) {
    status.className = "status error";
    status.textContent = body.error || "保存失败";
    return;
  }
  status.textContent = `已保存至 ${body.path}`;
  await load();
};

load();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # quieter
        pass

    def _json(self, code: int, obj: object) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, code: int, data: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._bytes(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/rounds":
            self._bytes(200, ROUNDS_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/queue":
            self._bytes(200, QUEUE.read_bytes(), "application/json; charset=utf-8")
            return
        if path == "/api/labels":
            if not LABELS.exists():
                LABELS.write_text("{}", encoding="utf-8")
            self._bytes(200, LABELS.read_bytes(), "application/json; charset=utf-8")
            return
        if path == "/api/rounds":
            try:
                frames = _load_queue_frames()
                manifest = _enrich_rounds_manifest(_load_rounds_manifest(), frames)
                self._json(200, manifest)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path == "/api/rounds/preview":
            query = parse_qs(urlparse(self.path).query)
            try:
                start = float(query.get("start", [""])[0])
                end = float(query.get("end", [""])[0])
            except (TypeError, ValueError):
                self._json(400, {"error": "start and end query params required"})
                return
            try:
                frames = _load_queue_frames()
                payload = build_preview_payload(frames, start=start, end=end)
                self._json(200, payload)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        if path.startswith("/frame/"):
            rel = unquote(path[len("/frame/") :])
            fp = (ROOT / rel).resolve()
            if not str(fp).startswith(str(ROOT.resolve())) or not fp.is_file():
                self._json(404, {"error": "missing"})
                return
            ctype = mimetypes.guess_type(fp.name)[0] or "image/jpeg"
            self._bytes(200, fp.read_bytes(), ctype)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        if path == "/api/labels":
            LABELS.write_bytes(body)
            self._json(200, {"ok": True})
            return
        if path == "/api/export":
            queue = json.loads(QUEUE.read_text(encoding="utf-8"))
            labels = json.loads(LABELS.read_text(encoding="utf-8"))
            lines: list[str] = []
            for item in queue:
                lab = labels.get(item["id"])
                if not lab:
                    continue
                row = {
                    "video_id": item["video_id"],
                    "video_path": item["video_path"],
                    "timestamp_sec": item["timestamp_sec"],
                    "label": lab["label"],
                    "split": item["split"],
                    "source_type": item["source_type"],
                    "session_id": item["session_id"],
                    "notes": lab.get("notes") or "",
                }
                lines.append(json.dumps(row, ensure_ascii=False))
            MANIFEST.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            self._json(200, {"count": len(lines), "path": str(MANIFEST)})
            return
        if path == "/api/rounds":
            try:
                payload = json.loads(body.decode("utf-8"))
                out_path = save_confirmed_rounds(ROOT, payload)
                self._json(200, {"ok": True, "path": str(out_path)})
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            return
        self._json(404, {"error": "not found"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local keyboard label UI for Valorant phase frames")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Annotation root containing queue.json and labels.json "
        "(default: ~/LSC/datasets/valorant_phase/annotate)",
    )
    parser.add_argument("--port", type=int, default=PORT, help=f"HTTP port (default: {PORT})")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    paths = configure_paths(args.root)
    if not paths.queue.exists():
        raise SystemExit(f"missing {paths.queue}; run build_round_boundary_queue.py first")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Label UI: http://127.0.0.1:{args.port}/")
    print(f"Frames root: {paths.root}")
    server.serve_forever()


if __name__ == "__main__":
    main()

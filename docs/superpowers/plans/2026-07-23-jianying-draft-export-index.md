# 剪映草稿导出 — Plan Index (J1→J3)

> 总览索引。执行时按里程碑顺序打开对应 plan；每阶段独立可交付、可回滚。

**Spec:** `docs/spec-jianying-draft-export.md`  
**日期:** 2026-07-23

---

## Plans

| 里程碑 | Plan 文件 | 内容 |
|--------|-----------|------|
| J1 | [`2026-07-23-jianying-j1-builder.md`](./2026-07-23-jianying-j1-builder.md) | 依赖 + DTO + `jianying_draft.py` builder + 单测 |
| J2 | [`2026-07-23-jianying-j2-backend.md`](./2026-07-23-jianying-j2-backend.md) | WS handler + settings + TimelineContext 装配 |
| J3 | [`2026-07-23-jianying-j3-frontend.md`](./2026-07-23-jianying-j3-frontend.md) | 三选一导出 + 会话入口 + 设置页 + `_isSafePath` |

**明确不做：** 剪映自动导出 / uiautomation、AI 配音字幕特效、跨重启历史会话（H12）、macOS/CapCut。

---

## Spec → 代码校正（所有 plan 已吸收）

1. **`trange` 单位：** `pyJianYingDraft.trange(float, float)` 把数字当作**微秒**；秒必须写成 `trange(f"{s}s", f"{d}s")` 或 `trange(s * SEC, d * SEC)`（`SEC=1_000_000`）。Spec 伪代码里的裸 float 秒数**不能**直接传。
2. **`VideoMaterial.duration`：** 单位是**微秒**，换算 `dur_sec = material.duration / SEC`。
3. **竖屏裁剪：** 用 `VideoMaterial(..., crop_settings=CropSettings(...))`，不是 `ClipSettings`（后者只有 alpha/scale/transform）。中心 9:16 归一化坐标见 J1 Task。
4. **同名覆盖：** 优先 `DraftFolder.create_draft(..., allow_replace=True)`，无需手删文件夹（H7）。
5. **handler 落点：** 新建 `python-backend/handlers/jianying_handlers.py`，在 `register_room_handlers` 末尾注册（仿 `timeline_handlers`），避免继续膨胀 `room_handler.py`。
6. **「更多」菜单：** 工作台顶部目前无 ⋯ Dropdown；J3 在顶部操作栏右侧新增 `Dropdown`（不要虚构已有菜单）。

---

## 依赖顺序

```
J1 → J2 → J3
```

J1 纯新增零风险；J2 只加新 WS 消息；J3 默认 `exportTarget='mp4'` 保持现状行为。

---

## 执行方式

每个 plan 头部要求：`subagent-driven-development`（推荐）或 `executing-plans`。  
默认 **不要 git commit**，除非用户明确要求。

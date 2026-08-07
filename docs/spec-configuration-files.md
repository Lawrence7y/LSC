# 配置文件规范（LSC）

> **权威参考**：本规范以代码实际实现为准（`python-backend/persistence.py`、`python-backend/handlers/room_handler.py`、`lsc/config.py`），与 `CLAUDE.md` 第 3 章的差异在 §6 列出。
> 修改任何持久化字段时，必须同步更新前端类型（`lsc-electron/src/types/index.ts` 的 `RecordSettings`/`AppSettings`）与本文件。

---

## 1. 文件总览与路径

| 文件 | 开发模式路径 | 打包模式路径（`LSC_DATA_DIR` = Electron userData） | 写入方 |
| :--- | :--- | :--- | :--- |
| `settings.json` | `<仓库根>/python-backend/settings.json` | `<userData>/settings.json` | `room_handler.py:2384` `save_settings` |
| `data/rooms.json` | `<仓库根>/data/rooms.json` | `<userData>/data/rooms.json` | `persistence.py:109` `save_rooms` |
| `data/rooms.json.bak` | 同目录 `.bak` | 同目录 `.bak` | `persistence.py:99` `_backup_existing` |
| `recording_history.json` | `<仓库根>/recording_history.json` | `<userData>/recording_history.json` | `room_handler.py:184` `_save_recording_history` |
| `lsc_config.json` | 项目根（仅开发模式） | —（用 `LSC_CONFIG_PATH` 覆盖） | 手工编辑；`lsc/config.py:263` `_load_config_overrides` |
| `data/ocr_accel_probe.json` | `<根>/data/` | `<userData>/data/` | `lsc/analyzer/ocr_accel.py` |
| `{basename}.analysis.json` | 录制文件同目录 | 同左 | `persistence.py:237` |
| `app-settings.json` | `<userData>/` | 同左 | `lsc-electron/electron/main.ts:161`（仅 `autoLaunch`/`minimizeToTray`） |

路径定义：`persistence.py:24-28`（`_PERSISTENCE_ROOT = LSC_DATA_DIR` 或项目根）；`room_handler.py:158-160`。

> ⚠️ 打包模式下**一切持久化写入 userData**，禁止写入 Program Files。历史遗留 `lsc/gui/multi_room/config/rooms.json`（旧 PySide6 GUI）已弃用，勿混淆。

---

## 2. settings.json 字段规范

存储于 `settings.json`，顶层为完整设置对象。读取带 mtime 缓存（`room_handler.py:2290-2300`），损坏时回退默认值。

### 2.1 录制/预览/分析键

| 键 | 类型 | 默认值 | 合法值 / 说明 | 校验与降级 | 生效位置 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `output_dir` | str | `~/LSC/output` | 任意可写路径；**黑名单**拒绝系统目录（POSIX `/etc /boot /sys /proc /dev /sbin /bin /usr` + Windows `SYSTEMROOT/Program Files`） | 保存时 `ValueError("导出目录不在允许范围内")`，WS 层双重校验 | 录制输出、导出输出、磁盘检测 |
| `encoder` | str | `h264_nvenc` | `h264_nvenc` `hevc_nvenc` `h264_qsv` `h264_amf` `libx264` `libx265` `copy`（另接受显示名别名 `H.264 NVENC` 等） | 不在集合 → 默认 | 录制/导出编码 |
| `crf` | int | `23` | 0–51 | 非 int → 23；录制时 clamp 0–51 | 录制/导出质量 |
| `param_mode` | str | `CRF 质量` | `CRF 质量` `自定义码率` `码率限制` `不限制` | 不在集合 → 默认 | 录制/导出 |
| `bitrate` | str/int | `8000` | 数字字符串（kbps） | 非数字 → `'8000'` | 自定义码率模式 |
| `bitrate_unit` | str | `kbps` | `kbps` `Mbps` | 不在集合 → `kbps` | 录制 |
| `quality` | str | `原画` | UI 枚举 `原画` `蓝光` `超清` `高清` `流畅` | 无后端校验，透传平台 | 平台流画质选择 |
| `resolution` | str | `原画` | `原画` `1920:1080` `1280:720` `854:480` | 不在集合 → `原画`；导出侧兼容 `:`/`x`，非法清空 | 录制/导出分辨率 |
| `framerate` | str | `原画` | `原画` `60` `30` `24` | 不在集合 → `原画` | 录制/导出帧率 |
| `audio_codec` | str | 无默认（不存在） | 前端默认 `AAC 128k` | 无后端读取方（冗余透传键） | — |
| `audio_bitrate` | str | `128k` | 录制 `128k` `192k` `256k`；导出允许 `96k` 等 | 不在集合 → `128k` | 录制/导出音频码率 |
| `preview_quality` | str | `高清` | `原画` `高清` `标清` `流畅`（分辨率/码率见 `_PREVIEW_QUALITY_PRESETS`） | 未知 → `高清` | MSE 预览画质 |
| `preset` | str | 读取处兜底 `medium` | UI `ultrafast` `fast` `medium` `slow`；硬件映射见 `lsc/config.py:133-159` | 缺失 → `medium` | 编码预设 |
| `ocr_accel` | str | `dml` | `auto` `dml` `cuda` `cpu`（别名 `automatic/directml/gpu` 归一化） | 非法 → `auto`；声明后端不可用 → `cpu`；变更时 `invalidate_ocr()` | OCR/回合检测/帧分类推理加速 |
| `export_max_concurrent` | int | `2` | **仅 `1` 或 `2`** | 其他值/非 int → `2` | 全局导出 Semaphore 并发上限 |
| `jianying_draft_dir` | str | `''` | 空 = 自动探测 `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft`；或可写目录 | 保存时 `ValueError("剪映草稿目录无效或不可写")`，WS 层校验非 str 拒绝 | 剪映草稿导出、Electron openPath 白名单 |
| `shared_ingest_enabled` | bool | 运行时 `LscConfig` 默认 `False` | `true`/`false` | `get_settings` 缺省回填运行时真值；保存时同步到 `LscConfig` 单例 | 共享进样模式（单 FFmpeg 双输出） |

### 2.2 appSettings 子对象

| 键 | 类型 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `theme` | str | `dark` | 纯前端（`.dark` class 切换） |
| `language` | str | `zh-CN` | 仅存储，无读取方 |
| `autoLaunch` | bool | `false` | 经 `window.app.setAutoLaunch` → Electron `app-settings.json`；settings.json 副本仅存储 |
| `minimizeToTray` | bool | `false` | 同上 |
| `default_export_preset` | str | `douyin_vertical`（前端 store 默认） | 内置预设 `douyin_vertical` `bilibili_horizontal` `original` `high_quality` `small_file` 或自定义预设 id |
| `custom_export_presets` | array | `[]` | 字段：`id/name/description/resolution/framerate/codec/crf/vertical_crop/audio_bitrate` |

### 2.3 写入边界

- `save_settings` 强校验仅两项：`output_dir`（str + 白名单）、`jianying_draft_dir`（str/可写）；其余键透传（`room_handler.py:4172-4200`）。
- 校验失败返回 `{success:false, error}` **不落盘**。
- 前端保存模型：设置页任一变更 → 300ms 防抖 → `save_settings` 全量对象（含 appSettings）。
- settings.json **无 `.bak` 恢复**（区别于 rooms.json）。

---

## 3. data/rooms.json

顶层 `{"rooms": [...]}`（兼容 legacy 顶层数组）；`rooms` 非列表 → 警告返回空。

**room 对象**（`room_handler.py:2489-2541` `_room_to_dict` 序列化，38 字段）：

```
room_id, room_url, platform, platform_name, streamer_name, stream_title,
stream_url, is_connecting, is_connected, is_recording, is_recording_starting,
is_recording_queued, recording_queue_position, is_reconnecting,
record_output_path, record_started_at(ISO8601|null), record_size_mb, last_error,
preview_enabled, preview_paused, preview_muted, preview_mode("live_mse"),
preview_quality, mark_in/mark_out(float|null), mark_in_wallclock/mark_out_wallclock(float|null),
recording_start_mono/recording_media_start_mono(float|null), preview_latency,
content_offset, align_group_id, category, preview_epoch_id, recording_id
```

**恢复规则**（`room_handler.py:2559-2614` `restore_persisted_rooms`）：仅恢复 `mark_in/mark_out/content_offset`（float）、`align_group_id/category`（str）、`preview_muted`、`include_in_cut`；其余瞬时状态不恢复；兼容旧 `url` 字段。

**写入频率**：`schedule_save_rooms` 1 秒合并写 + 每 5 次写 fsync（`persistence.py:144-181`）。

---

## 4. recording_history.json

顶层 JSON 数组，上限 **500 条**（`room_handler.py:194`，加载与追加时均裁剪）。

| 字段 | 类型 | 写入时机 |
| :--- | :--- | :--- |
| `title` | str | 录制启动成功时（主播名，缺省"未知主播"） |
| `platform` | str | 同上 |
| `start_time` | str | `datetime.now().isoformat()` |
| `room_id` | str | 同上 |
| `end_time` | str | 停止录制时补写（匹配未闭合的 room_id 记录） |
| `duration` | str | 同上，格式 `HH:MM:SS` |

写入用 `_atomic_write_json`（`room_handler.py:176-181`，tmp+replace，**无锁/无 fsync/无备份**）。

---

## 5. lsc_config.json（LscConfig 覆盖，手工编辑）

- 位置：项目根（可被 `LSC_CONFIG_PATH` 覆盖）。
- **仅允许 11 个键**（`lsc/config.py:282-295`），类型不匹配跳过并告警：

```
ffmpeg_path, ffprobe_path, output_path, output_dir,
shared_ingest_enabled, shared_ingest_preview_queue_bytes,
shared_ingest_preview_drop_policy, shared_ingest_preview_crf,
shared_ingest_preview_preset, max_rooms, max_concurrent_previews
```

- 运行时 `LscConfig` 默认（`lsc/config.py:204-231`）：`shared_ingest_enabled=False`、`shared_ingest_preview_queue_bytes=2MB`、`drop_policy="drop_oldest"`、`preview_crf=23`、`preview_preset="veryfast"`、`max_rooms=12`、`max_concurrent_previews=4`、`output_path=~/LSC/recordings`。
- ⚠️ **与 settings.json 的关系**：`shared_ingest_enabled` 是交集键——UI 保存时经 `_apply_shared_ingest_from_settings`（`room_handler.py:2339-2354`）覆盖运行时值，**以 settings.json 为准**。若两份文件值不一致，运行时以最近一次 UI 保存为准。

---

## 6. 原子写入机制

| 文件 | 机制 | 说明 |
| :--- | :--- | :--- |
| rooms.json | `.tmp` + flush(可选 fsync) + **先备份 `.bak`** + `replace`；进程内 `_persist_lock` 互斥 | 读侧损坏自动尝试 `.bak` 恢复（`persistence.py:73-96`） |
| settings.json | `.tmp` + replace + 锁；**无 `.bak`** | — |
| analysis.json | `.tmp` + replace + **每次 fsync**；含 `video_mtime` 过期校验 | — |
| recording_history.json | `.tmp` + replace；无锁/无 fsync | — |
| ocr_accel_probe.json | `.tmp` + replace | — |

---

## 7. 与 CLAUDE.md 第 3 章的差异清单（以本文档为准）

| # | CLAUDE.md 说法 | 代码实际 |
| :--- | :--- | :--- |
| 1 | `output_dir` 默认 `~/LSC/recordings` | `load_settings` 默认 `~/LSC/output`（`room_handler.py:2304`）；`LscConfig.output_path` 才是 `~/LSC/recordings` |
| 2 | `resolution` 默认 `""` | 默认 `'原画'` |
| 3 | 无 `preset`/`audio_codec`/`jianying_draft_dir` 键 | 均实际存在 |
| 4 | 无 `appSettings` 子对象 | 实际存于 settings.json |
| 5 | `shared_ingest_preview_crf/preset` 列为 settings.json 键 | 属于 **lsc_config.json（LscConfig）**，settings.json 不存 |
| 6 | `param_mode` 仅 2 个枚举 | 实际 4 个：`CRF 质量/自定义码率/码率限制/不限制` |
| 7 | `quality` 仅 `原画/高清/流畅` | UI 允许 5 个：`原画/蓝光/超清/高清/流畅` |
| 8 | 无录制历史上限 | `_MAX_RECORDING_HISTORY = 500` |
| 9 | 设置/历史存放在项目根 | 打包模式全部移入 userData（`LSC_DATA_DIR`） |
| 10 | 原子写入仅"临时文件+replace" | rooms.json 另有 `.bak` 备份恢复；history 为无锁变体 |

---

## 8. 修改规范（红线）

1. 新增配置键必须三处同步：后端 `load_settings` 默认值 / 前端 `RecordSettings` 或 `AppSettings` 类型 / 设置页 UI。
2. 新增键只读场景无校验，**写场景必须有合法值校验与降级**（回退默认值）。
3. 持久化写入必须走原子写路径（tmp+replace），禁止直接 `open(f, 'w')`。
4. `LSC_DATA_DIR` 与 `settings.json` 的迁移必须保持打包/开发双模式一致。

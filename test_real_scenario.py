"""真实场景测试脚本 v2 - 用抖音直播链接测试各个功能模块。"""
import os
import sys
import time
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from lsc.platforms.registry import parse_stream

TEST_URL = "https://www.douyin.com/follow/live/295380890971?anchor_id=2524898145613127"

all_issues = []


def log_issue(area: str, desc: str) -> None:
    all_issues.append((area, desc))
    print(f"    ❌ ISSUE: {desc}")


def test_1_stream_parse():
    """测试 1: 直播流解析"""
    print("\n" + "=" * 60)
    print("TEST 1: 直播流解析")
    print("=" * 60)
    try:
        info = parse_stream(TEST_URL)
        print(f"  平台: {info.platform}")
        print(f"  是否直播: {info.is_live}")
        if info.stream_url:
            print(f"  流地址: {info.stream_url[:80]}...")
        else:
            print(f"  流地址: 无")
        print(f"  主播名: '{info.streamer}'")
        print(f"  标题: '{info.title}'")
        print(f"  选中画质: {info.selected_quality}")
        print(f"  可用画质: {list(info.quality_urls.keys())}")
        print(f"  错误: {info.error}")
        if info.headers:
            print(f"  HTTP Headers: {list(info.headers.keys())}")

        # 检查问题
        if not info.streamer:
            log_issue("parse", "主播名为空")
        if not info.title:
            log_issue("parse", "直播标题为空")

        if info.is_live and info.stream_url:
            print("  ✅ 解析成功，直播正常")
            return True, info
        else:
            print("  ⚠️  解析完成，但直播可能未开播")
            return False, info
    except Exception as e:
        log_issue("parse", f"解析异常: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def test_2_multi_room_manager(info):
    """测试 2: 多房间管理器"""
    print("\n" + "=" * 60)
    print("TEST 2: 多房间管理器")
    print("=" * 60)

    from lsc.gui.multi_room.manager import MultiRoomManager, MAX_ROOMS

    manager = MultiRoomManager()

    # 2.1 添加房间
    print("\n  2.1 添加房间")
    room = manager.add_room(TEST_URL)
    if room:
        room_id = room.room_id
        print(f"    ✅ 添加成功, room_id={room_id}")
        print(f"    房间数量: {manager.room_count()}/{MAX_ROOMS}")
    else:
        log_issue("multi_room", "add_room 返回 None")
        return

    # 2.2 连接房间
    print("\n  2.2 连接房间 (等待最多 20 秒)")
    success = manager.connect_room(room_id)
    print(f"    connect_room 返回: {success}")

    connected = False
    for i in range(20):
        app.processEvents()
        time.sleep(1)
        room = manager.get_room(room_id)
        if room.is_connected:
            connected = True
            print(f"    ✅ 连接成功 (用时 {i+1}s)")
            print(f"    主播: '{room.streamer_name}'")
            print(f"    标题: '{room.stream_title}'")
            print(f"    平台: {room.platform_name}")
            print(f"    选中画质: {room.selected_quality}")
            if not room.streamer_name:
                log_issue("multi_room", "连接后主播名为空")
            if not room.stream_title:
                log_issue("multi_room", "连接后直播标题为空")
            break
        if room.last_error:
            print(f"    ❌ 连接失败: {room.last_error}")
            log_issue("multi_room", f"连接失败: {room.last_error}")
            break
        if i % 5 == 4:
            print(f"    等待中... ({i+1}/20)")

    if not connected and not room.last_error:
        print("    ⚠️  连接超时")
        log_issue("multi_room", "连接超时 (20s)")

    # 2.3 开启预览
    if connected:
        print("\n  2.3 开启预览 (等待 8 秒)")
        success = manager.start_preview(room_id)
        room = manager.get_room(room_id)
        if success:
            print(f"    ✅ 预览启动成功")
            has_positive_pos = False
            for i in range(8):
                app.processEvents()
                time.sleep(1)
                room = manager.get_room(room_id)
                widget = room.preview_widget
                if widget:
                    pos_fn = getattr(widget, "time_pos", None)
                    if callable(pos_fn):
                        try:
                            pos = float(pos_fn() or 0.0)
                            if pos > 0:
                                has_positive_pos = True
                                if i % 2 == 0:
                                    print(f"    第 {i+1}s - 播放位置: {pos:.1f}s")
                        except Exception as e:
                            print(f"    第 {i+1}s - 获取位置失败: {e}")
                if room.preview_error:
                    print(f"    ⚠️  预览错误: {room.preview_error}")

            if not has_positive_pos:
                log_issue("preview", "预览 8 秒内播放位置始终为 0，可能无画面")
            else:
                print(f"    ✅ 预览播放正常")

            # 检查卡顿计数器
            room = manager.get_room(room_id)
            print(f"    卡顿计数器: {room.preview_stall_counter}")
            print(f"    重连次数: {room.preview_reconnect_attempts}")
        else:
            print(f"    ❌ 预览启动失败: {room.preview_error}")
            log_issue("preview", f"预览启动失败: {room.preview_error}")

        # 2.4 停止预览
        print("\n  2.4 停止预览")
        manager.stop_preview(room_id)
        room = manager.get_room(room_id)
        if not room.preview_enabled:
            print("    ✅ 预览已停止")
        else:
            log_issue("preview", "停止预览后 preview_enabled 仍为 True")

    # 2.5 断开连接
    print("\n  2.5 断开连接")
    manager.disconnect_room(room_id)
    room = manager.get_room(room_id)
    if not room.is_connected:
        print("    ✅ 已断开连接")
    else:
        log_issue("multi_room", "断开连接后 is_connected 仍为 True")

    # 2.6 删除房间
    print("\n  2.6 删除房间")
    manager.remove_room(room_id)
    if manager.room_count() == 0:
        print("    ✅ 房间已删除")
    else:
        log_issue("multi_room", f"删除房间后仍有 {manager.room_count()} 个房间")


def test_3_recording(info):
    """测试 3: 录制控制器"""
    print("\n" + "=" * 60)
    print("TEST 3: 录制控制器 (快速录制测试 5 秒)")
    print("=" * 60)

    from lsc.gui.pages.recording_controller import RecordingController

    ctrl = RecordingController()
    ctrl.init_capture()
    ctrl.init_exporter()

    stream_url = info.stream_url
    legacy = info.to_legacy_dict()
    input_args = legacy.get("_inputArgs", [])

    output_dir = os.path.join(tempfile.gettempdir(), "lsc_test_recording")
    os.makedirs(output_dir, exist_ok=True)

    print(f"  输出目录: {output_dir}")
    print(f"  流地址: {stream_url[:60]}...")
    print(f"  input_args: {input_args}")

    # 开始录制
    print("\n  3.1 开始录制")
    try:
        success, output_path, encoder_used, error_msg = ctrl.start_recording_with_crf(
            stream_url=stream_url,
            output_dir=output_dir,
            encoder="Copy",
            crf=23,
            param_mode="不限制",
            bitrate=None,
            bitrate_unit="kbps",
            input_args=input_args,
        )
        if success:
            print(f"    ✅ 录制已启动")
            print(f"    输出文件: {output_path}")
            print(f"    使用编码器: {encoder_used}")
        else:
            print(f"    ❌ 录制启动失败: {error_msg}")
            log_issue("recording", f"启动失败: {error_msg}")
            return
    except Exception as e:
        print(f"    ❌ 录制启动异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("recording", f"启动异常: {e}")
        return

    # 录制 5 秒
    print("\n  3.2 录制 5 秒...")
    for i in range(5):
        app.processEvents()
        time.sleep(1)
        ctrl.tick()
        watchdog_error = ctrl.watchdog_check()
        if watchdog_error:
            print(f"    ⚠️  第 {i+1}s - watchdog: {watchdog_error}")
            log_issue("recording", f"watchdog 报错 (第 {i+1}s): {watchdog_error}")
        else:
            try:
                elapsed = ctrl.elapsed_seconds
                print(f"    第 {i+1}s - 已录制 {elapsed:.1f}s")
            except Exception:
                print(f"    第 {i+1}s - 录制中")

    # 停止录制
    print("\n  3.3 停止录制")
    try:
        success, duration, error = ctrl.stop_recording()
        print(f"    success={success}, duration={duration:.1f}s, error='{error}'")
        if success:
            print("    ✅ 录制已停止")
        else:
            log_issue("recording", f"停止失败: {error}")
    except Exception as e:
        print(f"    ❌ 停止录制异常: {e}")
        log_issue("recording", f"停止异常: {e}")

    # 检查文件是否生成
    time.sleep(2)  # 等待文件写入完成
    print("\n  3.4 录制文件验证")
    files = [f for f in os.listdir(output_dir) if f.endswith('.mp4')]
    if files:
        for f in files:
            fpath = os.path.join(output_dir, f)
            size_mb = os.path.getsize(fpath) / (1024 * 1024)
            print(f"    ✅ 文件: {f} ({size_mb:.2f} MB)")
        total_size = sum(os.path.getsize(os.path.join(output_dir, f)) for f in files)
        if total_size < 1024:
            log_issue("recording", "录制文件太小 (<1KB)，可能录制有问题")
    else:
        print(f"    ❌ 未找到录制文件")
        log_issue("recording", "停止录制后未生成 mp4 文件")

    # 清理
    try:
        for f in files:
            os.remove(os.path.join(output_dir, f))
        os.rmdir(output_dir)
    except Exception:
        pass


def test_4_gui_pages():
    """测试 4: 各个 GUI 页面是否能正常创建"""
    print("\n" + "=" * 60)
    print("TEST 4: GUI 页面创建测试")
    print("=" * 60)

    pages = {
        "DashboardPage": "lsc.gui.pages.dashboard",
        "SettingsPage": "lsc.gui.pages.settings",
    }

    for page_name, module_name in pages.items():
        try:
            module = __import__(module_name, fromlist=[page_name])
            page_cls = getattr(module, page_name)
            page = page_cls()
            page.resize(1200, 800)
            page.show()
            app.processEvents()
            print(f"  ✅ {page_name} 创建成功")
            # 检查是否有 page_header
            if hasattr(page, '_page_header'):
                print(f"     - 已集成 PageHeader")
            else:
                log_issue("gui", f"{page_name} 没有 PageHeader")
        except Exception as e:
            print(f"  ❌ {page_name} 创建失败: {e}")
            log_issue("gui", f"{page_name} 创建失败: {e}")
            import traceback
            traceback.print_exc()

    # RecordPage 单独处理，因为它可能需要更多资源
    print("\n  测试 RecordPage:")
    try:
        from lsc.gui.pages.record import RecordPage
        page = RecordPage()
        page.resize(1200, 800)
        page.show()
        app.processEvents()
        print(f"    ✅ RecordPage 创建成功")
        if hasattr(page, '_page_header'):
            print(f"     - 已集成 PageHeader")
        else:
            log_issue("gui", "RecordPage 没有 PageHeader")
        page.cleanup()
    except Exception as e:
        print(f"    ⚠️  RecordPage 创建异常: {e}")
        # 这可能是正常的（比如 mpv 没装）


def main():
    print("\n" + "#" * 60)
    print("#  LSC 真实场景测试 v2")
    print("#  直播链接: " + TEST_URL[:60] + "...")
    print("#" * 60)

    # Test 1: 流解析
    ok, info = test_1_stream_parse()
    if not ok or not info:
        print("\n⚠️  直播流解析有问题，部分测试可能无法进行")

    # Test 2: 多房间管理器
    test_2_multi_room_manager(info)

    # Test 3: 录制控制器
    if info and info.is_live and info.stream_url:
        test_3_recording(info)
    else:
        print("\n⚠️  跳过录制测试 (直播未开播)")

    # Test 4: GUI 页面
    test_4_gui_pages()

    # 总结
    print("\n" + "=" * 60)
    print("问题汇总")
    print("=" * 60)

    if not all_issues:
        print("\n🎉 未发现明显问题！")
    else:
        print(f"\n共发现 {len(all_issues)} 个问题：\n")
        for i, (area, desc) in enumerate(all_issues, 1):
            print(f"  {i}. [{area}] {desc}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()

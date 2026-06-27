"""综合真实场景测试 - 模拟真实用户完整使用流程。"""
import os
import sys
import time
import tempfile
import psutil

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from lsc.platforms.registry import parse_stream

TEST_URL = "https://www.douyin.com/follow/live/295380890971?anchor_id=2524898145613127"

issues = []


def log_issue(area: str, desc: str) -> None:
    issues.append((area, desc))
    print(f"    ❌ ISSUE: {desc}")


def get_memory_mb() -> float:
    """获取当前进程内存使用 (MB)."""
    try:
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def step(name: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def test_full_workflow():
    """完整工作流测试。"""
    from lsc.gui.multi_room.manager import MultiRoomManager, MAX_ROOMS

    step("STEP 0: 初始状态检查")
    mem_start = get_memory_mb()
    print(f"  初始内存: {mem_start:.1f} MB")

    manager = MultiRoomManager()
    print(f"  管理器创建成功")
    print(f"  房间数量: {manager.room_count}/{MAX_ROOMS}")

    # ──────────────────────────────────────
    step("STEP 1: 添加直播间")
    room = manager.add_room(TEST_URL)
    if not room:
        log_issue("add_room", "添加直播间失败")
        return
    room_id = room.room_id
    print(f"  ✅ 添加成功, room_id={room_id[:16]}...")
    print(f"  房间URL: {room.room_url}")
    print(f"  房间数量: {manager.room_count}/{MAX_ROOMS}")
    print(f"  内存: {get_memory_mb():.1f} MB")

    # ──────────────────────────────────────
    step("STEP 2: 连接直播间 (等待最多20秒)")
    start_time = time.time()
    success = manager.connect_room(room_id)
    print(f"  connect_room 返回: {success}")

    connected = False
    for i in range(20):
        app.processEvents()
        time.sleep(1)
        room = manager.get_room(room_id)
        if room.is_connected:
            connected = True
            elapsed = time.time() - start_time
            print(f"  ✅ 连接成功 (用时 {elapsed:.1f}s)")
            print(f"     主播: '{room.streamer_name}'")
            print(f"     标题: '{room.stream_title}'")
            print(f"     平台: {room.platform_name}")
            print(f"     画质: {room.selected_quality}")
            if not room.streamer_name:
                log_issue("connect", "连接后主播名为空")
            if not room.stream_title:
                log_issue("connect", "连接后直播标题为空")
            break
        if room.last_error:
            print(f"  ❌ 连接失败: {room.last_error}")
            log_issue("connect", f"连接失败: {room.last_error}")
            break
        if i % 5 == 4:
            print(f"    等待中... ({i+1}/20)")

    if not connected and not room.last_error:
        log_issue("connect", "连接超时 (20s)")
    print(f"  内存: {get_memory_mb():.1f} MB")

    if not connected:
        print("\n  ⚠️  连接失败，后续测试无法进行")
        return

    # ──────────────────────────────────────
    step("STEP 3: 开启预览 (观察10秒)")
    success = manager.start_preview(room_id)
    room = manager.get_room(room_id)
    if not success:
        print(f"  ❌ 预览启动失败: {room.preview_error}")
        log_issue("preview", f"启动失败: {room.preview_error}")
    else:
        print(f"  ✅ 预览已启动")
        has_positive_pos = False
        max_pos = 0.0
        stall_count = 0
        last_pos = 0.0

        for i in range(10):
            app.processEvents()
            time.sleep(1)
            room = manager.get_room(room_id)
            widget = room.preview_widget
            current_pos = 0.0
            if widget:
                pos_fn = getattr(widget, "time_pos", None)
                if callable(pos_fn):
                    try:
                        current_pos = float(pos_fn() or 0.0)
                    except Exception:
                        pass

            if current_pos > 0:
                has_positive_pos = True
                if current_pos > max_pos:
                    max_pos = current_pos
                if current_pos <= last_pos + 0.1:
                    stall_count += 1
                else:
                    stall_count = 0
                last_pos = current_pos

            if i % 3 == 2:
                status = "播放中" if has_positive_pos else "无画面"
                print(f"    第 {i+1}s - 位置: {current_pos:.1f}s - {status}")

            if room.preview_error:
                print(f"    预览错误: {room.preview_error}")

        if has_positive_pos:
            print(f"  ✅ 预览播放正常 (最大位置: {max_pos:.1f}s)")
        else:
            print(f"  ⚠️  预览10秒内无画面 (offscreen模式可能正常)")

        print(f"  卡顿计数器: {room.preview_stall_counter}")
        print(f"  重连次数: {room.preview_reconnect_attempts}")
    print(f"  内存: {get_memory_mb():.1f} MB")

    # ──────────────────────────────────────
    step("STEP 4: 开始录制 (录制15秒)")
    output_dir = os.path.join(tempfile.gettempdir(), "lsc_real_test")
    os.makedirs(output_dir, exist_ok=True)
    print(f"  输出目录: {output_dir}")

    success = manager.start_recording(room_id, output_dir, "Copy", 23, "不限制", "", "kbps")
    room = manager.get_room(room_id)
    if not success:
        print(f"  ❌ 录制启动失败")
        log_issue("recording", "启动失败")
    else:
        print(f"  ✅ 录制已启动")
        print(f"  输出文件: {room.record_output_path}")

        # 录制 15 秒
        for i in range(15):
            app.processEvents()
            time.sleep(1)
            room = manager.get_room(room_id)
            if i % 5 == 4:
                size_mb = room.record_size_mb
                print(f"    第 {i+1}s - 录制中, 文件大小: {size_mb:.2f} MB")

            if not room.is_recording:
                print(f"    ⚠️  录制提前停止 (第 {i+1}s)")
                break

        room = manager.get_room(room_id)
        if room.is_recording:
            print(f"  ✅ 15秒录制完成")
        else:
            log_issue("recording", "录制提前终止")

    print(f"  内存: {get_memory_mb():.1f} MB")

    # ──────────────────────────────────────
    step("STEP 5: 停止录制")
    if room.is_recording:
        success = manager.stop_recording(room_id)
        room = manager.get_room(room_id)
        print(f"  stop_recording 返回: {success}")
        print(f"  录制状态: {room.is_recording}")

        # 等待文件写入完成
        time.sleep(3)
        app.processEvents()

        # 检查文件
        if os.path.exists(room.record_output_path):
            size_mb = os.path.getsize(room.record_output_path) / (1024 * 1024)
            print(f"  ✅ 录制文件存在: {size_mb:.2f} MB")
            if size_mb < 0.1:
                log_issue("recording", f"录制文件过小 ({size_mb:.2f} MB)")
        else:
            print(f"  ❌ 录制文件不存在: {room.record_output_path}")
            log_issue("recording", "停止录制后文件不存在")

    print(f"  内存: {get_memory_mb():.1f} MB")

    # ──────────────────────────────────────
    step("STEP 6: 停止预览")
    manager.stop_preview(room_id)
    room = manager.get_room(room_id)
    if not room.preview_enabled:
        print(f"  ✅ 预览已停止")
    else:
        log_issue("preview", "停止后 preview_enabled 仍为 True")
    print(f"  内存: {get_memory_mb():.1f} MB")

    # ──────────────────────────────────────
    step("STEP 7: 断开连接")
    manager.disconnect_room(room_id)
    room = manager.get_room(room_id)
    if not room.is_connected:
        print(f"  ✅ 已断开连接")
    else:
        log_issue("connect", "断开后 is_connected 仍为 True")
    print(f"  内存: {get_memory_mb():.1f} MB")

    # ──────────────────────────────────────
    step("STEP 8: 删除房间")
    manager.remove_room(room_id)
    if manager.room_count == 0:
        print(f"  ✅ 房间已删除")
    else:
        log_issue("add_room", f"删除后仍有 {manager.room_count} 个房间")

    # 等一下看看内存释放
    for _ in range(5):
        app.processEvents()
        time.sleep(0.2)

    mem_end = get_memory_mb()
    print(f"  最终内存: {mem_end:.1f} MB")
    print(f"  内存增长: {mem_end - mem_start:.1f} MB")

    if mem_end - mem_start > 200:
        log_issue("memory", f"内存增长过多 ({mem_end - mem_start:.1f} MB)")

    # 清理测试文件
    try:
        import shutil
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
    except Exception:
        pass


def test_multi_room():
    """多房间测试 - 添加多个直播间。"""
    from lsc.gui.multi_room.manager import MultiRoomManager, MAX_ROOMS

    step("STEP 9: 多房间压力测试 (3个房间)")
    manager = MultiRoomManager()

    room_ids = []
    for i in range(3):
        room = manager.add_room(TEST_URL)
        if room:
            room_ids.append(room.room_id)
            print(f"  ✅ 添加房间 {i+1}: {room.room_id[:16]}...")
        else:
            print(f"  ❌ 添加房间 {i+1} 失败")
            log_issue("multi_room", f"添加第 {i+1} 个房间失败")

    print(f"  总房间数: {manager.room_count}")

    if len(room_ids) < 3:
        print("  ⚠️  房间不足3个，跳过连接测试")
    else:
        # 同时连接3个房间
        print("\n  同时连接3个房间...")
        for rid in room_ids:
            manager.connect_room(rid)

        # 等待连接
        connected_count = 0
        for i in range(25):
            app.processEvents()
            time.sleep(1)
            connected_count = sum(
                1 for rid in room_ids
                if manager.get_room(rid) and manager.get_room(rid).is_connected
            )
            if connected_count == 3:
                break
            if i % 5 == 4:
                print(f"    等待中... 已连接 {connected_count}/3")

        print(f"  连接完成: {connected_count}/3 个房间已连接")
        if connected_count < 3:
            log_issue("multi_room", f"3个房间中只有 {connected_count} 个连接成功")

        # 同时开启预览
        if connected_count >= 1:
            print("\n  同时开启预览...")
            preview_count = 0
            for rid in room_ids:
                if manager.get_room(rid).is_connected:
                    if manager.start_preview(rid):
                        preview_count += 1

            print(f"  已开启 {preview_count} 个预览")
            print("  观察 5 秒...")
            for i in range(5):
                app.processEvents()
                time.sleep(1)

            # 停止所有预览
            for rid in room_ids:
                manager.stop_preview(rid)
            print("  所有预览已停止")

        # 断开所有连接
        for rid in room_ids:
            manager.disconnect_room(rid)
        print("  所有连接已断开")

    # 删除所有房间
    for rid in room_ids:
        manager.remove_room(rid)
    print(f"  所有房间已删除，剩余: {manager.room_count}")

    print(f"  内存: {get_memory_mb():.1f} MB")


def main():
    print("\n" + "#" * 60)
    print("#  LSC 综合真实场景测试")
    print("#  直播链接: " + TEST_URL[:60] + "...")
    print("#" * 60)
    print(f"  测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 先验证流
    step("预检查: 直播流解析")
    info = parse_stream(TEST_URL)
    print(f"  是否直播: {info.is_live}")
    print(f"  主播: '{info.streamer}'")
    print(f"  标题: '{info.title}'")
    print(f"  画质数量: {len(info.quality_urls)}")

    if not info.is_live:
        print("\n⚠️  直播未开播，测试可能无法完整进行")
        return

    # 完整工作流
    try:
        test_full_workflow()
    except Exception as e:
        print(f"\n❌ 完整工作流测试异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("workflow", f"测试异常: {e}")

    # 多房间测试
    try:
        test_multi_room()
    except Exception as e:
        print(f"\n❌ 多房间测试异常: {e}")
        import traceback
        traceback.print_exc()
        log_issue("multi_room", f"测试异常: {e}")

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    if not issues:
        print("\n🎉 所有测试通过，未发现明显问题！")
    else:
        print(f"\n共发现 {len(issues)} 个问题：\n")
        for i, (area, desc) in enumerate(issues, 1):
            print(f"  {i}. [{area}] {desc}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()

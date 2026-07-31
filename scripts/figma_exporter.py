import os
import xml.etree.ElementTree as ET


def create_svg_element(tag, attrib=None, text=None):
    element = ET.Element(tag, attrib or {})
    if text:
        element.text = text
    return element

def build_workbench_svg():
    width = 1920
    height = 1080

    svg = ET.Element('svg', {
        'xmlns': 'http://www.w3.org/2000/svg',
        'width': str(width),
        'height': str(height),
        'viewBox': f'0 0 {width} {height}',
        'style': 'background-color: #0b0d0f; font-family: "SF Pro Text", "Segoe UI", sans-serif;'
    })

    # SVG Definitions (Gradients & Filters)
    defs = ET.SubElement(svg, 'defs')

    # Brand Glow Gradient
    grad = ET.SubElement(defs, 'linearGradient', {'id': 'brandGlow', 'x1': '0%', 'y1': '0%', 'x2': '100%', 'y2': '100%'})
    ET.SubElement(grad, 'stop', {'offset': '0%', 'stop-color': '#31b3ae', 'stop-opacity': '0.8'})
    ET.SubElement(grad, 'stop', {'offset': '100%', 'stop-color': '#175c58', 'stop-opacity': '0.3'})

    # 1. Background
    ET.SubElement(svg, 'rect', {'x': '0', 'y': '0', 'width': str(width), 'height': str(height), 'fill': '#0b0d0f'})

    # 2. TOPBAR (Height: 52px)
    topbar = ET.SubElement(svg, 'g', {'id': 'Layer_TopHeaderBar'})
    ET.SubElement(topbar, 'rect', {'x': '0', 'y': '0', 'width': '1920', 'height': '52', 'fill': '#111417', 'stroke': 'rgba(255,255,255,0.08)', 'stroke-width': '1'})

    # Logo & Title
    logo_group = ET.SubElement(topbar, 'g', {'id': 'Logo_Brand'})
    ET.SubElement(logo_group, 'rect', {'x': '16', 'y': '12', 'width': '28', 'height': '28', 'rx': '6', 'fill': '#31b3ae'})
    ET.SubElement(logo_group, 'text', {'x': '30', 'y': '31', 'fill': '#062b2a', 'font-weight': 'bold', 'font-size': '16', 'text-anchor': 'middle'}, text='L')
    ET.SubElement(topbar, 'text', {'x': '54', 'y': '31', 'fill': '#f2f4f5', 'font-weight': '600', 'font-size': '15'}, text='LSC 直播切片系统 v5.0')

    # Top Status Badges
    status_group = ET.SubElement(topbar, 'g', {'id': 'Header_Status_Badges'})
    # Live indicator
    ET.SubElement(status_group, 'rect', {'x': '260', 'y': '14', 'width': '85', 'height': '24', 'rx': '12', 'fill': 'rgba(69,199,121,0.15)', 'stroke': 'rgba(69,199,121,0.4)', 'stroke-width': '1'})
    ET.SubElement(status_group, 'circle', {'cx': '272', 'cy': '26', 'r': '4', 'fill': '#45c779'})
    ET.SubElement(status_group, 'text', {'x': '284', 'y': '31', 'fill': '#45c779', 'font-size': '11', 'font-weight': '600'}, text='4 路监控中')

    # Recording Badge
    ET.SubElement(status_group, 'rect', {'x': '355', 'y': '14', 'width': '115', 'height': '24', 'rx': '12', 'fill': 'rgba(240,100,92,0.15)', 'stroke': 'rgba(240,100,92,0.4)', 'stroke-width': '1'})
    ET.SubElement(status_group, 'circle', {'cx': '367', 'cy': '26', 'r': '4', 'fill': '#f0645c'})
    ET.SubElement(status_group, 'text', {'x': '378', 'y': '31', 'fill': '#f0645c', 'font-size': '11', 'font-weight': '600'}, text='自动切片: 运行中')

    # Top Header Actions (Right side)
    header_actions = ET.SubElement(topbar, 'g', {'id': 'Header_Right_Actions'})
    # Add room button
    ET.SubElement(header_actions, 'rect', {'x': '1560', 'y': '11', 'width': '110', 'height': '30', 'rx': '6', 'fill': '#31b3ae'})
    ET.SubElement(header_actions, 'text', {'x': '1615', 'y': '30', 'fill': '#062b2a', 'font-size': '12', 'font-weight': '600', 'text-anchor': 'middle'}, text='+ 添加直播间')

    # Quick Batch button
    ET.SubElement(header_actions, 'rect', {'x': '1680', 'y': '11', 'width': '90', 'height': '30', 'rx': '6', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.14)', 'stroke-width': '1'})
    ET.SubElement(header_actions, 'text', {'x': '1725', 'y': '30', 'fill': '#b6bcc2', 'font-size': '12', 'text-anchor': 'middle'}, text='批量导出')

    # Settings Icon Button
    ET.SubElement(header_actions, 'rect', {'x': '1780', 'y': '11', 'width': '36', 'height': '30', 'rx': '6', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.14)', 'stroke-width': '1'})
    ET.SubElement(header_actions, 'text', {'x': '1798', 'y': '30', 'fill': '#b6bcc2', 'font-size': '14', 'text-anchor': 'middle'}, text='⚙')

    # Help Button
    ET.SubElement(header_actions, 'rect', {'x': '1824', 'y': '11', 'width': '36', 'height': '30', 'rx': '6', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.14)', 'stroke-width': '1'})
    ET.SubElement(header_actions, 'text', {'x': '1842', 'y': '30', 'fill': '#b6bcc2', 'font-size': '13', 'text-anchor': 'middle'}, text='?')

    # 3. LEFT SIDEBAR / ROOM MONITOR LIST (Width: 320px, Top: 52, Height: 992)
    sidebar = ET.SubElement(svg, 'g', {'id': 'Layer_LeftSidebar_RoomMonitor'})
    ET.SubElement(sidebar, 'rect', {'x': '0', 'y': '52', 'width': '320', 'height': '992', 'fill': '#111417', 'stroke': 'rgba(255,255,255,0.08)', 'stroke-width': '1'})

    # Search & Filter
    search_g = ET.SubElement(sidebar, 'g', {'id': 'Sidebar_Search_Box'})
    ET.SubElement(search_g, 'rect', {'x': '12', 'y': '64', 'width': '296', 'height': '32', 'rx': '6', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.08)', 'stroke-width': '1'})
    ET.SubElement(search_g, 'text', {'x': '24', 'y': '84', 'fill': '#5c646c', 'font-size': '12'}, text='🔍 搜索直播间名称 / 主播 / 状态...')

    # Room Cards List
    rooms = [
        {"id": "01", "name": "李佳琦 Austin 直播间", "platform": "淘宝直播", "status": "直播中", "speed": "10.4 MB/s", "clips": "18个切片", "active": True},
        {"id": "02", "name": "东方甄选 - 官方旗舰店", "platform": "抖音直播", "status": "直播中", "speed": "8.7 MB/s", "clips": "12个切片", "active": False},
        {"id": "03", "name": "罗永浩 交个朋友直播间", "platform": "抖音直播", "status": "直播中", "speed": "12.1 MB/s", "clips": "24个切片", "active": False},
        {"id": "04", "name": "小红书官方买手示范", "platform": "小红书", "status": "准备中", "speed": "0 KB/s", "clips": "0个切片", "active": False},
        {"id": "05", "name": "游戏解说 - 赛事官方直播", "platform": "Bilibili", "status": "已离线", "speed": "--", "clips": "5个切片", "active": False},
    ]

    cards_g = ET.SubElement(sidebar, 'g', {'id': 'Sidebar_RoomCards_List'})
    y_pos = 108
    for r in rooms:
        card = ET.SubElement(cards_g, 'g', {'id': f'RoomCard_{r["id"]}'})
        bg_color = '#1e2329' if r['active'] else '#171b1f'
        border_color = '#31b3ae' if r['active'] else 'rgba(255,255,255,0.08)'

        ET.SubElement(card, 'rect', {'x': '12', 'y': str(y_pos), 'width': '296', 'height': '84', 'rx': '8', 'fill': bg_color, 'stroke': border_color, 'stroke-width': '1.5' if r['active'] else '1'})

        # Room thumbnail placeholder
        ET.SubElement(card, 'rect', {'x': '20', 'y': str(y_pos + 12), 'width': '80', 'height': '60', 'rx': '4', 'fill': '#0b0d0f'})
        ET.SubElement(card, 'text', {'x': '60', 'y': str(y_pos + 48), 'fill': '#5c646c', 'font-size': '10', 'text-anchor': 'middle'}, text='[LIVE 预览]')

        # Room title & info
        ET.SubElement(card, 'text', {'x': '110', 'y': str(y_pos + 26), 'fill': '#f2f4f5', 'font-weight': '600', 'font-size': '12'}, text=r['name'])

        # Platform tag
        ET.SubElement(card, 'rect', {'x': '110', 'y': str(y_pos + 34), 'width': '52', 'height': '18', 'rx': '3', 'fill': 'rgba(49,179,174,0.12)'})
        ET.SubElement(card, 'text', {'x': '136', 'y': str(y_pos + 47), 'fill': '#31b3ae', 'font-size': '10', 'text-anchor': 'middle'}, text=r['platform'])

        # Speed & clips info
        ET.SubElement(card, 'text', {'x': '110', 'y': str(y_pos + 66), 'fill': '#8a9199', 'font-size': '10'}, text=f'码率: {r["speed"]}  |  {r["clips"]}')

        y_pos += 92

    # 4. MAIN CENTRAL VIDEO PLAYER AREA (Left: 320, Width: 1240, Top: 52, Height: 660)
    video_section = ET.SubElement(svg, 'g', {'id': 'Layer_MainVideo_Viewport'})
    ET.SubElement(video_section, 'rect', {'x': '320', 'y': '52', 'width': '1240', 'height': '660', 'fill': '#0b0d0f'})

    # Video Frame Container
    player_box = ET.SubElement(video_section, 'g', {'id': 'VideoPlayer_Canvas'})
    ET.SubElement(player_box, 'rect', {'x': '336', 'y': '68', 'width': '1208', 'height': '628', 'rx': '8', 'fill': '#171b1f', 'stroke': 'rgba(255,255,255,0.1)', 'stroke-width': '1'})

    # Simulated Video Content Frame
    ET.SubElement(player_box, 'rect', {'x': '340', 'y': '72', 'width': '1200', 'height': '620', 'rx': '6', 'fill': '#111417'})

    # AI OCR Detection Bounding Box Overlays
    ocr_box1 = ET.SubElement(player_box, 'g', {'id': 'AI_BoundingBox_PriceTag'})
    ET.SubElement(ocr_box1, 'rect', {'x': '420', 'y': '120', 'width': '220', 'height': '90', 'rx': '4', 'fill': 'rgba(49,179,174,0.08)', 'stroke': '#31b3ae', 'stroke-width': '1.5', 'stroke-dasharray': '4 2'})
    ET.SubElement(ocr_box1, 'rect', {'x': '420', 'y': '120', 'width': '140', 'height': '18', 'fill': '#31b3ae'})
    ET.SubElement(ocr_box1, 'text', {'x': '426', 'y': '133', 'fill': '#062b2a', 'font-weight': 'bold', 'font-size': '10'}, text='AI OCR: 价格/优惠券检测')

    ocr_box2 = ET.SubElement(player_box, 'g', {'id': 'AI_BoundingBox_Speaker'})
    ET.SubElement(ocr_box2, 'rect', {'x': '880', 'y': '180', 'width': '380', 'height': '460', 'rx': '4', 'fill': 'rgba(69,199,121,0.06)', 'stroke': '#45c779', 'stroke-width': '1.5'})
    ET.SubElement(ocr_box2, 'rect', {'x': '880', 'y': '180', 'width': '130', 'height': '18', 'fill': '#45c779'})
    ET.SubElement(ocr_box2, 'text', {'x': '886', 'y': '193', 'fill': '#062b2a', 'font-weight': 'bold', 'font-size': '10'}, text='主体说话人: 激活中')

    # Video Watermark / Metadata HUD
    hud = ET.SubElement(player_box, 'g', {'id': 'Player_HUD_Overlay'})
    ET.SubElement(hud, 'rect', {'x': '356', 'y': '88', 'width': '260', 'height': '32', 'rx': '6', 'fill': 'rgba(17,20,23,0.85)', 'stroke': 'rgba(255,255,255,0.1)', 'stroke-width': '1'})
    ET.SubElement(hud, 'text', {'x': '368', 'y': '109', 'fill': '#f2f4f5', 'font-size': '11', 'font-weight': '600'}, text='LIVE | 1080P 60FPS | H.264 | 12.4 Mbps')

    # 5. RIGHT SIDEBAR / CLIP PANEL (Left: 1560, Width: 360, Top: 52, Height: 992)
    right_panel = ET.SubElement(svg, 'g', {'id': 'Layer_RightPanel_ClipsQueue'})
    ET.SubElement(right_panel, 'rect', {'x': '1560', 'y': '52', 'width': '360', 'height': '992', 'fill': '#111417', 'stroke': 'rgba(255,255,255,0.08)', 'stroke-width': '1'})

    # Panel Title Header
    ET.SubElement(right_panel, 'text', {'x': '1576', 'y': '84', 'fill': '#f2f4f5', 'font-size': '14', 'font-weight': '600'}, text='智能高光切片列表')
    ET.SubElement(right_panel, 'rect', {'x': '1840', 'y': '68', 'width': '64', 'height': '24', 'rx': '4', 'fill': 'rgba(49,179,174,0.12)'})
    ET.SubElement(right_panel, 'text', {'x': '1872', 'y': '84', 'fill': '#31b3ae', 'font-size': '11', 'font-weight': '600', 'text-anchor': 'middle'}, text='共 18 个')

    # Clip Items List
    clips = [
        {"time": "14:22:10 - 14:24:45", "title": "【爆款讲解】美妆精华液买一送一特惠", "score": "98%", "status": "已导出"},
        {"time": "14:15:30 - 14:17:12", "title": "【主播互动】观众问答抽奖高光时刻", "score": "91%", "status": "已导出"},
        {"time": "14:02:00 - 14:04:15", "title": "【品牌介绍】新品首发独家优惠拆箱", "score": "88%", "status": "处理中"},
        {"time": "13:48:20 - 13:51:00", "title": "【价格反转】限时秒杀3秒抢购现场", "score": "95%", "status": "待处理"},
        {"time": "13:30:10 - 13:32:40", "title": "【开场高潮】人气破10万开场欢呼", "score": "86%", "status": "待处理"},
    ]

    clip_y = 108
    for c in clips:
        clip_card = ET.SubElement(right_panel, 'g', {'id': f'ClipItem_{c["score"]}'})
        ET.SubElement(clip_card, 'rect', {'x': '1576', 'y': str(clip_y), 'width': '328', 'height': '92', 'rx': '6', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.08)', 'stroke-width': '1'})

        # Thumbnail
        ET.SubElement(clip_card, 'rect', {'x': '1586', 'y': str(clip_y + 10), 'width': '100', 'height': '72', 'rx': '4', 'fill': '#0b0d0f'})
        ET.SubElement(clip_card, 'text', {'x': '1636', 'y': str(clip_y + 50), 'fill': '#5c646c', 'font-size': '10', 'text-anchor': 'middle'}, text='切片封面')

        # Details
        ET.SubElement(clip_card, 'text', {'x': '1696', 'y': str(clip_y + 26), 'fill': '#f2f4f5', 'font-size': '11', 'font-weight': '600'}, text=c['title'][:12] + '...')
        ET.SubElement(clip_card, 'text', {'x': '1696', 'y': str(clip_y + 46), 'fill': '#8a9199', 'font-size': '10'}, text=f'时长: {c["time"]}')

        # High Score Tag
        ET.SubElement(clip_card, 'rect', {'x': '1696', 'y': str(clip_y + 56), 'width': '60', 'height': '18', 'rx': '3', 'fill': 'rgba(231,160,73,0.15)'})
        ET.SubElement(clip_card, 'text', {'x': '1726', 'y': str(clip_y + 69), 'fill': '#e7a049', 'font-size': '10', 'font-weight': 'bold', 'text-anchor': 'middle'}, text=f'高光 {c["score"]}')

        # Status tag
        ET.SubElement(clip_card, 'rect', {'x': '1840', 'y': str(clip_y + 56), 'width': '52', 'height': '18', 'rx': '3', 'fill': 'rgba(69,199,121,0.15)'})
        ET.SubElement(clip_card, 'text', {'x': '1866', 'y': str(clip_y + 69), 'fill': '#45c779', 'font-size': '10', 'text-anchor': 'middle'}, text=c['status'])

        clip_y += 104

    # 6. BOTTOM TIMELINE CONTROLLER (Left: 320, Width: 1240, Top: 712, Height: 332)
    timeline = ET.SubElement(svg, 'g', {'id': 'Layer_BottomTimeline_Track'})
    ET.SubElement(timeline, 'rect', {'x': '320', 'y': '712', 'width': '1240', 'height': '332', 'fill': '#111417', 'stroke': 'rgba(255,255,255,0.08)', 'stroke-width': '1'})

    # Timeline Toolbar Header
    t_header = ET.SubElement(timeline, 'g', {'id': 'Timeline_Toolbar'})
    ET.SubElement(t_header, 'text', {'x': '336', 'y': '738', 'fill': '#f2f4f5', 'font-weight': '600', 'font-size': '13'}, text='多轨时间轴编辑器 (Waveform & AI Highlight Track)')

    # Timecode readout
    ET.SubElement(t_header, 'rect', {'x': '720', 'y': '722', 'width': '140', 'height': '24', 'rx': '4', 'fill': '#1e2329'})
    ET.SubElement(t_header, 'text', {'x': '790', 'y': '738', 'fill': '#31b3ae', 'font-family': 'monospace', 'font-size': '12', 'font-weight': 'bold', 'text-anchor': 'middle'}, text='01:14:22.08 / 02:30:00')

    # Control Buttons
    ET.SubElement(t_header, 'rect', {'x': '1360', 'y': '722', 'width': '80', 'height': '24', 'rx': '4', 'fill': '#31b3ae'})
    ET.SubElement(t_header, 'text', {'x': '1400', 'y': '738', 'fill': '#062b2a', 'font-size': '11', 'font-weight': 'bold', 'text-anchor': 'middle'}, text='✂ 标记切片')

    ET.SubElement(t_header, 'rect', {'x': '1450', 'y': '722', 'width': '90', 'height': '24', 'rx': '4', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.1)'})
    ET.SubElement(t_header, 'text', {'x': '1495', 'y': '738', 'fill': '#b6bcc2', 'font-size': '11', 'text-anchor': 'middle'}, text='⚡ 智能缩放')

    # Timeline Ruler Ticks
    ruler = ET.SubElement(timeline, 'g', {'id': 'Timeline_Ruler_Ticks'})
    ET.SubElement(ruler, 'rect', {'x': '336', 'y': '756', 'width': '1208', 'height': '24', 'fill': '#171b1f'})
    for idx, tick_x in enumerate(range(350, 1530, 80)):
        ET.SubElement(ruler, 'line', {'x1': str(tick_x), 'y1': '756', 'x2': str(tick_x), 'y2': '766', 'stroke': '#5c646c', 'stroke-width': '1'})
        time_str = f'14:{idx*5:02d}:00'
        ET.SubElement(ruler, 'text', {'x': str(tick_x), 'y': '776', 'fill': '#8a9199', 'font-size': '9', 'font-family': 'monospace', 'text-anchor': 'middle'}, text=time_str)

    # Track 1: Audio Waveform Track
    track1 = ET.SubElement(timeline, 'g', {'id': 'Timeline_Track_AudioWaveform'})
    ET.SubElement(track1, 'rect', {'x': '336', 'y': '788', 'width': '1208', 'height': '54', 'rx': '4', 'fill': '#171b1f', 'stroke': 'rgba(255,255,255,0.05)'})
    ET.SubElement(track1, 'text', {'x': '346', 'y': '820', 'fill': '#8a9199', 'font-size': '10'}, text='音频轨道 A1')
    # Simulated Waveform lines
    for wx in range(430, 1530, 6):
        h = (wx * 17) % 36 + 6
        ET.SubElement(track1, 'line', {'x1': str(wx), 'y1': str(815 - h//2), 'x2': str(wx), 'y2': str(815 + h//2), 'stroke': '#31b3ae', 'stroke-opacity': '0.7', 'stroke-width': '2'})

    # Track 2: AI Highlight Segment Track
    track2 = ET.SubElement(timeline, 'g', {'id': 'Timeline_Track_AI_Highlights'})
    ET.SubElement(track2, 'rect', {'x': '336', 'y': '848', 'width': '1208', 'height': '54', 'rx': '4', 'fill': '#171b1f', 'stroke': 'rgba(255,255,255,0.05)'})
    ET.SubElement(track2, 'text', {'x': '346', 'y': '880', 'fill': '#8a9199', 'font-size': '10'}, text='AI 高光轨道')

    # Highlight Blocks
    ET.SubElement(track2, 'rect', {'x': '500', 'y': '854', 'width': '180', 'height': '42', 'rx': '4', 'fill': 'rgba(231,160,73,0.3)', 'stroke': '#e7a049', 'stroke-width': '1'})
    ET.SubElement(track2, 'text', {'x': '590', 'y': '879', 'fill': '#e7a049', 'font-size': '10', 'font-weight': 'bold', 'text-anchor': 'middle'}, text='高光片段 #1 (评分98%)')

    ET.SubElement(track2, 'rect', {'x': '880', 'y': '854', 'width': '240', 'height': '42', 'rx': '4', 'fill': 'rgba(69,199,121,0.3)', 'stroke': '#45c779', 'stroke-width': '1'})
    ET.SubElement(track2, 'text', {'x': '1000', 'y': '879', 'fill': '#45c779', 'font-size': '10', 'font-weight': 'bold', 'text-anchor': 'middle'}, text='高光片段 #2 (评分95%)')

    # Playhead Cursor Scrubber Line
    playhead = ET.SubElement(timeline, 'g', {'id': 'Timeline_Playhead_Cursor'})
    ET.SubElement(playhead, 'line', {'x1': '720', 'y1': '750', 'x2': '720', 'y2': '1020', 'stroke': '#f0645c', 'stroke-width': '2'})
    ET.SubElement(playhead, 'polygon', {'points': '712,750 728,750 720,762', 'fill': '#f0645c'})

    # 7. BOTTOM STATUS BAR (Height: 36px)
    statusbar = ET.SubElement(svg, 'g', {'id': 'Layer_BottomStatusBar'})
    ET.SubElement(statusbar, 'rect', {'x': '0', 'y': '1044', 'width': '1920', 'height': '36', 'fill': '#111417', 'stroke': 'rgba(255,255,255,0.08)', 'stroke-width': '1'})

    ET.SubElement(statusbar, 'text', {'x': '16', 'y': '1067', 'fill': '#45c779', 'font-size': '11'}, text='● 系统就绪  |  WebSocket: 已连接 (12ms)')
    ET.SubElement(statusbar, 'text', {'x': '300', 'y': '1067', 'fill': '#8a9199', 'font-size': '11'}, text='CPU: 14%  |  GPU: 32% (NVENC 硬件加速)  |  RAM: 3.2 / 16.0 GB')
    ET.SubElement(statusbar, 'text', {'x': '1700', 'y': '1067', 'fill': '#8a9199', 'font-size': '11', 'text-anchor': 'end'}, text='磁盘存储剩余: 482.5 GB  |  LSC Node v5.0.4')

    return svg

def build_settings_svg():
    width = 1920
    height = 1080

    svg = ET.Element('svg', {
        'xmlns': 'http://www.w3.org/2000/svg',
        'width': str(width),
        'height': str(height),
        'viewBox': f'0 0 {width} {height}',
        'style': 'background-color: #0b0d0f; font-family: "SF Pro Text", "Segoe UI", sans-serif;'
    })

    # 1. Base Workbench Dimmed Background
    wb_base = build_workbench_svg()
    for child in list(wb_base):
        svg.append(child)

    # Dim Overlay
    overlay = ET.SubElement(svg, 'g', {'id': 'Modal_Dim_Overlay'})
    ET.SubElement(overlay, 'rect', {'x': '0', 'y': '0', 'width': '1920', 'height': '1080', 'fill': 'rgba(0,0,0,0.65)'})

    # 2. SETTINGS DIALOG MODAL WINDOW (Center: 510, 190, Width: 900, Height: 700)
    modal = ET.SubElement(svg, 'g', {'id': 'Layer_Settings_Dialog_Modal'})
    ET.SubElement(modal, 'rect', {'x': '510', 'y': '190', 'width': '900', 'height': '700', 'rx': '12', 'fill': '#171b1f', 'stroke': 'rgba(255,255,255,0.14)', 'stroke-width': '1'})

    # Modal Header
    ET.SubElement(modal, 'text', {'x': '540', 'y': '232', 'fill': '#f2f4f5', 'font-size': '18', 'font-weight': '600'}, text='系统设置 (Settings)')
    ET.SubElement(modal, 'line', {'x1': '510', 'y1': '254', 'x2': '1410', 'y2': '254', 'stroke': 'rgba(255,255,255,0.08)'})

    # Modal Left Nav Tabs (Width: 200)
    nav = ET.SubElement(modal, 'g', {'id': 'Settings_Left_Navigation'})
    tabs = [
        {"name": "直播账号与平台", "icon": "👤", "active": True},
        {"name": "录制与切片参数", "icon": "📹", "active": False},
        {"name": "AI 识别与模型", "icon": "🤖", "active": False},
        {"name": "快捷键与热键", "icon": "⌨", "active": False},
        {"name": "系统日志与诊断", "icon": "📑", "active": False},
    ]
    tab_y = 274
    for t in tabs:
        t_bg = 'rgba(49,179,174,0.12)' if t['active'] else 'transparent'
        t_color = '#31b3ae' if t['active'] else '#b6bcc2'
        ET.SubElement(nav, 'rect', {'x': '526', 'y': str(tab_y), 'width': '180', 'height': '40', 'rx': '6', 'fill': t_bg})
        ET.SubElement(nav, 'text', {'x': '546', 'y': str(tab_y + 25), 'fill': t_color, 'font-size': '13', 'font-weight': '600' if t['active'] else 'normal'}, text=f'{t["icon"]}  {t["name"]}')
        tab_y += 48

    ET.SubElement(modal, 'line', {'x1': '720', 'y1': '254', 'x2': '720', 'y2': '890', 'stroke': 'rgba(255,255,255,0.08)'})

    # Modal Right Form Content Area
    form = ET.SubElement(modal, 'g', {'id': 'Settings_Form_Content'})

    # Section 1: Cookie & Auth Token
    ET.SubElement(form, 'text', {'x': '750', 'y': '290', 'fill': '#f2f4f5', 'font-size': '14', 'font-weight': '600'}, text='淘宝 / 抖音直播凭证设置')

    # Input Field 1
    ET.SubElement(form, 'text', {'x': '750', 'y': '320', 'fill': '#8a9199', 'font-size': '12'}, text='Cookie 字符串 (Auto Refresh):')
    ET.SubElement(form, 'rect', {'x': '750', 'y': '330', 'width': '620', 'height': '36', 'rx': '6', 'fill': '#111417', 'stroke': 'rgba(255,255,255,0.1)'})
    ET.SubElement(form, 'text', {'x': '762', 'y': '353', 'fill': '#5c646c', 'font-size': '11', 'font-family': 'monospace'}, text='sessionid=9f823a10...; tgw_store=tb_live_v2; passport_csrf_token=...')

    # Section 2: Stream Output Directory
    ET.SubElement(form, 'text', {'x': '750', 'y': '405', 'fill': '#f2f4f5', 'font-size': '14', 'font-weight': '600'}, text='默认切片导出保存路径')
    ET.SubElement(form, 'rect', {'x': '750', 'y': '420', 'width': '510', 'height': '36', 'rx': '6', 'fill': '#111417', 'stroke': 'rgba(255,255,255,0.1)'})
    ET.SubElement(form, 'text', {'x': '762', 'y': '443', 'fill': '#f2f4f5', 'font-size': '12'}, text='D:/Project/直播切片多人/data/exports/')
    ET.SubElement(form, 'rect', {'x': '1270', 'y': '420', 'width': '100', 'height': '36', 'rx': '6', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.14)'})
    ET.SubElement(form, 'text', {'x': '1320', 'y': '443', 'fill': '#b6bcc2', 'font-size': '12', 'text-anchor': 'middle'}, text='浏览...')

    # Section 3: Toggles
    ET.SubElement(form, 'text', {'x': '750', 'y': '495', 'fill': '#f2f4f5', 'font-size': '14', 'font-weight': '600'}, text='自动化与智能识别选项')

    # Toggle 1
    t1 = ET.SubElement(form, 'g', {'id': 'Toggle_GPU_Acceleration'})
    ET.SubElement(t1, 'rect', {'x': '750', 'y': '515', 'width': '44', 'height': '24', 'rx': '12', 'fill': '#31b3ae'})
    ET.SubElement(t1, 'circle', {'cx': '782', 'cy': '527', 'r': '9', 'fill': '#062b2a'})
    ET.SubElement(t1, 'text', {'x': '806', 'y': '532', 'fill': '#f2f4f5', 'font-size': '13'}, text='启用 NVIDIA NVENC 硬件转码加速')

    # Toggle 2
    t2 = ET.SubElement(form, 'g', {'id': 'Toggle_Auto_Highlight'})
    ET.SubElement(t2, 'rect', {'x': '750', 'y': '555', 'width': '44', 'height': '24', 'rx': '12', 'fill': '#31b3ae'})
    ET.SubElement(t2, 'circle', {'cx': '782', 'cy': '567', 'r': '9', 'fill': '#062b2a'})
    ET.SubElement(t2, 'text', {'x': '806', 'y': '572', 'fill': '#f2f4f5', 'font-size': '13'}, text='自动捕获带价格 OCR 关键词的高光片段')

    # Modal Footer Buttons
    ET.SubElement(modal, 'line', {'x1': '510', 'y1': '830', 'x2': '1410', 'y2': '830', 'stroke': 'rgba(255,255,255,0.08)'})
    ET.SubElement(modal, 'rect', {'x': '1200', 'y': '842', 'width': '90', 'height': '36', 'rx': '6', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.14)'})
    ET.SubElement(modal, 'text', {'x': '1245', 'y': '865', 'fill': '#b6bcc2', 'font-size': '13', 'text-anchor': 'middle'}, text='取消')

    ET.SubElement(modal, 'rect', {'x': '1300', 'y': '842', 'width': '90', 'height': '36', 'rx': '6', 'fill': '#31b3ae'})
    ET.SubElement(modal, 'text', {'x': '1345', 'y': '865', 'fill': '#062b2a', 'font-size': '13', 'font-weight': 'bold', 'text-anchor': 'middle'}, text='保存修改')

    return svg

def build_design_system_svg():
    width = 1920
    height = 1080

    svg = ET.Element('svg', {
        'xmlns': 'http://www.w3.org/2000/svg',
        'width': str(width),
        'height': str(height),
        'viewBox': f'0 0 {width} {height}',
        'style': 'background-color: #0b0d0f; font-family: "SF Pro Text", "Segoe UI", sans-serif;'
    })

    # Title
    ET.SubElement(svg, 'text', {'x': '60', 'y': '60', 'fill': '#f2f4f5', 'font-size': '24', 'font-weight': 'bold'}, text='LSC Design System & UI Tokens Component Canvas')
    ET.SubElement(svg, 'text', {'x': '60', 'y': '90', 'fill': '#31b3ae', 'font-size': '14'}, text='Figma 设计规范与 UI 图层组件库 (Dark Mode UI Library)')

    # 1. COLOR PALETTE SWATCHES
    colors_group = ET.SubElement(svg, 'g', {'id': 'Section_Color_Palette_Tokens'})
    ET.SubElement(colors_group, 'text', {'x': '60', 'y': '140', 'fill': '#f2f4f5', 'font-size': '16', 'font-weight': '600'}, text='1. Color Swatches (主题配色)')

    swatches = [
        {"name": "Brand / Primary", "hex": "#31b3ae"},
        {"name": "Background 900", "hex": "#0b0d0f"},
        {"name": "Background 800", "hex": "#111417"},
        {"name": "Background 700", "hex": "#171b1f"},
        {"name": "Background 600", "hex": "#1e2329"},
        {"name": "Text Primary 50", "hex": "#f2f4f5"},
        {"name": "Text Muted 400", "hex": "#8a9199"},
        {"name": "Success", "hex": "#45c779"},
        {"name": "Warning", "hex": "#e7a049"},
        {"name": "Error / Danger", "hex": "#f0645c"},
    ]

    x_pos = 60
    for s in swatches:
        sw = ET.SubElement(colors_group, 'g', {'id': f'Swatch_{s["name"].replace(" ", "_")}'})
        ET.SubElement(sw, 'rect', {'x': str(x_pos), 'y': '160', 'width': '160', 'height': '80', 'rx': '8', 'fill': s['hex'], 'stroke': 'rgba(255,255,255,0.1)', 'stroke-width': '1'})
        ET.SubElement(sw, 'text', {'x': str(x_pos + 10), 'y': '260', 'fill': '#f2f4f5', 'font-size': '12', 'font-weight': '600'}, text=s['name'])
        ET.SubElement(sw, 'text', {'x': str(x_pos + 10), 'y': '278', 'fill': '#8a9199', 'font-size': '11', 'font-family': 'monospace'}, text=s['hex'])
        x_pos += 180

    # 2. BUTTON COMPONENTS
    btn_group = ET.SubElement(svg, 'g', {'id': 'Section_Button_Components'})
    ET.SubElement(btn_group, 'text', {'x': '60', 'y': '340', 'fill': '#f2f4f5', 'font-size': '16', 'font-weight': '600'}, text='2. Button Components (按钮组件集)')

    # Primary
    b1 = ET.SubElement(btn_group, 'g', {'id': 'Button_Primary'})
    ET.SubElement(b1, 'rect', {'x': '60', 'y': '360', 'width': '140', 'height': '36', 'rx': '6', 'fill': '#31b3ae'})
    ET.SubElement(b1, 'text', {'x': '130', 'y': '383', 'fill': '#062b2a', 'font-size': '13', 'font-weight': 'bold', 'text-anchor': 'middle'}, text='Primary Button')

    # Secondary
    b2 = ET.SubElement(btn_group, 'g', {'id': 'Button_Secondary'})
    ET.SubElement(b2, 'rect', {'x': '220', 'y': '360', 'width': '140', 'height': '36', 'rx': '6', 'fill': '#1e2329', 'stroke': 'rgba(255,255,255,0.14)', 'stroke-width': '1'})
    ET.SubElement(b2, 'text', {'x': '290', 'y': '383', 'fill': '#b6bcc2', 'font-size': '13', 'text-anchor': 'middle'}, text='Secondary Button')

    # Danger
    b3 = ET.SubElement(btn_group, 'g', {'id': 'Button_Danger'})
    ET.SubElement(b3, 'rect', {'x': '380', 'y': '360', 'width': '140', 'height': '36', 'rx': '6', 'fill': '#f0645c'})
    ET.SubElement(b3, 'text', {'x': '450', 'y': '383', 'fill': '#ffffff', 'font-size': '13', 'font-weight': 'bold', 'text-anchor': 'middle'}, text='Danger Button')

    # Ghost
    b4 = ET.SubElement(btn_group, 'g', {'id': 'Button_Ghost'})
    ET.SubElement(b4, 'rect', {'x': '540', 'y': '360', 'width': '140', 'height': '36', 'rx': '6', 'fill': 'transparent', 'stroke': 'rgba(255,255,255,0.2)', 'stroke-width': '1'})
    ET.SubElement(b4, 'text', {'x': '610', 'y': '383', 'fill': '#31b3ae', 'font-size': '13', 'text-anchor': 'middle'}, text='Ghost Button')

    # 3. TAG & BADGE COMPONENTS
    tag_group = ET.SubElement(svg, 'g', {'id': 'Section_Status_Badges'})
    ET.SubElement(tag_group, 'text', {'x': '60', 'y': '440', 'fill': '#f2f4f5', 'font-size': '16', 'font-weight': '600'}, text='3. Status Tags & Badges (状态标签与徽章)')

    tags = [
        {"label": "SUCCESS", "bg": "rgba(69,199,121,0.15)", "color": "#45c779"},
        {"label": "WARNING", "bg": "rgba(231,160,73,0.15)", "color": "#e7a049"},
        {"label": "ERROR", "bg": "rgba(240,100,92,0.15)", "color": "#f0645c"},
        {"label": "INFO", "bg": "rgba(85,156,232,0.15)", "color": "#559ce8"},
        {"label": "BRAND", "bg": "rgba(49,179,174,0.15)", "color": "#31b3ae"},
    ]
    tx_pos = 60
    for t in tags:
        tg = ET.SubElement(tag_group, 'g', {'id': f'Tag_{t["label"]}'})
        ET.SubElement(tg, 'rect', {'x': str(tx_pos), 'y': '460', 'width': '90', 'height': '26', 'rx': '4', 'fill': t['bg']})
        ET.SubElement(tg, 'text', {'x': str(tx_pos + 45), 'y': '477', 'fill': t['color'], 'font-size': '11', 'font-weight': 'bold', 'text-anchor': 'middle'}, text=t['label'])
        tx_pos += 110

    # 4. TYPOGRAPHY HIERARCHY
    typo_group = ET.SubElement(svg, 'g', {'id': 'Section_Typography_Hierarchy'})
    ET.SubElement(typo_group, 'text', {'x': '60', 'y': '540', 'fill': '#f2f4f5', 'font-size': '16', 'font-weight': '600'}, text='4. Typography Scale (字体排版层级)')

    ET.SubElement(typo_group, 'text', {'x': '60', 'y': '580', 'fill': '#f2f4f5', 'font-size': '24', 'font-weight': 'bold'}, text='Display Header / 24px Bold')
    ET.SubElement(typo_group, 'text', {'x': '60', 'y': '615', 'fill': '#f2f4f5', 'font-size': '18', 'font-weight': '600'}, text='Section Title / 18px SemiBold')
    ET.SubElement(typo_group, 'text', {'x': '60', 'y': '645', 'fill': '#f2f4f5', 'font-size': '14', 'font-weight': '600'}, text='Body Primary / 14px Medium')
    ET.SubElement(typo_group, 'text', {'x': '60', 'y': '670', 'fill': '#8a9199', 'font-size': '12'}, text='Caption Muted / 12px Regular')
    ET.SubElement(typo_group, 'text', {'x': '60', 'y': '695', 'fill': '#31b3ae', 'font-size': '11', 'font-family': 'monospace'}, text='Timecode Code Font / 11px Monospaced')

    return svg

def main():
    output_dir = r"d:\Project\直播切片多人\figma-export"
    os.makedirs(output_dir, exist_ok=True)

    # 1. Workbench Dashboard SVG
    wb_tree = ET.ElementTree(build_workbench_svg())
    wb_path = os.path.join(output_dir, "01_Workbench_Dashboard.svg")
    wb_tree.write(wb_path, encoding="utf-8", xml_declaration=True)
    print(f"Exported: {wb_path}")

    # 2. Settings Modal SVG
    st_tree = ET.ElementTree(build_settings_svg())
    st_path = os.path.join(output_dir, "02_Settings_Drawer_Modal.svg")
    st_tree.write(st_path, encoding="utf-8", xml_declaration=True)
    print(f"Exported: {st_path}")

    # 3. Design System Tokens & Components SVG
    ds_tree = ET.ElementTree(build_design_system_svg())
    ds_path = os.path.join(output_dir, "03_Design_System_Tokens.svg")
    ds_tree.write(ds_path, encoding="utf-8", xml_declaration=True)
    print(f"Exported: {ds_path}")

if __name__ == "__main__":
    main()

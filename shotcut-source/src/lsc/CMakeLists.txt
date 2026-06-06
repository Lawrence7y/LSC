cmake_minimum_required(VERSION 3.16)
project(lsc LANGUAGES CXX)
set(VCPKG_APPLOCAL_DEPS OFF CACHE BOOL "Disable vcpkg app-local dependency copying" FORCE)
set(CMAKE_VS_GLOBALS "VcpkgApplocalDeps=false")

# 全局配置和日志头文件
set(LSC_COMMON_HEADERS
    LscConfig.h
    LscLog.h
)

set(LSC_CORE_SOURCES
    core/TaskCenter.cpp
    core/WorkflowOrchestrator.cpp
    core/ErrorManager.cpp
    core/LscDatabase.cpp
)

set(LSC_CORE_HEADERS
    core/TaskCenter.h
    core/WorkflowOrchestrator.h
    core/ErrorManager.h
    core/LscDatabase.h
)

set(LSC_LIVESTREAM_SOURCES
    livestream/PlatformParser.cpp
    livestream/StreamCapture.cpp
    livestream/RecordingSession.cpp
    livestream/PreviewController.cpp
    livestream/GameplayDetector.cpp
    livestream/SelectiveRecorder.cpp
    livestream/platforms/DouyinParser.cpp
    livestream/platforms/BilibiliParser.cpp
)

set(LSC_LIVESTREAM_HEADERS
    livestream/PlatformParser.h
    livestream/IPlatformParser.h
    livestream/GameplayState.h
    livestream/StreamCapture.h
    livestream/RecordingSession.h
    livestream/PreviewController.h
    livestream/GameplayDetector.h
    livestream/SelectiveRecorder.h
    livestream/GameplayTimeline.h
    livestream/platforms/DouyinParser.h
    livestream/platforms/BilibiliParser.h
)

set(LSC_ANALYZER_SOURCES
    analyzer/AudioAnalyzer.cpp
    analyzer/VideoAnalyzer.cpp
    analyzer/SpeechRecognizer.cpp
    analyzer/HighlightDetector.cpp
    analyzer/IHighlightStrategy.h
    analyzer/BeatDetector.cpp
    analyzer/HighlightEngine.cpp
    analyzer/ClipExporter.cpp
    analyzer/ThumbnailGenerator.cpp
    analyzer/GameStrategy.cpp
    analyzer/DanceStrategy.cpp
    analyzer/DialogStrategy.cpp
    analyzer/GenericStrategy.cpp
    analyzer/CompositeHighlightStrategy.cpp
    analyzer/CommentaryStrategy.cpp
    analyzer/DanceSegmentScorer.cpp
    analyzer/RoundBoundaryDetector.cpp
    analyzer/CommentarySegmenter.cpp
    analyzer/PoseAnalyzer.cpp
    analyzer/GameHudAnalyzer.cpp
    analyzer/HighlightUtils.cpp
    analyzer/MaterialClassifier.cpp
    analyzer/HighlightRanker.cpp
    analyzer/RealtimeStrategy.cpp
    analyzer/RoundClipBuilder.cpp
    analyzer/ShortClipRefiner.cpp
    analyzer/FeedbackStore.cpp
    analyzer/ValorantEvaluation.cpp
)

set(LSC_ANALYZER_HEADERS
    analyzer/AudioAnalyzer.h
    analyzer/VideoAnalyzer.h
    analyzer/SpeechRecognizer.h
    analyzer/HighlightDetector.h
    analyzer/BeatDetector.h
    analyzer/HighlightEngine.h
    analyzer/ClipExporter.h
    analyzer/GameStrategy.h
    analyzer/DanceStrategy.h
    analyzer/DialogStrategy.h
    analyzer/GenericStrategy.h
    analyzer/ThumbnailGenerator.h
    analyzer/AnalysisProfile.h
    analyzer/CompositeHighlightStrategy.h
    analyzer/CommentaryStrategy.h
    analyzer/DanceSegmentScorer.h
    analyzer/RoundBoundaryDetector.h
    analyzer/CommentarySegmenter.h
    analyzer/PoseAnalyzer.h
    analyzer/GameHudAnalyzer.h
    analyzer/HighlightUtils.h
    analyzer/RankedClip.h
    analyzer/ValorantProfileConfig.h
    analyzer/MaterialClassifier.h
    analyzer/HighlightRanker.h
    analyzer/RealtimeStrategy.h
    analyzer/RoundClipBuilder.h
    analyzer/ShortClipRefiner.h
    analyzer/FeedbackStore.h
    analyzer/ValorantEvaluation.h
)

set(LSC_WIDGET_SOURCES
    widgets/ClipEditorWidget.cpp
    widgets/WheelEventBlocker.cpp
)

set(LSC_WIDGET_HEADERS
    widgets/ClipEditorWidget.h
    widgets/WheelEventBlocker.h
)

set(LSC_DIALOG_SOURCES
    dialogs/ExportSettingsDialog.cpp
)

set(LSC_DIALOG_HEADERS
    dialogs/ExportSettingsDialog.h
)

set(LSC_DOCK_SOURCES
    docks/LivestreamDock.cpp
    docks/AnalysisDock.cpp
    docks/HighlightPreviewWidget.cpp
    docks/LivePreviewWidget.cpp
    docks/PlayerDock.cpp
    docks/TaskDock.cpp
    docks/HistoryDock.cpp
    docks/FeedbackStatsDock.cpp
    docks/DiagnosticsDock.cpp
    docks/SettingsDock.cpp
)

set(LSC_DOCK_HEADERS
    docks/LivestreamDock.h
    docks/AnalysisDock.h
    docks/HighlightPreviewWidget.h
    docks/LivePreviewWidget.h
    docks/PlayerDock.h
    docks/TaskDock.h
    docks/HistoryDock.h
    docks/FeedbackStatsDock.h
    docks/DiagnosticsDock.h
    docks/SettingsDock.h
)

add_library(lsc STATIC
    ${LSC_COMMON_HEADERS}
    ${LSC_CORE_SOURCES}
    ${LSC_CORE_HEADERS}
    ${LSC_LIVESTREAM_SOURCES}
    ${LSC_LIVESTREAM_HEADERS}
    ${LSC_ANALYZER_SOURCES}
    ${LSC_ANALYZER_HEADERS}
    ${LSC_WIDGET_SOURCES}
    ${LSC_WIDGET_HEADERS}
    ${LSC_DIALOG_SOURCES}
    ${LSC_DIALOG_HEADERS}
    ${LSC_DOCK_SOURCES}
    ${LSC_DOCK_HEADERS}
)
set_target_properties(lsc PROPERTIES VS_GLOBAL_UseMultiToolTask false)

target_include_directories(lsc PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

# Skip Qt's post-build deployment helper in this developer-focused subproject.
# It triggers windeployqt / pwsh requirements that are unnecessary for local builds and tests.
set(QT_SKIP_SETUP_DEPLOYMENT ON)

find_package(Qt6 REQUIRED COMPONENTS Core Network Widgets Multimedia MultimediaWidgets Sql Test)
target_link_libraries(lsc PUBLIC
    Qt6::Core
    Qt6::Network
    Qt6::Widgets
    Qt6::Multimedia
    Qt6::MultimediaWidgets
    Qt6::Sql
)

get_target_property(LSC_QT_CORE_DLL Qt6::Core IMPORTED_LOCATION_RELEASE)
if(NOT LSC_QT_CORE_DLL)
    get_target_property(LSC_QT_CORE_DLL Qt6::Core IMPORTED_LOCATION)
endif()
get_filename_component(LSC_QT_BIN_DIR "${LSC_QT_CORE_DLL}" DIRECTORY)
get_filename_component(LSC_QT_ROOT_DIR "${LSC_QT_BIN_DIR}" DIRECTORY)
set(LSC_QT_PLUGIN_DIR "${LSC_QT_ROOT_DIR}/plugins")
string(REPLACE ";" "\\;" LSC_ESCAPED_SYSTEM_PATH "$ENV{PATH}")
set(LSC_TEST_ENVIRONMENT "PATH=${LSC_QT_BIN_DIR}\\;${LSC_ESCAPED_SYSTEM_PATH}")

target_compile_features(lsc PUBLIC cxx_std_17)

set_target_properties(lsc PROPERTIES
    AUTOMOC ON
    WIN32_EXECUTABLE OFF
)

option(LSC_BUILD_TESTS "Build standalone tests" ON)

# Standalone desktop application (always built)
add_executable(lsc_app
    app/main.cpp
    app/MainWindow.cpp
    app/MainWindow.h
)
target_link_libraries(lsc_app PRIVATE lsc Qt6::Core Qt6::Network Qt6::Widgets)
target_compile_features(lsc_app PRIVATE cxx_std_17)
set_target_properties(lsc_app PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)

add_executable(valorant_eval tools/valorant_eval.cpp)
target_link_libraries(valorant_eval PRIVATE lsc Qt6::Core Qt6::Network)
target_compile_features(valorant_eval PRIVATE cxx_std_17)
set_target_properties(valorant_eval PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)

if(WIN32)
    add_custom_command(TARGET lsc_app POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${LSC_QT_BIN_DIR}/Qt6Core.dll"
            "${LSC_QT_BIN_DIR}/Qt6Gui.dll"
            "${LSC_QT_BIN_DIR}/Qt6Widgets.dll"
            "${LSC_QT_BIN_DIR}/Qt6Network.dll"
            "${LSC_QT_BIN_DIR}/Qt6Multimedia.dll"
            "${LSC_QT_BIN_DIR}/Qt6MultimediaWidgets.dll"
            "${LSC_QT_BIN_DIR}/Qt6Sql.dll"
            "${LSC_QT_BIN_DIR}/Qt6Test.dll"
            "$<TARGET_FILE_DIR:lsc_app>"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/platforms"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/styles"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/iconengines"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/imageformats"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/multimedia"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/networkinformation"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/sqldrivers"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/tls"
        COMMAND ${CMAKE_COMMAND} -E make_directory "$<TARGET_FILE_DIR:lsc_app>/generic"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/platforms"
            "$<TARGET_FILE_DIR:lsc_app>/platforms"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/styles"
            "$<TARGET_FILE_DIR:lsc_app>/styles"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/iconengines"
            "$<TARGET_FILE_DIR:lsc_app>/iconengines"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/imageformats"
            "$<TARGET_FILE_DIR:lsc_app>/imageformats"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/multimedia"
            "$<TARGET_FILE_DIR:lsc_app>/multimedia"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/networkinformation"
            "$<TARGET_FILE_DIR:lsc_app>/networkinformation"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/sqldrivers"
            "$<TARGET_FILE_DIR:lsc_app>/sqldrivers"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/tls"
            "$<TARGET_FILE_DIR:lsc_app>/tls"
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${LSC_QT_PLUGIN_DIR}/generic"
            "$<TARGET_FILE_DIR:lsc_app>/generic"
        COMMENT "Copying Qt runtime for lsc_app"
    )
endif()

if(LSC_BUILD_TESTS)
    enable_testing()

    function(lsc_register_test target_name)
        add_test(NAME ${target_name} COMMAND ${target_name})
        set_tests_properties(${target_name} PROPERTIES ENVIRONMENT "${LSC_TEST_ENVIRONMENT}")
    endfunction()

    add_executable(test_platform_parser tests/test_platform_parser.cpp)
    target_link_libraries(test_platform_parser PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_platform_parser PRIVATE cxx_std_17)
    set_target_properties(test_platform_parser PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_platform_parser)

    add_executable(test_stream_capture tests/test_stream_capture.cpp)
    target_link_libraries(test_stream_capture PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_stream_capture PRIVATE cxx_std_17)
    set_target_properties(test_stream_capture PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_stream_capture)

    add_executable(test_recording_session tests/test_recording_session.cpp)
    target_link_libraries(test_recording_session PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_recording_session PRIVATE cxx_std_17)
    set_target_properties(test_recording_session PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_recording_session)

    add_executable(test_ssr_debug tests/test_ssr_debug.cpp)
    target_link_libraries(test_ssr_debug PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_ssr_debug PRIVATE cxx_std_17)
    set_target_properties(test_ssr_debug PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_ssr_debug)

    add_executable(test_audio_analyzer tests/test_audio_analyzer.cpp)
    target_link_libraries(test_audio_analyzer PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_audio_analyzer PRIVATE cxx_std_17)
    set_target_properties(test_audio_analyzer PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_audio_analyzer)

    add_executable(test_beat_detector tests/test_beat_detector.cpp)
    target_link_libraries(test_beat_detector PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_beat_detector PRIVATE cxx_std_17)
    set_target_properties(test_beat_detector PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_beat_detector)

    add_executable(test_video_analyzer tests/test_video_analyzer.cpp)
    target_link_libraries(test_video_analyzer PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_video_analyzer PRIVATE cxx_std_17)
    set_target_properties(test_video_analyzer PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_video_analyzer)

    add_executable(test_highlight_detector tests/test_highlight_detector.cpp)
    target_link_libraries(test_highlight_detector PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_highlight_detector PRIVATE cxx_std_17)
    set_target_properties(test_highlight_detector PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_highlight_detector)

    add_executable(test_highlight_engine tests/test_highlight_engine.cpp)
    target_link_libraries(test_highlight_engine PRIVATE lsc Qt6::Core Qt6::Network Qt6::Widgets)
    target_compile_features(test_highlight_engine PRIVATE cxx_std_17)
    set_target_properties(test_highlight_engine PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_highlight_engine)

    add_executable(test_dance_detector tests/test_dance_detector.cpp)
    target_link_libraries(test_dance_detector PRIVATE lsc Qt6::Core Qt6::Network Qt6::Widgets)
    target_compile_features(test_dance_detector PRIVATE cxx_std_17)
    set_target_properties(test_dance_detector PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_dance_detector)

    add_executable(test_analysis_dock tests/test_analysis_dock.cpp)
    target_link_libraries(test_analysis_dock PRIVATE lsc Qt6::Core Qt6::Network Qt6::Widgets Qt6::Test)
    target_compile_features(test_analysis_dock PRIVATE cxx_std_17)
    set_target_properties(test_analysis_dock PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_analysis_dock)

    add_executable(test_live_preview tests/test_live_preview.cpp)
    target_link_libraries(test_live_preview PRIVATE lsc Qt6::Core Qt6::Network Qt6::Test)
    target_compile_features(test_live_preview PRIVATE cxx_std_17)
    set_target_properties(test_live_preview PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_live_preview)

    add_executable(test_dance_segment_scorer tests/test_dance_segment_scorer.cpp)
    target_link_libraries(test_dance_segment_scorer PRIVATE lsc Qt6::Core Qt6::Network Qt6::Test)
    target_compile_features(test_dance_segment_scorer PRIVATE cxx_std_17)
    set_target_properties(test_dance_segment_scorer PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_dance_segment_scorer)

    add_executable(test_round_boundary_detector tests/test_round_boundary_detector.cpp)
    target_link_libraries(test_round_boundary_detector PRIVATE lsc Qt6::Core Qt6::Network Qt6::Test)
    target_compile_features(test_round_boundary_detector PRIVATE cxx_std_17)
    set_target_properties(test_round_boundary_detector PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_round_boundary_detector)

    add_executable(test_commentary_segmenter tests/test_commentary_segmenter.cpp)
    target_link_libraries(test_commentary_segmenter PRIVATE lsc Qt6::Core Qt6::Network Qt6::Test)
    target_compile_features(test_commentary_segmenter PRIVATE cxx_std_17)
    set_target_properties(test_commentary_segmenter PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_commentary_segmenter)

    add_executable(test_runtime_layout tests/test_runtime_layout.cpp)
    target_link_libraries(test_runtime_layout PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_runtime_layout PRIVATE cxx_std_17)
    set_target_properties(test_runtime_layout PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_runtime_layout)

    add_executable(test_task_center tests/test_task_center.cpp)
    target_link_libraries(test_task_center PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_task_center PRIVATE cxx_std_17)
    set_target_properties(test_task_center PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_task_center)

    add_executable(test_settings_dock tests/test_settings_dock.cpp)
    target_link_libraries(test_settings_dock PRIVATE lsc Qt6::Core Qt6::Network Qt6::Widgets)
    target_compile_features(test_settings_dock PRIVATE cxx_std_17)
    set_target_properties(test_settings_dock PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_settings_dock)

    add_executable(test_material_classifier tests/test_material_classifier.cpp)
    target_link_libraries(test_material_classifier PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_material_classifier PRIVATE cxx_std_17)
    set_target_properties(test_material_classifier PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_material_classifier)

    add_executable(test_highlight_ranker tests/test_highlight_ranker.cpp)
    target_link_libraries(test_highlight_ranker PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_highlight_ranker PRIVATE cxx_std_17)
    set_target_properties(test_highlight_ranker PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_highlight_ranker)

    add_executable(test_realtime_strategy tests/test_realtime_strategy.cpp)
    target_link_libraries(test_realtime_strategy PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_realtime_strategy PRIVATE cxx_std_17)
    set_target_properties(test_realtime_strategy PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_realtime_strategy)

    add_executable(test_round_clip_builder tests/test_round_clip_builder.cpp)
    target_link_libraries(test_round_clip_builder PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_round_clip_builder PRIVATE cxx_std_17)
    set_target_properties(test_round_clip_builder PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_round_clip_builder)

    add_executable(test_short_clip_refiner tests/test_short_clip_refiner.cpp)
    target_link_libraries(test_short_clip_refiner PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_short_clip_refiner PRIVATE cxx_std_17)
    set_target_properties(test_short_clip_refiner PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_short_clip_refiner)

    add_executable(test_feedback_store tests/test_feedback_store.cpp)
    target_link_libraries(test_feedback_store PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_feedback_store PRIVATE cxx_std_17)
    set_target_properties(test_feedback_store PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_feedback_store)

    add_executable(test_valorant_evaluation tests/test_valorant_evaluation.cpp)
    target_link_libraries(test_valorant_evaluation PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_valorant_evaluation PRIVATE cxx_std_17)
    set_target_properties(test_valorant_evaluation PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_valorant_evaluation)

    add_executable(test_gameplay_timeline tests/test_gameplay_timeline.cpp)
    target_link_libraries(test_gameplay_timeline PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_gameplay_timeline PRIVATE cxx_std_17)
    set_target_properties(test_gameplay_timeline PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_gameplay_timeline)

    add_executable(test_douyin_live tests/test_douyin_live.cpp)
    target_link_libraries(test_douyin_live PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_douyin_live PRIVATE cxx_std_17)
    set_target_properties(test_douyin_live PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)

    add_executable(test_live_recording tests/test_live_recording.cpp)
    target_link_libraries(test_live_recording PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_live_recording PRIVATE cxx_std_17)
    set_target_properties(test_live_recording PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)

    add_executable(test_highlight_analysis tests/test_highlight_analysis.cpp)
    target_link_libraries(test_highlight_analysis PRIVATE lsc Qt6::Core Qt6::Network Qt6::Widgets)
    target_compile_features(test_highlight_analysis PRIVATE cxx_std_17)
    set_target_properties(test_highlight_analysis PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)

    add_executable(test_clip_export tests/test_clip_export.cpp)
    target_link_libraries(test_clip_export PRIVATE lsc Qt6::Core Qt6::Network)
    target_compile_features(test_clip_export PRIVATE cxx_std_17)
    set_target_properties(test_clip_export PROPERTIES AUTOMOC ON WIN32_EXECUTABLE OFF QT_SKIP_SETUP_DEPLOYMENT ON)
    lsc_register_test(test_clip_export)

endif()

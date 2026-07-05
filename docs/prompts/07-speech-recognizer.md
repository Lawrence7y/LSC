# Task 7: Whisper 语音识别器

## 任务目标

集成 Whisper.cpp（OpenAI 开源语音识别模型的 C++ 移植），实现音频转文字字幕功能。

## 创建文件

- `src/lsc/analyzer/SpeechRecognizer.h`
- `src/lsc/analyzer/SpeechRecognizer.cpp`
- `third_party/whisper/CMakeLists.txt` — Whisper.cpp 依赖配置

## 前置条件

- Task 2 已完成（模块目录结构）

## Step 1: 创建 Whisper 依赖配置

创建 `third_party/whisper/CMakeLists.txt`：

```cmake
cmake_minimum_required(VERSION 3.16)

include(FetchContent)

FetchContent_Declare(
    whisper
    GIT_REPOSITORY https://github.com/ggerganov/whisper.cpp.git
    GIT_TAG master
    GIT_SHALLOW ON
)

set(WHISPER_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(WHISPER_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF)

FetchContent_MakeAvailable(whisper)
```

## Step 2: SpeechRecognizer.h

```cpp
#ifndef SPEECHRECOGNIZER_H
#define SPEECHRECOGNIZER_H

#include <QObject>
#include <QList>
#include <QString>

struct TranscriptionResult {
    int startMs;
    int endMs;
    QString text;
    float confidence;
};

class SpeechRecognizer : public QObject
{
    Q_OBJECT
public:
    explicit SpeechRecognizer(QObject* parent = nullptr);
    ~SpeechRecognizer();

    bool loadModel(const QString& modelPath);
    void transcribe(const QString& audioPath);
    bool isBusy() const { return m_busy; }
    void cancel();

signals:
    void modelLoaded(bool success);
    void progressChanged(int percent);
    void transcriptionReady(const QList<TranscriptionResult>& results);
    void errorOccurred(const QString& error);

private:
    void* m_whisperCtx;     // whisper_context* — C API handle
    bool m_busy = false;
    bool m_cancelRequested = false;
};

#endif
```

## Step 3: SpeechRecognizer.cpp

```cpp
#include "SpeechRecognizer.h"
#include <QFileInfo>
#include <QDebug>

// 当 whisper.cpp 编译链接后可取消注释:
// #include "whisper.h"

SpeechRecognizer::SpeechRecognizer(QObject* parent)
    : QObject(parent), m_whisperCtx(nullptr) {}

SpeechRecognizer::~SpeechRecognizer()
{
    cancel();
    // whisper_free(static_cast<whisper_context*>(m_whisperCtx));
}

bool SpeechRecognizer::loadModel(const QString& modelPath)
{
    QFileInfo fi(modelPath);
    if (!fi.exists()) {
        emit errorOccurred("模型文件不存在: " + modelPath);
        return false;
    }

    // 实际集成代码（需 whisper.cpp 编译链接）:
    // m_whisperCtx = whisper_init_from_file(
    //     modelPath.toUtf8().constData());
    // if (!m_whisperCtx) {
    //     emit errorOccurred("Whisper初始化失败");
    //     return false;
    // }

    emit modelLoaded(true);
    return true;
}

void SpeechRecognizer::transcribe(const QString& audioPath)
{
    if (m_busy) {
        emit errorOccurred("已经在处理中");
        return;
    }

    m_busy = true;
    m_cancelRequested = false;

    emit progressChanged(0);

    // 实际处理流程：
    // 1. 用FFmpeg将输入音频转为16kHz单声道WAV
    // 2. 读取WAV采样数据到 std::vector<float> pcmf32
    // 3. whisper_full_params params =
    //        whisper_full_default_params(WHISPER_SAMPLING_GREEDY);
    // 4. whisper_full(ctx, params, pcmf32.data(), pcmf32.size())
    // 5. 迭代获取各段落时间戳和文本

    QList<TranscriptionResult> results;
    // 示例结果（实际使用时替换为 whisper 输出）
    results.append({0, 2500, "大家好", 0.95f});
    results.append({2500, 5000, "欢迎来到直播间", 0.92f});

    emit progressChanged(100);
    m_busy = false;
    emit transcriptionReady(results);
}

void SpeechRecognizer::cancel()
{
    m_cancelRequested = true;
}
```

## Step 4: 下载模型文件

```bash
# 下载中文 whisper 模型 (small 约 466MB)
mkdir -p models
curl -L -o models/ggml-small.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
```

## 验证

```bash
cd shotcut-source/build
cmake --build . --config Release
```

预期：编译通过。模型加载后在运行时通过 `loadModel()` 指定模型路径即可。

## 说明

当 `whisper.cpp` 成功编译链接后，取消注释文件中的实际集成代码。标注释的代码展示了完整的 whisper.cpp C API 调用方式。

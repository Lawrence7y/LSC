const sharp = require('sharp');
const ico = require('sharp-ico');
const fs = require('fs');
const path = require('path');

// 源图片路径 (用户提供的最新霓虹灯图标)
const inputPath = 'C:\\Users\\Administrator\\AppData\\Roaming\\QoderCN\\SharedClientCache\\cache\\images\\task-10c\\ChatGPT Image 2026年6月27日 17_29_52-4755928e.png';
// 目标 .ico 路径
const outputPath = path.join(__dirname, 'assets', 'icon.ico');
// 目标 PNG 路径 (用于 React 侧边栏，带透明背景)
const outputPngPath = path.join(__dirname, 'public', 'assets', 'logo.png');

async function process() {
    try {
        // 1. 处理图片：将黑色/深色背景转为透明，保留青色发光部分
        // 使用 sharp 的 ensureAlpha 和 composite 或者简单的阈值处理来提取前景
        // 由于是霓虹灯效果，背景是纯黑或极深色，我们可以尝试通过亮度阈值来提取
        
        const image = sharp(inputPath);
        const metadata = await image.metadata();
        
        // 提取并转换
        const pngBuffer = await image
            .resize(256, 256, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } }) // 确保尺寸并设置透明背景
            .toFormat('png')
            .toBuffer();

        // 保存 PNG 用于 React 侧边栏
        // 注意：如果原图背景不是纯透明，这里可能需要更复杂的处理（如 removeAlpha 或 threshold）
        // 但 sharp 默认会保留原图的 alpha 通道。如果原图没有 alpha，我们需要手动处理。
        // 假设原图是 RGB，我们需要把接近黑色的像素变透明。
        
        // 更稳健的处理：使用 raw 数据或 composite。
        // 简单方案：直接保存，如果背景不透明，前端可以通过 CSS mix-blend-mode 或 filter 处理，
        // 或者在这里使用 sharp 的 threshold 和 boolean 操作来创建 mask。
        
        // 让我们尝试一个简单的“去黑底”处理：
        // 将图片转换为 RGBA，然后将所有 R<30, G<30, B<30 的像素 Alpha 设为 0
        const processedPngBuffer = await sharp(inputPath)
            .resize(256, 256) // ICO 格式要求最大 256x256
            .ensureAlpha()
            .raw()
            .toBuffer({ resolveWithObject: true })
            .then(({ data, info }) => {
                const pixels = new Uint8ClampedArray(data);
                for (let i = 0; i < pixels.length; i += 4) {
                    const r = pixels[i];
                    const g = pixels[i + 1];
                    const b = pixels[i + 2];
                    // 如果像素非常暗（接近黑色），则将其设为透明
                    if (r < 40 && g < 40 && b < 40) {
                        pixels[i + 3] = 0; // Alpha = 0
                    } else {
                        pixels[i + 3] = 255; // Alpha = 255 (保持不透明)
                    }
                }
                return sharp(pixels, {
                    raw: { width: info.width, height: info.height, channels: 4 }
                }).png().toBuffer();
            });

        fs.writeFileSync(outputPngPath, processedPngBuffer);
        console.log('Processed PNG logo generated at:', outputPngPath);

        // 2. 转换为 ICO 格式
        // 对于托盘图标，通常也需要透明背景。ICO 格式支持透明度。
        const icoBuffer = await ico.encode([processedPngBuffer]);
        fs.writeFileSync(outputPath, icoBuffer);
        console.log('ICO icon generated at:', outputPath);
        
    } catch (error) {
        console.error('Error processing icon:', error);
    }
}

process();

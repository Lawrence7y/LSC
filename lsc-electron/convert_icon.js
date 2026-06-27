const sharp = require('sharp');
const ico = require('sharp-ico');
const fs = require('fs');
const path = require('path');

// 源图片路径 (用户提供的图片)
const inputPath = 'C:\\Users\\Administrator\\AppData\\Roaming\\QoderCN\\SharedClientCache\\cache\\images\\task-10c\\cd2a0eea-d9f3-4dcd-a0b6-a9340ec5893a-9e7d88c9.jpg';
// 目标 .ico 路径
const outputPath = path.join(__dirname, 'assets', 'icon.ico');
// 目标 PNG 路径 (用于 React 侧边栏)
const outputPngPath = path.join(__dirname, 'assets', 'logo.png');

async function convert() {
    try {
        // 读取并转换图片为 PNG buffer
        const pngBuffer = await sharp(inputPath)
            .resize(256, 256)
            .png()
            .toBuffer();

        // 保存 PNG 用于 React 侧边栏
        fs.writeFileSync(outputPngPath, pngBuffer);
        console.log('PNG logo generated at:', outputPngPath);

        // 转换为 ICO 格式 (包含多种尺寸以兼容 Windows 托盘和窗口图标)
        const icoBuffer = await ico.encode([pngBuffer]);
        fs.writeFileSync(outputPath, icoBuffer);
        console.log('ICO icon generated at:', outputPath);
        
    } catch (error) {
        console.error('Error converting icon:', error);
    }
}

convert();

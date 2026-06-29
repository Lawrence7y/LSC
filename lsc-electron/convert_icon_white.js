const sharp = require('sharp');
const ico = require('sharp-ico');
const fs = require('fs');
const path = require('path');

const inputPng = path.join(__dirname, '..', 'extracted_icon.png');
const outputIco = path.join(__dirname, 'assets', 'icon.ico');
const outputPng = path.join(__dirname, 'assets', 'logo.png');
const outputPublicPng = path.join(__dirname, 'public', 'assets', 'logo.png');

async function createIcon() {
    try {
        // Get original image metadata
        const meta = await sharp(inputPng).metadata();
        console.log('Original size:', meta.width, 'x', meta.height);

        // Create white background version
        const whiteBgBuffers = [];
        const sizes = [16, 32, 48, 64, 128, 256];

        for (const size of sizes) {
            // Resize the icon, then composite onto white background
            const resized = await sharp(inputPng)
                .resize(size, size, { fit: 'contain' })
                .toBuffer();

            // Create white background and composite icon on top
            const withBg = await sharp({
                create: {
                    width: size,
                    height: size,
                    channels: 3,
                    background: { r: 255, g: 255, b: 255 }
                }
            })
                .composite([{ input: resized, top: 0, left: 0 }])
                .png()
                .toBuffer();

            whiteBgBuffers.push(withBg);
            console.log(`  Added size: ${size}x${size} (white bg)`);
        }

        // Generate ICO
        const icoBuffer = await ico.encode(whiteBgBuffers);
        fs.writeFileSync(outputIco, icoBuffer);
        console.log('Generated ICO:', outputIco);

        // Generate 256x256 PNG for logo (white background)
        const logoPng = await sharp({
            create: {
                width: 256,
                height: 256,
                channels: 3,
                background: { r: 255, g: 255, b: 255 }
            }
        })
            .composite([{
                input: await sharp(inputPng).resize(256, 256, { fit: 'contain' }).toBuffer(),
                top: 0,
                left: 0
            }])
            .png()
            .toBuffer();

        fs.writeFileSync(outputPng, logoPng);
        console.log('Generated logo PNG:', outputPng);

        // Also update public/assets/logo.png
        fs.mkdirSync(path.dirname(outputPublicPng), { recursive: true });
        fs.writeFileSync(outputPublicPng, logoPng);
        console.log('Generated public logo PNG:', outputPublicPng);

        console.log('\nDone!');
    } catch (error) {
        console.error('Error:', error);
        process.exit(1);
    }
}

createIcon();

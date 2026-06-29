const sharp = require('sharp');
const ico = require('sharp-ico');
const fs = require('fs');
const path = require('path');

// Input and output paths
const inputPng = path.join(__dirname, '..', 'extracted_icon.png');
const outputIco = path.join(__dirname, 'assets', 'icon.ico');
const outputPng = path.join(__dirname, 'assets', 'logo.png');

console.log('Reading icon:', inputPng);

// Read the PNG image
const pngBuffer = fs.readFileSync(inputPng);

// Define sizes for ICO (including 256x256)
const sizes = [16, 32, 48, 64, 128, 256];

async function createIcon() {
    try {
        // Create resized images for each size
        const buffers = [];
        
        for (const size of sizes) {
            const resized = await sharp(pngBuffer)
                .resize(size, size, {
                    fit: 'contain',
                    background: { r: 0, g: 0, b: 0, alpha: 0 }
                })
                .png()
                .toBuffer();
            
            buffers.push(resized);
            console.log(`  Added size: ${size}x${size}`);
        }
        
        // Convert to ICO format using sharp-ico
        const icoBuffer = await ico.encode(buffers);
        
        // Save ICO file
        fs.writeFileSync(outputIco, icoBuffer);
        console.log('\nGenerated ICO file:', outputIco);
        
        // Also save PNG version
        fs.copyFileSync(inputPng, outputPng);
        console.log('Generated PNG file:', outputPng);
        
        console.log('\nDone!');
    } catch (error) {
        console.error('Error creating icon:', error);
        process.exit(1);
    }
}

createIcon();

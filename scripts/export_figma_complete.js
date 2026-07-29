const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

async function exportAllPages() {
  const browser = await puppeteer.launch({
    executablePath: EDGE_PATH,
    headless: 'new',
    args: ['--no-sandbox', '--window-size=1920,1080']
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080 });

  const serializerScript = () => {
    function escapeXml(unsafe) {
      if (!unsafe) return '';
      return String(unsafe).replace(/[<>&'"]/g, function (c) {
        switch (c) {
          case '<': return '&lt;';
          case '>': return '&gt;';
          case '&': return '&amp;';
          case '\'': return '&apos;';
          case '"': return '&quot;';
        }
      });
    }

    const width = window.innerWidth || 1920;
    const height = window.innerHeight || 1080;

    let svgElements = [];

    function processNode(node) {
      if (!node || node.nodeType !== 1) return;
      
      const style = window.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return;

      const rect = node.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;

      // Layer name
      let layerName = node.id || node.getAttribute('data-page') || node.className || node.tagName.toLowerCase();
      if (typeof layerName === 'string') {
        layerName = layerName.trim().replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 30);
      } else {
        layerName = node.tagName.toLowerCase();
      }

      // Check background
      const bgColor = style.backgroundColor;
      const hasBg = bgColor && bgColor !== 'transparent' && bgColor !== 'rgba(0, 0, 0, 0)';
      
      // Check border
      const borderWidth = parseFloat(style.borderWidth) || 0;
      const borderColor = style.borderColor;
      const hasBorder = borderWidth > 0 && borderColor && borderColor !== 'transparent' && borderColor !== 'rgba(0, 0, 0, 0)';

      // Check border-radius
      const borderRadius = parseFloat(style.borderRadius) || 0;

      // Render background rectangle
      if (hasBg || hasBorder) {
        let fillAttr = hasBg ? bgColor : 'none';
        let strokeAttr = hasBorder ? `stroke="${borderColor}" stroke-width="${borderWidth}"` : '';
        let rxAttr = borderRadius > 0 ? `rx="${Math.min(borderRadius, rect.width/2, rect.height/2)}"` : '';

        svgElements.push(
          `<rect id="rect_${layerName}" x="${rect.left.toFixed(2)}" y="${rect.top.toFixed(2)}" width="${rect.width.toFixed(2)}" height="${rect.height.toFixed(2)}" fill="${fillAttr}" ${strokeAttr} ${rxAttr} />`
        );
      }

      // Render Inline SVG icons
      if (node.tagName.toLowerCase() === 'svg') {
        const svgContent = node.outerHTML
          .replace(/<svg[^>]*>/, `<g id="icon_${layerName}" transform="translate(${rect.left.toFixed(2)}, ${rect.top.toFixed(2)})">`)
          .replace(/<\/svg>/, '</g>');
        svgElements.push(svgContent);
        return;
      }

      // Render Text Content
      for (let child of node.childNodes) {
        if (child.nodeType === 3 && child.textContent.trim().length > 0) {
          const textContent = escapeXml(child.textContent.trim());
          const fontSize = parseFloat(style.fontSize) || 12;
          const fontWeight = style.fontWeight || 'normal';
          const fontFamily = escapeXml(style.fontFamily.replace(/"/g, ''));
          const color = style.color || '#f2f4f5';
          
          const textY = (rect.top + rect.height / 2 + fontSize * 0.35).toFixed(2);
          let textX = rect.left.toFixed(2);
          let anchor = 'start';
          if (style.textAlign === 'center') {
            textX = (rect.left + rect.width / 2).toFixed(2);
            anchor = 'middle';
          } else if (style.textAlign === 'right') {
            textX = (rect.left + rect.width - 4).toFixed(2);
            anchor = 'end';
          } else {
            textX = (rect.left + 4).toFixed(2);
          }

          svgElements.push(
            `<text id="text_${layerName}" x="${textX}" y="${textY}" fill="${color}" font-size="${fontSize}" font-weight="${fontWeight}" font-family="${fontFamily}" text-anchor="${anchor}">${textContent}</text>`
          );
        }
      }

      // Process children
      for (let child of node.children) {
        processNode(child);
      }
    }

    const root = document.querySelector('.app') || document.body;
    processNode(root);

    // Also process overlays (settings drawer, modals)
    const overlays = document.querySelectorAll('.settings-overlay.open, .modal-overlay.open');
    overlays.forEach(overlay => processNode(overlay));

    return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" style="background-color: #0b0d0f;">\n` +
      `  <g id="Layer_LSC_Full_UI">\n` +
      svgElements.map(e => '    ' + e).join('\n') +
      `\n  </g>\n</svg>`;
  };

  const outputDir = path.resolve(__dirname, '../figma-export');
  if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });

  const htmlPath = path.resolve(__dirname, '../lsc-ui-design/ui-v6-complete-prototype.html');
  await page.goto('file://' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'networkidle0' });

  // 1. Export Workbench (hide settings & modals)
  await page.evaluate(() => {
    document.getElementById('settingsPage').classList.remove('open');
    document.getElementById('modal-analysis').classList.remove('open');
    document.getElementById('modal-export').classList.remove('open');
    document.getElementById('modal-jianying').classList.remove('open');
  });
  await new Promise(r => setTimeout(r, 200));
  const wbSVG = await page.evaluate(serializerScript);
  fs.writeFileSync(path.join(outputDir, '01_Workbench_Complete.svg'), wbSVG);
  console.log('Saved: 01_Workbench_Complete.svg');

  // 2. Export Settings Drawer
  await page.evaluate(() => {
    document.getElementById('settingsPage').classList.add('open');
  });
  await new Promise(r => setTimeout(r, 200));
  const settingsSVG = await page.evaluate(serializerScript);
  fs.writeFileSync(path.join(outputDir, '02_Settings_Drawer.svg'), settingsSVG);
  console.log('Saved: 02_Settings_Drawer.svg');

  // 3. Export Analysis Modal
  await page.evaluate(() => {
    document.getElementById('settingsPage').classList.remove('open');
    document.getElementById('modal-analysis').classList.add('open');
  });
  await new Promise(r => setTimeout(r, 200));
  const analysisSVG = await page.evaluate(serializerScript);
  fs.writeFileSync(path.join(outputDir, '03_Analysis_Modal.svg'), analysisSVG);
  console.log('Saved: 03_Analysis_Modal.svg');

  // 4. Export Export Modal
  await page.evaluate(() => {
    document.getElementById('modal-analysis').classList.remove('open');
    document.getElementById('modal-export').classList.add('open');
  });
  await new Promise(r => setTimeout(r, 200));
  const exportSVG = await page.evaluate(serializerScript);
  fs.writeFileSync(path.join(outputDir, '04_Export_Modal.svg'), exportSVG);
  console.log('Saved: 04_Export_Modal.svg');

  // 5. Export Jianying Draft Modal
  await page.evaluate(() => {
    document.getElementById('modal-export').classList.remove('open');
    document.getElementById('modal-jianying').classList.add('open');
  });
  await new Promise(r => setTimeout(r, 200));
  const jianyingSVG = await page.evaluate(serializerScript);
  fs.writeFileSync(path.join(outputDir, '05_Jianying_Modal.svg'), jianyingSVG);
  console.log('Saved: 05_Jianying_Modal.svg');

  // 6. Full composite (workbench + settings open)
  await page.evaluate(() => {
    document.getElementById('modal-jianying').classList.remove('open');
    document.getElementById('settingsPage').classList.add('open');
  });
  await new Promise(r => setTimeout(r, 200));
  const fullSVG = await page.evaluate(serializerScript);
  fs.writeFileSync(path.join(outputDir, '06_Full_Composite.svg'), fullSVG);
  console.log('Saved: 06_Full_Composite.svg');

  await browser.close();
  console.log('\n✅ All 6 full-fidelity UI SVG snapshots exported!');
  console.log('   Import to Figma: drag SVG files into canvas, Ctrl+Shift+G to ungroup.');
}

exportAllPages().catch(err => {
  console.error('Export error:', err);
  process.exit(1);
});

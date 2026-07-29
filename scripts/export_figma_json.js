const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const EDGE_PATH = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';

async function generateFigmaJSON() {
  const browser = await puppeteer.launch({
    executablePath: EDGE_PATH,
    headless: 'new',
    args: ['--no-sandbox', '--window-size=1920,1080']
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080 });

  const htmlPath = path.resolve(__dirname, '../lsc-ui-design/ui-v6-complete-prototype.html');
  await page.goto('file://' + htmlPath.replace(/\\/g, '/'), { waitUntil: 'networkidle0' });

  // Hide overlays for clean workbench capture
  await page.evaluate(() => {
    document.getElementById('settingsPage').classList.remove('open');
    document.getElementById('modal-analysis').classList.remove('open');
    document.getElementById('modal-export').classList.remove('open');
    document.getElementById('modal-jianying').classList.remove('open');
  });
  await new Promise(r => setTimeout(r, 200));

  // Convert DOM to Figma JSON nodes
  const domToFigmaNodes = () => {
    function parseRgb(colorStr) {
      if (!colorStr || colorStr === 'transparent') return null;
      const m = colorStr.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
      if (!m) return null;
      return {
        r: parseInt(m[1]) / 255,
        g: parseInt(m[2]) / 255,
        b: parseInt(m[3]) / 255,
        a: m[4] !== undefined ? parseFloat(m[4]) : 1
      };
    }

    function buildNode(el) {
      if (!el || el.nodeType !== 1) return null;
      const style = window.getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return null;

      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return null;

      const name = el.id || (el.className && typeof el.className === 'string' ? el.className.split(' ')[0] : el.tagName.toLowerCase());

      const bg = parseRgb(style.backgroundColor);
      const border = parseRgb(style.borderColor);
      const borderWidth = parseFloat(style.borderWidth) || 0;
      const borderRadius = parseFloat(style.borderRadius) || 0;

      let figmaNode = {
        id: name + '_' + Math.random().toString(36).substr(2, 6),
        name: name,
        type: 'FRAME',
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        cornerRadius: borderRadius,
        fills: bg ? [{ type: 'SOLID', color: { r: bg.r, g: bg.g, b: bg.b }, opacity: bg.a }] : [],
        strokes: border && borderWidth > 0 ? [{ type: 'SOLID', color: { r: border.r, g: border.g, b: border.b }, opacity: border.a }] : [],
        strokeWeight: borderWidth,
        children: []
      };

      // Check text children
      let hasText = false;
      for (let ch of el.childNodes) {
        if (ch.nodeType === 3 && ch.textContent.trim()) {
          const txtColor = parseRgb(style.color) || { r: 0.95, g: 0.95, b: 0.95, a: 1 };
          figmaNode.children.push({
            id: 'txt_' + Math.random().toString(36).substr(2, 6),
            name: 'Text: ' + ch.textContent.trim().substr(0, 15),
            type: 'TEXT',
            x: Math.round(rect.left + 4),
            y: Math.round(rect.top + 4),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            characters: ch.textContent.trim(),
            fontSize: parseFloat(style.fontSize) || 12,
            fontFamily: style.fontFamily.split(',')[0].replace(/"/g, ''),
            fontWeight: style.fontWeight,
            fills: [{ type: 'SOLID', color: { r: txtColor.r, g: txtColor.g, b: txtColor.b }, opacity: txtColor.a }]
          });
          hasText = true;
        }
      }

      // Process children
      for (let child of el.children) {
        const childNode = buildNode(child);
        if (childNode) figmaNode.children.push(childNode);
      }

      return figmaNode;
    }

    const appRoot = document.querySelector('.app') || document.body;
    return buildNode(appRoot);
  };

  const figmaTree = await page.evaluate(domToFigmaNodes);

  const outputDir = path.resolve(__dirname, '../figma-export');
  fs.writeFileSync(path.join(outputDir, '01_Workbench_Complete.figma.json'), JSON.stringify(figmaTree, null, 2));
  console.log('Exported: 01_Workbench_Complete.figma.json');

  await browser.close();
}

generateFigmaJSON().catch(err => {
  console.error(err);
});

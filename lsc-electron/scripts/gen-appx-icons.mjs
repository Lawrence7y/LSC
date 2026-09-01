// LSC Live Stream Clipper — Microsoft Store tile generator (policy 10.1.1.11)
//
// Renders assets/logo*.svg with resvg (not GDI+), then composites onto
// opaque black tiles / transparent unplated icons.
// Output: build/appx/*.png  consumed by electron-builder as-is.
//
// Usage: node scripts/gen-appx-icons.mjs

import { createRequire } from "module"
import fs from "fs"
import path from "path"
import { fileURLToPath } from "url"

const require = createRequire(import.meta.url)
const sharp = require("sharp")
const { Resvg } = require("@resvg/resvg-js")
const sharpIco = require("sharp-ico")

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const appDir = path.resolve(__dirname, "..")
const assetsDir = path.join(appDir, "assets")
const outDir = path.join(appDir, "build", "appx")

const BLACK = { r: 0, g: 0, b: 0, alpha: 1 }
const TRANSPARENT = { r: 0, g: 0, b: 0, alpha: 0 }
const MASTER_PX = 2048
const SCALES = [100, 125, 150, 200, 400]
const TARGET_SIZES = [16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256]

function pickVariant(px) {
  if (px <= 32) return "micro"
  if (px <= 96) return "simple"
  return "full"
}

function contentBBox(pixels, width, height) {
  let minX = width
  let minY = height
  let maxX = -1
  let maxY = -1
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4
      const a = pixels[i + 3]
      const lum = pixels[i] + pixels[i + 1] + pixels[i + 2]
      if (a > 24 && lum > 24) {
        if (x < minX) minX = x
        if (y < minY) minY = y
        if (x > maxX) maxX = x
        if (y > maxY) maxY = y
      }
    }
  }
  if (maxX < 0) {
    throw new Error("Rendered SVG is empty")
  }
  return { left: minX, top: minY, width: maxX - minX + 1, height: maxY - minY + 1 }
}

function renderMaster(svgText) {
  const resvg = new Resvg(svgText, {
    fitTo: { mode: "width", value: MASTER_PX },
    background: "rgba(0,0,0,0)",
  })
  const img = resvg.render()
  const pixels = Buffer.from(img.pixels)
  const width = img.width
  const height = img.height
  const bbox = contentBBox(pixels, width, height)
  return { pixels, width, height, bbox }
}

async function placeMark(master, destW, destH, pad, background) {
  const innerW = Math.max(1, Math.round(destW * (1 - pad.l - pad.r)))
  const innerH = Math.max(1, Math.round(destH * (1 - pad.t - pad.b)))
  const extracted = await sharp(master.pixels, {
    raw: { width: master.width, height: master.height, channels: 4 },
  })
    .extract(master.bbox)
    .resize({
      width: innerW,
      height: innerH,
      fit: "contain",
      kernel: "lanczos3",
      background: TRANSPARENT,
    })
    .png()
    .toBuffer({ resolveWithObject: true })

  const left = Math.round(destW * pad.l + (innerW - extracted.info.width) / 2)
  const top = Math.round(destH * pad.t + (innerH - extracted.info.height) / 2)
  const canvas = sharp({
    create: {
      width: destW,
      height: destH,
      channels: 4,
      background,
    },
  })
  return canvas
    .composite([{ input: extracted.data, left, top }])
    .png({ compressionLevel: 9, adaptiveFiltering: true })
    .toBuffer()
}

function padAll(p) {
  return { t: p, r: p, b: p, l: p }
}

async function writePng(buf, name) {
  const dest = path.join(outDir, name)
  fs.writeFileSync(dest, buf)
  const meta = await sharp(buf).metadata()
  process.stdout.write(`  ${name}  ${meta.width}x${meta.height}  ${buf.length}B\n`)
  return dest
}

function scaledName(baseName, scale) {
  if (scale === 100) return baseName
  return baseName.replace(/\.png$/, `.scale-${scale}.png`)
}

async function writeScaledSet(baseName, baseW, baseH, variantForPx, padForPx, background) {
  for (const scale of SCALES) {
    const w = Math.round((baseW * scale) / 100)
    const h = Math.round((baseH * scale) / 100)
    const master = variantForPx(Math.min(w, h))
    const pad = padForPx(Math.min(w, h), w, h)
    const buf = await placeMark(master, w, h, pad, background)
    await writePng(buf, scaledName(baseName, scale))
  }
}

function loadSvg(name) {
  const text = fs.readFileSync(path.join(assetsDir, name), "utf8")
  return text.replace(/<!--[\s\S]*?-->/g, "")
}

async function main() {
  fs.mkdirSync(outDir, { recursive: true })
  for (const stale of fs.readdirSync(outDir)) {
    if (stale.endsWith(".png")) fs.unlinkSync(path.join(outDir, stale))
  }

  const svgs = {
    full: loadSvg("logo.svg"),
    simple: loadSvg("logo-simple.svg"),
    micro: loadSvg("logo-micro.svg"),
  }
  process.stdout.write(`Rendering SVG masters at ${MASTER_PX}px via resvg…\n`)
  const masters = {
    full: renderMaster(svgs.full),
    simple: renderMaster(svgs.simple),
    micro: renderMaster(svgs.micro),
  }

  const variant = (px) => masters[pickVariant(px)]
  const tilePad = () => padAll(0.12)
  const namedSquarePad = () => ({ t: 0.1, r: 0.12, b: 0.32, l: 0.12 })
  const namedWidePad = () => ({ t: 0.1, r: 0.1, b: 0.3, l: 0.1 })
  const storePad = () => padAll(0.1)

  process.stdout.write("Writing Store tile assets (opaque black, 100-400%)…\n")
  await writeScaledSet("Square44x44Logo.png", 44, 44, variant, tilePad, BLACK)
  await writeScaledSet("Square71x71Logo.png", 71, 71, variant, tilePad, BLACK)
  await writeScaledSet("SmallTile.png", 71, 71, variant, tilePad, BLACK)
  await writeScaledSet("Square150x150Logo.png", 150, 150, variant, namedSquarePad, BLACK)
  await writeScaledSet("Wide310x150Logo.png", 310, 150, variant, namedWidePad, BLACK)
  await writeScaledSet("Square310x310Logo.png", 310, 310, variant, tilePad, BLACK)
  await writeScaledSet("LargeTile.png", 310, 310, variant, tilePad, BLACK)
  await writeScaledSet("StoreLogo.png", 50, 50, variant, storePad, BLACK)
  await writeScaledSet("SplashScreen.png", 620, 300, variant, namedWidePad, BLACK)

  process.stdout.write("Writing Square44x44Logo targetsize (taskbar / Start pin)…\n")
  for (const size of TARGET_SIZES) {
    const master = variant(size)
    const pad = size <= 32 ? padAll(0.06) : padAll(0.08)
    const plated = await placeMark(master, size, size, pad, BLACK)
    const unplated = await placeMark(master, size, size, pad, TRANSPARENT)
    await writePng(plated, `Square44x44Logo.targetsize-${size}.png`)
    await writePng(unplated, `Square44x44Logo.targetsize-${size}_altform-unplated.png`)
    await writePng(unplated, `Square44x44Logo.targetsize-${size}_altform-lightunplated.png`)
  }

  process.stdout.write("Writing multi-resolution assets/icon.ico…\n")
  const icoSizes = [16, 24, 32, 48, 256]
  const icoImages = []
  for (const size of icoSizes) {
    const buf = await placeMark(variant(size), size, size, padAll(0.08), TRANSPARENT)
    icoImages.push(sharp(buf))
  }
  const icoPath = path.join(assetsDir, "icon.ico")
  await sharpIco.sharpsToIco(icoImages, icoPath)
  process.stdout.write(`  ${icoPath}\n`)

  const count = fs.readdirSync(outDir).filter((n) => n.endsWith(".png")).length
  process.stdout.write(`Generated ${count} PNGs in ${outDir}\n`)
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})

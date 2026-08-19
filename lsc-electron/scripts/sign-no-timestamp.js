/**
 * 自定义 Windows 签名脚本（electron-builder win.sign）。
 *
 * 两个目的：
 * 1. 跳过时间戳服务器（本机网络访问不到 digicert/sectigo 等 RFC3161 服务器，
 *    默认签名会因 "The specified timestamp server either could not be reached" 失败）。
 *    不带时间戳的签名在证书有效期内仍有效；Store 提交后微软会重新签名并补时间戳。
 * 2. 优先使用系统 Windows SDK 的 signtool：electron-builder 缓存版 signtool
 *    （winCodeSign）在部分 Windows 11 版本上签名 .appx/.msix 时报
 *    "SignTool Error: A required function is not present"（exe 签名正常，
 *    仅 AppX/MSIX 容器签名失败）；Windows SDK 自带的 signtool 无此问题。
 *
 * 证书与密码通过环境变量传入（由 build-msix.ps1 设置）：
 *   LSC_CERT_FILE / LSC_CERT_PASSWORD
 */
const { execFileSync } = require('child_process')
const fs = require('fs')
const path = require('path')
const os = require('os')

/** 查找系统 Windows SDK 的 signtool.exe（取最高版本），找不到返回 null */
function findSdkSigntool() {
  const roots = [
    path.join(process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Windows Kits', '10', 'bin'),
    path.join(process.env.ProgramFiles || 'C:\\Program Files', 'Windows Kits', '10', 'bin'),
  ]
  let best = null
  for (const root of roots) {
    if (!fs.existsSync(root)) continue
    for (const ver of fs.readdirSync(root).sort().reverse()) {
      const candidates = [
        path.join(root, ver, 'x64', 'signtool.exe'),
        path.join(root, ver, 'amd64', 'signtool.exe'),
      ]
      for (const c of candidates) {
        if (fs.existsSync(c)) {
          best = c
          break
        }
      }
      if (best) break
    }
    if (best) break
  }
  return best
}

/** 查找 electron-builder 缓存的 signtool.exe（winCodeSign 包） */
function findCachedSigntool() {
  const cacheDir = path.join(os.homedir(), 'AppData', 'Local', 'electron-builder', 'Cache', 'winCodeSign')
  if (!fs.existsSync(cacheDir)) return null
  const arch = process.arch === 'x64' ? 'x64' : 'ia32'
  for (const v of fs.readdirSync(cacheDir).sort().reverse()) {
    const p = path.join(cacheDir, v, 'windows-10', arch, 'signtool.exe')
    if (fs.existsSync(p)) return p
  }
  return null
}

module.exports = async function (config) {
  const file = config.path
  const cert = process.env.LSC_CERT_FILE
  const password = process.env.LSC_CERT_PASSWORD
  if (!cert || !fs.existsSync(cert)) {
    throw new Error(`sign-no-timestamp: certificate not found (LSC_CERT_FILE=${cert})`)
  }

  const signtool = findSdkSigntool() || findCachedSigntool()
  if (!signtool) {
    throw new Error('sign-no-timestamp: no signtool.exe found (Windows SDK or electron-builder cache)')
  }

  const args = ['sign', '/fd', 'sha256', '/f', cert]
  if (password) args.push('/p', password)
  args.push(file)
  execFileSync(signtool, args, { stdio: 'inherit' })
}

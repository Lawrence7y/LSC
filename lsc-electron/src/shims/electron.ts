// Electron API shim for Vite build.
// At build time, this module is resolved by Vite as a virtual module.
// At runtime inside Electron, it returns the real Electron API.
// Outside Electron (e.g., testing), it returns empty stubs.

// Detect Electron runtime
const isElectron = typeof process !== 'undefined' && 
  process.versions && 
  !!process.versions.electron;

let electronModule: any;

if (isElectron) {
  // Running inside Electron. Use a hack to get the real API.
  // The issue: require("electron") resolves to node_modules/electron/index.js
  // (which exports a string path to the exe) instead of Electron's built-in API.
  //
  // In Electron 28, the "electron" module is loaded via the internal JS2C system
  // and is not a standard Node.js builtin. The module resolution finds
  // node_modules/electron first.
  //
  // Fix: temporarily rename node_modules/electron/package.json to prevent
  // Node.js from resolving it, then call require("electron") to get the
  // real API from Electron's internal module system.
  const Module = require('module') as any;
  const electronPkgPath = require.resolve('electron/package.json');
  const electronIndexPath = require.resolve('electron');
  
  // Mark these as already-loaded to prevent re-resolution
  // The trick: we put the REAL electron API into the cache under the
  // key where the npm package would be. But we first need to GET the real API.
  
  // Strategy: temporarily rename package.json so the directory isn't recognized
  // as an npm package, then require electron will fall through to Electron's builtin
  const { renameSync } = require('fs') as typeof import('fs');
  const pkgBak = electronPkgPath + '.bak';
  const mainBak = electronIndexPath + '.bak';
  
  try {
    // 步骤 1: 重命名 package.json 和 index.js，使 node_modules 中的 electron 包不可解析
    renameSync(electronPkgPath, pkgBak);
    renameSync(electronIndexPath, mainBak);
    
    // 步骤 2: 清除 require 缓存
    delete Module._cache[electronIndexPath];
    delete Module._cache[electronPkgPath];
    
    // 步骤 3: require("electron") 现在应回退到 Electron 内置模块
    electronModule = require('electron');
  } catch (e) {
    // 如果仍然失败（例如在单元测试环境），使用空对象
    electronModule = {};
  } finally {
    // 步骤 4: 恢复原始文件
    try { renameSync(pkgBak, electronPkgPath); } catch (_) {}
    try { renameSync(mainBak, electronIndexPath); } catch (_) {}
  }
  
  // 如果 shim 返回空对象（例如因权限问题无法重命名），
  // 检查缓存中是否有 Electron 启动时预加载的 API
  if (!electronModule || typeof electronModule === 'string' || !electronModule.app) {
    for (const key of Object.keys(Module._cache)) {
      const m = (Module._cache as any)[key];
      if (m && m.exports && m.exports.app && m.exports.BrowserWindow) {
        electronModule = m.exports;
        break;
      }
    }
  }
} else {
  // Outside Electron, provide empty stubs
  electronModule = {};
}

// Make sure we have at least the basic properties
if (!electronModule.app) electronModule.app = undefined;
if (!electronModule.BrowserWindow) electronModule.BrowserWindow = undefined;

export const app = electronModule.app;
export const BrowserWindow = electronModule.BrowserWindow;
export const ipcMain = electronModule.ipcMain;
export const dialog = electronModule.dialog;
export const shell = electronModule.shell;
export const Tray = electronModule.Tray;
export const Menu = electronModule.Menu;
export const nativeImage = electronModule.nativeImage;

export default electronModule;

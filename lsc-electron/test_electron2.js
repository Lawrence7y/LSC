console.log("execPath:", process.execPath);
console.log("versions.electron:", process.versions.electron);

// 检查 electron.asar 是否存在
const path = require('path');
const fs = require('fs');
const nativeElectronPath = path.join(path.dirname(process.execPath), 'resources', 'electron.asar');
console.log("nativeElectronPath:", nativeElectronPath);
console.log("exists:", fs.existsSync(nativeElectronPath));

// 检查 Module._cache
const Module = require('module');
const cacheKeys = Object.keys(Module._cache);
console.log("Module._cache count:", cacheKeys.length);
const electronEntry = cacheKeys.find(k => k.includes('electron'));
console.log("electron in cache:", electronEntry);

// 尝试直接 require 内置 electron
try {
  const builtin = process._linkedBinding('electron_common_app');
  console.log("linkedBinding:", typeof builtin);
} catch(e) {
  console.log("linkedBinding failed:", e.message);
}

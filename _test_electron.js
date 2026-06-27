var fs = require('fs');
var Module = require('module');
var output = '';

// Use absolute path directly
var electronDir = 'D:/Project/直播切片多人/lsc-electron/node_modules/electron';
var electronPkg = electronDir + '/package.json';
var electronIndex = electronDir + '/index.js';

output += 'electronDir: ' + electronDir + '\n';
output += 'pkg exists: ' + fs.existsSync(electronPkg) + '\n';
output += 'index exists: ' + fs.existsSync(electronIndex) + '\n';

// Hide the electron package
try {
  fs.renameSync(electronPkg, electronPkg + '.hide');
  fs.renameSync(electronIndex, electronIndex + '.hide');
  output += 'Renamed electron package files successfully\n';
  
  // Clear require cache
  var cacheKey = electronIndex;
  delete Module._cache[cacheKey];
  delete Module._cache[electronPkg];
  
  // Try require electron
  try {
    var e2 = require('electron');
    output += 'require("electron") result type: ' + typeof e2 + '\n';
    if (typeof e2 === 'object' && e2) {
      output += '  app: ' + typeof e2.app + '\n';
      output += '  BrowserWindow: ' + typeof e2.BrowserWindow + '\n';
      output += '  ipcMain: ' + typeof e2.ipcMain + '\n';
    } else if (typeof e2 === 'string') {
      output += '  string value: ' + e2.substring(0, 80) + '\n';
    }
  } catch(err2) {
    output += 'require("electron") error: ' + err2.message + '\n';
    // Check if the module exists elsewhere
    output += '\nSearching Module._cache for electron...\n';
    var found = false;
    Object.keys(Module._cache).forEach(function(k) {
      if (k.indexOf('electron') >= 0 && k.indexOf('node_modules') < 0) {
        output += '  Cache key: ' + k + '\n';
        found = true;
      }
    });
    if (!found) output += '  No electron in cache\n';
  }
} catch(err) {
  output += 'Rename error: ' + err.message + '\n';
} finally {
  // Restore
  try { 
    if (fs.existsSync(electronPkg + '.hide')) fs.renameSync(electronPkg + '.hide', electronPkg); 
  } catch(_) {}
  try { 
    if (fs.existsSync(electronIndex + '.hide')) fs.renameSync(electronIndex + '.hide', electronIndex); 
  } catch(_) {}
  output += 'Restored electron package files\n';
}

fs.writeFileSync('D:/Project/直播切片多人/_cache_debug5.txt', output);

// Wrapper script that forces Electron's built-in module to be used
// instead of the node_modules/electron npm package.

// First, load the Electron API properly by accessing it through the
// process object or by clearing the require cache.

const Module = require('module');

// Check if electron module is already cached (Electron bootstrap should have it)
const cacheKeys = Object.keys(Module._cache);
let electronApi = null;

// Search for the electron module that was pre-loaded by Electron
for (const key of cacheKeys) {
  if (key.includes('electron') && !key.includes('node_modules')) {
    electronApi = Module._cache[key].exports;
    break;
  }
}

if (!electronApi) {
  // Last resort: monkey-patch _nodeModulePaths to skip node_modules
  const origNodeModulePaths = Module._nodeModulePaths;
  Module._nodeModulePaths = function() { return []; };
  try {
    electronApi = require('electron');
  } catch (e) {
    // Fallback
    electronApi = require('./node_modules/electron');
  } finally {
    Module._nodeModulePaths = origNodeModulePaths;
  }
}

// Override the require cache for the 'electron' module
// so that subsequent require('electron') calls return the Electron API
const electronCacheKey = require.resolve('electron');
Module._cache[electronCacheKey] = {
  id: electronCacheKey,
  filename: electronCacheKey,
  loaded: true,
  exports: electronApi,
};

// Now load the actual main script
require('./dist-electron/main/main.js');

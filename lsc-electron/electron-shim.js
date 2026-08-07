// Electron module shim - bridges the gap when built-in module registration fails
const electronPath = require('path').join(__dirname, 'node_modules/electron/dist/electron.exe');

// If running inside Electron, the built-in module should be available
// through process._linkedBinding or similar mechanism
if (process.versions && process.versions.electron) {
  try {
    // Try to get the actual electron module from Electron's internal modules
    const electronInternal = require('electron/js2c/node_init');
    if (electronInternal && typeof electronInternal === 'object') {
      module.exports = electronInternal;
      return;
    }
  } catch (e) {
    // Fall through
  }
}

// Fallback: return the path (for use by cli.js)
module.exports = electronPath;

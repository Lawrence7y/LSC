const Module = require('module');
const path = require('path');

console.log('process.versions.electron:', process.versions.electron);
console.log('process.resourcesPath:', process.resourcesPath);
console.log('__dirname:', __dirname);
try {
  const resolved = Module._resolveFilename('electron', module, false);
  console.log('resolved electron:', resolved);
} catch(ex) {
  console.log('resolve error:', ex.message);
}
try {
  const e = require('electron');
  console.log('typeof require(electron):', typeof e);
  console.log('is string:', typeof e === 'string');
  console.log('app:', typeof e.app);
} catch(ex) {
  console.log('error:', ex.message);
}

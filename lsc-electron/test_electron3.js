console.log("process.versions.electron:", process.versions.electron);
const electron = require("electron");
console.log("typeof electron:", typeof electron);
if (typeof electron === 'object') {
  console.log("keys:", Object.keys(electron).slice(0, 10));
  console.log("electron.app:", electron.app);
  console.log("electron.BrowserWindow:", typeof electron.BrowserWindow);
} else {
  console.log("electron value:", electron);
}

console.log("process.versions.electron:", process.versions.electron);

// 安全地测试每个 binding
const safeBindings = {};
const bindingNames = [
  'electron_common_app',
  'electron_browser_app',
  'electron_browser_window',
  'electron_browser_web_contents',
  'electron_browser_session',
  'electron_browser_tray',
  'electron_browser_menu',
  'electron_browser_dialog',
  'electron_browser_notification',
  'electron_browser_ipc_main',
  'electron_browser_auto_updater',
  'electron_browser_net',
  'electron_browser_protocol',
  'electron_browser_shell',
  'electron_browser_native_theme',
  'electron_browser_download_item',
];

for (const name of bindingNames) {
  try {
    const binding = process._linkedBinding(name);
    if (binding && typeof binding === 'object') {
      safeBindings[name] = Object.keys(binding).length;
      console.log(`${name}: OK (${Object.keys(binding).length} keys)`);
    }
  } catch (e) {
    console.log(`${name}: FAILED (${e.message})`);
  }
}

console.log("\nSafe bindings:", JSON.stringify(safeBindings, null, 2));

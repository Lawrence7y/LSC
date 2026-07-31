console.log("process.versions.electron:", process.versions.electron);

// 尝试使用 process._linkedBinding 访问 Electron API
const bindings = [
  'electron_common_app',
  'electron_common_browser',
  'electron_common_renderer',
  'electron_common_v8_util',
  'electron_browser_app',
  'electron_browser_web_contents',
  'electron_browser_browser_view',
  'electron_browser_session',
  'electron_browser_tray',
  'electron_browser_menu',
  'electron_browser_dialog',
  'electron_browser_notification',
];

for (const name of bindings) {
  try {
    const binding = process._linkedBinding(name);
    if (binding) {
      console.log(`${name}: typeof=${typeof binding}, keys=${Object.keys(binding).slice(0, 5).join(',')}`);
    }
  } catch (e) {
    // ignore
  }
}

// 尝试使用 process.binding
console.log("\n--- process.binding ---");
for (const name of bindings) {
  try {
    const binding = process.binding(name);
    if (binding) {
      console.log(`${name}: typeof=${typeof binding}, keys=${Object.keys(binding).slice(0, 5).join(',')}`);
    }
  } catch (e) {
    // ignore
  }
}

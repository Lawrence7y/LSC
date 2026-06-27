const { app, BrowserWindow } = require('electron');
const fs = require('fs');

fs.writeFileSync('D:/Project/直播切片多人/_test_app/result.txt', 'app=' + typeof app + ' BrowserWindow=' + typeof BrowserWindow);

if (app) {
  app.whenReady().then(function() {
    fs.appendFileSync('D:/Project/直播切片多人/_test_app/result.txt', '\nREADY');
    app.quit();
  });
} else {
  fs.appendFileSync('D:/Project/直播切片多人/_test_app/result.txt', '\nFAILED: app is undefined');
  process.exit(1);
}

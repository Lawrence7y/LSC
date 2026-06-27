@echo off
"D:\Project\直播切片多人\lsc-electron\node_modules\electron\dist\electron.exe" "D:\Project\直播切片多人\lsc-electron\dist-electron\main\main.js" > "D:\Project\直播切片多人\_electron_stdout.txt" 2> "D:\Project\直播切片多人\_electron_stderr.txt"
echo Exit code: %ERRORLEVEL% >> "D:\Project\直播切片多人\_electron_stderr.txt"

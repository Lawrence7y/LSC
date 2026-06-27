import sys, os, traceback, datetime
os.chdir(r'D:\Project\直播切片多人')
sys.path.insert(0, '.')
log_path = os.path.join(os.getcwd(), 'launch_debug.log')
with open(log_path, 'w', encoding='utf-8') as log:
    def lw(msg):
        t = datetime.datetime.now().strftime('%H:%M:%S')
        log.write(f'[{t}] {msg}\n')
        log.flush()
    lw('=== LSC Debug Launcher ===')
    lw(f'Python: {sys.version}')
    lw(f'CWD: {os.getcwd()}')
    try:
        lw('Importing main...')
        from main import main
        lw('Calling main()...')
        main()
        lw('main() returned normally')
    except Exception as e:
        lw(f'FATAL: {type(e).__name__}: {e}')
        traceback.print_exc(file=log)
    except SystemExit as e:
        lw(f'SystemExit: {e}')
    finally:
        lw('=== Launcher exiting ===')

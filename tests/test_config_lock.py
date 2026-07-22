import threading

from lsc.config import load_config, reset_config


def test_load_config_concurrent():
    reset_config()
    errs = []

    def worker():
        try:
            for _ in range(20):
                load_config(force_reload=True)
        except Exception as e:
            errs.append(e)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs
    assert load_config() is load_config()

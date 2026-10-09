"""One worker per persistent database; holds the process lock across network calls."""
import fcntl,json,os,signal,time
import bridge
running=True
def stop(*_):
    global running
    running=False
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
while running:
    if os.environ.get('WORKER_ENABLED')=='1':
        try:
            with (bridge.ROOT/'worker.lock').open('a') as lock:
                try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError: pass
                else:
                    result=bridge.tick()
                    if result.get('state') not in ('idle','outside_window','interval'):
                        print(json.dumps(result),flush=True)
        except Exception:
            print('{"state":"worker_error","details":"redacted"}',flush=True)
    for _ in range(60):
        if not running: break
        time.sleep(1)

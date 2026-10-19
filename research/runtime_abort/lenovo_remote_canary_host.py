"""Executed only via x1 SSH -c source injection. SUPPORT is a safe in-memory
module namespace containing lenovo_loopback_abort.py, not a remote file."""
import json
import os
from pathlib import Path
import resource
import select
import signal
import subprocess
import sys
import tempfile
import time

S=SUPPORT
mem,live_pid,live_start=S["preflight"]()
args=[str(S["BINARY"]),"--model",str(S["MODEL"]),
      "--host","127.0.0.1","--port",str(S["PORT"]),
      "--gpu-layers","0","--threads","2","--threads-batch","2",
      "--parallel","1","--ctx-size","1024","--no-webui","--slots",
      "--timeout","20","--alias","research-lenovo-lfm-abort-only"]
with tempfile.TemporaryDirectory(prefix="lenovo-llama-router-fence-") as td:
    private=Path(td)
    os.chmod(private,0o700)
    logfile=private/"private.log"
    env={**os.environ,"LD_LIBRARY_PATH":str(S["BINARY"].parent),"OMP_NUM_THREADS":"2"}
    with logfile.open("w") as output:
        child=subprocess.Popen(args,stdout=output,stderr=subprocess.STDOUT,
                               env=env,start_new_session=True,
                               preexec_fn=S["limit_child"])
    try:
        for _ in range(130):
            if child.poll() is not None:
                raise RuntimeError("NO_GO temporary Lenovo server exited before ready: "
                                   +logfile.read_text(errors="replace")[-320:])
            try:
                if S["requests"].get(
                    f"http://127.0.0.1:{S['PORT']}/health",timeout=1.0
                ).status_code==200:
                    break
            except S["requests"].RequestException:
                pass
            time.sleep(.25)
        else:
            raise RuntimeError("NO_GO temporary Lenovo server did not become ready")
        print(json.dumps({"event":"ready","physical_host":"lenovo",
                          "port":12742,"private_model_process":child.pid,
                          "model_size":S["MODEL"].stat().st_size}),flush=True)
        readable,_,_=select.select([sys.stdin],[],[],65)
        if not readable:
            raise RuntimeError("NO_GO Lenovo remote controller deadline")
        if sys.stdin.readline().strip()!="STOP":
            raise RuntimeError("NO_GO control channel closed or invalid")
    finally:
        if child.poll() is None:
            os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=6)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL)
                child.wait(timeout=5)
        if child.poll() is None:
            raise RuntimeError("CRITICAL remote Lenovo process cleanup failed")
        unit_ok=S["requests"].get(
            f"http://127.0.0.1:{S['LIVE_PORT']}/health",timeout=3
        ).status_code==200
        same_pid=(Path(f"/proc/{live_pid}/stat").read_text().split()[21]==live_start)
        print(json.dumps({"event":"stopped","isolated_process_terminated":True,
                          "live_health_200":unit_ok,
                          "live_process_identity_unchanged":same_pid}),flush=True)

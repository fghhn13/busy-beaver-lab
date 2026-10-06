"""Run against the existing local service; creates isolated experiment directories."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = "http://127.0.0.1:8765"


def call(*args, code=0, remote=True):
    command = [sys.executable, "-m", "experiments.cli"] + (["--server",SERVER] if remote else []) + list(args)
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    value = json.loads(result.stdout)
    assert result.returncode == code, (result.returncode,value,result.stderr)
    return value


data = call("run","tm5_three_symbols","--max-steps","1000","--detach","--start-paused")["data"]
mid, rid = data["machine_id"], data["run_id"]
call("control",mid,rid,"pause")
timeout = call("wait",mid,rid,"--wait-seconds","0",code=6)
assert timeout["data"]["snapshot"]["steps"] == "0"
call("control",mid,rid,"step")
deadline = time.monotonic()+5
while call("status",mid,rid)["data"]["snapshot"]["steps"] != "1":
    assert time.monotonic() < deadline
    time.sleep(.05)
assert call("frame",mid,rid,"--step","1")["data"]["cells"] == [{"position":"0","symbol":2}]
call("control",mid,rid,"resume")
assert call("wait",mid,rid,"--wait-seconds","10")["data"]["result"]["conclusion"] == "HALTED"
assert call("verify",mid,rid)["data"]["valid"] is True
artifact = call("export",mid,rid)["data"]
assert Path(artifact["path"]).exists()
data = call("run","right_drifter","--detach","--start-paused")["data"]
call("control",data["machine_id"],data["run_id"],"cancel")
assert call("wait",data["machine_id"],data["run_id"],"--wait-seconds","10")["data"]["result"]["conclusion"] == "UNKNOWN"
call("machines","show","nonexistent_machine",code=3)
call("run","tm5_three_symbols","--wait-seconds","-1",code=2)
call("--server","http://127.0.0.1:1","machines","list",code=4,remote=False)
print(json.dumps({"passed":True,"checks":"background, control, exact replay, wait timeout, verification, SVG export, cancel, missing resource, invalid input, unavailable service","example_run":rid,"artifact":artifact}))

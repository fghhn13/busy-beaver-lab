from contextlib import asynccontextmanager
import json
import base64
import binascii
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from core.machine import integer
from deciders.verify import verify_evidence
from experiments.runner import Laboratory, replay_frame
from experiments.storage import FILE_IO_LOCK, ROOT, list_machines, list_runs, load_machine, read_json, run_path, save_machine, write_json


@asynccontextmanager
async def lifespan(app):
    app.state.lab = Laboratory()
    yield
    app.state.lab.close()


app = FastAPI(title="Busy Beaver Lab", lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Reject cross-site mutation even when its body uses a simple content type.
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin != str(request.base_url).rstrip("/"):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Only the local page may submit changes"}, status_code=403)
    return await call_next(request)


def guarded(call, *args):
    try:
        return call(*args)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/machines")
def machines():
    return list_machines()


@app.get("/api/machines/{machine_id}")
def machine(machine_id: str):
    return guarded(load_machine, machine_id)


@app.post("/api/machines", status_code=201)
def add_machine(data: dict):
    return guarded(save_machine, data)


@app.get("/api/runs")
def runs():
    return list_runs()


@app.post("/api/runs", status_code=201)
async def start_run(request: Request, data: dict):
    return await run_in_threadpool(guarded, request.app.state.lab.start, data.get("machine_id", ""), data.get("options", {}))


@app.get("/api/runs/{machine_id}/{run_id}")
def run_detail(machine_id: str, run_id: str):
    path = guarded(run_path, machine_id, run_id)
    with FILE_IO_LOCK:
        metadata = read_json(path / "run.json")
        return {"run": metadata, "machine": read_json(path / "machine.json"),
                "snapshot": read_json(path / "snapshot.json"),
                "result": read_json(path / "result.json") if (path / "result.json").exists() else None}


@app.post("/api/runs/{machine_id}/{run_id}/control")
async def control(machine_id: str, run_id: str, request: Request, data: dict):
    return await run_in_threadpool(guarded, request.app.state.lab.control, machine_id, run_id, data.get("action"))


@app.get("/api/runs/{machine_id}/{run_id}/frame")
def frame(machine_id: str, run_id: str, step: str):
    path = guarded(run_path, machine_id, run_id)
    return guarded(replay_frame, path, guarded(integer, step))


@app.get("/api/runs/{machine_id}/{run_id}/trace")
def trace(machine_id: str, run_id: str):
    path = guarded(run_path, machine_id, run_id)
    samples = []
    with (path / "traces" / "samples.jsonl").open(encoding="utf-8") as file:
        for line in file:
            try:
                samples.append(json.loads(line))
            except ValueError:
                # The final line may still be in flight while the worker appends it.
                continue
    # Bound response size for experiments with many manual steps or recoveries.
    stride = max(1, (len(samples) + 1199) // 1200)
    sampled = samples[::stride]
    if samples and (not sampled or samples[-1]["steps"] != sampled[-1]["steps"]):
        sampled.append(samples[-1])
    return {"samples": sampled, "stored_interval": read_json(path / "run.json")["trace_interval"], "response_stride": stride}


@app.post("/api/runs/{machine_id}/{run_id}/verify")
def verify(machine_id: str, run_id: str):
    path = guarded(run_path, machine_id, run_id)
    certificate = guarded(read_json, path / "evidence" / "certificate.json")
    result = guarded(verify_evidence, read_json(path / "machine.json"), certificate)
    write_json(path / "evidence" / "verification.json", result)
    return result


@app.get("/api/runs/{machine_id}/{run_id}/download")
def download(machine_id: str, run_id: str):
    path = guarded(run_path, machine_id, run_id)
    result = path / "result.json"
    if not result.exists():
        raise HTTPException(409, "Experiment has no final result yet")
    return FileResponse(result, filename=f"{machine_id}_{run_id}.json")


@app.post("/api/runs/{machine_id}/{run_id}/visualization")
def visualization(machine_id: str, run_id: str, data: dict):
    path = guarded(run_path, machine_id, run_id)
    encoded = data.get("png", "")
    if not isinstance(encoded, str) or not encoded.startswith("data:image/png;base64,") or len(encoded) > 8_000_000:
        raise HTTPException(400, "A PNG image of at most 8 MB is required")
    try:
        binary = base64.b64decode(encoded.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, "Invalid image encoding") from exc
    if not binary.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, "Invalid image format")
    image_id = uuid.uuid4().hex
    image = path / "visualizations" / f"spacetime_{image_id}.png"
    image.write_bytes(binary)
    write_json(image.with_suffix(".json"), {"view": "sampled_spacetime", "frame_step": data.get("frame_step"),
               "sample_steps": data.get("sample_steps", []), "stored_interval": data.get("stored_interval"),
               "response_stride": data.get("response_stride")})
    return {"saved": True, "path": f"results/{machine_id}/{run_id}/visualizations/{image.name}"}


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")

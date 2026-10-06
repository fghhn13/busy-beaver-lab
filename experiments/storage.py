from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from core.machine import ID_PATTERN, machine_digest, validate_machine

ROOT = Path(__file__).resolve().parents[1]
MACHINES = ROOT / "machines"
RESULTS = ROOT / "results"
WRITE_LOCK = threading.Lock()
FILE_IO_LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    with FILE_IO_LOCK:
        return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        with FILE_IO_LOCK:
            for attempt in range(10):
                try:
                    os.replace(temp, path)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(.01 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


def machine_path(machine_id):
    if not ID_PATTERN.fullmatch(machine_id):
        raise ValueError("Invalid machine ID")
    path = (MACHINES / f"{machine_id}.json").resolve()
    if not path.is_relative_to(MACHINES.resolve()):
        raise ValueError("Configuration path leaves the machines directory")
    return path


def run_path(machine_id, run_id):
    machine_path(machine_id)
    if not re.fullmatch(r"[0-9]{8}T[0-9]{12}Z_[a-f0-9]{12}", run_id):
        raise ValueError("Invalid run ID")
    path = (RESULTS / machine_id / run_id).resolve()
    if not path.is_relative_to(RESULTS.resolve()):
        raise ValueError("Result path leaves the results directory")
    if not path.is_dir():
        raise FileNotFoundError("Run not found")
    return path


def list_machines():
    items = []
    for path in sorted(MACHINES.glob("*.json")):
        try:
            data = validate_machine(read_json(machine_path(path.stem)))
            if data["id"] != path.stem:
                raise ValueError("ID does not match the filename")
            items.append({"id": data["id"], "name": data.get("name", data["id"]),
                          "states": len(data["states"]), "symbols": len(data["symbols"]), "description": data.get("description", "")})
        except (ValueError, OSError) as exc:
            items.append({"id": path.stem, "error": str(exc)})
    return items


def load_machine(machine_id):
    data = validate_machine(read_json(machine_path(machine_id)))
    if data["id"] != machine_id:
        raise ValueError("ID does not match the filename")
    return data


def save_machine(data):
    validate_machine(data)
    with WRITE_LOCK:
        path = machine_path(data["id"])
        if path.exists():
            raise FileExistsError("Machine ID already exists; save with a new ID")
        # Publish a complete file without overwriting a concurrent CLI/server import.
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with FILE_IO_LOCK:
                if os.name == "nt":
                    os.rename(temp, path)  # Windows rename rejects an existing destination.
                else:
                    os.link(temp, path)  # Atomic, exclusive publication on POSIX.
        finally:
            temp.unlink(missing_ok=True)
    return data


def create_run(definition, options):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + uuid.uuid4().hex[:12]
    path = RESULTS / definition["id"] / run_id
    path = path.resolve()
    if not path.is_relative_to(RESULTS.resolve()):
        raise ValueError("Result path leaves the results directory")
    path.mkdir(parents=True, exist_ok=False)
    for name in ("checkpoints", "traces", "evidence", "visualizations", "logs"):
        (path / name).mkdir()
    write_json(path / "machine.json", definition)
    for name in ("environment.yml", "conda-win-64.lock.txt", "pip-freeze.txt"):
        if (ROOT / name).exists():
            shutil.copyfile(ROOT / name, path / ("conda-explicit.txt" if name == "conda-win-64.lock.txt" else name))
    try:
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=3)
        revision = git.stdout.strip() if git.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        revision = None
    source_hash = hashlib.sha256()
    for directory in ("core", "deciders", "experiments", "server", "web"):
        for source in sorted((ROOT / directory).rglob("*")):
            if source.is_file() and source.suffix in (".py", ".js", ".html", ".css"):
                relative = source.relative_to(ROOT)
                source_hash.update(str(relative).replace("\\", "/").encode())
                source_hash.update(source.read_bytes())
                destination = path / "source" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
    metadata = {"schema_version": 1, "machine_id": definition["id"], "run_id": run_id,
                "machine_sha256": machine_digest(definition), "created_at": now(), "updated_at": now(),
                "code_version": "lab-v2", "git_revision": revision, "source_sha256": source_hash.hexdigest(),
                "python": sys.version, "options": options, "status": "QUEUED", "events": [],
                "active_seconds": 0, "trace_interval": max(options["trace_interval"], (options["max_steps"] + 999) // 1000)}
    write_json(path / "run.json", metadata)
    return path, metadata


def list_runs():
    runs = []
    for path in RESULTS.glob("*/*/run.json"):
        try:
            if not path.resolve().is_relative_to(RESULTS.resolve()):
                continue
            metadata = read_json(path)
            metadata["result"] = read_json(path.parent / "result.json") if (path.parent / "result.json").exists() else None
            metadata["snapshot"] = read_json(path.parent / "snapshot.json") if (path.parent / "snapshot.json").exists() else None
            if metadata["snapshot"]:
                metadata["snapshot"].pop("nonzero_cells", None)
                metadata["snapshot"].pop("cells", None)
            runs.append(metadata)
        except (ValueError, OSError):
            continue
    return sorted(runs, key=lambda r: r["created_at"], reverse=True)

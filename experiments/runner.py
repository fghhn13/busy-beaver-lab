from __future__ import annotations

import json
import threading
import time

from core.machine import Machine, machine_digest
from deciders.verify import verify_evidence
from experiments.storage import FILE_IO_LOCK, create_run, list_runs, load_machine, now, read_json, run_path, write_json

DEFAULTS = {"max_steps": 100000, "max_seconds": 30, "max_nonzero_cells": 10000,
            "trace_interval": 1, "checkpoint_interval": 10000, "detect_cycles": True,
            "start_paused": False}
LIMITS = {"max_steps": (1, 100_000_000), "max_seconds": (1, 3600), "max_nonzero_cells": (1, 100000),
          "trace_interval": (1, 1_000_000), "checkpoint_interval": (1, 100000)}


def validate_options(options):
    if not isinstance(options, dict) or set(options) - set(DEFAULTS):
        raise ValueError("Unsupported experiment options")
    result = DEFAULTS | options
    for key, (low, high) in LIMITS.items():
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be {low}–{high} an integer")
    for key in ("detect_cycles", "start_paused"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    return result


class Run:
    def __init__(self, path, metadata, machine):
        self.path, self.metadata, self.machine = path, metadata, machine
        self.condition = threading.Condition()
        self.io_lock = threading.RLock()
        self.paused = metadata["options"]["start_paused"]
        self.cancelled = False
        self.stopping = False
        self.permits = 0
        self.finished = False
        self.history = {}
        self.history_cells = 0
        self.cycles_active = metadata["options"]["detect_cycles"]
        self.last_saved = 0
        self.last_trace_step = -1
        self.active_seconds = float(metadata.get("active_seconds", 0))
        self.thread = threading.Thread(target=self.work, daemon=True, name=f"bb-{metadata['run_id']}")

    def event(self, kind):
        self.metadata["events"].append({"time": now(), "event": kind, "step": str(self.machine.steps)})

    def save(self, checkpoint=False, trace=False):
        with self.io_lock, FILE_IO_LOCK:
            snapshot = self.machine.snapshot()
            self.metadata.update(updated_at=now(), active_seconds=self.active_seconds,
                                 cycle_detection_active=self.cycles_active)
            write_json(self.path / "snapshot.json", snapshot)
            if checkpoint:
                write_json(self.path / "checkpoints" / f"{self.machine.steps:020d}.json", snapshot)
            if trace and self.last_trace_step != self.machine.steps:
                with (self.path / "traces" / "samples.jsonl").open("a", encoding="utf-8") as file:
                    file.write(json.dumps(snapshot, separators=(",", ":")) + "\n")
                self.last_trace_step = self.machine.steps
            write_json(self.path / "run.json", self.metadata)
            self.last_saved = time.monotonic()

    def finish(self, conclusion, reason, evidence=None):
        verification = None
        if evidence:
            evidence["machine_sha256"] = machine_digest(self.machine.definition)
            evidence["final"] = self.machine.snapshot()
            write_json(self.path / "evidence" / "certificate.json", evidence)
            verification = verify_evidence(self.machine.definition, evidence)
            write_json(self.path / "evidence" / "verification.json", verification)
            if conclusion == "NON_HALTING" and verification["valid"] is not True:
                conclusion, reason = "UNKNOWN", "evidence_verification_failed"
        with self.condition:
            self.finished = True
            self.metadata["status"] = "CANCELLED" if reason == "cancelled" else "COMPLETED"
            self.event(reason)
        with self.io_lock, FILE_IO_LOCK:
            write_json(self.path / "result.json", {"schema_version": 1, "conclusion": conclusion,
                       "reason": reason, "steps": str(self.machine.steps), "ones": self.machine.snapshot()["ones"],
                       "nonblank_count": str(len(self.machine.cells)), "symbol_counts": self.machine.snapshot()["symbol_counts"],
                       "state": self.machine.state, "head": str(self.machine.head), "completed_at": now(),
                       "verification": verification})
            self.save(checkpoint=True, trace=True)

    def work(self):
        opts = self.metadata["options"]
        try:
            self.metadata["status"] = "PAUSED" if self.paused else "RUNNING"
            self.event("worker_started")
            self.save(checkpoint=True, trace=True)
            while True:
                with self.condition:
                    if self.paused and not self.permits and not self.cancelled and not self.stopping:
                        self.metadata["status"] = "PAUSED"
                        self.save(checkpoint=True, trace=True)
                        self.condition.wait_for(lambda: not self.paused or self.permits or self.cancelled or self.stopping)
                    cancelled = self.cancelled
                    single_step = self.paused and self.permits > 0
                    if single_step:
                        self.permits -= 1
                    self.metadata["status"] = "PAUSED" if self.paused else "RUNNING"
                if self.stopping:
                    self.metadata["status"] = "INTERRUPTED"
                    self.event("service_shutdown")
                    self.save(checkpoint=True, trace=True)
                    self.finished = True
                    return
                if cancelled:
                    self.finish("UNKNOWN", "cancelled")
                    return
                if self.machine.state == "H":
                    self.finish("HALTED", "halt", {"kind": "halt", "end_step": str(self.machine.steps)})
                    return
                started = time.monotonic()
                if self.cycles_active:
                    # The key is the entire configuration, not a visible tape window or hash alone.
                    key = self.machine.key()
                    if key in self.history:
                        self.finish("NON_HALTING", "exact_cycle", {"kind": "exact_cycle",
                                    "start_step": str(self.history[key]), "end_step": str(self.machine.steps)})
                        return
                    cost = len(self.machine.cells) + 1
                    if len(self.history) >= 20000 or self.history_cells + cost > 200000:
                        self.cycles_active = False
                        self.history.clear()
                        self.event("cycle_cache_limit")
                    else:
                        self.history[key] = self.machine.steps
                        self.history_cells += cost
                if self.machine.steps >= opts["max_steps"]:
                    self.finish("UNKNOWN", "step_budget")
                    return
                if self.active_seconds >= opts["max_seconds"]:
                    self.finish("UNKNOWN", "time_budget")
                    return
                if len(self.machine.cells) > opts["max_nonzero_cells"]:
                    self.finish("UNKNOWN", "tape_budget")
                    return
                self.machine.step()
                if self.machine.state == "H":
                    self.finish("HALTED", "halt", {"kind": "halt", "end_step": str(self.machine.steps)})
                    return
                if self.cycles_active:
                    next_key = self.machine.key()
                    if next_key in self.history:
                        self.finish("NON_HALTING", "exact_cycle", {"kind": "exact_cycle",
                                    "start_step": str(self.history[next_key]), "end_step": str(self.machine.steps)})
                        return
                if self.machine.steps >= opts["max_steps"]:
                    self.finish("UNKNOWN", "step_budget")
                    return
                if len(self.machine.cells) > opts["max_nonzero_cells"]:
                    self.finish("UNKNOWN", "tape_budget")
                    return
                checkpoint = self.machine.steps % opts["checkpoint_interval"] == 0
                trace = self.machine.steps % self.metadata["trace_interval"] == 0
                if single_step or checkpoint or trace or time.monotonic() - self.last_saved > .25:
                    self.save(checkpoint=checkpoint or single_step, trace=trace or single_step)
                self.active_seconds += time.monotonic() - started
        except Exception as exc:
            with self.condition:
                self.finished = True
                self.metadata["status"] = "ERROR"
                self.event("error")
            write_json(self.path / "result.json", {"conclusion": "UNKNOWN", "reason": "error",
                       "message": str(exc), "steps": str(self.machine.steps)})
            self.save(checkpoint=True, trace=True)

    def control(self, action):
        with self.condition:
            if self.finished:
                raise ValueError("Run already ended; create a new experiment")
            if action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused, self.permits = False, 0
            elif action == "step":
                self.paused = True
                self.permits += 1
            elif action == "cancel":
                self.cancelled = True
            else:
                raise ValueError("Unsupported control action")
            self.event(action)
            self.condition.notify_all()


class Laboratory:
    def __init__(self):
        self.runs = {}
        self.lock = threading.Lock()
        # A previous process cannot still be resumed in memory; persisted snapshots can be recovered.
        for metadata in list_runs():
            if metadata["status"] in ("RUNNING", "PAUSED", "QUEUED"):
                path = run_path(metadata["machine_id"], metadata["run_id"])
                metadata.pop("result", None)
                metadata.pop("snapshot", None)
                metadata["status"] = "INTERRUPTED"
                write_json(path / "run.json", metadata)

    def _capacity(self):
        if sum(not run.finished for run in self.runs.values()) >= 4:
            raise ValueError("At most four experiments may be open; finish or stop an existing run first")

    def start(self, machine_id, options):
        options = validate_options(options)
        definition = load_machine(machine_id)
        with self.lock:
            self._capacity()
            path, metadata = create_run(definition, options)
            run = Run(path, metadata, Machine.initial(definition))
            self.runs[(machine_id, metadata["run_id"])] = run
            run.save(checkpoint=True, trace=True)
            run.thread.start()
            return metadata.copy()

    def control(self, machine_id, run_id, action):
        with self.lock:
            run = self.runs.get((machine_id, run_id))
            if run is None:
                path = run_path(machine_id, run_id)
                metadata = read_json(path / "run.json")
                if action not in ("resume", "step") or metadata["status"] != "INTERRUPTED":
                    raise ValueError("Only interrupted experiments can be recovered from disk")
                self._capacity()
                machine = Machine.restore(read_json(path / "machine.json"), read_json(path / "snapshot.json"))
                metadata["options"]["start_paused"] = action == "step"
                run = Run(path, metadata, machine)
                run.event("recovered_from_disk")
                self.runs[(machine_id, run_id)] = run
                if action == "step":
                    run.permits = 1
                run.thread.start()
            else:
                run.control(action)
        return {"accepted": True, "action": action}

    def close(self):
        for run in self.runs.values():
            if not run.finished:
                with run.condition:
                    run.stopping = True
                    run.condition.notify_all()
        for run in self.runs.values():
            run.thread.join(timeout=3)


def replay_frame(path, step):
    latest = read_json(path / "snapshot.json")
    if not 0 <= step <= int(latest["steps"]):
        raise ValueError("Replay step is outside the executed range")
    checkpoints = sorted((path / "checkpoints").glob("*.json"))
    candidates = [p for p in checkpoints if int(p.stem) <= step]
    if not candidates:
        raise ValueError("No checkpoint available")
    snapshot = read_json(candidates[-1])
    machine = Machine.restore(read_json(path / "machine.json"), snapshot)
    if step - machine.steps > 100000:
        raise ValueError("Replay exceeds 100000 steps; choose a closer checkpoint")
    while machine.steps < step:
        if not machine.step():
            raise ValueError("Requested step is past the halt time")
    return machine.snapshot()

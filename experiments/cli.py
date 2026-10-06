"""Agent CLI: one JSON response, stable exit codes, no GUI requirement."""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from core.machine import Machine, integer, machine_digest, validate_machine, MULTI_SEMANTICS
from deciders.verify import verify_evidence
from experiments.runner import Run, replay_frame, validate_options
from experiments.storage import create_run, list_machines, list_runs, load_machine, read_json, run_path, save_machine, write_json


class CliError(Exception):
    def __init__(self, message, code=2, data=None):
        super().__init__(message)
        self.code, self.data = code, data


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError(message)


def add_budget(sub):
    sub.add_argument("--max-steps", type=int, default=100000)
    sub.add_argument("--max-seconds", type=int, default=30)
    sub.add_argument("--max-cells", type=int, default=10000, help="Nonblank cell budget")
    sub.add_argument("--trace-interval", type=int, default=1)
    sub.add_argument("--checkpoint-interval", type=int, default=10000)
    sub.add_argument("--no-cycle-detection", action="store_true")


def parser():
    root = Parser(description="Busy Beaver Lab automation interface. Commands emit one JSON response.")
    root.add_argument("--server", help="Running local service URL, e.g. http://127.0.0.1:8765")
    root.add_argument("--pretty", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")
    machines = commands.add_parser("machines").add_subparsers(dest="operation", required=True)
    machines.add_parser("list")
    machines.add_parser("show").add_argument("machine_id")
    machines.add_parser("validate").add_argument("file", help="JSON file or - for stdin")
    machines.add_parser("import").add_argument("file", help="JSON file or - for stdin")
    template = machines.add_parser("template")
    template.add_argument("machine_id")
    template.add_argument("--states", type=int, default=5)
    template.add_argument("--symbols", default="0,1,2")
    listings = commands.add_parser("runs").add_subparsers(dest="operation", required=True).add_parser("list")
    listings.add_argument("--machine")
    listings.add_argument("--conclusion", choices=("HALTED", "NON_HALTING", "UNKNOWN"))
    listings.add_argument("--limit", type=int, default=20)
    run = commands.add_parser("run", help="Run locally, or submit to --server")
    run.add_argument("machine_id")
    add_budget(run)
    run.add_argument("--detach", action="store_true", help="Return immediately; requires --server")
    run.add_argument("--start-paused", action="store_true", help="Requires --server and --detach")
    run.add_argument("--wait-seconds", type=float, default=60)
    batch = commands.add_parser("batch", help="Run independent experiments sequentially")
    batch.add_argument("machine_ids", nargs="+")
    add_budget(batch)
    batch.add_argument("--wait-seconds", type=float, default=60)
    for name in ("status", "wait", "frame", "trace", "verify", "export", "control"):
        sub = commands.add_parser(name)
        sub.add_argument("machine_id")
        sub.add_argument("run_id")
        if name == "wait":
            sub.add_argument("--wait-seconds", type=float, default=60)
            sub.add_argument("--poll-seconds", type=float, default=.25)
        if name == "frame":
            sub.add_argument("--step", required=True)
        if name in ("trace", "export"):
            sub.add_argument("--limit", type=int, default=100 if name == "trace" else 500)
        if name == "verify":
            sub.add_argument("--max-steps", type=int, default=1_000_000)
        if name == "control":
            sub.add_argument("action", choices=("pause", "resume", "step", "cancel"))
    return root


def budget(args):
    return validate_options({"max_steps": args.max_steps, "max_seconds": args.max_seconds,
                             "max_nonzero_cells": args.max_cells, "trace_interval": args.trace_interval,
                             "checkpoint_interval": args.checkpoint_interval,
                             "detect_cycles": not args.no_cycle_detection,
                             "start_paused": getattr(args, "start_paused", False)})


def request(server, endpoint, data=None):
    parsed = urlparse(server)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise CliError("--server must be a local HTTP URL without credentials or a path")
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = Request(server.rstrip("/") + "/api" + endpoint, data=body,
                  headers={"Content-Type": "application/json"} if body else {})
    try:
        with urlopen(req, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            message = json.load(exc).get("detail", str(exc))
        except ValueError:
            message = str(exc)
        raise CliError(str(message), 3 if exc.code == 404 else 2 if exc.code < 500 else 5) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CliError(f"Local service unavailable: {exc}", 4) from exc


def endpoint(args):
    return f"/runs/{quote(args.machine_id, safe='')}/{quote(args.run_id, safe='')}"


def local_status(machine_id, run_id):
    path = run_path(machine_id, run_id)
    return {"run": read_json(path / "run.json"), "machine": read_json(path / "machine.json"),
            "snapshot": read_json(path / "snapshot.json"),
            "result": read_json(path / "result.json") if (path / "result.json").exists() else None,
            "result_dir": str(path)}


def status(args):
    value = request(args.server, endpoint(args)) if args.server else local_status(args.machine_id, args.run_id)
    value["result_dir"] = str(run_path(args.machine_id, args.run_id))
    return value


def wait(args):
    seconds, interval = args.wait_seconds, getattr(args, "poll_seconds", .25)
    if not 0 <= seconds <= 3600 or not .05 <= interval <= 10:
        raise CliError("Wait budget must be 0–3600 seconds; polling interval must be 0.05–10 seconds")
    deadline = time.monotonic() + seconds
    while True:
        value = status(args)
        if value["result"] is not None:
            if value["result"].get("reason") == "error":
                raise CliError("Experiment execution failed", 5, value)
            return value
        if time.monotonic() >= deadline:
            raise CliError("Wait deadline reached; experiment unchanged", 6, value)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def execute_run(args):
    options = budget(args)
    if args.server and not 0 <= args.wait_seconds <= 3600:
        raise CliError("Wait budget must be 0–3600 seconds")
    if getattr(args, "start_paused", False) and not (args.server and getattr(args, "detach", False)):
        raise CliError("--start-paused requires --server and --detach")
    if getattr(args, "detach", False) and not args.server:
        raise CliError("--detach requires --server; local runs execute in the foreground")
    if args.server:
        metadata = request(args.server, "/runs", {"machine_id": args.machine_id, "options": options})
        args.run_id = metadata["run_id"]
        if getattr(args, "detach", False):
            return {"machine_id": args.machine_id, "run_id": args.run_id, "run": metadata,
                    "result_dir": str(run_path(args.machine_id, args.run_id))}
        return wait(args)
    definition = load_machine(args.machine_id)
    path, metadata = create_run(definition, options)
    run = Run(path, metadata, Machine.initial(definition))
    run.thread.start()
    try:
        while run.thread.is_alive():
            run.thread.join(.1)
    except KeyboardInterrupt:
        if not run.finished:
            run.control("cancel")
        run.thread.join()
        raise CliError("Foreground experiment cancelled", 130, local_status(args.machine_id, metadata["run_id"]))
    value = local_status(args.machine_id, metadata["run_id"])
    if value["result"] is None or value["result"].get("reason") == "error":
        raise CliError("Experiment execution failed", 5, value)
    return value


def read_definition(file):
    return json.load(sys.stdin) if file == "-" else json.loads(Path(file).read_text(encoding="utf-8-sig"))


def trace_samples(args):
    if not 1 <= args.limit <= 1200:
        raise CliError("Trace sample limit must be 1–1200")
    if args.server:
        trace = request(args.server, endpoint(args) + "/trace")
    else:
        path, samples = run_path(args.machine_id, args.run_id), []
        with (path / "traces" / "samples.jsonl").open(encoding="utf-8") as file:
            for line in file:
                try:
                    samples.append(json.loads(line))
                except ValueError:
                    continue
        trace = {"samples": samples, "stored_interval": read_json(path / "run.json")["trace_interval"], "response_stride": 1}
    samples = trace["samples"]
    if len(samples) > args.limit:
        indexes = [len(samples)-1] if args.limit == 1 else [i*(len(samples)-1)//(args.limit-1) for i in range(args.limit)]
        trace["samples"] = [samples[i] for i in indexes]
    trace.update(returned_samples=len(trace["samples"]), available_samples=len(samples), cli_sampled=len(samples) > args.limit)
    return trace


def dispatch(args):
    if args.command == "capabilities":
        return {"interface_version": 1, "commands": ["machines list/show/validate/import/template", "run", "batch", "runs list", "status", "wait", "control", "frame", "trace", "verify", "export"],
                "modes": {"local": "No service required; foreground execution and file-based reads", "server": "Shared local service owns background execution and control"},
                "machine_schemas": [1, 2], "symbols": {"min_count": 2, "max_count": 32, "blank": 0},
                "exit_codes": {"0": "Succeeded (UNKNOWN is valid)", "2": "Invalid input or conflict", "3": "Not found", "4": "Service unavailable", "5": "Execution or evidence failure", "6": "Wait deadline", "130": "Interrupted"}}
    if args.command == "machines":
        if args.operation == "list":
            return request(args.server, "/machines") if args.server else list_machines()
        if args.operation == "show":
            return request(args.server, "/machines/" + quote(args.machine_id, safe="")) if args.server else load_machine(args.machine_id)
        if args.operation == "template":
            symbols = [int(x.strip()) for x in args.symbols.split(",")]
            if not 1 <= args.states <= 32:
                raise CliError("State count must be 1–32")
            states = ([s for s in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if s != "H"] + [f"S{i}" for i in range(1, 8)])[:args.states]
            data = {"schema_version": 2, "id": args.machine_id, "name": args.machine_id, "description": "Edit transitions before importing.", "semantics": MULTI_SEMANTICS,
                    "states": states, "symbols": symbols, "blank_symbol": 0, "halt_state": "H", "initial": {"state": "A", "head": 0, "cells": []},
                    "transitions": {s: {str(x): {"write": x, "move": "R", "next": "H"} for x in symbols} for s in states}}
            return validate_machine(data)
        data = validate_machine(read_definition(args.file))
        if args.operation == "validate":
            return {"valid": True, "machine_id": data["id"], "machine_sha256": machine_digest(data)}
        return request(args.server, "/machines", data) if args.server else save_machine(data)
    if args.command == "runs":
        if not 1 <= args.limit <= 1000:
            raise CliError("Run listing limit must be 1–1000")
        values = request(args.server, "/runs") if args.server else list_runs()
        return [v for v in values if (not args.machine or v["machine_id"] == args.machine) and (not args.conclusion or (v.get("result") or {}).get("conclusion") == args.conclusion)][:args.limit]
    if args.command == "run":
        return execute_run(args)
    if args.command == "batch":
        budget(args)
        for machine_id in args.machine_ids:
            request(args.server, "/machines/" + quote(machine_id, safe="")) if args.server else load_machine(machine_id)
        results = []
        for machine_id in args.machine_ids:
            args.machine_id = machine_id
            try:
                results.append(execute_run(args))
            except CliError as exc:
                raise CliError(str(exc), exc.code, {"completed": results, "current": exc.data}) from exc
        return results
    if args.command == "status":
        return status(args)
    if args.command == "wait":
        return wait(args)
    if args.command == "control":
        if not args.server:
            raise CliError("Control requires --server; only the owning service changes live runs")
        return request(args.server, endpoint(args) + "/control", {"action": args.action})
    if args.command == "frame":
        step = integer(args.step)
        return request(args.server, endpoint(args) + "/frame?" + urlencode({"step": str(step)})) if args.server else replay_frame(run_path(args.machine_id, args.run_id), step)
    if args.command == "trace":
        return trace_samples(args)
    if args.command == "export":
        from experiments.visualization import save_spacetime_svg
        trace = trace_samples(args)
        return save_spacetime_svg(run_path(args.machine_id, args.run_id), status(args)["machine"], trace)
    if args.command == "verify":
        if not 1 <= args.max_steps <= 1_000_000:
            raise CliError("Verification budget must be 1–1000000 steps")
        if args.server:
            if args.max_steps != 1_000_000:
                raise CliError("Server verification uses a fixed 1000000-step budget")
            result = request(args.server, endpoint(args) + "/verify", {})
        else:
            path = run_path(args.machine_id, args.run_id)
            result = verify_evidence(read_json(path / "machine.json"), read_json(path / "evidence" / "certificate.json"), args.max_steps)
            write_json(path / "evidence" / "verification.json", result)
        if result["valid"] is False:
            raise CliError("Evidence verification failed", 5, result)
        return result
    raise CliError("Unsupported command")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"capabilities", "machines", "runs", "run", "batch", "status", "wait", "frame", "trace", "verify", "export", "control"}
    if argv and not argv[0].startswith("-") and argv[0] not in commands:
        argv.insert(0, "run")  # Original MACHINE_ID invocation remains supported.
    args = None
    try:
        args = parser().parse_args(argv)
        response, code = {"interface_version": 1, "ok": True, "command": args.command, "data": dispatch(args)}, 0
    except CliError as exc:
        response, code = {"interface_version": 1, "ok": False, "error": {"code": exc.code, "message": str(exc)}, "data": exc.data}, exc.code
    except FileNotFoundError as exc:
        response, code = {"interface_version": 1, "ok": False, "error": {"code": 3, "message": str(exc)}}, 3
    except (ValueError, TypeError, KeyError, FileExistsError) as exc:
        response, code = {"interface_version": 1, "ok": False, "error": {"code": 2, "message": str(exc)}}, 2
    except KeyboardInterrupt:
        response, code = {"interface_version": 1, "ok": False, "error": {"code": 130, "message": "Interrupted; background runs unchanged"}}, 130
    except Exception as exc:
        response, code = {"interface_version": 1, "ok": False, "error": {"code": 5, "message": str(exc)}}, 5
    print(json.dumps(response, ensure_ascii=True, indent=2 if args and args.pretty else None))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""Finite-alphabet, two-way Turing machines with legacy binary compatibility."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

SEMANTICS = "binary-two-way-write-move-halt-v1"
MULTI_SEMANTICS = "multi-symbol-two-way-write-move-halt-v1"
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Coordinates and counts must be integers")
    if isinstance(value, str) and not re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
        raise ValueError("Invalid integer")
    return int(value)


def validate_machine(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Machine configuration must be a JSON object")
    if not isinstance(data.get("id"), str) or not ID_PATTERN.fullmatch(data["id"]):
        raise ValueError("Machine ID must contain 1–64 letters, digits, underscores or hyphens")
    legacy = data.get("schema_version") == 1 and data.get("semantics") == SEMANTICS
    multi = data.get("schema_version") == 2 and data.get("semantics") == MULTI_SEMANTICS
    if type(data.get("schema_version")) is not int or not (legacy or multi):
        raise ValueError("Unsupported configuration or semantics version")
    states = data.get("states")
    if (not isinstance(states, list) or not 1 <= len(states) <= 32
            or any(not isinstance(s, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,7}", s) for s in states)
            or len(set(states)) != len(states) or "H" in states):
        raise ValueError("Use 1–32 unique working states, excluding halt state H")
    symbols = data.get("symbols")
    if (not isinstance(symbols, list) or not 2 <= len(symbols) <= 32
            or any(type(x) is not int or not 0 <= x <= 255 for x in symbols)
            or len(set(symbols)) != len(symbols) or 0 not in symbols
            or (legacy and symbols != [0, 1])
            or type(data.get("blank_symbol")) is not int or data["blank_symbol"] != 0 or data.get("halt_state") != "H"):
        raise ValueError("Use 2–32 distinct integer symbols (0–255), blank 0, halt H; schema v1 requires [0, 1]")
    initial = data.get("initial", {})
    if not isinstance(initial, dict) or initial.get("state") not in states:
        raise ValueError("Initial state must be a working state")
    integer(initial.get("head"))
    cells = initial.get("nonzero_cells") if legacy else initial.get("cells")
    if not isinstance(cells, list) or len(cells) > 100000:
        raise ValueError("Initial tape must be a list of at most 100000 nonblank cells")
    if multi:
        if "nonzero_cells" in initial:
            raise ValueError("Schema v2 uses initial.cells, not initial.nonzero_cells")
        for cell in cells:
            if (not isinstance(cell, dict) or set(cell) != {"position", "symbol"}
                    or type(cell["symbol"]) is not int or cell["symbol"] == 0 or cell["symbol"] not in symbols):
                raise ValueError("Each initial cell needs a position and a declared nonblank symbol")
    positions = [integer(x if legacy else x["position"]) for x in cells]
    if len(set(positions)) != len(cells):
        raise ValueError("Initial cell positions must be unique")
    transitions = data.get("transitions")
    if not isinstance(transitions, dict) or set(transitions) != set(states):
        raise ValueError("Transition table must cover every working state")
    for state in states:
        row = transitions[state]
        if not isinstance(row, dict) or set(row) != set(map(str, symbols)):
            raise ValueError(f"State {state} needs a transition for every declared symbol")
        for action in row.values():
            if (not isinstance(action, dict) or type(action.get("write")) is not int
                    or action["write"] not in symbols or action.get("move") not in ("L", "R")
                    or action.get("next") not in states + ["H"]):
                raise ValueError(f"Invalid transition in state {state}")
    return data


def machine_digest(data):
    semantic = {key: data[key] for key in ("schema_version", "semantics", "states", "symbols",
                                          "blank_symbol", "halt_state", "initial", "transitions")}
    semantic["states"] = sorted(semantic["states"])
    semantic["initial"] = {"state": data["initial"]["state"], "head": str(integer(data["initial"]["head"]))}
    if data["schema_version"] == 1:
        semantic["initial"]["nonzero_cells"] = [str(x) for x in sorted(map(integer, data["initial"]["nonzero_cells"]))]
    else:
        semantic["symbols"] = sorted(data["symbols"])
        semantic["initial"]["cells"] = [{"position": str(p), "symbol": s} for p, s in sorted(initial_tape(data).items())]
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def initial_tape(definition):
    initial = definition["initial"]
    if definition["schema_version"] == 1:
        return {integer(p): 1 for p in initial["nonzero_cells"]}
    return {integer(c["position"]): c["symbol"] for c in initial["cells"]}


def snapshot_tape(snapshot):
    if "cells" in snapshot:
        return {integer(c["position"]): c["symbol"] for c in snapshot["cells"]}
    return {integer(p): 1 for p in snapshot["nonzero_cells"]}


@dataclass
class Machine:
    definition: dict
    state: str
    head: int
    cells: dict[int, int] = field(default_factory=dict)
    steps: int = 0
    min_head: int = 0
    max_head: int = 0
    last_transition: dict | None = None

    @classmethod
    def initial(cls, definition):
        validate_machine(definition)
        source = definition["initial"]
        head = integer(source["head"])
        return cls(definition, source["state"], head, initial_tape(definition),
                   min_head=head, max_head=head)

    @classmethod
    def restore(cls, definition, snapshot):
        return cls(definition, snapshot["state"], integer(snapshot["head"]),
                   snapshot_tape(snapshot), integer(snapshot["steps"]),
                   integer(snapshot["min_head"]), integer(snapshot["max_head"]), snapshot.get("last_transition"))

    def key(self):
        return self.state, self.head, tuple(sorted(self.cells.items()))

    def step(self):
        if self.state == "H":
            return False
        read = self.cells.get(self.head, 0)
        action = self.definition["transitions"][self.state][str(read)]
        self.last_transition = {"state": self.state, "read": read, "head": str(self.head), **action}
        if action["write"]:
            self.cells[self.head] = action["write"]
        else:
            self.cells.pop(self.head, None)
        self.head += 1 if action["move"] == "R" else -1
        self.state = action["next"]
        self.steps += 1
        self.min_head = min(self.min_head, self.head)
        self.max_head = max(self.max_head, self.head)
        return True

    def snapshot(self):
        counts = {str(s): str(sum(value == s for value in self.cells.values())) for s in self.definition["symbols"] if s != 0}
        return {"steps": str(self.steps), "state": self.state, "head": str(self.head),
                "cells": [{"position": str(p), "symbol": s} for p, s in sorted(self.cells.items())],
                "nonzero_cells": [str(p) for p, s in sorted(self.cells.items()) if s == 1],
                "ones": counts.get("1", "0"), "nonblank_count": str(len(self.cells)), "symbol_counts": counts,
                "min_head": str(self.min_head), "max_head": str(self.max_head),
                "last_transition": self.last_transition}

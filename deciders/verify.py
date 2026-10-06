"""A deliberately separate reference replay implementation for evidence checks."""
from core.machine import initial_tape, integer, machine_digest, snapshot_tape, validate_machine


def verify_evidence(definition, evidence, limit=1_000_000):
    validate_machine(definition)
    if evidence.get("machine_sha256") != machine_digest(definition):
        return {"valid": False, "reason": "Machine digest mismatch"}
    kind = evidence.get("kind")
    if kind not in ("halt", "exact_cycle"):
        return {"valid": False, "reason": "Unsupported evidence type"}
    end = integer(evidence.get("end_step", -1))
    start = integer(evidence.get("start_step", -1)) if kind == "exact_cycle" else -1
    if end < 1 or (kind == "exact_cycle" and not 0 <= start < end):
        return {"valid": False, "reason": "Invalid evidence step range"}
    if end > limit:
        return {"valid": None, "reason": f"Independent replay exceeds the verification budget of {limit} steps"}
    state = definition["initial"]["state"]
    head = integer(definition["initial"]["head"])
    tape = initial_tape(definition)
    first = None
    for step in range(end + 1):
        config = (state, head, frozenset(tape.items()))
        if step == start:
            first = config
        if step == end:
            valid = (state == "H") if kind == "halt" else (state != "H" and config == first)
            if evidence.get("final") is not None:
                claimed = evidence["final"]
                valid = valid and (claimed["state"], integer(claimed["head"]),
                                   frozenset(snapshot_tape(claimed).items())) == config
            return {"valid": valid, "reason": "Independent step-by-step replay verified" if valid else "Configuration or conclusion mismatch",
                    "checked_steps": str(end)}
        if state == "H":
            return {"valid": False, "reason": "Machine halted before the claimed step"}
        read = str(tape.get(head, 0))
        rule = definition["transitions"][state][read]
        if rule["write"] != 0:
            tape[head] = rule["write"]
        else:
            tape.pop(head, None)
        head += {"L": -1, "R": 1}[rule["move"]]
        state = rule["next"]

# Busy Beaver Lab — agent entry point

Read [AGENT_PROMPT.md](AGENT_PROMPT.md) for the project operating prompt and [docs/agent-cli.md](docs/agent-cli.md) for the terminal contract.

- Prefer `./bb-lab.ps1` or the repository Conda Python (`.conda/env/python.exe`) with `-m experiments.cli`. Do not depend on the active shell's Python.
- Prefer the terminal interface for experiments. Local foreground runs need no service; use `--server http://127.0.0.1:8765` for background runs and controls. Global flags come before the command.
- Parse JSON and preserve machine IDs, run IDs, paths, budgets and evidence. `UNKNOWN` is an experimental outcome, not an error or a non-halting proof.
- Use a new machine ID for changed behavior. Import configurations through the CLI; do not overwrite existing configurations or archived results.
- Never instantiate `Laboratory()` from an agent command to inspect or control running experiments: its startup recovery changes persisted task states. Use the owning service for controls.
- Keep the GUI and user-facing runtime messages in English. Preserve schema-v1 binary definitions, historical digests and checkpoints when modifying the implementation.
- Before claiming a result, inspect the result and, when available, verify its certificate. Report verification budget exhaustion as unverified.
- Run the relevant unit tests after core/interface changes. No GPU or additional environment is required for the present laboratory.

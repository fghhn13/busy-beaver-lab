# Agent terminal interface

Use `./bb-lab.ps1 <command>` from PowerShell, or `.conda/env/python.exe -m experiments.cli <command>`. The wrapper also works from another directory. No new dependencies are required. The original `python -m experiments.cli MACHINE_ID ...` form is retained, but now returns the JSON contract.

## Response and exit codes

Except for `--help`, stdout contains exactly one JSON object, compact by default. Put `--pretty` before the command for indented output. Success:

```json
{"interface_version":1,"ok":true,"command":"run","data":{"run":{},"snapshot":{},"result":{},"machine":{},"result_dir":"absolute path"}}
```

Errors contain `ok: false`, `error: {code, message}`, and optional `data` with partial progress. Do not extract identifiers with text matching; parse JSON. The actual process exit code matches `error.code`.

| Exit | Meaning |
|---|---|
| 0 | Command succeeded; a run may still be UNKNOWN or an evidence check may be unverified (`valid: null`) |
| 2 | Invalid input, conflict, unsupported mode |
| 3 | Missing machine, file, run or certificate |
| 4 | Local service unreachable |
| 5 | Execution error or rejected evidence |
| 6 | Wait deadline; the experiment remains unchanged |
| 130 | Command interrupted |

## Commands

| Command | Purpose |
|---|---|
| `capabilities` | Discover supported commands, schemas and exit codes |
| `machines list` | List machine definitions |
| `machines show ID` | Read one complete definition |
| `machines template ID --states 5 --symbols 0,1,2` | Return an editable v2 definition under `data`; saves nothing |
| `machines validate FILE` | Validate a definition and return its behavior digest |
| `machines import FILE` | Save a new independent machine; conflicts do not overwrite |
| `run ID [budgets]` | Run in the foreground, or submit/wait in server mode |
| `batch ID ID ... [budgets]` | Validate all machines first, then run sequentially |
| `runs list --machine ID --conclusion HALTED --limit 20` | Filter saved experiments |
| `status ID RUN_ID` | Read metadata, full snapshot, configuration and result |
| `wait ID RUN_ID --wait-seconds 30` | Observe completion with a bounded deadline |
| `control ID RUN_ID pause/resume/step/cancel` | Submit a control action; requires `--server` |
| `frame ID RUN_ID --step 3` | Reconstruct an exact configuration from a checkpoint |
| `trace ID RUN_ID --limit 100` | Read bounded sampled history with actual step numbers |
| `verify ID RUN_ID` | Independently check the saved certificate |
| `export ID RUN_ID --limit 500` | Save sampled spacetime as SVG and JSON metadata |

`FILE` may be `-` for JSON supplied on stdin. Both schemas 1 and 2 are supported. Inputs from files may have a UTF-8 BOM. Template output is an envelope: import only its `data` definition, not the entire response.

Budgets for `run`/`batch`: `--max-steps`, `--max-seconds`, `--max-cells`, `--trace-interval`, `--checkpoint-interval`, and `--no-cycle-detection`. The existing `max_nonzero_cells` metadata field means **all nonblank cells**, including symbols other than 1. Budgets are validated by the shared engine. For local mode, `--max-seconds` limits active computation; `--wait-seconds` applies only to server waiting.

The trace response records `stored_interval`, server `response_stride`, CLI `available_samples`/`returned_samples`, and `cli_sampled`. CLI sampling may be uneven; always use each frame's actual `steps`. SVG exports use these samples without inventing intermediate configurations.

## Local foreground workflow

```powershell
$response = ./bb-lab.ps1 run tm5_three_symbols --max-steps 1000 --max-seconds 30 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw $response.error.message }
$runId = $response.data.run.run_id
./bb-lab.ps1 status tm5_three_symbols $runId
./bb-lab.ps1 frame tm5_three_symbols $runId --step 1
./bb-lab.ps1 verify tm5_three_symbols $runId
./bb-lab.ps1 export tm5_three_symbols $runId
```

No HTTP server is required. A new result directory is created each time. Ctrl+C requests cancellation of the foreground experiment. Local commands never construct a `Laboratory` controller or reset active task states. To control a running server-owned experiment, use server mode.

## Background / shared-service workflow

Start the service in a separate terminal using `./start-lab.ps1` (no browser is necessary). Reuse an existing service for the same repository. Global options precede the command:

```powershell
$response = ./bb-lab.ps1 --server http://127.0.0.1:8765 run tm5_three_symbols --max-steps 1000 --detach --start-paused | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw $response.error.message }
$runId = $response.data.run_id
./bb-lab.ps1 --server http://127.0.0.1:8765 control tm5_three_symbols $runId step
./bb-lab.ps1 --server http://127.0.0.1:8765 status tm5_three_symbols $runId
./bb-lab.ps1 --server http://127.0.0.1:8765 control tm5_three_symbols $runId resume
./bb-lab.ps1 --server http://127.0.0.1:8765 wait tm5_three_symbols $runId --wait-seconds 30
```

Control acceptance does not guarantee the action has already executed. A zero-second wait is an immediate completion check. A wait timeout returns the latest state and does not cancel the run. The server may manage four open experiments; batch mode submits and waits sequentially. Server mode targets a local service for this same repository; SVG exports are written by the CLI to the shared result directory.

Local certificate verification accepts `--max-steps 1..1000000`; server verification uses its fixed one-million-step budget. `valid: null` is unverified, not valid. No certificate is expected for ordinary undecided results.

Files and artifacts are unchanged: `results/<machine_id>/<run_id>/`. All coordinates/counts that may exceed JavaScript integer precision remain decimal strings. Agents should preserve IDs and absolute paths from the response.

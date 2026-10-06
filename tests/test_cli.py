import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from experiments import cli, storage


class AgentCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        machines = self.root / "machines"
        machines.mkdir()
        for name in ("tm5_three_symbols", "three_symbol_cycle", "right_drifter"):
            shutil.copyfile(storage.MACHINES / f"{name}.json", machines / f"{name}.json")
        self.patches = [patch.object(storage, "MACHINES", machines), patch.object(storage, "RESULTS", self.root / "results")]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def call(self, args, stdin=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("sys.stdin", io.StringIO(stdin or "")):
            code = cli.main(args)
        value = json.loads(output.getvalue())  # Reject multiple objects or non-JSON noise.
        self.assertEqual(value["interface_version"], 1)
        self.assertEqual(value["ok"], code == 0)
        if code: self.assertEqual(value["error"]["code"], code)
        return code, value

    def run_demo(self):
        code, value = self.call(["run", "tm5_three_symbols", "--max-steps", "1000"])
        self.assertEqual(code, 0)
        return value["data"]

    def test_local_workflow_frame_trace_verify_export(self):
        data = self.run_demo()
        mid, rid = data["run"]["machine_id"], data["run"]["run_id"]
        self.assertEqual(data["result"]["conclusion"], "HALTED")
        code, value = self.call(["frame", mid, rid, "--step", "1"])
        self.assertEqual(value["data"]["cells"], [{"position":"0", "symbol":2}])
        code, value = self.call(["trace", mid, rid, "--limit", "2"])
        self.assertEqual([s["steps"] for s in value["data"]["samples"]], ["0","5"])
        self.assertTrue(value["data"]["cli_sampled"])
        code, value = self.call(["verify", mid, rid])
        self.assertTrue(value["data"]["valid"])
        code, value = self.call(["export", mid, rid])
        svg = Path(value["data"]["path"])
        self.assertTrue(svg.is_relative_to(self.root / "results"))
        self.assertEqual(ElementTree.parse(svg).getroot().tag, "{http://www.w3.org/2000/svg}svg")
        self.assertTrue(Path(value["data"]["metadata_path"]).exists())

    def test_template_stdin_import_validation_and_conflict(self):
        code, value = self.call(["machines", "template", "agent_machine", "--states", "32", "--symbols", "0,3,7,9"])
        definition = value["data"]
        self.assertNotIn("H", definition["states"])
        raw = json.dumps(definition)
        self.assertEqual(self.call(["machines", "validate", "-"], raw)[0], 0)
        self.assertEqual(self.call(["machines", "import", "-"], raw)[0], 0)
        self.assertEqual(self.call(["machines", "import", "-"], raw)[0], 2)

    def test_unknown_is_success_and_batch_is_independent(self):
        code, value = self.call(["batch", "right_drifter", "three_symbol_cycle", "--max-steps", "20"])
        self.assertEqual(code, 0)
        self.assertEqual([d["result"]["conclusion"] for d in value["data"]], ["UNKNOWN", "NON_HALTING"])
        self.assertNotEqual(value["data"][0]["result_dir"], value["data"][1]["result_dir"])
        code, listing = self.call(["runs", "list", "--conclusion", "UNKNOWN"])
        self.assertEqual(len(listing["data"]), 1)

    def test_errors_missing_invalid_control_and_no_partial_batch(self):
        for args, expected in ((["machines","show","absent"],3),(["run","tm5_three_symbols","--detach"],2),
                               (["run","tm5_three_symbols","--max-steps","invalid"],2),
                               (["control","tm5_three_symbols","unused","pause"],2),
                               (["batch","tm5_three_symbols","missing"],3),
                               (["--server","https://example.com","machines","list"],2)):
            self.assertEqual(self.call(args)[0], expected)
        self.assertEqual(storage.list_runs(), [])

    def test_verification_failure_and_insufficient_budget(self):
        data = self.run_demo()
        mid, rid = data["run"]["machine_id"], data["run"]["run_id"]
        code, value = self.call(["verify", mid, rid, "--max-steps", "1"])
        self.assertEqual(code, 0)
        self.assertIsNone(value["data"]["valid"])
        path = Path(data["result_dir"]) / "evidence" / "certificate.json"
        certificate = storage.read_json(path)
        certificate["end_step"] = "4"
        storage.write_json(path, certificate)
        self.assertEqual(self.call(["verify", mid, rid])[0], 5)

    def test_wait_timeout_preserves_unfinished_run(self):
        definition = storage.load_machine("right_drifter")
        path, metadata = storage.create_run(definition, cli.validate_options({"start_paused":True}))
        storage.write_json(path / "snapshot.json", cli.Machine.initial(definition).snapshot())
        code, value = self.call(["wait", "right_drifter", metadata["run_id"], "--wait-seconds", "0"])
        self.assertEqual(code, 6)
        self.assertEqual(value["data"]["run"]["run_id"], metadata["run_id"])
        self.assertFalse((path / "result.json").exists())
        self.assertEqual(storage.read_json(path / "run.json")["status"], "QUEUED")

    def test_legacy_invocation_still_returns_json(self):
        self.assertEqual(self.call(["tm5_three_symbols", "--max-steps", "1000"])[0], 0)

    def test_concurrent_import_cannot_replace_a_newly_published_machine(self):
        _, template = self.call(["machines", "template", "concurrent_import"])
        path = storage.machine_path("concurrent_import")
        original = dict(template["data"], description="first publisher")
        publish = storage.os.rename if storage.os.name == "nt" else storage.os.link

        def competing_publish(source, destination):
            path.write_text(json.dumps(original), encoding="utf-8")
            return publish(source, destination)

        operation = "rename" if storage.os.name == "nt" else "link"
        with patch.object(storage.os, operation, side_effect=competing_publish):
            self.assertEqual(self.call(["machines", "import", "-"], json.dumps(template["data"]))[0], 2)
        self.assertEqual(storage.read_json(path)["description"], "first publisher")


if __name__ == "__main__":
    unittest.main()

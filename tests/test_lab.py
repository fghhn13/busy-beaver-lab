import copy
import tempfile
import time
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from core.machine import Machine, machine_digest, validate_machine, MULTI_SEMANTICS
from deciders.verify import verify_evidence
from experiments import storage
from experiments.runner import Laboratory, Run, replay_frame, validate_options


class CoreTests(unittest.TestCase):
    def test_champion_and_negative_coordinates(self):
        machine = Machine.initial(storage.load_machine("bb2_champion"))
        while machine.step():
            pass
        self.assertEqual((machine.steps, machine.state, len(machine.cells)), (6, "H", 4))
        self.assertEqual(machine.cells, {-2: 1, -1: 1, 0: 1, 1: 1})
        self.assertEqual(machine.min_head, -2)

    def test_restore_matches_uninterrupted_execution(self):
        original = Machine.initial(storage.load_machine("bb2_champion"))
        for _ in range(3):
            original.step()
        resumed = Machine.restore(original.definition, original.snapshot())
        while original.step():
            resumed.step()
        self.assertEqual(original.snapshot(), resumed.snapshot())

    def test_configuration_validation(self):
        definition = storage.load_machine("halt_immediately")
        for mutation in (lambda d: d.update(id="../escape"),
                         lambda d: d["transitions"]["A"].pop("1"),
                         lambda d: d["transitions"]["A"]["0"].update(next="X"),
                         lambda d: d.update(symbols=[False, True]),
                         lambda d: d["initial"].update(nonzero_cells=[0, "0"])):
            invalid = copy.deepcopy(definition)
            mutation(invalid)
            with self.assertRaises(ValueError):
                validate_machine(invalid)

    def test_digest_ignores_description_and_normalizes_coordinates(self):
        definition = storage.load_machine("bb2_champion")
        edited = copy.deepcopy(definition)
        edited["description"] = "new label"
        edited["initial"]["head"] = "0"
        self.assertEqual(machine_digest(definition), machine_digest(edited))

    def test_cycle_evidence_rejects_forged_witness(self):
        definition = storage.load_machine("two_step_cycle")
        evidence = {"kind": "exact_cycle", "start_step": "0", "end_step": "2", "machine_sha256": machine_digest(definition)}
        self.assertTrue(verify_evidence(definition, evidence)["valid"])
        self.assertFalse(verify_evidence(definition, evidence | {"end_step": "1"})["valid"])
        self.assertFalse(verify_evidence(definition, evidence | {"machine_sha256": "bad"})["valid"])
        self.assertIsNone(verify_evidence(definition, evidence, limit=1)["valid"])

    def test_multi_symbol_execution_counts_and_restore(self):
        definition = storage.load_machine("tm5_three_symbols")
        machine = Machine.initial(definition)
        machine.step()
        self.assertEqual(machine.cells, {0:2})
        self.assertEqual(machine.snapshot()["ones"], "0")
        resumed = Machine.restore(definition, machine.snapshot())
        while machine.step():
            resumed.step()
        self.assertEqual(machine.snapshot(), resumed.snapshot())
        self.assertEqual((machine.steps,machine.state,machine.cells), (5,"H",{0:2,1:1,2:2}))
        self.assertEqual(machine.snapshot()["symbol_counts"], {"1":"1","2":"2"})

    def test_symbol_values_are_part_of_cycle_identity(self):
        definition = storage.load_machine("three_symbol_cycle")
        left = Machine.initial(definition)
        right = Machine.initial(definition)
        left.cells[0], right.cells[0] = 1, 2
        self.assertNotEqual(left.key(), right.key())
        evidence={"kind":"exact_cycle","start_step":"0","end_step":"4","machine_sha256":machine_digest(definition)}
        self.assertTrue(verify_evidence(definition,evidence)["valid"])
        claimed=Machine.initial(definition).snapshot()
        claimed["cells"]=[{"position":"0","symbol":2}]
        self.assertFalse(verify_evidence(definition,evidence | {"final":claimed})["valid"])

    def test_multi_symbol_validation_and_initial_tape(self):
        definition=storage.load_machine("tm5_three_symbols")
        for mutation in (lambda d:d["transitions"]["A"].pop("2"),
                         lambda d:d["transitions"]["A"]["0"].update(write=3),
                         lambda d:d["initial"].update(cells=[{"position":0,"symbol":0}]),
                         lambda d:d["initial"].update(cells=[{"position":0,"symbol":2},{"position":"0","symbol":1}])):
            invalid=copy.deepcopy(definition); mutation(invalid)
            with self.assertRaises(ValueError): validate_machine(invalid)
        definition["initial"]["cells"]=[{"position":"-5","symbol":2}]
        self.assertEqual(Machine.initial(definition).cells,{-5:2})

    def test_nonconsecutive_four_symbol_alphabet(self):
        definition={"schema_version":2,"id":"four_symbols","semantics":MULTI_SEMANTICS,
                    "states":["A"],"symbols":[0,3,7,9],"blank_symbol":0,"halt_state":"H",
                    "initial":{"state":"A","head":0,"cells":[{"position":0,"symbol":7}]},
                    "transitions":{"A":{str(s):{"write":9,"move":"L","next":"H"} for s in [0,3,7,9]}}}
        machine=Machine.initial(definition); machine.step()
        self.assertEqual(machine.cells,{0:9})
        self.assertEqual(machine.last_transition["read"],7)

    def test_legacy_snapshot_and_digest_compatibility(self):
        definition=storage.load_machine("bb2_champion")
        self.assertEqual(machine_digest(definition),"f4d50cb8946a9b3c49dfdba9112b170c9053783d2b9b4c582d8219612b9bc4aa")
        legacy={"state":"A","head":"0","steps":"0","nonzero_cells":[],"ones":"0","min_head":"0","max_head":"0","last_transition":None}
        self.assertEqual(Machine.restore(definition,legacy).snapshot(),Machine.initial(definition).snapshot())
        final=Machine.initial(definition)
        while final.step(): pass
        old_final=final.snapshot()
        for key in ("cells","symbol_counts","nonblank_count"): old_final.pop(key)
        evidence={"kind":"halt","end_step":"6","machine_sha256":machine_digest(definition),"final":old_final}
        self.assertTrue(verify_evidence(definition,evidence)["valid"])


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(storage, "RESULTS", Path(self.temp.name) / "results")
        self.patcher.start()
        self.lab = Laboratory()

    def tearDown(self):
        self.lab.close()
        self.patcher.stop()
        self.temp.cleanup()

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("worker did not reach requested state")
            time.sleep(.01)

    def run_machine(self, machine_id, **options):
        metadata = self.lab.start(machine_id, options)
        run = self.lab.runs[(machine_id, metadata["run_id"])]
        run.thread.join(timeout=5)
        self.assertFalse(run.thread.is_alive())
        return run, storage.read_json(run.path / "result.json")

    def test_three_conclusions_and_isolated_results(self):
        paths = []
        for machine_id, conclusion, steps in (("halt_immediately", "HALTED", 1),
                                              ("two_step_cycle", "NON_HALTING", 2),
                                              ("right_drifter", "UNKNOWN", 20)):
            run, result = self.run_machine(machine_id, max_steps=20)
            self.assertEqual((result["conclusion"], result["steps"]), (conclusion, str(steps)))
            self.assertTrue((run.path / "machine.json").is_file())
            self.assertTrue(list((run.path / "checkpoints").glob("*.json")))
            paths.append(run.path)
        second, _ = self.run_machine("halt_immediately")
        self.assertNotIn(second.path, paths)

    def test_replay_between_checkpoints(self):
        run, result = self.run_machine("bb2_champion", checkpoint_interval=5)
        expected = Machine.initial(run.machine.definition)
        for step in range(7):
            self.assertEqual(replay_frame(run.path, step), expected.snapshot())
            expected.step()
        with self.assertRaises(ValueError):
            replay_frame(run.path, 7)

    def test_multi_symbol_results_replay_and_evidence(self):
        run,result=self.run_machine("tm5_three_symbols",checkpoint_interval=3)
        self.assertEqual((result["conclusion"],result["steps"],result["nonblank_count"],result["ones"]),("HALTED","5","3","1"))
        self.assertEqual(result["symbol_counts"],{"1":"1","2":"2"})
        self.assertTrue(result["verification"]["valid"])
        self.assertEqual(replay_frame(run.path,1)["cells"],[{"position":"0","symbol":2}])
        cycle,result=self.run_machine("three_symbol_cycle")
        self.assertEqual((result["conclusion"],result["steps"]),("NON_HALTING","4"))
        self.assertTrue(result["verification"]["valid"])

    def test_pause_step_resume_and_terminal_single_step(self):
        metadata = self.lab.start("halt_immediately", {"start_paused": True})
        run = self.lab.runs[("halt_immediately", metadata["run_id"])]
        self.wait(lambda: run.metadata["status"] == "PAUSED")
        self.assertEqual(run.machine.steps, 0)
        run.control("step")
        run.thread.join(timeout=5)
        self.assertEqual(storage.read_json(run.path / "result.json")["conclusion"], "HALTED")
        metadata = self.lab.start("bb2_champion", {"start_paused": True})
        run = self.lab.runs[("bb2_champion", metadata["run_id"])]
        self.wait(lambda: run.metadata["status"] == "PAUSED")
        run.control("step")
        self.wait(lambda: run.machine.steps == 1)
        self.assertTrue(run.paused)
        run.control("resume")
        run.thread.join(timeout=5)
        self.assertEqual(run.machine.steps, 6)

    def test_disk_recovery(self):
        definition = storage.load_machine("bb2_champion")
        path, metadata = storage.create_run(definition, validate_options({}))
        machine = Machine.initial(definition)
        for _ in range(3):
            machine.step()
        interrupted = Run(path, metadata, machine)
        interrupted.metadata["status"] = "PAUSED"
        interrupted.save(checkpoint=True, trace=True)
        recovered_lab = Laboratory()
        self.assertEqual(storage.read_json(path / "run.json")["status"], "INTERRUPTED")
        recovered_lab.control(definition["id"], metadata["run_id"], "resume")
        run = recovered_lab.runs[(definition["id"], metadata["run_id"])]
        run.thread.join(timeout=5)
        self.assertEqual(run.machine.steps, 6)
        self.assertEqual(storage.read_json(path / "result.json")["conclusion"], "HALTED")
        recovered_lab.close()

    def test_cancel_and_resource_budget_remain_unknown(self):
        metadata = self.lab.start("right_drifter", {"start_paused": True})
        run = self.lab.runs[("right_drifter", metadata["run_id"])]
        run.control("cancel")
        run.thread.join(timeout=5)
        self.assertEqual(storage.read_json(run.path / "result.json")["conclusion"], "UNKNOWN")
        run, result = self.run_machine("bb2_champion", max_nonzero_cells=1)
        self.assertEqual((result["conclusion"], result["reason"]), ("UNKNOWN", "tape_budget"))

    def test_graceful_shutdown_preserves_resumable_checkpoint(self):
        metadata = self.lab.start("bb2_champion", {"start_paused": True})
        run = self.lab.runs[("bb2_champion", metadata["run_id"])]
        self.wait(lambda: run.metadata["status"] == "PAUSED")
        run.control("step")
        self.wait(lambda: run.machine.steps == 1)
        self.lab.close()
        self.assertEqual(storage.read_json(run.path / "run.json")["status"], "INTERRUPTED")
        self.assertFalse((run.path / "result.json").exists())
        recovered_lab = Laboratory()
        recovered_lab.control("bb2_champion", metadata["run_id"], "resume")
        recovered = recovered_lab.runs[("bb2_champion", metadata["run_id"])]
        recovered.thread.join(timeout=5)
        self.assertEqual(recovered.machine.steps, 6)
        recovered_lab.close()

    def test_atomic_write_retries_transient_windows_access_denial(self):
        destination = Path(self.temp.name) / "atomic.json"
        storage.write_json(destination, {"version": 1})
        replace = os.replace
        calls = 0

        def transient(source, target):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError("transient read handle")
            return replace(source, target)

        with patch.object(storage.os, "replace", side_effect=transient):
            storage.write_json(destination, {"version": 2})
        self.assertEqual(storage.read_json(destination), {"version": 2})
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()

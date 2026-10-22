"""Regression cases for queue outcomes and concurrent diagnostic publication."""

import json
import math
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src.diagnostics.validator_session import validate_session_logs
from tests.support.problem_diagnostics_fixture import make_diagnostic_app


class DiagnosticReliabilityTests(unittest.TestCase):
    def test_failed_queue_survives_application_close(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            counts = {"queued_url_count": 3, "downloaded_video_count": 2,
                      "failed_download_count": 1, "problematic_download_count": 0,
                      "converted_audio_count": 2}
            app.write_problem_session_summary("failed", counts)
            app.write_problem_session_summary("finished", {"runtime_elapsed_sec": 25})
            summary = json.loads(app.problem_session_summary_file.read_text(encoding="utf-8"))
            self.assertEqual(summary["context"].get("failed_download_count"), 1)
            self.assertEqual([b["status"] for b in summary.get("download_batches", [])], ["failed"])
            self.assertEqual(summary.get("download_outcome"), "failed")

    def test_all_queues_count_once_and_keep_failed_outcome(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            good = {"queued_url_count": 2, "downloaded_video_count": 2,
                    "failed_download_count": 0, "problematic_download_count": 0,
                    "converted_audio_count": 2}
            app.write_problem_session_summary("completed", good)
            # Re-publication of the same queue must not count it twice.
            app.write_problem_session_summary("completed", good)
            app.current_session_start = "2026-08-03 12:01:00"
            bad = {"queued_url_count": 3, "downloaded_video_count": 2,
                   "failed_download_count": 0, "problematic_download_count": 1,
                   "converted_audio_count": 2}
            app.write_problem_session_summary("failed", bad)
            app.write_problem_session_summary("finished", {})
            summary = json.loads(app.problem_session_summary_file.read_text(encoding="utf-8"))
            self.assertEqual(len(summary.get("download_batches", [])), 2)
            self.assertEqual(summary["download_totals"]["queued_url_count"], 5)
            self.assertEqual(summary["download_totals"]["problematic_download_count"], 1)
            self.assertEqual(summary.get("download_outcome"), "failed")
            history = [json.loads(line) for line in app.problem_health_history_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["queued_url_count"], 5)
            self.assertEqual(history[0]["problematic_download_count"], 1)

    def test_next_success_does_not_erase_previous_failed_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            app.write_problem_session_summary("failed", {"queued_url_count": 1, "failed_download_count": 1})
            app.current_session_start = "2026-08-03 12:02:00"
            app.write_problem_session_summary("completed", {"queued_url_count": 1, "downloaded_video_count": 1})
            summary = json.loads(app.problem_session_summary_file.read_text(encoding="utf-8"))
            self.assertEqual(summary.get("download_outcome"), "failed")
            self.assertEqual(summary["download_totals"]["failed_download_count"], 1)

    def test_concurrent_events_keep_sequence_and_mirrors(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            first_ready, second_done = threading.Event(), threading.Event()
            original = app._problem_fingerprint

            def fingerprint(operation, *args, **kwargs):
                if operation == "first":
                    first_ready.set()
                    second_done.wait(0.3)
                return original(operation, *args, **kwargs)

            def second():
                app.record_problem("second", "INFO", "second")
                second_done.set()

            with mock.patch.object(app, "_problem_fingerprint", side_effect=fingerprint):
                one = threading.Thread(target=app.record_problem, args=("first", "INFO", "first"))
                one.start()
                self.assertTrue(first_ready.wait(2))
                two = threading.Thread(target=second)
                two.start()
                one.join(3)
                two.join(3)
            self.assertFalse(one.is_alive() or two.is_alive())
            events = [json.loads(line) for line in app.problem_events_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["sequence"] for item in events], [1, 2])
            self.assertEqual(app.problem_events_file.read_bytes(), app.problem_log_file.read_bytes())

    def test_terminal_state_cannot_be_overwritten_by_pending_running_update(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            running_ready, terminal_done = threading.Event(), threading.Event()
            original = app._compact_problem_context

            def compact(context, *args, **kwargs):
                if context == {"pending": True}:
                    running_ready.set()
                    terminal_done.wait(0.3)
                return original(context, *args, **kwargs)

            def terminal():
                app._write_active_run_state("finished", "application_closed", terminal=True)
                terminal_done.set()

            with mock.patch.object(app, "_compact_problem_context", side_effect=compact):
                one = threading.Thread(target=app._write_active_run_state,
                                       args=("running", "heartbeat", {"pending": True}))
                one.start()
                self.assertTrue(running_ready.wait(2))
                two = threading.Thread(target=terminal)
                two.start()
                one.join(3)
                two.join(3)
            self.assertFalse(one.is_alive() or two.is_alive())
            for path in (app.problem_session_active_state_file, app.problem_active_run_state_file):
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "finished")

    def test_access_denied_preserves_previous_json_without_blind_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            target = Path(directory) / "данные.json"
            target.write_text('{"previous": true}', encoding="utf-8")
            previous = target.read_bytes()
            denied = PermissionError(13, "Access denied")
            denied.winerror = 5
            with mock.patch.object(os, "replace", side_effect=denied) as replace:
                with self.assertRaises(PermissionError):
                    app._atomic_write_json_file(target, {"new": True})
            self.assertEqual(replace.call_count, 1)
            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(target.parent.glob(".данные.json.*.tmp")), [])

    def test_invalid_numeric_payload_never_replaces_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            target = Path(directory) / "state.json"
            target.write_text('{"previous": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                app._atomic_write_json_file(target, {"invalid": math.nan})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"previous": True})

    def test_failed_state_publication_reaches_emergency_and_keeps_previous_root(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            app._write_active_run_state("running", "before")
            previous = app.problem_active_run_state_file.read_bytes()
            original = os.replace

            def denied_root(source, target):
                if Path(target) == app.problem_active_run_state_file:
                    error = PermissionError(13, "Access denied")
                    error.winerror = 5
                    raise error
                return original(source, target)

            with mock.patch.object(os, "replace", side_effect=denied_root):
                app._write_active_run_state("finished", "application_closed", terminal=True)
            self.assertEqual(app.problem_active_run_state_file.read_bytes(), previous)
            local = json.loads(app.problem_session_active_state_file.read_text(encoding="utf-8"))
            self.assertEqual(local["status"], "finished")
            emergency = [json.loads(line) for line in app.problem_emergency_log_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["operation"] for item in emergency], ["active_run_state_write_failed"])
            app._write_active_run_state("finished", "application_closed", terminal=True)
            self.assertTrue(app.problem_runtime_terminal)

    def test_late_event_cannot_reopen_closed_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            app.write_problem_session_summary("completed", {"queued_url_count": 1, "downloaded_video_count": 1})
            app.finalize_problem_diagnostics("finished", "application_closed")
            app.record_problem("late worker evidence", "WARNING", "late_worker")
            summary = json.loads(app.problem_session_summary_file.read_text(encoding="utf-8"))
            pointer = json.loads(app.problem_latest_run_index_file.read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "finished")
            self.assertEqual(pointer["status"], "finished")
            self.assertEqual(summary["event_counts"]["WARNING"], 1)

    def test_root_terminal_failure_does_not_reopen_local_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            app._write_active_run_state("running", "before")
            original = os.replace

            def denied_root(source, target):
                if Path(target) == app.problem_active_run_state_file:
                    raise PermissionError(13, "Access denied")
                return original(source, target)

            with mock.patch.object(os, "replace", side_effect=denied_root):
                app._write_active_run_state("finished", "application_closed", terminal=True)
            app._write_active_run_state("running", "late_worker")
            local = json.loads(app.problem_session_active_state_file.read_text(encoding="utf-8"))
            self.assertEqual(local["status"], "finished")
            self.assertTrue(app.problem_runtime_terminal)
            app._write_active_run_state("finished", "application_closed", terminal=True)
            self.assertEqual(json.loads(app.problem_active_run_state_file.read_text(encoding="utf-8"))["status"], "finished")

    def test_finalize_can_republish_terminal_after_root_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            original = os.replace

            def denied_root(source, target):
                if Path(target) == app.problem_active_run_state_file:
                    raise PermissionError(13, "Access denied")
                return original(source, target)

            with mock.patch.object(os, "replace", side_effect=denied_root):
                app.finalize_problem_diagnostics("finished", "application_closed")
            self.assertTrue(app.problem_runtime_terminal)
            self.assertFalse(app.problem_terminal_state_published)
            app.record_problem("late worker evidence", "WARNING", "late_worker")
            app.finalize_problem_diagnostics("finished", "application_closed")
            self.assertTrue(app.problem_terminal_state_published)
            self.assertEqual(json.loads(app.problem_active_run_state_file.read_text(encoding="utf-8"))["status"], "finished")

    def test_direct_queue_summary_serializes_with_application_close(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            closing_ready, queue_done = threading.Event(), threading.Event()
            original = app._write_active_run_state

            def state(*args, **kwargs):
                if kwargs.get("terminal"):
                    closing_ready.set()
                    queue_done.wait(0.3)
                return original(*args, **kwargs)

            def queue_result():
                app.write_problem_session_summary("completed", {"queued_url_count": 1, "downloaded_video_count": 1})
                queue_done.set()

            with mock.patch.object(app, "_write_active_run_state", side_effect=state):
                one = threading.Thread(target=app.finalize_problem_diagnostics)
                one.start()
                self.assertTrue(closing_ready.wait(2))
                two = threading.Thread(target=queue_result)
                two.start()
                one.join(3)
                two.join(3)
            self.assertFalse(one.is_alive() or two.is_alive())
            self.assertEqual(json.loads(app.problem_session_summary_file.read_text(encoding="utf-8"))["status"], "finished")
            self.assertEqual(json.loads(app.problem_latest_run_index_file.read_text(encoding="utf-8"))["status"], "finished")

    def test_parallel_mixed_events_produce_valid_ordered_session(self):
        with tempfile.TemporaryDirectory() as directory:
            app = make_diagnostic_app(Path(directory))
            start = threading.Barrier(4)

            def write(worker):
                start.wait(timeout=2)
                for index in range(4):
                    app.record_problem("synthetic evidence", "WARNING" if index % 2 else "INFO",
                                       "parallel_test", {"worker": worker, "sample": index})

            threads = [threading.Thread(target=write, args=(worker,)) for worker in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            app.write_problem_session_summary("finished", {})
            records = [json.loads(line) for line in app.problem_events_file.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["sequence"] for item in records], list(range(1, 17)))
            report = validate_session_logs(app.problem_events_file, app.problem_incidents_file,
                                           app.problem_schema_file, legacy_events_file=app.problem_log_file,
                                           emergency_file=app.problem_emergency_log_file)
            self.assertEqual(report["status"], "passed", report)


if __name__ == "__main__":
    unittest.main()

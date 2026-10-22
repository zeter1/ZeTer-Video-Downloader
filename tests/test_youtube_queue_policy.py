"""Synthetic queue transitions: no network, GUI, real settings or user output."""

import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.downloads.youtube_downloader import YouTubeDownloaderMixin
from src.downloads.youtube_strategy import YouTubeStrategyMixin
from src.downloads.youtube_failure_diagnostics import analyze_yt_dlp_failure
from src.downloads.youtube_success import handle_successful_attempt
from src.core.subprocess_runner import SubprocessRunnerMixin
from src.downloads.manager import DownloadManagerMixin


class SyntheticApp(YouTubeDownloaderMixin, YouTubeStrategyMixin):
    def __init__(self):
        self.cancel_flag = threading.Event()
        self.file_lock = threading.Lock()
        self.settings = {"download_subtitles": False}
        self.log = Mock()
        self.record_problem = Mock()
        self.record_yt_dlp_attempt = Mock()
        self._snapshot_download_target_files = Mock(return_value=[])
        self._note_youtube_primary_strategy_result = Mock()
        self.cleanup_download_temp_files = Mock()
        self.file_logger = Mock()
        self.build_youtube_ejs_args = Mock(return_value=[])
        self.is_youtube_url = Mock(return_value=True)


def strategy(name, args=(), network=(), **kwargs):
    return {"name": name, "args": list(args), "network_args": list(network), **kwargs}


class QueuePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = SyntheticApp()
        self.setup = SimpleNamespace(video_dir=self.root, quality="1080p", expected_video_id="id",
            download_temp_dir=self.root / "pending", video_files_before=set(),
            quality_candidate_dir=self.root / "candidate", proxy_args=[],
            proxy_enabled=False, proxy_url_masked="")
        self.setup.download_temp_dir.mkdir()
        self.addCleanup(patch.stopall)
        patch("src.downloads.youtube_downloader.prepare_download_setup", return_value=self.setup).start()
        patch("src.downloads.youtube_downloader.log_first_attempt").start()
        patch("src.downloads.youtube_downloader.build_attempt_context", return_value={}).start()

    def run_queue_video(self, plan, error, url="https://youtube.com/watch?v=id"):
        self.app.run_command = Mock(side_effect=lambda cmd, *_args:
            subprocess.CompletedProcess(cmd, 1, "", error))
        with patch("src.downloads.youtube_downloader.build_ytdlp_strategy_plan",
                   return_value=(plan, [], "1")), patch.object(self.app.cancel_flag, "wait", return_value=False):
            self.app.method_yt_dlp(url)
        return self.app.run_command.call_args_list

    def test_ipv6_dns_failure_disables_only_ipv6_for_queue(self):
        plan = [strategy("ipv6", network=["--force-ipv6"]), strategy("ipv4", network=["--force-ipv4"])]
        self.run_queue_video(plan, "getaddrinfo failed")
        calls = self.run_queue_video(plan, "generic transient failure", "https://youtube.com/watch?v=next")
        self.assertEqual(len(calls), 1)
        self.assertIn("--force-ipv4", calls[0].args[0])

    def test_chrome_copy_failure_skips_all_chrome_variants_but_not_other_clients(self):
        plan = [strategy("chrome", ["--cookies-from-browser", "chrome"]),
                strategy("chrome-any", ["--cookies-from-browser", "chrome"], fallback_format=True),
                strategy("android", ["--extractor-args", "youtube:player_client=android"])]
        calls = self.run_queue_video(plan, "Could not copy Chrome cookie database")
        self.assertEqual(len(calls), 2)
        calls = self.run_queue_video(plan, "generic transient failure")
        self.assertEqual(len(calls), 1)
        self.assertIn("youtube:player_client=android;lang=ru", calls[0].args[0])

    def test_ios_repeated_missing_formats_across_videos_is_queue_scoped(self):
        ios = strategy("ios", ["--extractor-args", "youtube:player_client=ios"])
        for index in range(3):
            self.assertEqual(len(self.run_queue_video([ios], "Requested format is not available", str(index))), 1)
        self.assertEqual(len(self.run_queue_video([ios], "Requested format is not available", "fourth")), 0)
        from src.downloads.youtube_queue_policy import reset_queue_capabilities
        reset_queue_capabilities(self.app)
        self.assertEqual(len(self.run_queue_video([ios], "Requested format is not available", "fresh")), 1)

    def test_manager_resets_capabilities_before_new_batch_and_transient_failure_does_not_ban(self):
        ipv6 = strategy("ipv6", network=["--force-ipv6"])
        self.run_queue_video([ipv6], "getaddrinfo failed")
        self.app.settings.update(save_path=str(self.root), merge_audio=False)
        self.app.cleanup_stale_download_temp_files = Mock(return_value=0)
        self.app.cleanup_stale_download_temp_dirs = Mock(return_value=0)
        self.app.convert_videos_to_audio = Mock(return_value=[])
        self.app.write_problem_session_summary = Mock()
        self.app.download_start_time = None
        self.app.download_threads = []
        self.app.max_concurrent = 1
        DownloadManagerMixin._run_download_session(self.app, [])
        self.assertEqual(len(self.run_queue_video([ipv6], "temporary generic failure")), 1)
        self.assertEqual(len(self.run_queue_video([ipv6], "temporary generic failure")), 1)

    def test_concurrent_distinct_ios_evidence_keeps_profile_and_success_boundaries(self):
        from concurrent.futures import ThreadPoolExecutor
        from src.downloads.youtube_queue_policy import QueueCapabilities
        state = QueueCapabilities()
        ios = strategy("ios", ["--extractor-args", "youtube:player_client=ios"])
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda url: state.observe(ios, url, "Requested format is not available"), ["a", "b", "c"]))
        self.assertIsNotNone(state.skip_reason(ios))
        self.assertIsNone(state.skip_reason(dict(ios, fallback_format=True)))
        state.observe(ios, "c", success=True)
        self.assertIsNone(state.skip_reason(ios))

    def test_auth_dedup_preserves_different_clients_and_cookie_fallback(self):
        plan = [strategy("default"), strategy("default-ipv4", network=["--force-ipv4"]),
                strategy("android", ["--extractor-args", "youtube:player_client=android"]),
                strategy("cookies", ["--cookies-from-browser", "chrome"])]
        calls = self.run_queue_video(plan, "Войдите в свой аккаунт")
        self.assertEqual(len(calls), 3)
        self.assertTrue(any("--cookies-from-browser" in call.args[0] for call in calls))

    def test_filename_and_download_plan_do_not_suppress_working_auth_scope(self):
        for title in ("Cookies recipe", "Sign in to confirm", "Войдите в свой аккаунт"):
            with self.subTest(title=title):
                plan = [strategy("first"), strategy("ipv4", network=["--force-ipv4"])]
                output = (f"[download] Destination: {title}.mp4\n"
                          f"[DOWNLOAD_PLAN] id=id format_note={title}\n"
                          f"[debug] Title: {title}\n"
                          "ERROR: unable to download video data: HTTP Error 403: Forbidden")
                self.app.run_command = Mock(side_effect=[subprocess.CompletedProcess([], 1, output, ""),
                                                         subprocess.CompletedProcess([], 0, "", "")])
                decision = SimpleNamespace(last_error=None, candidate_path=None, action="success", video_path="published.mp4")
                with patch("src.downloads.youtube_downloader.build_ytdlp_strategy_plan", return_value=(plan, [], "1")), \
                     patch("src.downloads.youtube_downloader.handle_successful_attempt", return_value=decision), \
                     patch.object(self.app.cancel_flag, "wait", return_value=False):
                    self.assertEqual(self.app.method_yt_dlp("fixture"), "published.mp4")
                self.assertEqual(self.app.run_command.call_count, 2)

    def test_semantically_rejected_success_consumes_failed_budget(self):
        for rejection in ("missing_file", "invalid_media", "above_height_cap"):
            with self.subTest(rejection=rejection):
                self.app.run_command = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
                self.app.parse_yt_dlp_output = Mock(return_value=(None if rejection == "missing_file"
                                                                else str(self.root / "synthetic.mp4")))
                self.app.validate_downloaded_video = Mock(return_value=(rejection != "invalid_media", "invalid"))
                self.app.move_video_to_manual_processing = Mock()
                self.app.get_video_dimensions = Mock(return_value=(1920, 1200))
                plan = [strategy(str(index)) for index in range(27)]
                with patch("src.downloads.youtube_downloader.build_ytdlp_strategy_plan", return_value=(plan, [], "1")):
                    self.assertIsNone(self.app.method_yt_dlp("fixture"))
                self.assertEqual(self.app.run_command.call_count, 12)

    def test_generic_failures_have_attempt_budget(self):
        calls = self.run_queue_video([strategy(str(index)) for index in range(27)], "unclassified failure")
        self.assertEqual(len(calls), 12)
        operations = [call.args[2] for call in self.app.record_problem.call_args_list]
        self.assertIn("yt_dlp_retry_budget_exhausted", operations)

    def test_elapsed_budget_stops_failed_work_but_not_a_large_success(self):
        plan = [strategy(str(index)) for index in range(27)]
        with patch("src.downloads.youtube_downloader.time.monotonic", side_effect=[0, 301, 302, 603]):
            calls = self.run_queue_video(plan, "unclassified failure")
        self.assertEqual(len(calls), 2)
        self.app.run_command = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        decision = SimpleNamespace(last_error=None, candidate_path=None, action="success", video_path="published.mp4")
        with patch("src.downloads.youtube_downloader.build_ytdlp_strategy_plan", return_value=(plan, [], "1")), \
             patch("src.downloads.youtube_downloader.time.monotonic", side_effect=[0, 1800]), \
             patch("src.downloads.youtube_downloader.handle_successful_attempt", return_value=decision):
            self.assertEqual(self.app.method_yt_dlp("healthy"), "published.mp4")
        self.assertEqual(self.app.run_command.call_count, 1)

    def test_known_low_video_id_is_excluded_only_for_same_selection_scope(self):
        plan = [strategy("first"), strategy("network-variant", network=["--force-ipv4"]),
                strategy("other-client", ["--extractor-args", "youtube:player_client=android"])]
        output = "[DOWNLOAD_PLAN] id=id format_id=136+140 protocol=https ext=mp4 height=720 width=1280 vcodec=h264 acodec=aac language=ru"
        self.app.run_command = Mock(side_effect=[subprocess.CompletedProcess([], 0, output, ""),
            subprocess.CompletedProcess([], 1, "", "requested format is not available"),
            subprocess.CompletedProcess([], 1, "", "requested format is not available")])
        decision = SimpleNamespace(last_error=None, candidate_path=str(self.root / "reserve.mp4"),
                                   candidate_height=720, candidate_width=1280, action="retry", lower_video_format_id="136")
        with patch("src.downloads.youtube_downloader.build_ytdlp_strategy_plan", return_value=(plan, [], "1")), \
             patch("src.downloads.youtube_downloader.handle_successful_attempt", return_value=decision), \
             patch("src.downloads.youtube_downloader.finalize_lower_quality_candidate", return_value=None):
            self.app.method_yt_dlp("fixture")
        commands = [call.args[0] for call in self.app.run_command.call_args_list]
        self.assertIn("[format_id!=136]", commands[1][commands[1].index("-f") + 1])
        self.assertNotIn("[format_id!=136]", commands[2][commands[2].index("-f") + 1])
        self.assertNotIn("bestaudio[format_id!=", commands[1][commands[1].index("-f") + 1])

    def test_timeout_message_uses_effective_watchdog_timeout(self):
        self.app.run_command = Mock(side_effect=subprocess.TimeoutExpired(["yt-dlp"], 240))
        with patch("src.downloads.youtube_downloader.build_ytdlp_strategy_plan", return_value=([strategy("one")], [], "1")):
            self.app.method_yt_dlp("fixture")
        messages = "\n".join(call.args[0] for call in self.app.log.call_args_list)
        self.assertIn("240", messages)
        self.assertNotIn(">60 мин", messages)

    def test_low_candidate_is_preserved_when_failed_attempt_budget_expires(self):
        candidate = self.root / "candidate.mp4"
        candidate.write_bytes(b"synthetic saved candidate")
        self.app.run_command = Mock(side_effect=[subprocess.CompletedProcess([], 0, "", "")] +
            [subprocess.CompletedProcess([], 1, "", "unclassified failure")] * 27)
        decision = SimpleNamespace(last_error=None, candidate_path=str(candidate), candidate_height=720,
                                   candidate_width=1280, action="retry")
        with patch("src.downloads.youtube_downloader.build_ytdlp_strategy_plan", return_value=([strategy(str(i)) for i in range(28)], [], "1")), \
             patch("src.downloads.youtube_downloader.handle_successful_attempt", return_value=decision), \
             patch("src.downloads.youtube_downloader.finalize_lower_quality_candidate", return_value=str(candidate)) as finalize, \
             patch.object(self.app.cancel_flag, "wait", return_value=False):
            result = self.app.method_yt_dlp("fixture")
        self.assertEqual(result, str(candidate))
        self.assertEqual(self.app.run_command.call_count, 13)
        self.assertEqual(finalize.call_args.kwargs["candidate_path"], str(candidate))


class FailureAndQualityPolicyTests(unittest.TestCase):
    def test_streamed_inactivity_retains_reason_and_actual_threshold(self):
        app = SimpleNamespace(cancel_flag=threading.Event(), subprocess_flags=0)
        for name in ("_register_process", "_unregister_process", "_set_ytdlp_runtime",
                     "_remove_ytdlp_runtime", "_record_active_ytdlp_snapshot", "log", "terminate_process"):
            setattr(app, name, Mock())
        app._utf8_subprocess_env = Mock(return_value={})
        app._command_to_string = Mock(return_value="synthetic")
        child = SimpleNamespace(pid=42, stdout=iter([]), poll=lambda: None)
        with patch("src.core.subprocess_runner.subprocess.Popen", return_value=child), \
             patch("src.core.subprocess_runner.time.time", side_effect=[0, 240]):
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                SubprocessRunnerMixin.run_command_streamed(app, ["synthetic"], 3600, "yt_dlp_download")
        self.assertEqual(getattr(raised.exception, "reason", None), "output_inactivity")
        self.assertEqual(raised.exception.timeout, 240)
        app.terminate_process.assert_called_once_with(child)
        app._unregister_process.assert_called_once_with(child)

    def test_decisive_failures_have_explicit_classification(self):
        for message, expected in [("Войдите в свой аккаунт", "authentication_required"),
                ("Видео недоступно.", "video_unavailable"),
                ("Could not copy Chrome cookie database", "browser_cookie_read_failed"),
                ("getaddrinfo failed", "dns_resolution_failed"),
                ("Requested format is not available", "yt_dlp_format_unavailable")]:
            with self.subTest(message=message):
                self.assertEqual(analyze_yt_dlp_failure(message)["kind"], expected)

    def quality_decision(self, root, width, height, output="", prior=None):
        app = SyntheticApp()
        source = root / "source.mp4"
        source.write_bytes(b"synthetic video")
        app.parse_yt_dlp_output = Mock(return_value=str(source))
        app.validate_downloaded_video = Mock(return_value=(True, ""))
        app.get_video_dimensions = Mock(return_value=(width, height))
        app.finalize_downloaded_video_file = Mock(return_value=str(root / "published.mp4"))
        app._log_video_info = Mock()
        return handle_successful_attempt(app, result=subprocess.CompletedProcess([], 0, output, ""),
            url="fixture", attempt=1, strategy=strategy("direct"), command=[],
            download_temp_dir=root / "pending", video_dir=root,
            quality_candidate_dir=root / "candidate", video_files_before=set(), expected_video_id="id",
            best_candidate_path=str(prior) if prior else None,
            best_candidate_height=720 if prior else -1, best_candidate_width=1280 if prior else -1,
            attempt_summaries=[])

    def test_inferior_discarded_candidate_still_reports_its_own_downloaded_format(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior = root / "reserve.mp4"
            prior.write_bytes(b"keep this 720p candidate")
            output = "[DOWNLOAD_PLAN] id=id format_id=18 height=360 width=640"
            decision = self.quality_decision(root, 640, 360, output, prior)
            self.assertEqual(decision.candidate_path, str(prior))
            self.assertEqual(prior.read_bytes(), b"keep this 720p candidate")
            self.assertEqual(getattr(decision, "lower_video_format_id", None), "18")
            self.assertFalse(decision.semantic_failure)

    def test_native_wide_1080_class_is_accepted_without_fetching_identical_fallbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.quality_decision(Path(directory), 1920, 1032).action, "success")

    def test_720_and_portrait_continue_search_and_above_cap_is_rejected(self):
        for width, height in [(1280, 720), (600, 1032), (1920, 1200)]:
            with self.subTest(dimensions=(width, height)), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(self.quality_decision(Path(directory), width, height).action, "retry")


if __name__ == "__main__":
    unittest.main()

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import daily_control
import publisher
import publisher_batch as batch
import setup_wizard
from daily_settings import paper_limit, schedule
from publisher_preflight import arxiv_ids
from publisher_state import PublisherState

CANDIDATE = {
    "title": "Test paper", "arxiv_id": "arXiv:2405.20693v2",
    "url": "https://arxiv.org/abs/2405.20693", "discord_message": "Paper summary",
}
SETTINGS = {
    "zotero_collection_key": "ABCD2345", "zotero_collection_name": "Daily Papers",
    "discord_channel_id": "123", "reader_user": "reader",
}
SCOPE = {"user_id": "1", "collection": "ABCD2345", "channel": "123"}
WEBHOOK = "https://discord.com/api/webhooks/1/mock"
CONTEXT = ("https://api.zotero.org/users/1", "mock", WEBHOOK, SCOPE)


class Workflow(unittest.TestCase):
    def test_missing_installed_control_asks_to_save(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "daily_control.py")
            with patch.object(setup_wizard, "CONTROL", ("/usr/bin/python3", "-B", missing)), patch.object(
                setup_wizard, "privileged"
            ) as execute, self.assertRaisesRegex(ValueError, "Click Save setup first"):
                setup_wizard.control("health")
            execute.assert_not_called()

    def test_arxiv_normalization_and_isolated_invalid_candidate(self):
        for value in (
            "arXiv:2405.20693v2", "ID: 2405.20693", "arXiv：２４０５．２０６９３",
            "https://arxiv.org/pdf/2405.20693v2.pdf", "https://doi.org/10.48550/arXiv.2405.20693",
        ):
            self.assertEqual(arxiv_ids(value, explicit=True), {"2405.20693"})
        entries = list(batch.prepare_candidates([
            {"title": "Bad", "discord_message": "Brief", "arxiv_id": "?"}, CANDIDATE,
        ]))
        self.assertEqual(entries[0][2]["status"], "invalid_candidate")
        self.assertEqual(entries[1][1]["arxiv_id"], "2405.20693")

    def test_timezone_and_limit(self):
        self.assertEqual(schedule({"publish_time": "18:30", "timezone": "Asia/Shanghai"})[1].key, "Asia/Shanghai")
        with self.assertRaises(ValueError):
            schedule({"timezone": "UTC\nOnCalendar=bad"})
        self.assertEqual(paper_limit({"max_papers": 4}), 4)
        with patch.object(batch.sys, "stdin", io.StringIO("[{},{},{},{},{}]")), self.assertRaises(ValueError):
            batch.read_candidates(4)

    def test_connection_checks_separate_and_no_candidates_needed(self):
        with patch.object(batch, "load_credentials", return_value=("1", "mock", WEBHOOK)), patch.object(
            batch, "check_zotero", side_effect=ValueError("denied")
        ), patch.object(batch, "check_channel") as discord:
            checks = batch.checks(SETTINGS, Path("unused"))
        self.assertEqual([check["ok"] for check in checks], [False, True])
        discord.assert_called_once_with(WEBHOOK, "123")

    def test_top_level_health_includes_reader_and_credential_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            file = home / ".config/daily-papers/credentials.json"
            file.parent.mkdir(parents=True)
            file.touch()
            checks = [{"name": name, "ok": True} for name in ("Zotero", "Discord")]
            with patch.object(daily_control.pwd, "getpwnam", return_value=Mock(pw_dir=directory)), patch.object(
                daily_control, "batch", return_value=Mock(stdout=json.dumps(checks))
            ), patch.object(daily_control.subprocess, "run", return_value=Mock(returncode=0)) as run:
                results = daily_control.health(SETTINGS)
            self.assertEqual([item["name"] for item in results], ["Zotero", "Discord", "Credential isolation", "Model"])
            self.assertTrue(all(item["ok"] for item in results))
            self.assertIn("--check-provider", run.call_args.args[0][-1])

    def test_empty_review_and_manual_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            state = PublisherState(Path(directory) / "state.json")
            self.assertEqual(batch.process([], "review", SETTINGS, CONTEXT, state), ([], False))
            self.assertEqual(state.data["review"]["entries"], [])
            entries = list(batch.prepare_candidates([CANDIDATE]))
            with patch.object(batch, "check_channel"), patch.object(batch, "preflight", return_value={"duplicates": []}), patch.object(batch, "publish") as send:
                batch.process(entries, "review", SETTINGS, CONTEXT, state)
                send.assert_not_called()
            review = state.data["review"]
            selector = {"review_id": review["id"], "indices": [1]}
            self.assertEqual(batch.approved_entries(state, selector, SCOPE, 2)[0][1]["title"], CANDIDATE["title"])
            with self.assertRaises(ValueError):
                batch.approved_entries(state, {**selector, "review_id": "stale"}, SCOPE, 2)

    def test_discord_failure_recovers_saved_payload_without_archiving_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = PublisherState(path)
            entries = list(batch.prepare_candidates([CANDIDATE]))
            def archive(candidate, settings, api, key, webhook, on_archive):
                on_archive(candidate, "ITEMKEY1")
                self.assertEqual(PublisherState(path).data["deliveries"]["ITEMKEY1"]["status"], "discord_unconfirmed")
                return {"status": "discord_unconfirmed", "zotero_item_key": "ITEMKEY1"}
            with patch.object(batch, "check_channel"), patch.object(batch, "publish", side_effect=archive):
                result, failed = batch.process(entries, "auto", SETTINGS, CONTEXT, state)
            self.assertTrue(failed)
            self.assertEqual(result[0]["status"], "discord_unconfirmed")
            restored = PublisherState(path)
            receipt = {"status": "published", "zotero_item_key": "ITEMKEY1", "discord_message_id": "999"}
            with patch.object(batch, "resume_discord", return_value=receipt) as retry, patch.object(batch, "publish") as archive_again:
                self.assertEqual(batch.retry_delivery(restored, "ITEMKEY1", SETTINGS, CONTEXT), receipt)
                retry.assert_called_once()
                archive_again.assert_not_called()
                with self.assertRaises(ValueError):
                    batch.retry_delivery(restored, "ITEMKEY1", SETTINGS, CONTEXT)
            self.assertEqual(PublisherState(path).data["deliveries"]["ITEMKEY1"]["discord_message_id"], "999")

    def test_archive_hook_precedes_discord(self):
        events = []
        def archived(candidate, item):
            events.append(("stored", item))
        def send(message, webhook, item):
            self.assertEqual(events, [("stored", item)])
            return {"status": "published"}
        with patch.object(publisher, "preflight", return_value={
            "duplicates": [], "proposed_archive": CANDIDATE, "collection": {"key": "ABCD2345"},
        }), patch.object(publisher, "post_json", return_value={"successful": {"0": {"key": "ITEMKEY1"}}}), patch.object(
            publisher, "send_discord", side_effect=send
        ):
            publisher.publish(CANDIDATE, SETTINGS, *CONTEXT[:3], on_archive=archived)


@unittest.skipUnless(os.getenv("DISPLAY"), "Requires a graphical display or Xvfb")
class DesktopSmoke(unittest.TestCase):
    def test_setup_and_operations_render(self):
        import tkinter as tk
        from operations_gui import show_operations

        original = tk.Tk
        snapshot = {
            "timer": {}, "service": {}, "mode": "review", "time": "09:00", "timezone": "Europe/Paris",
            "runs": [], "deliveries": {}, "review": {"id": "preview", "at": "today", "entries": [{
                "index": 1, "status": "ready", "candidate": CANDIDATE,
            }]},
        }
        def root_window():
            root = original()
            root.after(100, root.destroy)
            return root
        with patch.object(tk, "Tk", side_effect=root_window):
            setup_wizard.gui({**setup_wizard.DEFAULTS, "requirements": "Topic"})
        root = original()
        try:
            show_operations(root, snapshot, lambda *args: None, lambda: None)
            root.update_idletasks()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()

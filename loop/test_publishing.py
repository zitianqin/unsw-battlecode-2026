"""Publishing uses immutable accepted source and never retries an ambiguous POST blindly."""

import testhome  # before anything that reads live paths
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from urllib.error import HTTPError
import zipfile
from pathlib import Path
from unittest.mock import patch

import online


def archive(source="accepted"):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as bundle:
        bundle.writestr("main.cpp", source)
    return result.getvalue()


class TestPublishing(unittest.TestCase):
    def test_export_refuses_a_repo_whose_object_was_rewritten_in_place(self):
        # What a coder can do through a hard link into the main repo's objects: same file, new bytes, old name.
        import publishing
        with tempfile.TemporaryDirectory() as directory, patch.object(publishing.time, "sleep"):  # fsck's retry waits
            repo = Path(directory) / "repo"
            (repo / "bot").mkdir(parents=True)
            (repo / "bot/main.cpp").write_text("int main() { return 0; }\n")
            run = lambda *args: subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
            run("init", "-q")
            run("add", ".")
            run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "bot")
            publishing.export_bot(repo, "HEAD", Path(directory) / "ok")
            blob = run("rev-parse", "HEAD:bot/main.cpp").stdout.strip()
            path = repo / ".git/objects" / blob[:2] / blob[2:]
            other = repo / "other.txt"
            other.write_text("int main() { return 1; }\n")
            forged = run("hash-object", "-w", str(other)).stdout.strip()
            path.chmod(0o644)
            path.write_bytes((repo / ".git/objects" / forged[:2] / forged[2:]).read_bytes())
            with self.assertRaisesRegex(RuntimeError, "git fsck failed"):
                publishing.export_bot(repo, "HEAD", Path(directory) / "forged")
            path.unlink()
            os.link(repo / ".git/objects" / forged[:2] / forged[2:], path)  # the link itself, before any rewrite
            with self.assertRaisesRegex(RuntimeError, "hard-linked elsewhere"):
                publishing.export_bot(repo, "HEAD", Path(directory) / "linked")

    def test_a_file_gone_while_the_git_folder_is_walked_is_skipped(self):
        # Round-3 review: fetch_checked makes and deletes its quarantine repo in .git, as git does its temporary files;
        # one gone between the walk's listing and its lstat raised FileNotFoundError.
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "a.txt").write_text("a\n")
            for args in (("init", "-q"), ("add", "."),
                         ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a")):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
            gone = repo / ".git/fetch-quarantine-x"
            gone.write_text("")
            lstat = os.lstat

            def deleted_meanwhile(path, *args, **kwargs):
                if str(path) == str(gone):
                    gone.unlink(missing_ok=True)
                return lstat(path, *args, **kwargs)
            with patch.object(publishing.os, "lstat", side_effect=deleted_meanwhile):
                publishing.repo_intact(repo)
            self.assertFalse(gone.exists())

    def test_a_checked_fetch_deletes_quarantines_a_crash_left_but_not_a_running_fetchs(self):
        # Fix round 4: a crash inside fetch_checked left its fetch-quarantine-* repo in the main repo's .git for good.
        import fcntl
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            repo, clone = Path(directory) / "repo", Path(directory) / "clone"
            repo.mkdir()
            (repo / "a.txt").write_text("a\n")
            run = lambda *args, cwd=repo: subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)
            run("init", "-q", "-b", "main"), run("add", "."), run("-c", "user.name=t", "-c", "user.email=t@t",
                                                                  "commit", "-qm", "a")
            run("clone", "-q", str(repo), str(clone), cwd=Path(directory))
            old = time.time() - publishing.QUARANTINE_STALE - 60
            crashed, running, fresh = (repo / f".git/{publishing.QUARANTINE_PREFIX}{n}" for n in ("a", "b", "c"))
            for folder in (crashed, running, fresh):
                (folder / "objects").mkdir(parents=True)
            for folder in (crashed, running):
                os.utime(folder, (old, old))
            held = os.open(running, os.O_RDONLY)
            self.addCleanup(os.close, held)
            fcntl.flock(held, fcntl.LOCK_EX)  # as a slow fetch in another process holds it
            self.assertEqual(publishing.stale_quarantines(repo / ".git", time.time()), [crashed])
            publishing.fetch_checked(repo, clone, "HEAD:refs/heads/fetched")
            self.assertEqual(sorted(p.name for p in (repo / ".git").glob(f"{publishing.QUARANTINE_PREFIX}*")),
                             [running.name, fresh.name])
            run("rev-parse", "--verify", "refs/heads/fetched")

    def test_definite_rejection_retries_later_but_keeps_upload_budget(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get", return_value=[]), \
                    patch.object(publishing, "submit", side_effect=HTTPError("site", 429, "rate limit", {}, None)):
                result = publishing.cycle(state, state)
            self.assertNotIn("pending", result)
            self.assertGreater(result["retry_after"], 0)
            self.assertEqual(len((state / "uploads.jsonl").read_text().splitlines()), 1)

    def test_a_spent_upload_budget_is_a_wait_that_keeps_online_games_running(self):
        # After v0031 (2026-09-26) the spent budget read as an error, and maintenance stops matches on any error.
        import publishing
        import time
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            now = time.time()
            (state / "uploads.jsonl").write_text("".join(json.dumps({"at": now - 60 * m}) + "\n" for m in range(12)))
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get", return_value=[]), \
                    patch.object(publishing, "submit", side_effect=AssertionError("uploaded past the budget")):
                result = publishing.cycle(state, state)
            self.assertIsNone(result.get("error"))
            self.assertIn("upload budget exhausted (12 an hour, the site's limit); next slot", result["waiting"])
            self.assertFalse(any(result.get(k) for k in ("blocked", "pending", "error")))  # maintenance's match gate

    def test_site_versions_are_kept_by_commit_and_shown_beside_the_tag(self):
        # Real /submissions names (2026-09-26): v0031 was never uploaded, so v0032 (7e4b60b) is the site's v31.
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            rows = [{"id": 7359, "version": 31, "name": "c7e4b60b-026de484", "status": "active"},
                    {"id": 7003, "version": 30, "name": "c2c79f81-51910935", "status": "idle"},
                    {"id": 1, "version": 1, "name": "baseline upload", "status": "idle"}]
            self.assertEqual(publishing.record_versions(state, rows), {"7e4b60b": 31, "2c79f81": 30})
            repo = Path(__file__).resolve().parent.parent
            with patch.object(publishing.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 0, stdout="v0032\n", stderr="")):
                self.assertEqual(publishing.site_label(state, repo, "7e4b60b1f9a4"), "v0032 (site v31)")
            with patch.object(publishing.subprocess, "run",
                              return_value=subprocess.CompletedProcess([], 0, stdout="v0031\n", stderr="")):
                self.assertEqual(publishing.site_label(state, repo, "5f37cbff9329"), "v0031 (not on the site)")

    def setUp(self):
        # Authorization itself is exercised below; publication tests fake only this git/results boundary.
        self.authorization = patch("publishing.accepted", return_value=True, create=True)
        self.authorization.start()
        self.addCleanup(self.authorization.stop)

    def test_unproven_branch_move_is_not_publishable(self):
        import publishing
        self.authorization.stop()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(publishing.subprocess, "check_output", return_value="abc\n"):
                self.assertFalse(publishing.accepted(state, "abc"))
                (state / "results.jsonl").write_text(json.dumps({"candidate": "abc", "verdict": "promising"}) + "\n")
                self.assertFalse(publishing.accepted(state, "abc"))
                (state / "results.jsonl").write_text(json.dumps({"candidate": "abc", "verdict": "kept",
                    "sprt": {"decision": "H1"}, "cpu": {"ok": True}, "bot_tests": {"ok": True}}) + "\n")
                self.assertTrue(publishing.accepted(state, "abc"))
                # A targeted keep's H1 is only no loss: its target test is the rest of its proof.
                targeted = {"candidate": "abc", "verdict": "kept", "idea": {"kind": "targeted"},
                            "sprt": {"decision": "H1"}, "cpu": {"ok": True}, "bot_tests": {"ok": True}}
                for target, proven in (({"ok": False}, False), ({}, False), ({"ok": True}, True)):
                    (state / "results.jsonl").write_text(json.dumps({**targeted, "target": target}) + "\n")
                    self.assertEqual(publishing.accepted(state, "abc"), proven)

    def test_snapshot_ignores_dirty_checkout_and_includes_nested_sdk_sources(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.DEVNULL, text=True).strip()
            git("init", "-q")
            (repo / "bot/sub").mkdir(parents=True)
            (repo / "bot/bot.toml").write_text('[project]\nlanguage="cpp"\ninclude=["*.cpp"]\n')
            (repo / "bot/main.cpp").write_text("accepted")
            (repo / "bot/sub/nested.cpp").write_text("nested")
            git("add", "bot")
            git("commit", "-qm", "accepted fixture")
            git("branch", "loop/champion")
            (repo / "bot/main.cpp").write_text("unfinished")
            commit, raw, language = publishing.snapshot(repo)
            self.assertEqual(commit, git("rev-parse", "HEAD"))
            self.assertEqual(language, "cpp")
            with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
                self.assertEqual(bundle.read("main.cpp"), b"accepted")
                self.assertEqual(bundle.read("sub/nested.cpp"), b"nested")
            destination = repo / "evaluation"
            publishing.export_bot(repo, commit, destination)
            self.assertEqual((destination / "main.cpp").read_text(), "accepted")

    def test_failed_build_never_activates(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            online.save(state / "publishing.json", {"pending": {"name": "unique"}})
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get", return_value=[{"id": 8, "name": "unique", "status": "failed"}]), \
                    patch.object(publishing.ladder, "post", side_effect=AssertionError("failed build")):
                result = publishing.cycle(state, state)
            self.assertTrue(result["blocked"])

    def test_verified_active_source_needs_no_upload(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get", return_value=[{"id": 7, "status": "active"}]), \
                    patch.object(publishing.ladder, "download", return_value=archive()), \
                    patch.object(publishing, "submit", side_effect=AssertionError("no duplicate upload")):
                report = publishing.cycle(state, state)
            self.assertEqual(report["verified"]["submission_id"], 7)
            self.assertEqual(report["verified"]["commit"], "abc")

    def test_unknown_upload_keeps_reservation_and_reconciles_on_restart(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get", return_value=[{"id": 7, "status": "active"}]), \
                    patch.object(publishing.ladder, "download", return_value=archive("old")), \
                    patch.object(publishing, "submit", side_effect=TimeoutError("unknown outcome")):
                first = publishing.cycle(state, state)
                second = publishing.cycle(state, state)
            self.assertIn("unknown", first["error"])
            self.assertEqual(first["pending"]["name"], second["pending"]["name"])
            self.assertEqual(len((state / "uploads.jsonl").read_text().splitlines()), 1)
            name = first["pending"]["name"]
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get", return_value=[{"id": 8, "status": "active", "name": name}]), \
                    patch.object(publishing.ladder, "download", return_value=archive()), \
                    patch.object(publishing, "submit", side_effect=AssertionError("no duplicate upload")):
                report = publishing.cycle(state, state)
            self.assertEqual(report["verified"]["submission_id"], 8)
            self.assertNotIn("pending", report)
            # The orchestrator hears of it at once (REFLECT 2011 F1), and only once.
            lines = (state / "host-alerts.log").read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertIn("PUBLISHED: submission 8 (abc (not on the site)) is live, commit abc", lines[0])
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get",
                                 return_value=[{"id": 8, "status": "active", "name": name}]), \
                    patch.object(publishing.ladder, "download", return_value=archive()):
                publishing.cycle(state, state)
            self.assertEqual(len((state / "host-alerts.log").read_text().splitlines()), 1)
            # The upload is saved by then: an alert that can't be written doesn't fail the cycle.
            (state / "publishing.json").write_text(json.dumps(dict(report, verified=None)))
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get",
                                 return_value=[{"id": 8, "status": "active", "name": name}]), \
                    patch.object(publishing.ladder, "download", return_value=archive()), \
                    patch.object(publishing, "alert", side_effect=OSError("disk full")):
                self.assertEqual(publishing.cycle(state, state)["verified"]["submission_id"], 8)

    def test_mismatched_auto_activated_upload_rolls_back_and_blocks_more_writes(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            pending = {"name": "unique", "commit": "abc", "digest": online.archive_digest(archive()),
                       "previous": 7, "previous_digest": online.archive_digest(archive("old"))}
            online.save(state / "publishing.json", {"pending": pending})
            calls = []
            def get(path):
                return [{"id": 7, "status": "active"}] if calls else [{"id": 8, "name": "unique", "status": "active"}]
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get", side_effect=get), \
                    patch.object(publishing.ladder, "download", side_effect=lambda p: archive("old" if "/7/" in p else "wrong")), \
                    patch.object(publishing.ladder, "post", side_effect=lambda p: calls.append(p)):
                result = publishing.cycle(state, state)
            self.assertEqual(calls, ["/submissions/7/activate"])
            self.assertTrue(result["blocked"])
            self.assertEqual(result["rollback"], 7)

    def test_a_standing_hold_uploads_nothing_and_reactivates_the_held_submission(self):
        # The Sprint freeze (1 Oct 09:00): a champion promoted after sig's pick must not replace it on the site. A block
        # or a rejection's retry_after stops uploads, not the hold, which keeps the block's cause and says so.
        import publishing
        held = "h" * 40
        for start in ({}, {"blocked": True, "error": "uploaded source mismatch", "retry_after": time.time() + 3600}):
            with self.subTest(start=start), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                (state / publishing.HOLD).write_text(json.dumps({"version": "v0078", "why": "sig's Sprint pick",
                                                                 "until": "2099-01-01T12:00+10:00"}))
                (state / "uploads.jsonl").write_text(json.dumps({"commit": held, "name": "chhhhhhh-1", "at": 1}) + "\n")
                online.save(state / "publishing.json", {"verified": {"commit": "abc", "submission_id": 9}, **start})
                rows = [{"id": 3, "name": "chhhhhhh-1", "status": "ready", "version": 76},
                        {"id": 9, "name": "cabc0000-2", "status": "active"},
                        {"id": 2, "name": "chhhhhhh-1", "status": "failed"}]  # a failed build is never the held one
                posts = []

                def activate(path):
                    posts.append(path)
                    rows[0]["status"], rows[1]["status"] = "active", "ready"
                snap = lambda repo, ref="loop/champion": (held if ref == "refs/tags/v0078" else "abc", archive(), "cpp")
                with patch.object(publishing, "snapshot", side_effect=snap), \
                        patch.object(publishing.ladder, "get", side_effect=lambda path: rows), \
                        patch.object(publishing.ladder, "download", return_value=archive()), \
                        patch.object(publishing.ladder, "post", side_effect=activate), \
                        patch.object(publishing, "submit", side_effect=AssertionError("uploaded during a hold")):
                    report = publishing.cycle(state, state)
                    self.assertEqual(posts, ["/submissions/3/activate"])
                    self.assertEqual((report["verified"]["commit"], report["verified"]["submission_id"]), (held, 3))
                    self.assertEqual(report["error"], start.get("error"))
                    self.assertEqual(publishing.cycle(state, state)["error"], start.get("error"))  # live: nothing to do
                self.assertEqual(posts, ["/submissions/3/activate"])
                self.assertEqual(len((state / "uploads.jsonl").read_text().splitlines()), 1)
                self.assertEqual(report.get("blocked"), start.get("blocked"))
                published = (state / "host-alerts.log").read_text()
                self.assertIn("PUBLISHED: submission 3", published)
                self.assertEqual("still blocked: uploaded source mismatch" in published, bool(start))

    def test_hold_takes_the_publishing_lock_and_warns_of_a_pending_upload(self):
        # A pending upload the site may still auto-activate would replace the held version until the next cycle.
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "uploads.jsonl").write_text(json.dumps({"commit": "h" * 40, "name": "chhhhhhh-1"}) + "\n")
            online.save(state / "publishing.json", {"pending": {"commit": "p" * 40, "name": "cppppppp-2"}})
            taken, real = [], publishing.locked

            def locked(path):
                taken.append(path)
                return real(path)
            found = subprocess.CompletedProcess([], 0, stdout="h" * 40 + "\n")
            out = io.StringIO()
            with patch.object(publishing, "locked", side_effect=locked), \
                    patch.object(publishing.subprocess, "run", return_value=found), patch("sys.stdout", out):
                publishing.main(["hold", "v0078", "--until", "2099-01-01T12:00+10:00", "--why", "Sprint"], state, state)
            self.assertEqual(taken, [state / "publishing"])
            self.assertEqual(json.loads((state / publishing.HOLD).read_text())["version"], "v0078")
            self.assertIn("WARNING: upload cppppppp-2 (ppppppp) is still pending", out.getvalue())

    def test_an_expired_hold_is_ignored(self):
        import publishing
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / publishing.HOLD).write_text(json.dumps({"version": "v0078", "until": "2020-10-01T12:00+10:00",
                                                             "why": "sig's Sprint pick"}))
            with patch.object(publishing, "snapshot", return_value=("abc", archive(), "cpp")), \
                    patch.object(publishing.ladder, "get", return_value=[]), \
                    patch.object(publishing, "submit") as submit:
                publishing.cycle(state, state)
            submit.assert_called_once()

    def test_a_spoofing_champion_waits_for_its_release_time(self):
        # sig, 1 Oct: keep publishing champions, but not one that spoofs sonar until shortly before a freeze.
        import publishing

        def cycle(state, params):
            shown = subprocess.CompletedProcess([], 0, stdout=f"struct Params {{\n{params}\n}};\n", stderr="")
            with patch.object(publishing, "snapshot", return_value=("abc1234", archive(), "cpp")), \
                    patch.object(publishing.subprocess, "run", return_value=shown), \
                    patch.object(publishing.ladder, "get", return_value=[]), \
                    patch.object(publishing, "submit") as submit:
                return publishing.cycle(state, state), submit.called

        spoof = "    bool spoof = true;  // send fake sonar"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            release = state / publishing.SPOOF_RELEASE
            for written in (None, "2099-10-10T12:00+11:00"):  # no file holds indefinitely
                if written:
                    release.write_text(json.dumps({"release_at": written, "why": "Qualifiers"}))
                report, uploaded = cycle(state, spoof)
                self.assertFalse(uploaded)
                self.assertIn("spoofs sonar", report["waiting"])
                self.assertFalse(any(report.get(k) for k in ("blocked", "pending", "error")))
            self.assertIn("2099-10-10T12:00:00+11:00", report["waiting"])
            self.assertEqual((state / "host-alerts.log").read_text().count("SPOOF HELD"), 1)
            release.write_text(json.dumps({"release_at": "2020-10-10T12:00+11:00", "why": "Qualifiers"}))
            self.assertTrue(cycle(state, spoof)[1])
        for params in ("    bool spoof = false;", "    int splitAtLength = 4;"):
            with self.subTest(params=params), tempfile.TemporaryDirectory() as directory:
                self.assertTrue(cycle(Path(directory), params)[1])

    def test_stop_prevents_all_network_work(self):
        import publishing
        for marker in ("STOP", "ASTRA_STOP"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                state = Path(directory)
                (state / marker).touch()
                with patch.object(publishing.ladder, "get", side_effect=AssertionError("stopped")):
                    self.assertEqual(publishing.cycle(state, state)["status"], "stopped")


if __name__ == "__main__":
    unittest.main()

"""Publish only the accepted champion, reconciling uncertain uploads before another POST; while sig's hold stands
(`publishing.py hold`), upload nothing and keep the held version's saved submission live instead (hold_cycle).

A champion promoted while the site's upload slots are spent waits, and one superseded meanwhile is never uploaded, so
our `v*` tags and the site's version numbers can differ (the pairs are in site-versions.json; site_label shows both).
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.error import HTTPError
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import ladder
import online
from meta import REPO, STATE, alert, latest_rows, locked

# The existing, user-selected pre-loop bot. Every later champion needs a kept referee result.
BOOTSTRAP_COMMIT = "c666f243f36a335b33b7e32e74878098753c830b"


def stopped(state: Path) -> bool:
    return (state / "STOP").exists() or (state / "ASTRA_STOP").exists()


# A kept result's acceptance proof: the SPRT's H1 (a gain, or no loss for a noloss, formula or targeted idea),
# identical decisions with CPU no higher for a speed or identical idea, or a gain (or, for the non-inferiority kinds,
# no loss) kept on probability; a targeted keep also needs its target test passed, or its SPRT read as a gain on
# probability (driver.target_verdict's limit_kept "of": "gain"). A result re-judged under a changed rule
# (driver.rejudge) is a later row for its id carrying "rejudged", and only an id's latest row counts (accepted); one
# kept by judgement (limit_kept "of": "judged", LOOP.md: Judged verdicts) is proven by its judged keep record: the
# guard judge.check applied, or the orchestrator's recorded call on an ask.
def proven(row: dict) -> bool:
    sprt = row.get("sprt", {})
    limit = sprt.get("limit_kept", {})
    if limit.get("of") == "judged":
        judged = row.get("judged") or {}
        return judged.get("verdict") == "keep" and (limit.get("probability", 0) >= limit.get("threshold", 1)
                                                     or bool(judged.get("orchestrator")))
    if row.get("idea", {}).get("kind") == "targeted" and (row.get("target") or {}).get("ok") is not True:
        return bool(limit.get("of") == "gain" and limit.get("probability", 0) >= limit.get("threshold", 1))
    return (sprt.get("decision") == "H1" or bool(row.get("identity", {}).get("ok"))
            or bool(limit and limit.get("probability", 0) >= limit.get("threshold", 1)))


def accepted(state: Path, commit: str) -> bool:
    """A branch name alone is not evidence of acceptance; require the recorded gates and checkout."""
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=state / "champion", text=True).strip()
    if checkout != commit:
        return False
    if commit == BOOTSTRAP_COMMIT:
        return True
    path = state / "results.jsonl"
    rows = latest_rows([json.loads(line) for line in path.read_text().splitlines() if line.strip()]
                       if path.exists() else [])
    return any(r.get("candidate") == commit and r.get("verdict") == "kept" and r.get("cpu", {}).get("ok")
               and r.get("bot_tests", {}).get("ok") and proven(r) for r in rows)


# Raises unless every object in `repo` matches its hash and no file in its .git is hard-linked elsewhere: a coder
# hard-linking a main-repo file into its clone could rewrite it there (a backup, since the lanes' sandbox refused such a
# link in a test), and git reads objects without rehashing them, so testing, promoting and publishing check first
# (about a second). The link count catches a link while it exists; fsck catches a rewrite left in place (fsck_error).
def repo_intact(repo: Path) -> None:
    linked = [os.path.join(folder, name) for folder, _, names in os.walk(repo / ".git") for name in names
              if link_count(os.path.join(folder, name)) > 1]
    if linked:
        time.sleep(1)  # git names a new object or pack by linking its temp file, then drops the temp
        for path in linked:
            if link_count(path) > 1:
                raise RuntimeError(f"{path} is hard-linked elsewhere; not using {repo}'s objects")
    if (error := fsck_error(repo, repo / ".git")) is not None:
        raise RuntimeError(f"git fsck failed in {repo}; not using its objects: {error}")


# A file's hard links, 1 when it is gone: git's temporary files, and fetch_checked's quarantine repo, come and go in
# .git while repo_intact walks it.
def link_count(path: str) -> int:
    try:
        return os.lstat(path).st_nlink
    except FileNotFoundError:
        return 1


# What `git fsck --full --no-dangling` in `cwd` finds wrong (its last 400 characters), or None. It can fail while a
# fetch or `git gc` is changing the objects it reads, which are `git_dir`'s, so a failure waits for gc and is checked
# twice more.
def fsck_error(cwd: Path, git_dir: Path) -> str | None:
    for attempt in range(3):
        if attempt:
            for _ in range(60):  # a background `git gc --auto` holds gc.pid while it repacks
                if not (git_dir / "gc.pid").exists():
                    break
                time.sleep(2)
            time.sleep(5)
        check = subprocess.run(["git", "fsck", "--full", "--no-dangling", "--no-progress"], cwd=cwd, text=True,
                               capture_output=True, timeout=300)
        if not check.returncode:
            return None
    return (check.stderr.strip() or check.stdout.strip() or f"exit {check.returncode}, no output")[-400:]  # fsck prints some faults on stdout


class FetchRefused(Exception):
    pass


QUARANTINE_PREFIX = "fetch-quarantine-"
QUARANTINE_STALE = 10 * 60  # a checked fetch takes seconds; each of its git steps is stopped at its timeout


# Holds an exclusive flock on the quarantine folder while fetch_checked uses it, so stale_quarantines never takes a
# slow fetch's quarantine for a crashed one (the kernel drops the lock when its process dies).
@contextmanager
def quarantine_lock(folder: Path) -> Iterator[None]:
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


# The quarantine folders in the git folder `common` that a crashed fetch_checked left behind: older than
# QUARANTINE_STALE and not locked by a fetch still using one. Each is safe to delete.
def stale_quarantines(common: Path, now: float) -> list[Path]:
    stale = []
    for path in sorted(common.glob(f"{QUARANTINE_PREFIX}*")):
        try:
            if path.is_symlink() or not path.is_dir() or now - path.stat().st_mtime <= QUARANTINE_STALE:
                continue
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            continue  # deleted meanwhile
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            continue  # a fetch still using it
        finally:
            os.close(fd)
        stale.append(path)
    return stale


# Fetches `refspec` ("[+]<src>:<dst>", a <dst> outside refs/ being a branch) from the untrusted repo at `source` (a
# coder's or worker's clone) into `repo` so that repo_intact still passes after it. fetch.fsckObjects alone isn't
# enough: git checks .gitmodules and .gitattributes blobs only after it has saved the pack, and a blob `repo` already
# has (a notes file on a wip branch) is left out of the pack, so a refused fetch of a commit renaming it to
# bot/.gitmodules still left that commit and its trees in `repo`, failing every `git fsck --full` until gc (MISSES 47).
# So the fetch goes first into a throwaway bare repo in `repo`'s git folder (which no coder can write) that borrows its
# objects (objects/info/alternates) and holds copies of its refs, so the pack holds what a direct fetch's would; there
# fetch.fsckObjects and then repo_intact's fsck (fsck_error, over `repo`'s objects too, about a second) check it, and
# only then is it fetched on from there into `repo`. The quarantine is always deleted, and one a crash left behind
# (stale_quarantines) is deleted by the next call. A refusal raises FetchRefused with git's reason and adds no object
# or ref to `repo`.
def fetch_checked(repo: Path, source: Path, refspec: str, timeout: float = 600) -> None:
    force, (src, dst) = refspec.startswith("+"), refspec.removeprefix("+").split(":", 1)
    dst = dst if dst.startswith("refs/") else f"refs/heads/{dst}"

    def git(*args: str, cwd: Path, stdin: str | None = None, config: tuple[str, ...] = ()) -> str:
        options = [o for c in ("core.hooksPath=/dev/null", "core.fsmonitor=false", *config) for o in ("-c", c)]
        proc = subprocess.run(["git", *options, *args], cwd=cwd, input=stdin, text=True, capture_output=True,
                              timeout=timeout)
        if proc.returncode:
            why = " ".join((proc.stderr or proc.stdout or "no output").split())[-300:]
            raise FetchRefused(f"git refused the fetch of {refspec} from {source} (git {args[0]}, exit "
                               f"{proc.returncode}): {why}")
        return proc.stdout

    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=repo).strip())
    refs = git("for-each-ref", "--format=create %(refname) %(objectname)", cwd=repo)
    for stale in stale_quarantines(common, time.time()):
        shutil.rmtree(stale, ignore_errors=True)
    with tempfile.TemporaryDirectory(prefix=QUARANTINE_PREFIX, dir=common) as folder, quarantine_lock(Path(folder)):
        quarantine = Path(folder)
        git("init", "-q", "--bare", "--template=", str(quarantine), cwd=repo)
        (quarantine / "objects/info").mkdir(parents=True, exist_ok=True)
        (quarantine / "objects/info/alternates").write_text(f"{common / 'objects'}\n")
        git("update-ref", "--stdin", cwd=quarantine, stdin=refs)
        checked = ("fetch.fsckObjects=true",)
        git("fetch", "-q", "--no-tags", str(source), f"+{src}:refs/quarantine/fetched", cwd=quarantine, config=checked)
        if (error := fsck_error(quarantine, common)) is not None:
            raise FetchRefused(f"git fsck refused the objects of {refspec} from {source}: {error}")
        git("fetch", "-q", "--no-tags", str(quarantine), f"{'+' if force else ''}refs/quarantine/fetched:{dst}",
            cwd=repo, config=checked)


def export_bot(repo: Path, commit: str, destination: Path) -> None:
    """Testing and publishing both read committed sources, excluding ignored/uncommitted files."""
    export_tree(repo, commit, "bot", destination)


# Writes commit `commit`'s folder `folder` (as export_bot does bot/) to `destination`; an empty folder when the commit
# has none there (a candidate with no bot tests), except bot/, which it must have.
def export_tree(repo: Path, commit: str, folder: str, destination: Path) -> None:
    repo_intact(repo)
    if folder != "bot" and subprocess.run(["git", "cat-file", "-e", f"{commit}:{folder}"], cwd=repo,
                                          capture_output=True).returncode:
        destination.mkdir(parents=True, exist_ok=True)
        return
    raw = subprocess.check_output(["git", "archive", "--format=zip", f"{commit}:{folder}"], cwd=repo)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in archive.infolist():
            path = Path(entry.filename)
            if path.is_absolute() or ".." in path.parts or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("unsafe bot archive path")
        archive.extractall(destination)


def snapshot(repo: Path, ref: str = "loop/champion") -> tuple[str, bytes, str]:
    """Package a pinned git tree using the installed SDK, never the mutable champion checkout."""
    from unswbc.project import Project
    from unswbc.submit import _zip, MAX_ZIP_BYTES
    commit = subprocess.check_output(["git", "rev-parse", f"{ref}^{{commit}}"], cwd=repo, text=True).strip()
    with tempfile.TemporaryDirectory(prefix="bc-publish-") as directory:
        folder = Path(directory)
        export_bot(repo, commit, folder)
        project = Project.from_dir(folder)
        project.collect_sources()
        if not project.sources:
            raise ValueError("champion has no uploadable source")
        bundle = _zip(project)
        if len(bundle) > MAX_ZIP_BYTES:
            raise ValueError("champion archive exceeds server limit")
        return commit, bundle, project.language


def submit(bundle: bytes, language: str, name: str, commit: str) -> dict:
    """Use the SDK's multipart format with the same fixed server and safe redirect handling as reads."""
    from unswbc.submit import _multipart
    body, content_type = _multipart({"name": name, "language": language,
                                     "description": f"Accepted champion {commit}"}, "bot.zip", bundle)
    request = urllib.request.Request(ladder.API + "/submissions", data=body, method="POST",
        headers={"Authorization": f"Bearer {ladder.api_key()}", "Content-Type": content_type,
                 "Origin": ladder.SERVER, "User-Agent": "unsw-battlecode-loop"})
    # The upload opens its own connection (a multipart body), so it waits out any 429 the other calls met first.
    delay = ladder.quiet_until() - time.time()
    if delay > 0:
        time.sleep(min(delay, ladder.QUIET_CAP))
    with urllib.request.build_opener(ladder.SafeRedirect()).open(request, timeout=120) as response:
        return json.load(response)


# Records submission `sid` as the verified live bot, and in "uploads" (commit to submission id) for a hold to
# re-activate (hold_cycle).
def bind(report: dict, commit: str, digest: str, sid: int, version: object = None) -> None:
    report.update(verified={"commit": commit, "digest": digest, "submission_id": sid, "at": time.time(),
                            "site_version": version if isinstance(version, int) else None}, error=None)
    report.setdefault("uploads", {})[commit] = sid
    report.pop("pending", None)
    report.pop("waiting", None)


# The site numbers each upload itself (version), and our tags can't follow it: a champion the daily budget skips is
# never uploaded (v0031, 2026-09-26), so from then on v0032 is the site's v31. This keeps the pairs, by commit, from
# /submissions (each upload's name starts with "c" and its commit's first 7 characters), in <loop home>/site-versions.json.
def record_versions(state: Path, rows: list[dict]) -> dict[str, int]:
    path = state / "site-versions.json"
    known = online.read(path, {})
    for row in rows:
        name, version = str(row.get("name") or ""), row.get("version")
        if name.startswith("c") and len(name) > 8 and name[8] == "-" and isinstance(version, int):
            known[name[1:8]] = version
    online.save(path, known)
    return known


# "v0032 (site v31)" for a champion commit, or "v0031 (not on the site)"; the tag alone when nothing is known.
def site_label(state: Path, repo: Path, commit: str) -> str:
    tags = subprocess.run(["git", "tag", "--points-at", commit, "v*"], cwd=repo, capture_output=True, text=True).stdout
    tag = " ".join(t for t in tags.split() if t[1:].isdigit()) or commit[:7]
    version = online.read(state / "site-versions.json", {}).get(commit[:7])
    return f"{tag} (site v{version})" if version else f"{tag} (not on the site)"


def reconcile(state: Path, report: dict, rows: list[dict], current_commit: str) -> None:
    """A unique persisted name identifies even a POST whose response was lost."""
    pending = report["pending"]
    matches = [s for s in rows if s.get("name") == pending["name"]]
    if len(matches) != 1:
        report["error"] = "upload outcome unknown; retained reservation, no automatic re-upload"
        return
    submission = matches[0]
    sid = submission["id"]
    status = submission.get("status")
    if status in ("processing", "pending", "building", "queued"):
        report["error"] = None
        return
    if status not in ("ready", "active"):
        report.update(blocked=True, error=f"champion build failed or unknown status: {status}")
        return
    actual = online.archive_digest(ladder.download(f"/submissions/{sid}/download"))
    if actual != pending["digest"]:
        report.update(blocked=True, error="uploaded source mismatch")
        # The server may already have activated this build. Restore the exact previous archive if possible.
        previous = pending.get("previous")
        if status == "active" and previous and not stopped(state):
            if online.archive_digest(ladder.download(f"/submissions/{previous}/download")) != pending["previous_digest"]:
                raise RuntimeError("rollback archive also changed; manual intervention required")
            ladder.post(f"/submissions/{previous}/activate")
            active = online.active_submission()
            if not active or active["id"] != previous:
                raise RuntimeError("rollback activation not confirmed")
            report["rollback"] = previous
        return
    if status == "ready":
        if pending["commit"] != current_commit:
            report.pop("pending")
            report["error"] = "superseded build verified; will publish newer champion next cycle"
            return
        if stopped(state):
            return
        ladder.post(f"/submissions/{sid}/activate")
        if (online.active_submission() or {}).get("id") != sid:
            raise RuntimeError("activation not confirmed; will reconcile next cycle")
    bind(report, pending["commit"], actual, sid, submission.get("version"))


HOLD = "PUBLISH_HOLD"
SPOOF_RELEASE = "spoof-release.json"
SPOOF_FIELD = re.compile(r"^\s*bool\s+spoof\s*=\s*(true|false)\s*;", re.M)


# A champion whose bot/params.hpp defaults `bool spoof` to true sends fake sonar, which every replay of it shows other
# teams, so it goes live only from the release time in <loop home>/spoof-release.json ({"release_at": an ISO time
# with its offset, "why": ...}; sig, 1 Oct: "after we implement active spoofing we stop"). Returns why commit `commit`
# of `repo` is held at `now` (read with git show, never a checkout), or None when it publishes as any other champion.
# A missing or unreadable file holds it indefinitely.
def spoof_hold(repo: Path, state: Path, commit: str, now: float) -> str | None:
    params = subprocess.run(["git", "show", f"{commit}:bot/params.hpp"], cwd=repo, capture_output=True, text=True)
    field = SPOOF_FIELD.search(params.stdout) if params.returncode == 0 else None
    if field is None or field.group(1) != "true":
        return None
    path = state / SPOOF_RELEASE
    try:
        release = datetime.fromisoformat(json.loads(path.read_text())["release_at"])
        if release.tzinfo is None:
            raise ValueError("release_at needs its offset")
    except FileNotFoundError:
        return f"champion {commit[:7]} spoofs sonar (P.spoof true); held until {path} sets release_at"
    except (OSError, ValueError, KeyError, TypeError) as error:
        return f"champion {commit[:7]} spoofs sonar (P.spoof true); held: {path} unreadable ({error})"
    if release.timestamp() <= now:
        return None
    return f"champion {commit[:7]} spoofs sonar (P.spoof true); held until {release.isoformat()}"


# sig's pick for a tournament (LOOP.md: Publishing): <loop home>/PUBLISH_HOLD, {"version": "v0078", "until": an ISO
# time with its offset, "why": ...}, written by `publishing.py hold` and removed by `release`. The standing hold, or
# None when there is no marker or its `until` has passed (publishing then resumes with the champion). Raises
# ValueError on a marker it can't read, so a broken hold uploads nothing rather than being ignored.
def active_hold(state: Path, now: float) -> dict | None:
    path = state / HOLD
    if not path.exists():
        return None
    try:
        hold = json.loads(path.read_text())
        until = datetime.fromisoformat(hold["until"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"{path} unreadable ({type(error).__name__}: {error}); uploading nothing") from error
    if until.tzinfo is None or not isinstance(hold.get("version"), str):
        raise ValueError(f"{path} needs a version tag and an until with its offset; uploading nothing")
    return hold if until.timestamp() > now else None


# The site submission (a /submissions row of `rows`) holding commit `commit`: bind's saved id, else the one row named
# as an upload of it in uploads.jsonl (a unique saved name, as reconcile matches). None unless exactly one is found.
# Only built rows ("ready" or "active") count: a failed or unfinished build can't be activated.
def held_submission(state: Path, report: dict, rows: list[dict], commit: str) -> dict | None:
    rows = [s for s in rows if s.get("status") in ("ready", "active")]
    by_id = {s.get("id"): s for s in rows}
    if (sid := (report.get("uploads") or {}).get(commit)) in by_id:
        return by_id[sid]
    log = state / "uploads.jsonl"
    names = {r.get("name") for r in (json.loads(line) for line in log.read_text().splitlines() if line.strip())
             if r.get("commit") == commit} if log.exists() else set()
    matches = [s for s in rows if s.get("name") in names]
    return matches[0] if len(matches) == 1 else None


# While a hold stands the upkeep uploads nothing and keeps the held version live: when another submission is active,
# the held version's saved submission is re-activated once its archive matches the tag's commit, never re-uploaded.
# A pending upload waits, unreconciled, for the hold to end.
def hold_cycle(repo: Path, state: Path, report: dict, hold: dict, rows: list[dict]) -> None:
    version = hold["version"]
    report.pop("waiting", None)
    commit, bundle, _ = snapshot(repo, f"refs/tags/{version}")
    held = held_submission(state, report, rows, commit)
    if held is None:
        report["error"] = f"held {version} ({commit[:7]}) has no saved upload on the site; uploading nothing"
        return
    active = next((s for s in rows if s.get("status") == "active"), None)
    is_live = bool(active and active.get("id") == held["id"])
    if is_live and (report.get("verified") or {}).get("submission_id") == held["id"]:
        report["error"] = None
        return
    digest = online.archive_digest(bundle)
    if online.archive_digest(ladder.download(f"/submissions/{held['id']}/download")) != digest:
        report["error"] = f"held {version}'s submission {held['id']} doesn't match {commit[:7]}; not activating it"
        return
    if not is_live:
        if stopped(state):
            return
        ladder.post(f"/submissions/{held['id']}/activate")
        if (online.active_submission() or {}).get("id") != held["id"]:
            raise RuntimeError(f"activation of held {version} not confirmed; will retry next cycle")
    pending = report.get("pending")
    bind(report, commit, digest, held["id"], held.get("version"))
    if pending:
        report["pending"] = pending


def cycle(repo: Path, state: Path) -> dict:
    """One nonblocking build check per cycle; unknown writes retain their reservation across restarts. A newly
    verified live submission (another id or commit) writes a PUBLISHED line to host-alerts.log (REFLECT 2011 F1).
    A standing hold (active_hold) replaces the upload with hold_cycle; a spoofing champion waits (spoof_hold)."""
    if stopped(state):
        return {"status": "stopped"}
    with locked(state / "publishing"), locked(state / "uploads.jsonl"):
        path = state / "publishing.json"
        report = online.read(path, {})
        blocked = report.get("error") if report.get("blocked") else None
        live = lambda: tuple((report.get("verified") or {}).get(k) for k in ("submission_id", "commit"))
        before = live()
        try:
            hold = active_hold(state, time.time())
        except ValueError as error:
            hold = error
        if hold is None and (report.get("blocked") or time.time() < report.get("retry_after", 0)):
            return report
        try:
            if isinstance(hold, ValueError):
                raise hold
            if hold is not None:
                # A block stops uploads, not the hold: it activates only an archive it has just verified. retry_after
                # only delays uploads, so it doesn't apply either.
                rows = ladder.items(ladder.get("/submissions"), "submissions")
                record_versions(state, rows)
                hold_cycle(repo, state, report, hold, rows)
            else:
                commit, bundle, language = snapshot(repo)
                if not accepted(state, commit):
                    raise RuntimeError("champion has no matching accepted result/checkout; not publishing")
                digest = online.archive_digest(bundle)
                rows = ladder.items(ladder.get("/submissions"), "submissions")
                record_versions(state, rows)
                if report.get("pending"):
                    reconcile(state, report, rows, commit)
                else:
                    active = next((s for s in rows if s.get("status") == "active"), None)
                    previous_digest = (online.archive_digest(ladder.download(f"/submissions/{active['id']}/download"))
                                       if active else None)
                    if previous_digest == digest:
                        bind(report, commit, digest, active["id"], active.get("version"))
                    else:
                        log = state / "uploads.jsonl"
                        uploads = [json.loads(line) for line in log.read_text().splitlines() if line.strip()] if log.exists() else []
                        recent = sorted(r["at"] for r in uploads if r["at"] >= time.time() - ladder.UPLOAD_WINDOW_S)
                        held = spoof_hold(repo, state, commit, time.time())
                        if held is not None:
                            # A wait like the budget's: the live bot keeps playing, one alert per held champion.
                            report["error"] = None
                            report["waiting"] = f"{held}; the live bot stays up"
                            if report.get("spoof_held") != commit:
                                alert(state, f"SPOOF HELD: {held}; not uploading it")
                            report["spoof_held"] = commit
                        elif len(recent) >= ladder.MAX_UPLOADS:
                            # A wait, not an error: the verified live bot keeps playing its online games meanwhile
                            # (maintenance runs matches unless publishing is blocked, pending or in error). As an error it
                            # stopped them for three hours after v0031 on 2026-09-26.
                            report["error"] = None
                            report["waiting"] = (f"upload budget exhausted ({ladder.MAX_UPLOADS} an hour, the site's limit); next slot "
                                                 + time.strftime("%H:%M", time.localtime(recent[0] + ladder.UPLOAD_WINDOW_S)))
                        elif not stopped(state):
                            report.pop("waiting", None)
                            report.pop("spoof_held", None)
                            name = f"c{commit[:7]}-{uuid4().hex[:8]}"
                            report["pending"] = {"commit": commit, "digest": digest, "name": name,
                                "at": time.time(), "previous": active["id"] if active else None,
                                "previous_digest": previous_digest}
                            # Save intent and spend budget before sending; failures never invite duplicate uploads.
                            with log.open("a") as handle:
                                handle.write(json.dumps(report["pending"]) + "\n")
                            online.save(path, report)
                            try:
                                submit(bundle, language, name, commit)
                            except HTTPError as error:
                                if error.code in (400, 401, 403, 413, 415, 422, 429):
                                    report["rejected"] = report.pop("pending")
                                    report["retry_after"] = time.time() + 3600
                                error.close()
                                raise
                            report["error"] = None
        except (Exception, SystemExit) as error:
            report["error"] = f"{type(error).__name__}: {error}"[:1200]
        if blocked is not None:  # keep the block's cause for whoever resolves it
            report["error"] = blocked if report.get("error") in (None, blocked) else f"{blocked}; {report['error']}"
        online.save(path, report)
        if live() != before and live()[0] is not None:
            sid, commit = live()
            note = f"; publishing is still blocked: {blocked}" if blocked is not None else ""
            try:  # the upload is done and saved: a failed alert write must not fail the cycle
                alert(state, f"PUBLISHED: submission {sid} ({site_label(state, repo, commit)}) is live, "
                             f"commit {commit[:7]}{note}")
            except OSError as error:
                print(f"WARNING PUBLISHED alert not written: {error}", file=sys.stderr)
        return report


# `publishing.py hold v0078 --until 2026-10-01T12:00+10:00 --why "sig's Sprint pick"` writes <loop home>/PUBLISH_HOLD
# (atomically) for a tagged version this loop has uploaded before; `publishing.py release` removes it.
def main(argv: list[str] | None = None, state: Path = STATE, repo: Path = REPO) -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Hold the site's active submission at one version, or release it.")
    commands = parser.add_subparsers(dest="command", required=True)
    hold = commands.add_parser("hold", help="keep VERSION live and upload nothing until --until")
    hold.add_argument("version", help="a champion tag, such as v0078")
    hold.add_argument("--until", required=True, help="ISO time with its offset, such as 2026-10-01T12:00+10:00")
    hold.add_argument("--why", required=True)
    commands.add_parser("release", help="remove the hold; the next upkeep cycle publishes the champion")
    args = parser.parse_args(argv)
    path = state / HOLD
    if args.command == "release":
        with locked(state / "publishing"):
            path.unlink(missing_ok=True)
        print(f"released: removed {path}")
        return
    try:
        until = datetime.fromisoformat(args.until)
    except ValueError:
        parser.error(f"--until {args.until!r} is not an ISO time")
    if until.tzinfo is None or until.timestamp() <= time.time():
        parser.error("--until needs its offset (+10:00) and must be in the future")
    found = subprocess.run(["git", "rev-parse", "--verify", "-q", f"refs/tags/{args.version}^{{commit}}"], cwd=repo,
                           capture_output=True, text=True)
    if found.returncode:
        parser.error(f"no tag {args.version}")
    commit = found.stdout.strip()
    log = state / "uploads.jsonl"
    with locked(state / "publishing"):  # cycle holds it through a whole upload, so the hold starts between two
        report = online.read(state / "publishing.json", {})
        uploaded = commit in (report.get("uploads") or {}) or (
            log.exists() and any(json.loads(line).get("commit") == commit for line in log.read_text().splitlines()
                                 if line.strip()))
        if not uploaded:
            parser.error(f"{args.version} ({commit[:7]}) has no saved upload; a hold never uploads, so it can't be "
                         "held")
        online.save(path, {"version": args.version, "until": until.isoformat(), "why": args.why})
    print(f"held: {args.version} ({commit[:7]}) until {until.isoformat()}; the next upkeep cycle makes it live")
    if (pending := report.get("pending")) and pending.get("commit") != commit:
        print(f"WARNING: upload {pending.get('name')} ({str(pending.get('commit'))[:7]}) is still pending and the "
              f"site may activate it itself; the hold re-activates {args.version} on the next cycle after that")


if __name__ == "__main__":
    main()

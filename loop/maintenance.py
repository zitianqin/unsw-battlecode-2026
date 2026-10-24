"""Upkeep, ticked about every minute by both providers, each job on its own clock (docs and toolkit hourly), with no
model calls of its own beyond the background workers it starts (map ideas, the Discord digest, the REFLECT list,
the replay audit, the riser study, the backlog audit);
toolchain changes are applied only at experiment boundaries."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

import decoys
import discord_teams
import quota
import throughput
from home import current_code, load_fraction_copy, nice_increment
from meta import locked

INTERVAL = 3600
# The backlog audit's (loop/backlog.py) interval, sig 2026-09-29 ~16:20: "lets do it once a day? in the loop".
BACKLOG_EVERY = 24 * 3600


def read_state(state: Path) -> dict:
    path = state / "maintenance.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save_state(state: Path, record: dict) -> None:
    temporary = state / "maintenance.json.tmp"
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(state / "maintenance.json")


def due(record: dict, now: float) -> bool:
    return "checked_at" not in record or now - record["checked_at"] >= INTERVAL


def publish_docs(source: Path, target: Path) -> list[str]:
    """Publish a fully fetched mirror, leaving unchanged pages (including their dates) alone."""
    changed = []
    for page in sorted(source.glob("*.md")):
        destination = target / page.name
        text = page.read_text()
        old = destination.read_text() if destination.exists() else ""
        if re.sub(r"\A<!--.*?-->\s*", "", text, flags=re.S) == re.sub(r"\A<!--.*?-->\s*", "", old, flags=re.S):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".md.tmp")
        temporary.write_text(text)
        temporary.replace(destination)
        changed.append(page.name)
    return changed


def command(args: list[str], cwd: Path, *, env: dict | None = None, timeout: int = 300) -> str:
    """Run maintenance without toolkit self-updates, editor installs, or model invocations."""
    environment = {**os.environ, "UNSWBC_NO_UPDATE": "1", "UNSWBC_NO_VSCODE": "1", **(env or {})}
    result = subprocess.run(args, cwd=cwd, env=environment, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} {args[1]} failed: {result.stderr[-1000:]}")
    return result.stdout


def fetch_updates(repo: Path, state: Path) -> dict:
    """Use a private gstack browser, not an LLM or the user's browser session. The docs mirror is staged whole in a
    temporary folder and published only when complete, writing only changed pages (publish_docs): an incomplete fetch
    keeps the working mirror."""
    browser = os.environ.get("GSTACK_BROWSE", str(Path.home() / ".claude/skills/gstack/browse/dist/browse"))
    if not Path(browser).is_file():
        raise RuntimeError("gstack browse unavailable; set GSTACK_BROWSE")
    env = {"BROWSE_STATE_FILE": str(state / "maintenance-browser/browse.json"), "GSTACK_BROWSE": browser}
    with tempfile.TemporaryDirectory(prefix="docs-", dir=state) as directory:
        try:
            command([browser, "newtab", "https://game.battlecode.au/docs/overview"], repo, env=env)
            output = command(["uv", "run", str(repo / "scripts/mirror_docs.py"), "--docs-only",
                              "--out", directory], repo, env=env, timeout=300)
            if "WARN" in output:
                raise RuntimeError("incomplete docs mirror: " + output[-1000:])
            changed = publish_docs(Path(directory), repo / "docs/unsw")
            command([browser, "goto", "https://pypi.org/pypi/unswbc/json"], repo, env=env)
            payload = command([browser, "js", "JSON.parse(document.body.innerText).info.version"], repo, env=env)
            version = payload.strip().strip('"')
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                raise ValueError("PyPI did not return a stable unswbc version")
            return {"available": version, "changed_docs": changed}
        finally:
            command([browser, "stop"], repo, env=env)


def check(repo: Path, state: Path) -> dict:
    """Persist the hourly clock across restarts and serialize checks from both providers. <loop home>/maintenance.json
    records the timing, changed docs, pending updates and failures."""
    state.mkdir(parents=True, exist_ok=True)
    with locked(state / "maintenance"):
        record = read_state(state)
        if not due(record, time.time()):
            return record
        record["checked_at"] = time.time()
        save_state(state, record)
        started = time.monotonic()
        try:
            record.update(fetch_updates(repo, state), check_error=None)
        except Exception as error:
            record["check_error"] = f"{type(error).__name__}: {error}"[:1200]
        try:
            compare_engine(repo, state, record)
            record["compare_error"] = None
        except Exception as error:  # nothing is skipped: the version waits for an idle boundary as before
            record["compare_error"] = f"{type(error).__name__}: {error}"[:1200]
        record["check_seconds"] = round(time.monotonic() - started, 2)
        save_state(state, record)
        return record


# Files of the unswbc package that can't change a game, its CPU count or an upload: the VS Code replay viewer (1.2.1
# changed only it and the version string). Bytecode caches and the top-level __init__.py's __version__ line are left
# out too.
NOT_ENGINE = {"replay-viewer.vsix"}
PACKAGE = ("import json, os, importlib.metadata as m, unswbc; "
           "print(json.dumps({'dir': os.path.dirname(unswbc.__file__), 'requires': sorted(m.requires('unswbc') or [])}))")


def install_toolchain(repo: Path, state: Path, version: str) -> Path:
    """Install one toolkit version into its own venv under toolchains/ and return its python."""
    folder = state / "toolchains" / version
    python = folder / "bin/python"
    if not python.exists():
        command(["uv", "venv", str(folder)], repo)
    command(["uv", "pip", "install", "--python", str(python), f"unswbc=={version}",
             f"pycapnp=={importlib.metadata.version('pycapnp')}"], repo)
    return python


def engine_digest(python: Path, repo: Path) -> str:
    """One hash over the installed package's files, except NOT_ENGINE, and its declared requirements (wasmtime, which
    meters CPU, is unpinned): equal digests play identical games."""
    installed = json.loads(command([str(python), "-c", PACKAGE], repo))
    package = Path(installed["dir"])
    total = hashlib.sha256(json.dumps(installed["requires"]).encode())
    for path in sorted(p for p in package.rglob("*") if p.is_file()):
        relative = path.relative_to(package)
        if "__pycache__" in relative.parts or str(relative) in NOT_ENGINE:
            continue
        data = path.read_bytes()
        if str(relative) == "__init__.py":
            data = b"".join(line for line in data.splitlines(True) if not line.startswith(b"__version__"))
        total.update(str(relative).encode() + b"\0" + hashlib.sha256(data).digest())
    return total.hexdigest()


def compare_engine(repo: Path, state: Path, record: dict) -> None:
    """Once per new version, record in `engine_compared` whether it plays exactly like the active runtime. A same-engine
    release (only NOT_ENGINE files differ) is never applied, so it holds no claims for an idle boundary and splits no
    results into a new engine epoch; the next release is compared with the active one again."""
    version, active = record.get("available"), record.get("active") or {}
    compared = record.setdefault("engine_compared", {})
    if not version or not active.get("python") or version == active.get("version") \
            or compared.get(version, {}).get("against") == active.get("version"):
        return
    same = engine_digest(install_toolchain(repo, state, version), repo) == engine_digest(Path(active["python"]), repo)
    compared[version] = {"against": active.get("version"), "same": same, "at": time.time()}


# Whether the available version plays exactly like the active runtime, so there is nothing to upgrade.
def same_engine(record: dict) -> bool:
    entry = (record.get("engine_compared") or {}).get(record.get("available"), {})
    return bool(entry.get("same")) and entry.get("against") == (record.get("active") or {}).get("version")


def check_compatibility(rows: list[dict]) -> bool:
    if len(rows) != 2:
        return False
    for row in rows:
        if row.get("error"):
            return False
        for side in ("a", "b"):
            bot = row.get(side) or {}
            points = bot.get("points") or {}
            if (bot.get("crashes") != 0 or bot.get("timeouts") != 0
                    or not 0 <= points.get("p99", -1) <= 50_000_000
                    or not 0 <= points.get("max", -1) <= 90_000_000):
                return False
    return True


def compatibility_suite(repo: Path, champion: Path) -> Path:
    """Use the same pre-test bootstrap policy as new lanes; preserve intentional suite deletions."""
    suite = champion / "tests/bot"
    if not suite.exists() and not command(["git", "log", "-1", "--format=%H", "HEAD", "--", "tests/bot"], champion).strip():
        return repo / "tests/bot"
    return suite


def prepare_toolchain(repo: Path, state: Path, version: str) -> dict:
    """Test the new engine in isolation against unchanged champion code before switching anything."""
    python = install_toolchain(repo, state, version)
    folder = python.parent.parent
    champion = state / "champion"
    tests = command([str(python), str(repo / "loop/bot_tests.py"), "--bot", str(champion / "bot"),
                     "--tests", str(compatibility_suite(repo, champion))], repo, timeout=600)
    (folder / "bot-tests.log").write_text(tests)
    # Publishing uses these SDK helpers too. Probe packaging locally before accepting an SDK upgrade.
    command([str(python), "-c", "import io, sys, zipfile; from unswbc.project import Project; "
             "from unswbc.submit import _zip, _multipart; p=Project.from_dir(sys.argv[1]); p.collect_sources(); "
             "blob=_zip(p); assert zipfile.is_zipfile(io.BytesIO(blob)); "
             "body,kind=_multipart({'name':'probe','language':p.language},'bot.zip',blob); "
             "assert blob in body and kind.startswith('multipart/form-data')", str(champion / "bot")], repo)
    maps = sorted((state / "maps-train").glob("*.map"))
    if not maps:
        raise RuntimeError("no maps for toolchain compatibility; run loop setup first")
    out = folder / "compatibility.jsonl"
    command([str(python), str(repo / "loop/native.py"), "play", "referee/run.py", "--a",
             str(champion / "bot"), "--b", str(champion / "bot"), "--maps", str(maps[0]),
             "--sandbox", "--workers", "1", "--out", str(out)], state / "referee", timeout=2400)
    rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    if not check_compatibility(rows):
        raise RuntimeError(f"new engine failed replay/CPU compatibility: {out}")
    # record.py refuses a toolkit whose internals nobody has checked it against: alert now, not when a test or a
    # prediction first needs a recording. It never fails the upgrade, as predicted games only save time, and alerts
    # once per version: a later step failing retries the whole upgrade hourly.
    probe = "import record; print(record.supported() or '')"
    try:
        unrecorded = command([str(python), "-c", probe], repo / "referee").strip()
    except Exception as error:  # a failed or hung probe is still an alert, never a failed upgrade
        unrecorded = f"its probe failed: {error}"
    alerts, head = state / "host-alerts.log", f"toolkit {version}: referee/record.py can't record"
    try:  # an unwritable log mustn't fail the upgrade either
        if unrecorded and head not in (alerts.read_text(errors="replace") if alerts.exists() else ""):
            with alerts.open("a") as handle:  # the tail keeps a traceback's last line, the one naming the error
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {head}, so no game can be predicted: "
                             f"{' '.join(unrecorded.split())[-500:]}\n")
    except OSError:
        pass
    return {"version": version, "python": str(python), "validated_at": time.time(), "epoch": f"unswbc-{version}",
            "champion": command(["git", "rev-parse", "HEAD"], champion).strip(), "compatibility": str(out)}


# Whether a toolchain upgrade is ready to try now: a valid version newer than the active one, not one that plays
# exactly like the active runtime (same_engine: recorded, never applied), and not in its retry wait. While it is true
# and an idea is being tried, driver.claim_next holds new claims until the pipeline is idle.
def upgrade_waiting(state: Path) -> bool:
    record = read_state(state)
    version, active = record.get("available"), record.get("active", {})
    if not version or version == active.get("version") or not re.fullmatch(r"\d+\.\d+\.\d+", version) \
            or same_engine(record):
        return False
    if active and tuple(map(int, version.split("."))) < tuple(map(int, active["version"].split("."))):
        return False
    return time.time() - record.get("upgrade_attempt_at", 0) >= INTERVAL


def apply_pending(repo: Path, state: Path, busy: bool) -> dict:
    """Caller holds the claim/boundary lock. Never install while an idea is being tried."""
    if busy:
        return {"status": "deferred"}
    with locked(state / "maintenance"):
        record = read_state(state)
        version = record.get("available")
        active = record.get("active", {})
        if not version or version == active.get("version") or same_engine(record):
            return {"status": "unchanged"}
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("invalid package version")
        if active and tuple(map(int, version.split('.'))) < tuple(map(int, active["version"].split('.'))):
            return {"status": "unchanged"}
        if time.time() - record.get("upgrade_attempt_at", 0) < INTERVAL:
            return {"status": "retry-later", "error": record.get("upgrade_error")}
        record["upgrade_attempt_at"] = time.time()
        save_state(state, record)
        previous_version = importlib.metadata.version("unswbc")
        try:
            candidate = prepare_toolchain(repo, state, version)
            command(["uv", "add", f"unswbc=={version}"], repo)
            command(["uv", "tool", "install", "--force", f"unswbc=={version}"], repo)
            record.update(active=candidate, upgrade_error=None)
            save_state(state, record)
            with (state / "toolchain-epochs.jsonl").open("a") as handle:
                handle.write(json.dumps(candidate) + "\n")
            return {"status": "upgraded", **candidate}
        except Exception as error:
            record["upgrade_error"] = f"{type(error).__name__}: {error}"[:1200]
            if not active and previous_version != version:
                try:
                    record["active"] = prepare_toolchain(repo, state, previous_version)
                except Exception as fallback_error:
                    record["upgrade_error"] += f"; previous runtime validation failed: {fallback_error}"[:500]
            save_state(state, record)
            return {"status": "failed", "error": record["upgrade_error"]}


NATIVE_GATES = "gates-run.json"  # in <loop home>/native-judge: the native_gates step's last launch ({"at", "pid"})
NATIVE_GATE_STATUS = "remote-status-native-gate.json"  # in <loop home>: its hosts' games (throughput.status_files)


# Hourly upkeep step, with no model involved: starts referee/native_fidelity.py --gate-missing in the background, from
# the referee checkout (the code evaluations play) with the active toolchain's Python, playing the champion's commit
# as it stood at the start (pinned_champion) against the rotation, through loop/native.py play as
# a screen filler (BC_YIELD=1: beside an evaluation, the late screen's share of an 8+-core host, none on a smaller one
# or while a tune plays; an idle filler's none everywhere left gates waiting for room for hours), its hosts' games in
# flight in remote-status-native-gate.json (throughput and health read it), which gates each place the loop plays
# native games at (the Mac hosts, whose gate this Mac's own games go on too, here only with no Mac host; each group of
# Linux hosts building alike: on 29 Sep all 11 Linux hosts, az-* included, reported one compiler, so one group, gated
# on ll) that has no gate yet for the current native_judge.key,
# the hosts' places first, and ends at once when none is missing. A place whose game finds no room within an hour
# keeps the games that matched (enabled.json's "progress") and the run goes on to the next place; the next hour's run
# plays the rest, so a gate completes over several windows. A
# toolkit upgrade (apply_pending, which never waits for this) or a referee refresh that changes the judge or the shim
# makes a new key; until a place's gate passes, its games play through unswbc run. Each gate records its verdict in
# <loop home>/native-judge/enabled.json and alerts on a failure; health.native_judge reports failed and missing gates.
# One run at a time, its output in native-judge/gates.log (the latest run's only).
def native_gates(state: Path, now: float | None = None) -> str | None:
    import meta

    now = time.time() if now is None else now
    folder = state / "native-judge"
    path = folder / NATIVE_GATES
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        record = {}
    record = record if isinstance(record, dict) else {}
    if meta.alive(record.get("pid")) or now - record.get("at", 0) < INTERVAL:
        return None
    referee = state / "referee" / "referee"
    python = toolchain(state).get("python")
    if not python or not (referee / "native_fidelity.py").is_file():
        return None
    import driver
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (champion := pinned_champion(state, folder)) is None:
        return "native judge: no gate this hour: the champion's commit couldn't be exported (pinned_champion)"
    bots = [str(champion), *(str(bot) for _, bot in driver.rotation_bots(list(driver.ROTATION)))]
    (folder / "gates.log").write_text("")
    native = Path(__file__).resolve().with_name("native.py")
    pid = meta.spawn([python, str(native), "play", "referee/native_fidelity.py", "--gate-missing", "--bots", *bots,
                      "--out", str(folder)], referee.parent, folder / "gates.log",
                     {**os.environ, "BC_YIELD": "1", "UNSWBC_NO_UPDATE": "1", "UNSWBC_NO_VSCODE": "1",
                      "BC_REMOTE_STATUS": str(state / NATIVE_GATE_STATUS)})
    path.write_text(json.dumps({"at": now, "pid": pid}) + "\n")
    return f"native judge: gating any place without a gate for this key in the background ({folder / 'gates.log'})"


# The champion's bot as its commit holds it, exported (git archive) to <folder>/bots/champion-<commit>, which the gate
# plays instead of <loop home>/champion/bot: a gate can take hours, and a promotion swapping that checkout under it
# (29 Sep 15:49, the first 300-game run) made one game's two ways play different bots, read as mismatches. The gauntlet
# opponents are frozen builds already (driver.rotation_bots). One export is kept: native_gates calls this only with no
# gate running, and it removes the others. None when the commit can't be read or exported.
def pinned_champion(state: Path, folder: Path) -> Path | None:
    import shutil

    git = ["git", "-c", "core.fsmonitor=false", "-C", str(state / "champion")]
    commit = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return None
    bots = folder / "bots"
    pinned, part = bots / f"champion-{commit[:12]}", bots / f".champion-{commit[:12]}.part"
    if not pinned.is_dir():
        shutil.rmtree(part, ignore_errors=True)
        part.mkdir(parents=True)
        archive = subprocess.run([*git, "archive", "--format=tar", f"{commit}:bot"], capture_output=True)
        if archive.returncode or subprocess.run(["tar", "-x", "-C", str(part)], input=archive.stdout).returncode:
            shutil.rmtree(part, ignore_errors=True)
            return None
        part.rename(pinned)
    for old in bots.iterdir():
        if old != pinned:
            shutil.rmtree(old, ignore_errors=True)
    return pinned


HOST_CRASHES = "host-crashes.json"  # the Mac hosts' bot crash reports (host_crashes), which health.bot_crashes reads


# Hourly upkeep step: each host of this Mac's platform (it plays this Mac's builds, so a report's build UUID names our
# build as here) is asked for its bot crash reports (Host.crash_reports: build UUIDs and times alone) since the newest
# it gave before, on its own clock (the first time since health.CRASH_FIRST_S ago), and each is kept in HOST_CRASHES
# with its host and the time it was found here ("found"), which health.bot_crashes reads beside this Mac's own
# reports (MISSES 86's detector read only this Mac's). "found" is when the file is saved, after every host answered:
# bot_crashes keeps rows found after its last check, and a health run while the hosts were asked (every driver.record
# runs one) moved that past the step's start. Rows found over health.CRASH_FIRST_S ago are dropped, so the
# file holds two days of crashes; each host's platform is asked once and remembered. A host that doesn't answer is
# asked again next hour from where it was; "fetched_at" is the last hour a Mac host answered (health.UPKEEP_JOBS).
# Returns a line when reports were found, else None.
def host_crashes(repo: Path, state: Path, now: float | None = None) -> str | None:
    import health
    import home
    import online

    started = time.time()
    now = started if now is None else now
    path = state / HOST_CRASHES
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        record = {}
    record = record if isinstance(record, dict) else {}
    if now - record.get("at", 0) < INTERVAL:
        return None
    if str(repo / "referee") not in sys.path:
        sys.path.append(str(repo / "referee"))  # appended: loop/'s modules keep their names
    import remote

    platforms, read = record.get("platforms", {}), record.get("read", {})
    rows = [row for row in record.get("reports", []) if now - row.get("found", 0) < health.CRASH_FIRST_S]
    new, failed, answered = [], [], False
    for host in remote.load_hosts():
        try:
            if host.name not in platforms:
                platforms[host.name] = host.platform()
            if platforms[host.name] != remote.PLATFORM:
                continue
            since = read.get(host.name, now - health.CRASH_FIRST_S)
            reports = host.crash_reports(home.BOT_NAME, since)
        except remote.RemoteError as error:
            failed.append(f"{host.name} ({str(error)[:80]})")
            continue
        read[host.name], answered = max([since, *(report["at"] for report in reports)]), True
        new += [{"host": host.name, "uuid": str(report["uuid"]), "at": report["at"]} for report in reports]
    saved = now + time.time() - started  # `now` then, on the caller's clock
    rows += [{**row, "found": saved} for row in new]
    fetched = now if answered else record.get("fetched_at")  # health.upkeep alerts when no Mac host answers for long
    online.save(path, {"at": now, "fetched_at": fetched, "attempt_at": now, "error": "; ".join(failed),
                       "platforms": platforms, "read": read, "reports": rows})
    return f"host crash reports: {len(new)} bot crash reports found on the Mac hosts ({path})" if new else None


def toolchain(state: Path) -> dict:
    """Return a frozen runtime for a new experiment, including pre-maintenance installations."""
    active = read_state(state).get("active")
    if active:
        return active
    version = importlib.metadata.version("unswbc")
    return {"version": version, "python": sys.executable, "epoch": f"unswbc-{version}", "validated_at": None}


DISCORD_EVERY = 24 * 3600  # sig, 2026-09-29: "discord digest once a day" (it was every 4 h)
# While this marker exists in the loop home the upkeep reads no Discord and starts no digest (sig, 2026-09-30:
# "Temporarily pause all discord digests until future notice"); delete it to resume. health.py stays quiet meanwhile.
DISCORD_PAUSED = "DISCORD_PAUSED"
DISCORD_RETRY = 3600  # after a failed read (signed out, sig's browser gone, killed, another read running)
DISCORD_MAX_HOURS = 72  # the longest window a read covers, however long since the last good one
# The reader's budget (tools/discord_read.py --minutes) and the kill after it: a day's read took an estimated 6-12
# min, 15-25 on a 2,000-message day (the Sprint's, say), and the kill stays under health.UPKEEP_TICK_HUNG.
DISCORD_MINUTES = 40
DISCORD_KILL = 50 * 60
MAPIDEAS_ENABLED = True  # off from 2a4a0b7 to e10b861, while its worker's file tools and reads were unconfined


# The hours a read starting at `now` covers: back to the start of the last good read (`fetched_from`, or its
# `fetched_at` in a record from before that was kept) plus half an hour, so nothing falls between reads however long
# they failed, at most DISCORD_MAX_HOURS; with no good read yet, DISCORD_EVERY plus half an hour.
def discord_hours(report: dict, now: float) -> float:
    since = report.get("fetched_from") or report.get("fetched_at")
    if not isinstance(since, (int, float)):
        return DISCORD_EVERY / 3600 + 0.5
    return round(min((now - since) / 3600 + 0.5, DISCORD_MAX_HOURS), 2)


# Every DISCORD_EVERY (a failed read again after DISCORD_RETRY), the chosen channels back to the last good read's
# start plus half an hour (discord_hours) through the logged-in gstack browser (tools/discord_read.py, read-only; sig
# 2026-09-26). discord.json keeps attempt_at/fetched_at/error like the other jobs, so health.py alerts when it keeps
# failing, e.g. when the browser has been signed out and needs sig to sign in again, or isn't running and needs sig to
# open it.
def discord(repo: Path, state: Path) -> None:
    import mapideas
    with locked(state / "discord"):
        path = state / "discord.json"
        report = json.loads(path.read_text()) if path.exists() else {}
        started = time.time()
        if started - report.get("attempt_at", 0) < (DISCORD_RETRY if report.get("error") else DISCORD_EVERY):
            return
        report["attempt_at"] = started
        store = state / "discord" / "messages.jsonl"
        before = len(store.read_text().splitlines()) if store.exists() else 0  # the reader only appends
        code, out, err = None, "", ""
        try:
            done = subprocess.run([sys.executable, str(repo / "tools/discord_read.py"),
                                   "--hours", f"{discord_hours(report, started):g}", "--minutes", str(DISCORD_MINUTES)],
                                  capture_output=True, text=True, timeout=DISCORD_KILL)
            code, out, err = done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired:
            pass
        if code == 3 and "another discord_read.py is running" in err:  # a backfill held its lock: nothing was read,
            code = 4  # so it is a failed read, retried after DISCORD_RETRY, not a partial one that moves the window on
        # Counted on every exit: a signed-out, partial or killed read has still stored what it read.
        added = mapideas.parse(store.read_text().splitlines()[before:] if store.exists() else [])
        maps, at = len(mapideas.new_messages(added)), time.time()
        problems = [line[len("discord: problem: "):] for line in err.splitlines()
                    if line.startswith("discord: problem: ")]
        if code in (0, 3):  # 3: a partial read (see tools/discord_read.py); what it read is stored and in latest.md
            report.update(fetched_at=at, fetched_from=started, error=None, summary=out.strip()[-200:])
            if code == 3:
                report.update(partial="; ".join(problems)[:500] or "(no problem listed)",
                              partial_runs=report.get("partial_runs", 0) + 1)
            else:
                report.update(partial=None, partial_runs=0)
        else:  # 2: signed out, or sig's browser isn't running or answering (health.py's upkeep alert shows this
            # error, the reader's own line naming which, once health.UPKEEP_JOBS' limit passes without a good read);
            # anything else, or killed at DISCORD_KILL
            report["error"] = (f"timed out after {DISCORD_KILL // 60} min" if code is None else
                               (err.strip() or f"exit {code}")[-500:])
        # The orchestrator watches this file: it reads latest.md and decides what becomes an idea, a rule or loop
        # change, or news for sig (sig, 2026-09-26); the reader itself doesn't judge. Every read that finished is
        # reported, so a quiet one is seen to have run; a failed one when it stored messages.
        partial = f" (partial: {problems[0][:160] if problems else 'no problem listed'})" if code == 3 else ""
        latest = state / "discord" / "latest.md" if code is not None else store  # a killed read wrote no latest.md
        news = None
        if added:
            report["new_at"] = at  # health.py alerts if no digest follows
            failed = f" (failed: {report['error'].splitlines()[-1][:160]})" if code not in (0, 3) else ""
            news = (f"{len(added)} new messages ({maps} in map channels){partial}{failed}; see {latest} (the digest "
                    "worker's report follows)")
        elif code in (0, 3):
            news = f"0 new messages, nothing to digest{partial}"
        if news:
            with (state / "host-alerts.log").open("a") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} discord read: {news}\n")
        path.write_text(json.dumps(report, indent=1) + "\n")


def scout(repo: Path, state: Path) -> None:
    """Hourly public-opponent diagnostics, version labeled and never mixed with our own old submissions: downloads at
    most 10 new replays and summarises the latest 20 indexed games with their exact map identities, less our own."""
    import ladder
    import online
    with locked(state / "scout"):
        path = state / "scout.json"
        report = online.read(path, {})
        if time.time() - report.get("attempt_at", 0) < INTERVAL:
            return
        report["attempt_at"] = time.time()
        online.save(path, report)
        try:
            folder = state / "scout-replays"
            command([sys.executable, str(repo / "tools/replays.py"), "fetch", "--top-battles",
                     "--max-games", "10", "--out", str(folder)], repo, timeout=300)
            our_team = ladder.get("/team")["team"]["id"]
            index = folder / "index.jsonl"
            rows = [json.loads(line) for line in index.read_text().splitlines() if line.strip()]
            games = []
            for row in rows[-20:]:
                if (state / "STOP").exists() or (state / "ASTRA_STOP").exists():
                    return
                match = ladder.get(f"/battles/{row['match']}").get("match", {})
                # The site stopped naming a battle's submissions on 2026-09-28 (online.API_GONE): kept as None.
                if (match.get("status") not in online.FINISHED
                        or our_team in (match.get("teamAId"), match.get("teamBId"))):
                    continue
                summary = json.loads(command([sys.executable, str(repo / "tools/replays.py"), "summary",
                                               str(folder / f"{row['match']}.replay")], repo, timeout=120))
                if summary.get("checks", {}).get("ok"):
                    games.append({**{k: match.get(k) for k in ("id", "teamAId", "teamBId", "submissionAId",
                        "submissionBId", "mapId", "mapHash", "ranked", "requestedAt")}, "summary": summary})
            report.update(games=games, fetched_at=time.time(), error=None)
            (state / "scout.md").write_text("# Public opponent observations\n\n"
                f"Fetched at {report['fetched_at']}. Exact submissions below where the site named them (null since "
                "2026-09-28 19:30 AEST: unknown); historical versions may differ from today's bots. "
                "No games involving our team. Style hypotheses only, not independent samples or promotion evidence.\n\n"
                + json.dumps(games, indent=2) + "\n")
        except (Exception, SystemExit) as error:
            report["error"] = f"{type(error).__name__}: {error}"[:1200]
            (state / "scout.md").write_text(f"Scout refresh failed; no current evidence. See {path}.\n")
        online.save(path, report)


STUDY_KEEP = (10, 10, 4, 4, 4)  # recent games studied per ladder rank, us excluded: most for ranks 1-2
STUDY_NEW = (8, 8, 3, 3, 3)  # new replays downloaded per team per hour (at most 25)


def study_plan(board: list[dict], our_id: int) -> list[tuple[dict, int, int]]:
    """The top-ranked teams other than us, each with how many games to study and to download this hour."""
    top = [t for t in sorted(board, key=lambda t: t.get("rank") or 10**9) if t.get("id") != our_id]
    return list(zip(top, STUDY_KEEP, STUDY_NEW))


def team_games(rows: list[dict], team_id: int, keep: int) -> list[tuple[dict, str]]:
    """A team's latest `keep` indexed ranked games, each with the side it played. Only ranked games surely show a
    leader's real bot: some top teams play a weaker one in unranked games, where their rating isn't at stake (sig,
    2026-09-26), so those could teach us to copy a decoy."""
    games = [(r, "A" if r.get("teamAId") == team_id else "B") for r in rows
             if team_id in (r.get("teamAId"), r.get("teamBId")) and r.get("ranked") is True]
    return games[-keep:]


# Games of one version the study reads at most, per team and for us: every saved ranked game of a leader's current
# version (REFLECT 2026-09-28 14:51 F29; it read each team's latest 10 before). The cost bound: summaries are cached
# in study.json by file and analyser (about 4 KB each, so at most 6 x STUDY_MAX, about 2.4 MB); an hour adds one
# `replays.py summary` per replay STUDY_NEW downloads, and a changed tools/replays.py re-summarises up to 6 x STUDY_MAX.
STUDY_MAX = 100


def version_games(rows: list[dict], team_id: int, keep: int) -> list[tuple[dict, str]]:
    """A team's indexed ranked games (team_games) of its current version, the latest STUDY_MAX of them: those naming
    the submission its latest game names. When its latest names none (rows fetched before the index saved submission
    ids, or since the site stopped naming them, online.API_GONE), its games after the last one that names any, or its
    latest `keep` while those are fewer: by team and time, so possibly more than one of its versions (MISSES 77)."""
    games = team_games(rows, team_id, len(rows))
    named = lambda i: games[i][0].get(f"submission{games[i][1]}Id")
    if not games or (version := named(-1)) is None:
        since = next((i + 1 for i in reversed(range(len(games))) if named(i)), 0)
        run = games[since:]
        return (run if len(run) >= keep else games[-keep:])[-STUDY_MAX:]
    return [(r, side) for r, side in games if r.get(f"submission{side}Id") == version][-STUDY_MAX:]


def decoy_games(rows: list[dict], team_id: int) -> list[tuple[object, float, float]]:
    """A team's indexed ranked games (team_games) as the decoy check reads them (loop/decoys.py): (its submission or
    None, the request time, its score: the index names a winner, never a draw)."""
    out = []
    for row, side in team_games(rows, team_id, len(rows)):
        winner = row.get("winner")
        score = 0.5 if winner not in ("a", "b") else float(winner == side.lower())
        if (at := decoy_time(row)) is not None:  # a row with no request time (older fetches) can't be placed
            out.append((row.get(f"submission{side}Id"), at, score))
    return out


def decoy_time(row: dict) -> float | None:
    at = row.get("requestedAt")
    return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp() if at else None


def mean_facts(facts: list[dict]) -> dict:
    """Per-game means of every number in the teams' summaries (a missing count is 0), keeping their shape. A group
    some games lack (None or absent, like `at400` in a game that ended before round 400, or in an older summary) is
    averaged over the games that have it, with their number as its `n`, so the others don't count as zeros."""
    out: dict = {}
    keys = sorted({k for f in facts for k in f})
    for key in keys:
        values = [f.get(key) for f in facts]
        if any(isinstance(v, dict) for v in values):
            groups = [v for v in values if isinstance(v, dict)]
            out[key] = mean_facts(groups) | ({"n": len(groups)} if len(groups) < len(values) else {})
        elif all(v is None or isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            numbers = [v or 0 for v in values]
            out[key] = round(sum(numbers) / len(numbers), 2)
    return out


def study_section(title: str, games: list[tuple[dict, str, dict]], replays: list[Path], note: str = "",
                  turning: str = "") -> str:
    """One team's block in study.md: record, per-game means of its facts, and replays to open; `note` says where
    the games come from when that isn't the section's usual source, `turning` is its turning_line."""
    facts = team_facts(games)
    wins = sum(f.get("won") is True for f in facts)
    maps = sorted({summary["map"]["name"] for _, _, summary in games})
    lines = [f"## {title}", "", f"{len(games)} games studied, {wins} won; maps: {', '.join(maps) or 'none'}."]
    if note:
        lines.append(note)
    if turning:
        lines.append(turning)
    if (tools := str(Path(__file__).resolve().parent.parent / "tools")) not in sys.path:  # once, not once a section
        sys.path.insert(0, tools)
    import replays as tool
    if race := tool.race_text(facts):  # M78: how it contests pearls, pooled, whatever its opponents
        lines.append(f"Pearl race (pearls both teams saw, first seen by both in r{tool.RACE_ROUNDS[0]}-"
                     f"r{tool.RACE_ROUNDS[1]}, by its nearest head's gap to the other team's then; its share eaten): "
                     f"{race}.")
    if sprints := tool.sprint_text(facts):  # M77: a sprint with no head-on and no enemy head in view is movement
        lines.append(f"Sprints {sprints}.")
    if deaths := deaths_line([s["doom"][side] for _, side, s in games if s.get("doom")], len(games)):
        lines.append(deaths)
    if replays:
        lines.append("Replays to open with tools/replays.py (summary, deaths, lengths): "
                     + ", ".join(str(r) for r in replays))
    if facts:
        lines += ["", "Per-game means:", "```json", json.dumps(mean_facts(facts), indent=1), "```"]
    return "\n".join(lines) + "\n"


# A study.md block's line of bceval's turning points in the team's losses and draws among `games` ((replay, its side,
# summary)), each read from its own side (bceval_read.brief; study_text gives the caveat once). Cached per
# replay, so an hour reads only the new games', 0.1-0.3 s each at nice 19 (the first study read up to about 250).
# "" while bceval is off.
def turning_line(state: Path, games: list[tuple[Path, str, dict]]) -> str:
    import online
    bceval_read = online.bceval_module()
    if not bceval_read.enabled():
        return ""
    lost = [(file, side) for file, side, summary in games if summary["teams"][side].get("won") is not True]
    return bceval_read.brief([bceval_read.game(file, side, state) for file, side in lost], "losses and draws")


def team_facts(games: list[tuple[dict, str, dict]]) -> list[dict]:
    return [summary["teams"][side] for _, side, summary in games]


# The stats whose gap to ranks 1-2 study.md tracks over our submissions (method note M18): a name, where the number
# sits in a team's per-game means (or a function of them), the smallest difference that counts as a gap, and the
# phrases that mark an idea as citing it. An idea cites a stat when its title or hypothesis contains one of the
# phrases, ignoring case: plain substring matching, so the phrases are the words ideas actually use.
GapStat = tuple[str, Callable[[dict], float | None], float, tuple[str, ...]]


def _at(*keys: str) -> Callable[[dict], float | None]:
    def get(means: dict) -> float | None:
        for key in keys:
            means = means.get(key) if isinstance(means, dict) else None
        return means if isinstance(means, (int, float)) else None
    return get


def _direction_bias(means: dict) -> float | None:
    """How far the most common first step of a blind opening move is above an even 25%."""
    shares = ((means.get("behaviour") or {}).get("opening") or {}).get("blindDirPct") or {}
    return round(max(shares.values()) - 25, 2) if shares else None


GAP_STATS: tuple[GapStat, ...] = (
    ("pearl in view % of moves, r0-99", _at("behaviour", "opening", "pearlInViewPct"), 5,
     ("pearl in view", "pearl-in-view", "pearlinview", "see a pearl")),
    ("map seen % by r100", _at("behaviour", "opening", "mapSeenPct"), 5,
     ("map seen", "mapseen", "of the map by r100", "seen by r100", "tiles seen")),
    ("blind moves keeping their direction %, r0-99", _at("behaviour", "opening", "blindStraightPct"), 5,
     ("keep their heading", "keep its heading", "keeps its heading", "previous heading", "heading share",
      "straight ahead", "blindstraight", "keep their direction", "keeps its direction")),
    ("blind moves' direction bias (top share - 25), r0-99", _direction_bias, 5,
     ("direction bias", "scan order", "north share", "blinddirpct", "go north")),
    ("pearls eaten r0-99", _at("behaviour", "pearlsByPhase", "early"), 20,
     ("pearls r0-99", "pearls eaten r0-99", "early pearls", "pearlsbyphase", "opening pearls")),
    ("dragons alive at r100", _at("aliveAt100"), 4, ("alive at r100", "alive at 100", "aliveat100")),
)
# A gap "stayed as large" when the current one is at least this share of the previous one: they are 10-game means.
GAP_KEPT = 0.8
GAP_MIN_GAMES = 5  # a mean over fewer games (a version just gone live) is too noisy to compare


def gap_stats(games: list[tuple[dict, str, dict]]) -> dict[str, float] | None:
    """A team's per-game means of the GAP_STATS, or None with no games."""
    facts = team_facts(games)
    if not facts:
        return None
    means = mean_facts(facts)
    return {name: value for name, get, _, _ in GAP_STATS if (value := get(means)) is not None}


def references(row: dict) -> list[tuple[str, dict]]:
    """The teams a history row compares us with, each labelled, when it has GAP_MIN_GAMES games: ranks 1-2 (ranked
    games), then the dev benchmark (its side of our benchmark sweeps; method note M20)."""
    teams = [(f"rank {t['rank']}", t) for t in row.get("leaders", []) if t.get("rank") in (1, 2)]
    if dev := row.get("dev"):
        teams.append((f"dev {dev.get('id')}", dev))
    return [(label, t) for label, t in teams if (t.get("games") or 0) >= GAP_MIN_GAMES]


def gap(row: dict, name: str) -> float | None:
    """The references (ranks 1-2 and the dev benchmark) minus us for one stat in a history row, when every one of
    them with GAP_MIN_GAMES games is on the same side of us: the smaller difference, signed. None when we have fewer
    games or no number, when no reference has enough games, or when they sit either side of us."""
    ours = (row.get("us") or {}).get(name)
    theirs = [t["stats"][name] for _, t in references(row) if name in t.get("stats", {})]
    if ours is None or (row.get("us_games") or 0) < GAP_MIN_GAMES or not theirs:
        return None
    diffs = [value - ours for value in theirs]
    if not (all(d > 0 for d in diffs) or all(d < 0 for d in diffs)):
        return None
    return min(diffs, key=abs)


def citing(ideas: list[dict], phrases: tuple[str, ...]) -> list[dict]:
    """The live ideas (not merged or dropped) whose title or hypothesis contains one of `phrases`, ignoring case."""
    return [i for i in ideas if i.get("status") not in ("merged", "dropped")
            and any(p in f"{i.get('title', '')} {i.get('hypothesis', '')}".lower() for p in phrases)]


def working_on(idea: dict) -> bool:
    """An idea still at work on a gap: being coded or evaluated, or waiting untested at priority 1-3. A tested one
    (tried, kept, followup) is finished with it: the gap outlived it."""
    status = idea.get("status") or ""
    return status.startswith("trying") or status == "todo" and idea.get("priority", 5) <= 3


def unchanged_gaps(history: list[dict], ideas: list[dict]) -> list[str] | None:
    """study.md's "Gaps unchanged" flags: a gap to the references (ranks 1-2 and the dev benchmark) of at least the
    stat's minimum at both our current submission and the previous one (each's latest history row), on the same side
    and now at least GAP_KEPT of its old size, while no idea citing the stat is working on it (every untested one sits
    at priority 4-5, or none cites it). Only rows written in the latest row's pool period (online.pool_period of
    its `at`) count: from online.POOL_CUTOFF a map id may carry other pearl timings. None when those rows don't
    cover two submissions yet."""
    import online
    period = lambda row: online.pool_period({"requested_at": row.get("at")})
    history = [row for row in history if period(row) == period(history[-1])]
    latest: dict = {}
    for row in history:
        latest.pop(row["submission"], None)  # re-inserted last, so the dict keeps submissions in order of last row
        latest[row["submission"]] = row
    if len(latest) < 2:
        return None
    before, now = list(latest.values())[-2:]
    flags = []
    for name, _, minimum, phrases in GAP_STATS:
        old, new = gap(before, name), gap(now, name)
        if old is None or new is None or old * new <= 0 or min(abs(old), abs(new)) < minimum:
            continue
        cites = citing(ideas, phrases)
        if abs(new) < GAP_KEPT * abs(old) or any(working_on(i) for i in cites):
            continue
        leaders = ", ".join(f"{label} {t['stats'][name]:g}" for label, t in references(now)
                            if name in t.get("stats", {}))
        waiting = [f"{i.get('id')} (priority {i.get('priority')})" for i in cites if i.get("status") == "todo"]
        tested = [i.get("id") for i in cites if i.get("status") != "todo"]
        cited = "; ".join(filter(None, [
            f"untested {', '.join(waiting)}" if waiting else "",
            f"tested before {', '.join(tested)}" if tested else ""]))
        cited = cited or f"none (none mentions {' / '.join(repr(p) for p in phrases)})"
        flags.append(f"- **{name}**: us {now['us'][name]:g} vs {leaders} at submission {now['submission']} "
                     f"(gap {new:+.1f}); {old:+.1f} at {before['submission']}. Ideas citing it: {cited}.")
    return flags


def gaps_text(history: list[dict], ideas: list[dict], path: Path) -> str:
    """study.md's "Gaps unchanged" section, from the history at `path` and the backlog."""
    flags = unchanged_gaps(history, ideas)
    text = ("## Gaps unchanged\n\nGaps between us and ranks 1-2 and the dev benchmark (each with 5+ games, all on one "
            "side of us) that stayed as large over our last two submissions while no idea citing them is in flight or "
            f"queued at priority 1-3 (history: {path}).\n\n")
    if flags is None:
        return text + ("Not measurable yet: the history has fewer than two of our submissions in the current pool "
                       "period (online.pool_period).\n\n")
    return text + ("\n".join(flags) if flags else "None.") + "\n\n"


# The stats study.md's map-matched block compares (method M69): the GAP_STATS, then pearls a game and per 100
# dragon-turns (turns counts one a dragon a round, so the rate is apart from how many dragons a team keeps), the
# share of rounds at the unit ceiling (tools/replays.py capRoundsPct, M71), portal crossings a game and the share of
# them whose crosser died within 10 rounds (tools/replays.py portal_crossings, M80), and in-place deaths and dead-end
# stations a game (in_place_deaths, M84). Each reads a per-game means dict.
def _per_turns(means: dict) -> float | None:
    pearls, turns = means.get("pearlsEaten"), means.get("turns")
    return round(100 * pearls / turns, 2) if isinstance(pearls, (int, float)) and turns else None


# The share of a map's crossings dying within 10 rounds, from its means (a ratio of means: every crossing counts once).
def _crossing_deaths(means: dict) -> float | None:
    crossed, died = _at("portals", "crossings")(means), _at("portals", "diedWithin10")(means)
    return round(100 * died / crossed, 2) if crossed and died is not None else None


MATCHED_STATS: tuple[tuple[str, Callable[[dict], float | None]], ...] = (
    *((name, get) for name, get, _, _ in GAP_STATS),
    ("pearls eaten a game", _at("pearlsEaten")), ("pearls per 100 dragon-turns", _per_turns),
    ("rounds at the unit ceiling %", _at("capRoundsPct")), ("portal crossings a game", _at("portals", "crossings")),
    ("% of crossings dying within 10 rounds", _crossing_deaths), ("in-place deaths a game", _at("inPlace", "deaths")),
    ("dead-end stations a game", _at("inPlace", "stations")))


def matched_block(label: str, theirs: list[tuple[dict, str, dict]], ours: list[tuple[dict, str, dict]]) -> str:
    """One team's map-matched lines against our games (method M69: pooled per-game means confound a gap with the map
    mix, rank 1's 941 pearls a game to our 640 over different maps): each MATCHED_STATS stat as the mean over the maps
    both played of each side's per-map mean, so every such map counts once for both, with the maps and games each."""
    def by_map(games: list[tuple[dict, str, dict]]) -> dict[str, list[dict]]:
        maps: dict[str, list[dict]] = {}
        for _, side, summary in games:
            maps.setdefault(summary["map"]["name"], []).append(summary["teams"][side])
        return maps

    them, us = by_map(theirs), by_map(ours)
    common = sorted(set(them) & set(us))
    if not common:
        return f"- {label}: no map both played ({len(theirs)} games v our {len(ours)}).\n"
    counts = ", ".join(f"{m} {len(them[m])}/{len(us[m])}" for m in common)
    cells = []
    for name, get in MATCHED_STATS:
        pairs = [(get(mean_facts(them[m])), get(mean_facts(us[m]))) for m in common]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        if pairs:
            a, b = (sum(x) / len(pairs) for x in zip(*pairs))
            fewer = f", {len(pairs)} maps" if len(pairs) < len(common) else ""
            cells.append(f"{name} {a:.1f} v our {b:.1f} ({a - b:+.1f}{fewer})")
    return (f"- {label} on the {len(common)} maps both played (games theirs/ours: {counts}): "
            + ("; ".join(cells) or "no stat both sides have") + ".\n")


def matched_text(leaders: list[tuple[str, list]], dev: tuple[str, list] | None, ours: list) -> str:
    """study.md's map-matched section: matched_block for each leader and the dev benchmark against our games."""
    text = ("## Map-matched gaps (method M69)\n\nEach team against our online games on the maps both played only: a "
            "stat is the mean over those maps of each side's per-map mean, so a gap is not the map mix. Small counts "
            "are leads, not findings.\n\n")
    if not ours:
        return text + "No games of ours to match yet.\n\n"
    teams = [*leaders, *([dev] if dev else [])]
    return text + "".join(matched_block(label, games, ours) for label, games in teams if games) + "\n"


def read_jsonl(path: Path) -> list[dict]:
    """A JSON-lines file's rows, skipping lines that don't parse (a torn append, or a file read mid-rewrite)."""
    rows = []
    for line in path.read_text().splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


Summarise = Callable[[Path], dict | None]  # a replay's `tools/replays.py summary`, or None if its checks failed

STUDY_DOOM_READS = 60  # new replays a study reads with `watch.py doom` (about 2 s each), the rest at later studies


# M56: a replay's deaths by how they came about ({"A": kinds, "B": kinds}, tools/watch.py doom --json's death_kinds
# read from side A), for study_section's deaths line: cached in `dooms` under `key` (the replay and watch.py's hash,
# so a changed doom tool reads again), at most STUDY_DOOM_READS new reads a study (`reads` counts them), None until
# read or when the tool fails on it (tried again at the next study).
def study_dooms(repo: Path, file: Path, dooms: dict, key: str, used: set, reads: list[int]) -> dict | None:
    used.add(key)
    if key not in dooms and reads[0] < STUDY_DOOM_READS:
        reads[0] += 1
        try:
            kinds = json.loads(command([sys.executable, str(repo / "tools/watch.py"), "doom", "--json", "--team", "A",
                                        str(file)], repo, timeout=300))["kinds"]
            dooms[key] = {"A": kinds["ours"], "B": kinds["theirs"]}
        except (RuntimeError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass
    return dooms.get(key)


# study_section's deaths line (M56, MISSES 64): the team's deaths by kind (watch.death_kind) over the games whose doom
# kinds are read, each kind's deaths a game and share, most common first, with its own-move deaths (a trigger of its
# own move: ate, portal, deeper) and split-outs added up apart. None without any.
def deaths_line(kinds: list[dict[str, int]], games: int) -> str | None:
    if not kinds:
        return None
    total = Counter()
    for k in kinds:
        total.update(k)
    deaths, n = sum(total.values()), len(kinds)
    if not deaths:
        return f"Deaths by how they came about (tools/watch.py doom, M56): none in {n} of {games} games."
    own = sum(v for kind, v in total.items() if kind.split(" ")[-1] in ("ate", "portal", "deeper"))
    share = lambda v: f"{v / n:.2f}/g, {100 * v / deaths:.0f}%"
    shown = total.most_common(8)
    rest = deaths - sum(v for _, v in shown)
    return (f"Deaths by how they came about (tools/watch.py doom, M56; {n} of {games} games read, "
            f"{deaths / n:.1f} a game): own move {share(own)}, split-out {share(total.get('split-out', 0))}; by kind: "
            + ", ".join(f"{kind} {share(v)}" for kind, v in shown)
            + (f", {len(total) - len(shown)} other kinds {share(rest)}" if rest else "") + ". A count, not a "
            "diagnosis: watch 3 cases with watch.py frames at since-1, since and the death round.")


def study(repo: Path, state: Path) -> None:
    """Hourly study of the ladder's top teams, weighted to ranks 1-2, next to our own current games; no model calls.
    A failed leader fetch keeps the last good leader blocks, marked with their age; our games are always current."""
    import online
    with locked(state / "study"):
        path = state / "study.json"
        report = online.read(path, {})
        if time.time() - report.get("attempt_at", 0) < INTERVAL:
            return
        report["attempt_at"] = time.time()
        online.save(path, report)
        cache, used = report.get("summaries", {}), set()
        # Keyed by the analyser's own hash too, so a new measure re-summarises old replays instead of reading 0.
        analyser = hashlib.sha256((repo / "tools/replays.py").read_bytes()).hexdigest()[:12]
        dooms, doom_key = report.get("dooms", {}), hashlib.sha256((repo / "tools/watch.py").read_bytes()).hexdigest()[:12]
        doom_reads = [0]

        def summarise(file: Path) -> dict | None:
            key = f"{file}@{analyser}"
            used.add(key)
            if key not in cache:
                cache[key] = json.loads(command([sys.executable, str(repo / "tools/replays.py"),
                                                 "summary", str(file)], repo, timeout=300))
            summary = cache[key]
            if not summary.get("checks", {}).get("ok"):
                return None
            kinds = study_dooms(repo, file, dooms, f"{file}@{doom_key}", used, doom_reads)
            return {**summary, "doom": kinds} if kinds else summary

        blocks: dict[str, list] = {}  # each block's games, for the map-matched section (matched_text)
        try:
            fetched = leader_sections(repo, state, summarise, blocks)
            if fetched is None:  # a stop marker appeared mid-fetch
                return
            leaders, leader_stats = fetched
            report.update(leaders=leaders, fetched_at=time.time(), error=None)
        except (Exception, SystemExit) as error:
            report["error"] = f"{type(error).__name__}: {error}"[:1200]
            leader_stats = None
        submission, our_stats, our_games = None, None, 0
        try:
            ours, submission, our_stats, our_games = our_section(state, summarise, blocks)
        except (Exception, SystemExit) as error:
            ours = f"## Us\n\nOur online games could not be summarised: {type(error).__name__}: {str(error)[:300]}\n"
        try:
            dev, dev_stats = dev_section(state, summarise, blocks)
        except (Exception, SystemExit) as error:
            cause = f"{type(error).__name__}: {str(error)[:300]}"
            dev = f"## Dev benchmark\n\nIts games could not be summarised: {cause}\n"
            dev_stats = {"games": 0, "error": cause}
        if not report.get("error"):  # after a failed fetch, keep the leaders' cached summaries for the next one
            cache = {k: v for k, v in cache.items() if k in used}
            dooms = {k: v for k, v in dooms.items() if k in used}
        report["summaries"], report["dooms"] = cache, dooms
        # Which opponent versions a study has read, "team:submission" -> games, for health.unstudied_versions.
        studied = report.setdefault("studied", {})
        for row in [*(leader_stats or []), *([dev_stats] if dev_stats and dev_stats.get("id") else [])]:
            if row.get("submission") and row.get("games"):
                key = f"{row['id']}:{row['submission']}"
                studied[key] = max(studied.get(key, 0), row["games"])
        history = state / "study-history.jsonl"
        if leader_stats is not None and our_stats and submission is not None:  # only fresh leaders go in the history
            row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "submission": submission,
                   "us": our_stats, "us_games": our_games, "leaders": leader_stats, "dev": dev_stats}
            torn = history.exists() and not history.read_bytes().endswith(b"\n")  # else this row joins a torn one
            with history.open("a") as f:
                f.write("\n" * torn + json.dumps(row, ensure_ascii=False) + "\n")
        try:
            gaps = gaps_text(read_jsonl(history), read_jsonl(state / "ideas.jsonl"), history)
        except Exception as error:  # the flags must never stop study.md from updating
            gaps = (f"## Gaps unchanged\n\nThe gap flags could not be computed: {type(error).__name__}: "
                    f"{str(error)[:300]}\n\n")
        try:
            gaps += matched_text([(label, games) for label, games in blocks.items() if label.startswith("rank ")],
                                 ("dev benchmark", blocks["dev"]) if blocks.get("dev") else None,
                                 blocks.get("us", []))
        except Exception as error:  # nor must the map-matched block
            gaps += f"## Map-matched gaps (method M69)\n\nNot computed: {type(error).__name__}: {str(error)[:300]}\n\n"
        try:  # M85: the live build's ram cells online, ours and theirs on us
            import rulecounts
            rams = "\n".join(rulecounts.ram_swing_lines(rulecounts.online_counterparts(repo, state, leaders=False)))
        except Exception as error:
            rams = f"## Ram swings here and online (method M85)\n\nNot computed: {type(error).__name__}: {error}"[:400]
        (state / "study.md").write_text(study_text(report, dev + "\n" + ours + "\n" + rams, path, time.time(), gaps))
        online.save(path, report)


def leader_sections(repo: Path, state: Path, summarise: Summarise,
                    studied: dict | None = None) -> tuple[str, list[dict]] | None:
    """Fetches the top teams' latest replays and returns their study.md blocks, each over its current version's saved
    ranked games (version_games), with each team's GAP_STATS means (rank, id, name, submission, games, stats), or None
    if a stop marker appeared. `studied` gets each team's games as "rank N (team ID)" (matched_text)."""
    import ladder
    folder = state / "study-replays"
    our_id = ladder.get("/team")["team"]["id"]
    plan = study_plan(ladder.items(ladder.get("/leaderboard"), "teams"), our_id)
    for team, keep, new in plan:
        if (state / "STOP").exists() or (state / "ASTRA_STOP").exists():
            return None
        command([sys.executable, str(repo / "tools/replays.py"), "fetch", "--team", str(team["id"]),
                 "--battles", str(-(-keep // 5)), "--max-games", str(new), "--ranked", "--out", str(folder)],
                repo, timeout=600)
    index = folder / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines() if line.strip()] if index.exists() else []
    sections, stats = [], []
    names = discord_teams.read(state)  # the last hour's older names, so a renamed leader is still recognised
    for rank, (team, keep, _) in enumerate(plan, 1):
        games = []
        for row, side in version_games(rows, team["id"], keep):
            file = folder / f"{row['match']}.replay"
            if file.exists() and (summary := summarise(file)):
                games.append((row, side, summary))
        older = discord_teams.formerly(names, team["id"], team.get("name"))
        title = (f"Rank {rank}: {team.get('name')} (team {team['id']}{older}, Elo {team.get('elo')}"
                 f"{', dev' if team.get('dev') else ''})")
        top = [folder / f"{row['match']}.replay" for row, _, _ in games[-3:]] if rank <= 2 else []
        turning = turning_line(state, [(folder / f"{row['match']}.replay", side, summary)
                                       for row, side, summary in games])
        decoy = decoys.check(decoy_games(rows, team["id"]), looks=len(plan))
        note = decoys.line(decoy) + (" Those games are left out of its gap stats." if decoy["flagged"] else "") \
            if decoy else ""
        sections.append(study_section(title, games, top, note, turning=turning))
        if studied is not None:
            studied[f"rank {rank} (team {team['id']})"] = games
        # Its version only when every game names the same one (version_games may span several once unnamed).
        named = {row.get(f"submission{side}Id") for row, side, _ in games}
        version = named.pop() if len(named) == 1 else None
        flagged = bool(decoy and decoy["flagged"])  # a possible decoy's play is no fit of the team's (MISSES 103)
        kept = [g for g in games if not flagged or decoy_time(g[0]) < decoy["first_at"]]
        stats.append({"rank": rank, "id": team["id"], "name": team.get("name"), "submission": version,
                      "games": len(games), "stats": gap_stats(kept) or {}, "decoy": flagged})
    return "\n".join(sections), stats


def leader_facts(repo: Path, state: Path, ranks: tuple[int, ...] = (1, 2)) -> list[tuple[str, list[dict]]]:
    """The team facts (each game's summary of its own side) of leader_summaries' games: for rule-counts.md's pearl
    race (M78)."""
    return [(label, [summary["teams"][side] for summary, side in games])
            for label, games in leader_summaries(repo, state, ranks)]


def leader_summaries(repo: Path, state: Path,
                     ranks: tuple[int, ...] = (1, 2)) -> list[tuple[str, list[tuple[dict, str]]]]:
    """The latest study's leaders at `ranks`, each labelled "rank N (name)" with its games' (whole replay summary, its
    side), over the games the study reads (version_games) whose summaries by the current tools/replays.py it has
    cached in study.json, read without summarising anything: for rule-counts.md's pearl race (leader_facts) and
    two-king row (M86). A team with none cached yet (the study re-summarises within the hour of a tools change) has an
    empty list."""
    import online
    try:
        latest = read_jsonl(state / "study-history.jsonl")[-1]["leaders"]
    except (IndexError, KeyError):
        return []
    cache = online.read(state / "study.json", {}).get("summaries", {})
    analyser = hashlib.sha256((repo / "tools/replays.py").read_bytes()).hexdigest()[:12]
    folder = state / "study-replays"
    rows = read_jsonl(folder / "index.jsonl")
    out = []
    for team in sorted((t for t in latest if t.get("rank") in ranks), key=lambda t: t["rank"]):
        games = []
        for row, side in version_games(rows, team["id"], STUDY_KEEP[team["rank"] - 1]):
            summary = cache.get(f"{folder / (str(row['match']) + '.replay')}@{analyser}")
            if summary and summary.get("checks", {}).get("ok"):
                games.append((summary, side))
        out.append((f"rank {team['rank']} ({team.get('name')})", games))
    return out


def our_section(state: Path, summarise: Summarise,
                studied: dict | None = None) -> tuple[str, int | None, dict[str, float] | None, int]:
    """study.md's block for our current submission's (with its play group, online.same_play) latest STUDY_MAX result-blind ladder and autoscrim games
    (online.sample_games) whose replays are saved and summarise, with the submission id, its GAP_STATS means (None without games) and how many games they are over. Dev games
    are left out (online.against_dev): at a few hundred an hour they would be nearly all, and the dev benchmark has its
    own block (dev_section), so study-history's gap rows keep comparing the leaders with our ladder games."""
    import online
    live = online.read(state / "online.json", {})
    dev = online.dev_ids(live)
    ours = [g for g in online.sample_games(live.get("games", []))
            if g.get("submission_id") in online.same_play(live) and not online.against_dev(g, dev)][-STUDY_MAX:]
    games = []
    for game in ours:
        file = Path(game["replay"])
        if file.exists() and (summary := summarise(file)):
            games.append((game, game["side"], summary))
    title = f"Us: submission {live.get('submission_id')} (online games)"
    if studied is not None:  # as "us", for matched_text
        studied["us"] = games
    turning = turning_line(state, [(Path(g["replay"]), side, summary) for g, side, summary in games])
    return (study_section(title, games, [], turning=turning), live.get("submission_id"), gap_stats(games),
            len(games))


DEV_KEEP = 30  # the dev benchmark's latest games studied: of up to online.DEV_REPLAYS replays a version of ours keeps


def benchmark_games(state: Path, team_id: int) -> list[dict]:
    """Our online games against team `team_id`, each once and oldest first: every version's online-replays index
    plus online.json's current games (the newest record of a game wins)."""
    import online
    sources = sorted((p for p in (state / "online-replays").glob("*/index.json") if p.parent.name.isdigit()),
                     key=lambda p: int(p.parent.name))
    games: dict = {}
    for rows in [online.read(p, {"games": []})["games"] for p in sources] + [
            online.read(state / "online.json", {}).get("games", [])]:
        games.update((g["id"], g) for g in rows if g.get("opponent") == team_id)
    return sorted(games.values(), key=lambda g: (g.get("requested_at") or "", g["id"]))


def dev_section(state: Path, summarise: Summarise, studied: dict | None = None) -> tuple[str, dict | None]:
    """study.md's block for the dev benchmark online.json names (online.dev_benchmark): its side of the latest
    DEV_KEEP of the result-blind files (online.file_selected's complement: no replays kept only because we lost, nor
    a pruned version's dev files) against the
    submission of its latest game (not the highest id; once the site stopped naming it, online.API_GONE, the games
    after the last one naming any, MISSES 77), and its GAP_STATS means (id, name, submission, games, stats), or
    (a "no dev benchmark yet" block, None) when online.json has none. Dev teams play no ranked games and don't fake the unranked
    ones, so this is a strong reference whose games are real even while the ladder leaders have few ranked games
    (M20)."""
    import online
    team = (online.read(state / "online.json", {}).get("benchmark") or {}).get("team")
    if not team:
        return "## Dev benchmark\n\nNo dev benchmark in online.json yet.\n", None
    played = benchmark_games(state, team["id"])
    submissions = Counter(g.get("opponent_submission_id") for g in played)
    # The submission it plays now (its latest game's), not the highest id: a rollback to an older bot counts too. None
    # once the site stopped naming it (online.API_GONE): then the games after the last one naming any (MISSES 77).
    newest = played[-1].get("opponent_submission_id") if played else None
    named = [i for i, g in enumerate(played) if g.get("opponent_submission_id")]
    since = named[-1] + 1 if newest is None and named else 0
    games = []
    # Replays kept only because we lost (online.DEV_LOSS_REPLAYS, "kept": "loss") would tilt the sample to its wins,
    # as would an old version's files once prune packed its dev wins' (online.file_selected).
    sample = [g for g in played[since:] if not online.file_selected(g) and g.get("opponent_submission_id") == newest]
    for game in sample[-DEV_KEEP:]:
        file = Path(game["replay"])
        if file.exists() and (summary := summarise(file)):
            games.append((game, "B" if game["side"] == "A" else "A", summary))
    ours = sorted({g["submission_id"] for g, _, _ in games})
    older = discord_teams.formerly(discord_teams.read(state), team["id"], team.get("name"))
    title = (f"Dev benchmark: {team.get('name')} (team {team['id']}{older}, Elo {team.get('elo')}, dev), submission "
             f"{newest or 'unknown (unnamed since 2026-09-28)'}")
    note = (f"Its side of our unranked benchmark sweeps, against our submissions {', '.join(map(str, ours)) or 'none'}."
            " Its submissions we have played (games): "
            + (", ".join(f"{s} ({n})" for s, n in sorted(submissions.items(), key=lambda i: i[0] or 0)) or "none")
            + "; only the newest is studied.")
    replays = [Path(g["replay"]) for g, _, _ in games[-3:]]
    if studied is not None:  # as "dev", for matched_text
        studied["dev"] = games
    stats = {"id": team["id"], "name": team.get("name"), "submission": newest, "games": len(games),
             "stats": gap_stats(games) or {}}
    turning = turning_line(state, [(Path(g["replay"]), side, summary) for g, side, summary in games])
    return study_section(title, games, replays, note, turning), stats


def study_text(report: dict, ours: str, path: Path, now: float, gaps: str = "") -> str:
    """study.md: when the leaders were fetched and the file written (so an old file shows its age even if the study
    stops running), a stale note when this hour's fetch failed, the gap flags, the last good leader blocks, then
    `ours` (the current blocks: the dev benchmark's and our games)."""
    def utc(at: float) -> str:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(at))

    text = ("# Top-team study\n\nThe ladder's top five teams (us excluded), most games for ranks 1-2, then the "
            "organisers' dev benchmark, then our own current submission. Leaders' games are ranked only, since some "
            "teams play a weaker bot in unranked games; the dev benchmark's are its side of our unranked benchmark "
            "sweeps (dev teams play no ranked games and never fake theirs); ours are our other online games, dev "
            "games left out. Numbers are per-game means over the listed games, from each team's own side. "
            "Opponents' versions change; treat differences as hypotheses to test, not measured gains.\n\n")
    import online
    if (bceval_read := online.bceval_module()).enabled():  # each block's turning_line
        text += f"Turning points: {bceval_read.CAVEAT}\n\n"
    leaders, when = report.get("leaders"), report.get("fetched_at")
    fetched = f"Leaders fetched {utc(when)}" if leaders and when else "No leader fetch has succeeded yet"
    text += f"{fetched}; this file written {utc(now)}.\n\n"
    if error := report.get("error"):
        cause = [line for line in error.splitlines() if line.strip()][-1][:200]  # a fetch's own error ends its stderr
        age = f"; the leader blocks below are {(now - when) / 3600:.1f} h old" if leaders and when else ""
        text += f"**Stale: this hour's leader fetch failed ({cause}){age}. See {path}.**\n\n"
    return text + gaps + (leaders + "\n" if leaders else "") + ours


# Keeps the copy of <state>/load-fraction that coders' sandboxes read (native.load_fraction) equal to it, and removes
# the copy when sig's share ends, so a coder never keeps a stale one.
def mirror_load_fraction(state: Path) -> None:
    source, copy = state / "load-fraction", load_fraction_copy()
    if not source.exists():
        copy.unlink(missing_ok=True)
    elif not copy.exists() or copy.read_text() != source.read_text():
        copy.write_text(source.read_text())


def tick(repo: Path, state: Path, stop_path: Path, models: bool = True) -> None:
    """The same upkeep sequence for both providers; no work after either stop marker. A step that raises is logged
    and the rest still run (health included). No model calls of its own: the map-ideas, Discord digest, REFLECT,
    replay-audit, riser-study and backlog-audit steps may start their background model workers (loop/mapideas.py,
    loop/digest.py, loop/reflect.py, loop/audit.py, loop/risers.py, loop/backlog.py), only with
    `models`, which native sessions turn off, and the filler steps their background games
    (loop/rescreen.py, loop/sweep.py, loop/baselines.py, loop/advmaps.py, and a dead official-map report's,
    loop/official.py), the map pool's fill (loop/mappool.py) and the online fit rows' replays (loop/online_fit.py), which it never waits for."""
    import advmaps
    import audit
    import backlog
    import baselines
    import behaviour_gaps
    import digest
    import health
    import ladder_maps
    import mapideas
    import mappool
    import meta
    import official
    import online
    import online_fit
    import pack
    import prune
    import publishing
    import reflect
    import replay_library
    import rescreen
    import risers
    import screenport
    import sweep

    def stopped() -> bool:
        return stop_path.exists() or publishing.stopped(state)

    def step(name: str, run: Callable[[], object]) -> object:
        if stopped():
            return None
        try:
            return run()
        except Exception as error:
            print(f"maintenance: {name} failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
            return None

    step("load allowance copy", lambda: mirror_load_fraction(state))
    # The health checks whose minutes matter (health.QUICK) run beside the tick, not in it (ticking's quick thread):
    # the full health check below runs hourly, after steps that can take many minutes.
    if line := step("reflect", lambda: reflect.check(state, time.time())):  # loop/reflect.py
        print(line, file=sys.stderr, flush=True)
    if line := step("REFLECT list", lambda: reflect.tick(state, models)):  # its background worker (loop/reports.py)
        print(line, file=sys.stderr, flush=True)
    # A background review or audit whose worker has exited is applied here, not only at the driver's next claim,
    # which can be an hour away with every lane busy. Never launches one; each writes its outcome to host-alerts.log.
    for name, apply in (("finished review", lambda: meta.apply_finished(state)),
                        ("finished audit", lambda: audit.apply_finished(state))):
        if applied := step(name, apply):
            print(applied, file=sys.stderr, flush=True)
    # Early: each is quick, only starting its background run and never waiting for it, and the steps below can take
    # many minutes, which would hold the fillers back at every tick. The late screen first: a champion that never met a
    # screen entry is the more urgent gap, and its run is short (loop/rescreen.py). Then the variant sweep
    # (loop/sweep.py), the prewarm, the champion's baseline games ahead of its tests (loop/baselines.py), and the
    # search for maps the champion loses to the starter bot on, and past its first budget, while nothing else wants the
    # machines, to the SPRT rotation's opponents (loop/advmaps.py). Last the map pool, never-used maps a new champion's
    # shared set takes instead of making them in its first evaluation (loop/mappool.py; this Mac only, niced).
    for name, filler in (("late screen", rescreen.tick), ("variant sweep", sweep.tick),
                         ("baseline prewarm", baselines.tick), ("adversarial maps", advmaps.tick),
                         ("map pool", mappool.tick)):
        if started := step(name, lambda: filler(repo, state)):
            print(started, file=sys.stderr, flush=True)
    # A screen entry built on an old champion that plays level with this one gets an idea porting its values
    # (loop/screenport.py, M24). It reads results.jsonl only when a result, a late screen or the champion is new.
    for line in step("screen ports", lambda: screenport.tick(repo, state)) or []:
        print(line, file=sys.stderr, flush=True)
    # A result's official-map report whose background job died (loop/official.py).
    for line in step("official-map reports", lambda: official.tick(state)) or []:
        print(line, file=sys.stderr, flush=True)
    # Saved loss replays nothing reads unpacked again, packed losslessly (loop/prune.py): several GB a day otherwise.
    if line := step("prune losses", lambda: prune.tick(state)):
        print(line, file=sys.stderr, flush=True)
    # Finished clones' replay folders, packed losslessly (loop/pack.py): about 12 GB an hour otherwise.
    if line := step("pack clone replays", lambda: pack.tick(repo, state)):
        print(line, file=sys.stderr, flush=True)
    # Not while the usage pause holds (loop/quota.py): they stay owed, and the first tick after the reset reruns them.
    for line in (step("owed coder turns", lambda: owed_turns(repo, state, time.time()))
                 if models and not quota.paused(state) else None) or []:
        print(line, file=sys.stderr, flush=True)
    report = step("docs and toolkit check", lambda: check(repo, state))
    if isinstance(report, dict) and report.get("check_error"):
        print(f"maintenance: {report['check_error']}", file=sys.stderr, flush=True)
    published = step("publishing", lambda: publishing.cycle(repo, state))
    if not isinstance(published, dict):  # it raised (or a stop marker appeared): no matches with an unknown bot
        published = {"error": "the publishing step did not finish"}
    elif published.get("error"):
        print(f"publishing: {published['error']}", file=sys.stderr, flush=True)
    if online.matches_allowed(published):
        step("online games", lambda: online.cycle(repo, state, stop_path))
    # Beside the online games, not in them: online.cycle is skipped while publishing waits (loop/ladder_maps.py).
    step("ladder map pool", lambda: ladder_maps.refresh(state))
    # Hourly while games wait (loop/online_fit.py): our result-blind online games replayed with the debug line on, for
    # fit.py, and the site's pearl variants rebuilt from them; in a background process at nice 19.
    if line := step("online fit rows", lambda: online_fit.tick(state, time.time())):
        print(line, file=sys.stderr, flush=True)
    step("public scouting", lambda: scout(repo, state))
    step("leader study", lambda: study(repo, state))
    # After the steps that save games: the index of those coders' rates and reads may count (loop/replay_library.py).
    if line := step("replay library", lambda: replay_library.refresh(state)):
        print(line, file=sys.stderr, flush=True)
    # Then our action rates beside the leaders' from it (loop/behaviour_gaps.py): at most its BUDGET replays a tick.
    if line := step("behaviour gaps", lambda: behaviour_gaps.main(state)):
        print(line, file=sys.stderr, flush=True)
    # Hourly (loop/risers.py): the top teams whose rating rose beyond chance on their wins, or a new rank 1, their
    # games before and after, and a background worker saying what changed. Quick: the check's reads and downloads run
    # in their own background process.
    if line := step("riser study", lambda: risers.tick(state, models)):
        print(line, file=sys.stderr, flush=True)
    # Daily (loop/backlog.py): a worker sweeps the waiting ideas for ones already live, covered, reversed or
    # duplicated, and the trusted side applies the edits it can check. Quick: the worker runs in the background.
    if line := step("backlog audit", lambda: backlog.tick(state, models)):
        print(line, file=sys.stderr, flush=True)
    # Before the read, so latest.md names each poster's team at this hour's ranks (loop/discord_teams.py).
    if not (state / DISCORD_PAUSED).exists():
        step("Discord teams", lambda: discord_teams.refresh(state, stop=stopped))
        step("Discord read", lambda: discord(repo, state))
        if line := step("Discord digest", lambda: digest.tick(state, models)):  # its background worker (loop/digest.py)
            print(line, file=sys.stderr, flush=True)
    if MAPIDEAS_ENABLED and (started := step("map ideas", lambda: mapideas.tick(repo, state, models=models))):
        print(started, file=sys.stderr, flush=True)
    # Hourly: gates the native judge wherever the current engine, toolkit, judge and shim have no gate yet, in the
    # background (native_gates).
    if line := step("native judge gates", lambda: native_gates(state)):
        print(line, file=sys.stderr, flush=True)
    if line := step("host crash reports", lambda: host_crashes(repo, state)):  # before the health check reads them
        print(line, file=sys.stderr, flush=True)
    for alert in step("health check", lambda: health.run(state, repo, time.time(), every=health.EVERY)) or []:
        print(f"WARNING health: {alert}", file=sys.stderr, flush=True)


# The open lock file whose flock makes this process the one that runs the upkeep tick (checking), or None while
# another process holds it. Claude drivers and native commands (`models` False) hold separate ones, as their ticks
# differ.
def upkeep_owner(state: Path, models: bool) -> int | None:
    fd = os.open(state / f"upkeep{'' if models else '-native'}.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


QUICK_EVERY = 120  # (sig, 1 Oct: "maybe every 2 minutes") how often each process running the upkeep asks for health.quick (ticking)
TICK_EVERY = 60  # the wait between one upkeep tick's end and the next's start (ticking, stand_in)


# A recent result's owed coder turn (prune.owed_turn: its post-mortem or follow-on) that never ran or ended in an
# error or waiting is rerun once, in the background (`driver.py --turn E####`, which run by hand was the only rerun),
# once health.turn_grace has passed since the result, its driver has exited and nothing holds its clone (no live
# process with its working directory there, lsof, and no live unrecorded experiment naming it), one rerun at a time.
# The attempt is recorded in runs/E####/<turn>.auto.json ({"pid", "at"}, written once, never removed), so it runs once; health.result_turns names it when the turn is still owed after
# it, the rerun by hand as before. Claude results only (a native one's turn is its model's). One the usage pause held
# (HELD_TURN, driver.turn_held) needs neither the grace nor its driver's exit. Returns lines to log.
AUTO_TURN = "{}.auto.json"
HELD_TURN = "turn-held.json"  # driver.HELD_TURN


def owed_turns(repo: Path, state: Path, now: float) -> list[str]:
    import health
    import meta
    import official
    import prune
    from home import clones_folder
    path = state / "results.jsonl"
    lines = path.read_text().splitlines()[-official.RECENT:] if path.exists() else []
    rows = meta.latest_rows(official.json_rows([line for line in lines if '"session"' in line]))
    grace, started, live = health.turn_grace(state, rows), [], None
    # One rerun at a time: after a usage limit many turns come due at once, and each would spend its only try on it.
    for marker in (state / "runs").glob("E*/*.auto.json"):
        with suppress(OSError, ValueError, AttributeError):
            if meta.alive(json.loads(marker.read_text()).get("pid")):
                return []
    # The clones a live, unrecorded experiment names (a re-evaluation or retest reuses one): never another writer.
    busy = set()
    for path in (state / "runs").glob("E*/worker.json"):
        with suppress(OSError, ValueError, AttributeError):
            worker = json.loads(path.read_text())
            if meta.alive(worker.get("driver_pid")) and not json.loads(
                    path.with_name("experiment.json").read_text()).get("finished"):
                busy.add(worker.get("clone") or path.parent.name)
    for row in rows:
        run, name = state / "runs" / row["id"], prune.owed_turn(row)
        held = (run / HELD_TURN).exists()  # the usage pause held it (driver.turn_held): no grace, whatever its driver
        if (not name or not (row.get("session") or {}).get("session_id")
                or not held and now - (row.get("finished") or now) <= grace
                or prune.turn_saved(run / f"{name}.json") or (run / AUTO_TURN.format(name)).exists()):
            continue
        clone = clones_folder(repo) / ((row.get("session") or {}).get("clone") or row["id"])
        try:
            worker = json.loads((run / "worker.json").read_text())
        except (OSError, ValueError):
            worker = {}
        if (not clone.is_dir() or clone.name in busy
                or not held and meta.alive(worker.get("driver_pid") if isinstance(worker, dict) else None)):
            continue
        if live is None:
            found = subprocess.run(["lsof", "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
            live = set(re.findall(r"^n(.+)$", found, re.M))
        if any(p == str(where) or p.startswith(f"{where}/") for p in live for where in {clone, clone.resolve()}):
            continue
        pid = meta.spawn([sys.executable, str(repo / "loop/driver.py"), "--turn", row["id"]], repo,
                         run / f"{name}.auto.log")
        (run / AUTO_TURN.format(name)).write_text(json.dumps({"pid": pid, "at": now}) + "\n")
        return [f"{row['id']}: its owed {name} never finished, so the upkeep reruns it once (pid {pid}, "
                f"{run / f'{name}.auto.log'})"]
    return started


# The last tick's {"pid", "started", "ended"} (ended None while it runs) for the Claude lock's ticks or the native
# one's (upkeep_owner's two locks, each its own file, as both may tick at once), written by timed_tick;
# health.upkeep_ticks alerts when none has run for too long, or one runs too long.
def tick_record(models: bool) -> str:
    return f"upkeep{'' if models else '-native'}-tick.json"


# Set while no upkeep tick runs in this process (timed_tick clears it for the tick), so driver.hand_off can wait for
# the tick to end before its exec, which would stop it mid-step (a publishing POST, an online request).
TICK_IDLE = threading.Event()
TICK_IDLE.set()

# The upkeep block's background parts (checking: the stand-ins' poll and the quick health checks, the utilisation
# sampler, the dev-battle poll), each registered by name as (its stop event, its thread) while it runs, so driver.
# hand_off can settle them before its exec (before_exec), which would otherwise stop each mid-step and close the pipes
# its stand-ins stop on, orphaning a stand-in's long step (a Discord read past STAND_IN_GRACE) or its children.
EXEC_PARTS: dict[str, tuple[threading.Event, threading.Thread]] = {}
EXEC_KEEP: list[int] = []  # stand-ins' pipe write ends a stopping poll detached rather than closed (before_exec)
DETACHING = threading.Event()  # set by before_exec: a stopping poll keeps its stand-ins' pipes for the exec
EXEC_WAIT = 120  # how long before_exec waits for the parts in all, a dev poll's requests or a quick check's run


# Stops every registered part and waits for them, up to `wait` seconds in all, then marks the stand-ins' pipes
# inheritable, so the exec'd image keeps them open: its stand-ins tick on for the same pid, holding the upkeep lock, and
# stop when it exits (the new image's own poll can't take the lock meanwhile, so nothing is doubled). Returns the names
# of the parts still running at the deadline, which the exec stops as before. After a failed exec the parts stay
# stopped: this process goes on without its own upkeep parts (other processes' continue).
def before_exec(wait: float = EXEC_WAIT) -> list[str]:
    DETACHING.set()
    deadline = time.monotonic() + wait
    parts = list(EXEC_PARTS.items())
    for _, (stop, _) in parts:
        stop.set()
    for _, (_, thread) in parts:
        thread.join(max(0.0, deadline - time.monotonic()))
    for fd in EXEC_KEEP:
        with suppress(OSError):
            os.set_inheritable(fd, True)
    return [name for name, (_, thread) in parts if thread.is_alive()]


# Registers a part (EXEC_PARTS) for the life of the block that runs it.
@contextmanager
def exec_part(name: str, stop: threading.Event, thread: threading.Thread):
    EXEC_PARTS[name] = (stop, thread)
    try:
        yield
    finally:
        if EXEC_PARTS.get(name, (None, None))[1] is thread:
            del EXEC_PARTS[name]


# tick, with its start and end saved in tick_record(models).
def timed_tick(repo: Path, state: Path, stop_path: Path, models: bool) -> None:
    record, path = {"pid": os.getpid(), "started": time.time(), "ended": None}, state / tick_record(models)

    def save(row: dict) -> None:
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(row) + "\n")
            temporary.replace(path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    save(record)
    TICK_IDLE.clear()
    try:
        tick(repo, state, stop_path, models)
    finally:
        TICK_IDLE.set()
        save({**record, "ended": time.time()})


# Ends the tick records (tick_record) of a tick this pid's older image ran when driver.hand_off exec'd over it: the pid
# stays alive, so health.upkeep_ticks would take the tick as running for an hour. Returns the records ended.
def end_exec_ticks(state: Path) -> list[str]:
    ended = []
    for models in (True, False):
        path = state / tick_record(models)
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(row, dict) and row.get("pid") == os.getpid() and row.get("ended") is None:
            temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps({**row, "ended": time.time(), "exec": True}) + "\n")
            temporary.replace(path)
            ended.append(path.name)
    return ended


# Stand-ins' stdout and stderr (hand_over), in the loop home: a stand-in outliving its parent never writes to that
# parent's stdio (a closed pipe mid-publish, or a Bash task held open). Bounded: over STAND_IN_LOG_BYTES at a hand-over,
# it becomes STAND_IN_LOG + ".1", replacing the older copy.
STAND_IN_LOG = "upkeep-stand-in.log"
STAND_IN_LOG_BYTES = 1_000_000
STAND_IN_GRACE = 600  # a stand-in whose parent has gone gets this long to finish its tick, then exits hard (stand_in)


# Starts a stand-in: a fresh process on the loop code now on disk (`maintenance.py upkeep`, pinned at its own start by
# home.pin_code, so all its code is of one version, MISSES 88) that ticks in place of the process owning the upkeep,
# which never ticks itself: one running older code would run the old upkeep (MISSES 76), and every tick runs at
# home.WORKER_NICE (/usr/bin/nice, same pid), below sig's work, since its analysis (replay reads, the leader study,
# behaviour gaps) ran at nice 0 in the drivers (29 Sep 16:05: this Mac at load 36 on 10 cores). `owner` is the upkeep lock's fd (upkeep_owner), handed to the stand-in, which
# holds it until it exits; the caller closes its own copy. `parent` is the read end of a pipe (os.pipe) whose write end
# the process whose upkeep block it serves keeps open and closes when that block ends (its death closes it too); the
# stand-in stops when it reaches end of file. The caller closes its copy of `parent` too; a stand-in handing over to
# its own successor passes its own on. Its stdin is /dev/null, its lines go to STAND_IN_LOG. Returns the process.
def hand_over(repo: Path, state: Path, stop_path: Path, models: bool, owner: int, parent: int) -> subprocess.Popen:
    log = state / STAND_IN_LOG
    try:
        if log.stat().st_size > STAND_IN_LOG_BYTES:
            log.replace(log.with_name(f"{STAND_IN_LOG}.1"))
    except FileNotFoundError:
        pass
    command = ["/usr/bin/nice", "-n", str(nice_increment()), sys.executable, str(Path(__file__).resolve()), "upkeep",
               "--repo", str(repo), "--state", str(state),
               "--stop", str(stop_path), "--lock-fd", str(owner), "--parent-fd", str(parent),
               *([] if models else ["--native"])]
    with log.open("a") as out:
        return subprocess.Popen(command, cwd=repo, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                pass_fds=(owner, parent), start_new_session=True)


# The stand-in's life (hand_over): ticks every TICK_EVERY while it runs the code on disk, holding the lock fd `owner`,
# until the pipe `parent` closes or a stop marker appears; when a merge makes its code older than the disk's it hands
# the lock to a successor at once and exits, so the tick never waits for a stale process's next minute. A tick still
# running STAND_IN_GRACE after the pipe closes is taken as hung: the process exits hard, freeing the lock. Neither fd
# passes to a tick's children.
def stand_in(repo: Path, state: Path, stop_path: Path, models: bool, owner: int, parent: int) -> None:
    os.set_inheritable(owner, False)
    os.set_inheritable(parent, False)
    ended, finished = threading.Event(), threading.Event()

    def watch() -> None:  # the raw fd, not sys.stdin: a buffered read held at exit aborts the interpreter
        try:
            while os.read(parent, 4096):
                pass
        except OSError:
            pass
        ended.set()
        if not finished.wait(STAND_IN_GRACE):
            print(f"maintenance: the upkeep stand-in {os.getpid()}'s tick still runs {STAND_IN_GRACE} s after its "
                  "parent went, so it exits and frees the lock", file=sys.stderr, flush=True)
            os._exit(1)

    threading.Thread(target=watch, daemon=True).start()
    try:  # it samples too, on the disk's code, replacing a minute's row an older sampler wrote (throughput.sample)
        with throughput.sampling(state, ended.is_set):
            while not ended.is_set() and not stop_path.exists() and not (state / "ASTRA_STOP").exists():
                if not current_code(everything=True):
                    successor = hand_over(repo, state, stop_path, models, owner, parent)
                    print(f"maintenance: the upkeep stand-in {os.getpid()} runs older loop code than on disk, so it "
                          f"hands the upkeep to a current one (pid {successor.pid})", file=sys.stderr, flush=True)
                    return
                try:
                    timed_tick(repo, state, stop_path, models)
                except Exception as error:
                    print(f"maintenance: {error}", file=sys.stderr, flush=True)
                ended.wait(TICK_EVERY)
    finally:
        finished.set()


@contextmanager
def ticking(repo: Path, state: Path, stop_path: Path, models: bool, halted: Callable[[], bool]):
    """Run the upkeep tick in a thread while the block runs, TICK_EVERY after each tick ends, but only while this
    process owns it (upkeep_owner, held until the block exits; the others try each minute to take over): on 28 Sep
    ~15:50 each of six `--once` drivers ran the whole tick every minute, 6-11% of a core apiece in 2-3 s bursts (a
    sampled driver: 14.0 of its 14.1 CPU seconds in 131 s in the tick, and 17 s of the tick waiting on the others' step
    locks). Beside it, in every process, a thread runs health.QUICK's checks every QUICK_EVERY (health.quick, which
    runs them once a minute across processes): a slow or hung hourly step in the owner's tick (docs, publishing,
    online games, scouting, Discord) never holds back LANES SHORT or a Mac gate stall. Both stop when the block
    exits or `halted()`. The owner never ticks in its own process: it hands the lock to a stand-in on the disk's code
    at home.WORKER_NICE (hand_over), and takes a free lock only to do so. On 28 Sep an owner started before a merge
    ran the old upkeep for hours (it dropped online.json's layouts key, re-marked pruned rows, lost a team rename;
    MISSES 76); on 29 Sep, after four merges in an hour, every driver ran older code, none ticked, and v0066, champion
    at 13:45, went unpublished for over 15 minutes (MISSES 91); and the drivers' ticks ran their analysis at nice 0
    beside sig's work."""
    import health
    stop = threading.Event()

    def poll() -> None:
        owner, stand_ins = None, []  # each stand-in started, with its pipe's write end (hand_over)
        try:
            while not stop.is_set() and not halted():
                try:
                    if owner is None and (owner := upkeep_owner(state, models)) is not None:
                        for process, pipe in stand_ins:  # the lock was free: every stand-in they started has exited
                            os.close(pipe)
                            process.poll()
                        stand_ins.clear()
                    if owner is not None:  # a stand-in, niced and on the disk's code, ticks for it
                        read, write = os.pipe()
                        try:
                            stand_ins.append((hand_over(repo, state, stop_path, models, owner, read), write))
                            print(f"maintenance: this process hands the upkeep to a stand-in (pid "
                                  f"{stand_ins[-1][0].pid})", file=sys.stderr, flush=True)
                        except BaseException:
                            os.close(write)
                            raise
                        finally:
                            os.close(read)
                            os.close(owner)
                            owner = None
                except Exception as error:
                    print(f"maintenance: {error}", file=sys.stderr, flush=True)
                stop.wait(TICK_EVERY)
        finally:
            if owner is not None:
                os.close(owner)
            for _, pipe in stand_ins:  # each ends after the tick it is in, if any (or STAND_IN_GRACE)
                if DETACHING.is_set():  # driver.hand_off's exec: the new image keeps it open (before_exec)
                    EXEC_KEEP.append(pipe)
                else:
                    os.close(pipe)

    # A process on older loop code runs them in a fresh one on the disk's (health.fresh, MISSES 100) when they are due
    # (health.json's quick_at, which health.quick checks again under its lock), so each such process starts one child a
    # minute at most, and the checks themselves still run once a minute across processes.
    def quick() -> None:
        every = QUICK_EVERY - 10
        while not stop.is_set() and not halted():
            try:
                due = time.time() - health.read_json(state / "health.json").get("quick_at", 0) >= every
                found = health.fresh(state, repo, every) if due and not current_code(everything=True) else None
                for alert in health.quick(state, repo, time.time(), every=every) if found is None else found:
                    print(f"WARNING health: {alert}", file=sys.stderr, flush=True)
            except Exception as error:
                print(f"maintenance: quick health checks failed: {type(error).__name__}: {error}", file=sys.stderr,
                      flush=True)
            stop.wait(QUICK_EVERY)

    ticker = threading.Thread(target=poll, name="hourly-maintenance", daemon=True)
    checker = threading.Thread(target=quick, name="quick-health", daemon=True)
    ticker.start()
    checker.start()
    try:
        with exec_part("upkeep-poll", stop, ticker), exec_part("quick-health", stop, checker):
            yield
    finally:
        stop.set()
        ticker.join()
        checker.join(timeout=10)  # a check waiting on the health lock (the hourly run holds it) needn't hold the exit


@contextmanager
def checking(repo: Path, state: Path, stop_path: Path, models: bool = True):
    """Run the upkeep (ticking) during a long driver/native command, and beside it the utilisation sampler
    (loop/throughput.py), whose minute rows mustn't wait for a slow tick, and the dev-battle poll (online.polling),
    which follows each finished dev battle within online.DEV_POLL_EVERY; stop them all when it exits."""
    import online
    halted = lambda: stop_path.exists() or (state / "ASTRA_STOP").exists()
    with ticking(repo, state, stop_path, models, halted), throughput.sampling(state, halted), \
            online.polling(state, stop_path, halted):
        yield


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    upkeep = commands.add_parser("upkeep", help="tick as a stand-in for a process on older loop code (hand_over)")
    upkeep.add_argument("--repo", type=Path, required=True)
    upkeep.add_argument("--state", type=Path, required=True)
    upkeep.add_argument("--stop", type=Path, required=True)
    upkeep.add_argument("--lock-fd", type=int, required=True)
    upkeep.add_argument("--parent-fd", type=int, required=True, help="a pipe's read end; the stand-in stops at its end")
    upkeep.add_argument("--native", action="store_true", help="a native session's tick: no model workers")
    args = parser.parse_args()
    stand_in(args.repo, args.state, args.stop, not args.native, args.lock_fd, args.parent_fd)


if __name__ == "__main__":
    import maintenance  # the module the rest of the loop imports, not a second copy of it as __main__
    maintenance.main()

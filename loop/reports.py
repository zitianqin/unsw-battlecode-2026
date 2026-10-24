"""Read-only report workers: a confined headless model reads what the loop prepared and writes one report, which the
trusted side saves and hands to the orchestrator in host-alerts.log. The orchestrator still checks the report's claims
and decides; only the report-writing is automatic. Two kinds use it: the Discord digest (loop/digest.py) and the
REFLECT list (loop/reflect.py).

Built like the replay audit (loop/audit.py): a work folder <loop home>/<kind>/<time_ns>/ holds the inputs, its prompt
and output live in meta.protected_io (worker-io/<kind>/<name>/), out of its reach, and <loop home>/<kind>.json is the
record. The worker gets Read, Grep, Glob and Write only (no shell, web or network) in acceptEdits mode, so it writes
only inside its folder; report.md is its one output, read back with meta.worker_file. A failed launch is retried
after RETRY_S; at meta.MAX_LAUNCHES the folder is abandoned and the kind's `gave_up` line tells the orchestrator what
to do by hand. A launch a model provider's quota error stopped (meta.provider_limited) is retried once the limit
resets when the error names the time (meta.when_due), else the same way, and never counted, since it says nothing
about the task (meta.after_failure). A worker still running past its kind's `timeout` is stopped (only a process
whose working folder is its work folder: never a reused pid) and counts as a failed launch. Nothing here waits for
a worker: the upkeep tick calls `tick` each minute. Under native orchestration (`launch` False) the tick collects a
worker a Claude driver started but launches none, as native reviews and audits hand their task to the active model
instead.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import meta
import online
import quota

TOOLS = "Read,Grep,Glob,Write"
RETRY_S = 3600  # after a failed launch (one a quota limit stopped: at its reset, meta.when_due)
REPORT = "report.md"


# One kind of report worker. `name` is its folder, record and worker-io name. `reads(state)`: folders besides its own
# it may read. `done(state, record, report)` saves the report where the orchestrator reads it and returns (the alert
# line, record fields to keep); `gave_up(state, record, why)` returns the same when the folder is abandoned (`why`
# says so: after two failed launches, or under native orchestration), its line the orchestrator's by-hand task.
# `keep`: record fields that outlive one folder. `timeout`: seconds a worker may run before it is stopped.
@dataclass(frozen=True)
class Kind:
    name: str
    effort: str
    reads: Callable[[Path], tuple[Path, ...]]
    done: Callable[[Path, dict, str], tuple[str, dict]]
    gave_up: Callable[[Path, dict, str], tuple[str, dict]]
    keep: tuple[str, ...]
    timeout: int


def read(state: Path, kind: Kind) -> dict:
    return online.read(state / f"{kind.name}.json", {})


def save(state: Path, kind: Kind, record: dict) -> None:
    online.save(state / f"{kind.name}.json", record)


def kept(kind: Kind, record: dict) -> dict:
    return {k: record[k] for k in (*kind.keep, "abandoned") if k in record}


# Opens a work folder holding `files` ({name: text}) with `prompt` ("{work}" replaced by the folder) as the worker's
# stdin, and records it as pending with `extra`. The caller has checked none is pending. Returns the folder.
def prepare(state: Path, kind: Kind, prompt: str, files: dict[str, str], extra: dict) -> Path:
    with meta.locked(state / kind.name):
        work = state / kind.name / str(time.time_ns())
        work.mkdir(parents=True)
        (meta.protected_io(state, kind.name, work) / "PROMPT.md").write_text(prompt.replace("{work}", str(work)))
        for name, text in files.items():
            (work / name).write_text(text)
        save(state, kind, {**kept(kind, read(state, kind)), "pending": str(work), "prepared_at": time.time(), **extra})
        return work


# The upkeep's step: collects the pending worker once it has exited, stops one past its timeout, or launches it
# (first, or again after RETRY_S or a quota limit's reset, meta.when_due; never with `launch` False). With none
# pending, `start` may open one (with prepare), which is then launched at once. One caller at a time (meta.only_one);
# another returns None. Returns a line to log, or None.
# While the 5-hour usage level holds the kind (quota.HELD), it only collects or stops a worker already launched: none
# is prepared or launched until the window resets.
def tick(state: Path, kind: Kind, start: Callable[[], str | None] | None = None, launch: bool = True) -> str | None:
    with meta.only_one(state / kind.name) as mine:
        if not mine:
            return None
        held = bool(quota.held(state, kind.name))
        if (line := step(state, kind, launch, held)) or start is None or held or read(state, kind).get("pending"):
            return line
        line = start()
        return step(state, kind, launch) if read(state, kind).get("pending") else line


def step(state: Path, kind: Kind, launch: bool = True, held: bool = False) -> str | None:
    record = read(state, kind)
    if not record.get("pending"):
        return None
    work, pid = Path(record["pending"]), record.get("pid")
    if pid:
        reap(pid)
        if (alive := running(pid, work)) is not False:
            if not alive or time.time() - record.get("launched_at", 0) < kind.timeout:
                return None  # running, or alive without proof it is ours: never stopped, only waited for
            stop(pid)
            return failed(state, kind, work, f"the worker ran past its {kind.timeout // 60} min cap and was stopped")
        return collect(state, kind, work)
    if not launch:  # native orchestration: a folder a Claude launch left goes to the orchestrator by hand
        return hand_over(state, kind, work, "under native orchestration, which runs no Claude worker")
    if held or time.time() < meta.when_due(record, "failed_at", RETRY_S):
        return None
    return start_worker(state, kind, work)


# Reaps `pid` if this process started it and it has exited, so a finished worker never lingers as a zombie that
# looks alive.
def reap(pid: int) -> None:
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass


# Whether `pid` is still the worker launched in `work`: True when alive and working in that folder (meta.in_folder
# cds there before it execs the session), False when gone or working elsewhere (a reused pid, never stopped), None
# when alive but its folder can't be read (lsof failed), which is waited for and never stopped. A pid another user
# owns can't be our worker (a reused pid whose folder lsof couldn't read would otherwise hold the slot).
def running(pid: int, work: Path) -> bool | None:
    if not meta.alive(pid):
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return False
    except OSError:
        pass
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        try:
            found = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True,
                                   text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        cwd = next((line[1:] for line in found.stdout.splitlines() if line.startswith("n")), "")
        if not cwd:
            return None if meta.alive(pid) else False
    return Path(cwd).resolve() == work.resolve()


# Stops the worker's process group (its session and every command it started): SIGTERM first, so meta.private_tmp
# deletes its temporary folder, then SIGKILL after 5 s.
def stop(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        for _ in range(50):
            reap(pid)
            if not meta.alive(pid):
                return
            time.sleep(0.1)


def start_worker(state: Path, kind: Kind, work: Path) -> str:
    try:
        folder = meta.protected_io(state, kind.name, work)
        meta.unlock(work)
        settings = meta.worker_settings(state, kind.name, work)
        command = meta.in_folder(work, meta.private_tmp(meta.worker_command(
            work, settings, tuple(p for p in kind.reads(state) if p.is_dir()), TOOLS, kind.effort, usage=kind.name)))
        meta.brief(state, work, folder)
        prompt, out, err = meta.worker_io(folder)
        try:
            proc = subprocess.Popen(command, cwd=folder, env=meta.worker_env(), stdin=prompt, stdout=out, stderr=err,
                                    start_new_session=True)
        finally:
            for fd in (prompt, out, err):
                os.close(fd)
    except Exception as error:
        return failed(state, kind, work, f"could not launch: {type(error).__name__}: {error}")
    with meta.locked(state / kind.name):
        save(state, kind, {**read(state, kind), "pid": proc.pid, "launched_at": time.time()})
    return f"{kind.name} worker launched in the background: {work}"


# Saves the report of the exited worker in `work` through the kind's `done` and alerts its line, or records the
# failed launch. The report is read with meta.worker_file: a link or anything but a plain file is refused.
def collect(state: Path, kind: Kind, work: Path) -> str:
    try:
        meta.unlock(work)  # a folder the worker left at mode 000 would make a finished report look missing
        if error := meta.session_error(meta.protected_io(state, kind.name, work)):
            raise ValueError(f"the session failed: {error}")
        report = meta.worker_file(work / REPORT)
        if not report.strip():
            raise ValueError(f"{REPORT} is empty")
    except (OSError, ValueError) as error:
        return failed(state, kind, work, f"no report: {type(error).__name__}: {error}")
    with meta.locked(state / kind.name):
        record = read(state, kind)
        if record.get("pending") != str(work):
            return f"{kind.name} {work} is no longer pending; its report is not used"
        line, fields = kind.done(state, record, report)
        save(state, kind, {**kept(kind, record), **fields, "finished_at": time.time(), "last": str(work)})
    meta.alert(state, line)
    return line


# Records a failed launch of the pending `work`: retried after RETRY_S, abandoned at meta.MAX_LAUNCHES counted
# failures with the kind's `gave_up` line (a provider's quota error is retried once the limit resets but not counted,
# meta.after_failure, meta.when_due). Writes the line to host-alerts.log and returns it.
def failed(state: Path, kind: Kind, work: Path, reason: str) -> str:
    with meta.locked(state / kind.name):
        record = read(state, kind)
        if record.get("pending") != str(work):
            return f"{kind.name} {work} is no longer pending; nothing recorded ({reason})"
        after, abandoned = meta.after_failure(record, work, reason, kind.keep)
        if abandoned:
            line, fields = kind.gave_up(state, record, f"after {meta.MAX_LAUNCHES} failed launches")
            save(state, kind, {**after, **fields})
        else:
            line = f"{kind.name} worker failed; retrying {meta.retry_when(after)} ({work}): {reason}"
            save(state, kind, {**after, "failed_at": time.time()})
    meta.alert(state, line)
    return line


# Abandons the pending `work` without a launch (`why`: under native orchestration) and alerts the kind's `gave_up`
# line, the orchestrator's by-hand task, so later reads or REFLECTs proceed. Returns the line.
def hand_over(state: Path, kind: Kind, work: Path, why: str) -> str:
    with meta.locked(state / kind.name):
        record = read(state, kind)
        if record.get("pending") != str(work):
            return f"{kind.name} {work} is no longer pending; nothing handed over"
        line, fields = kind.gave_up(state, record, why)
        entry = {"work": str(work), "at": time.time(), "failures": [*record.get("failures", []), why]}
        save(state, kind, {**kept(kind, record), "abandoned": [*record.get("abandoned", []), entry][-10:], **fields})
    meta.alert(state, line)
    return line


# Writes `text` to `path` in a folder no worker can write, through a temporary file, so the orchestrator never reads
# half a report.
def publish(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)

"""Records one team's side of a game, and checks another bot against that record without playing the game.

    <toolkit python> record.py MAP BOT_A BOT_B -o REPLAY [--seed N] --team A|B --recording FILE

plays the game exactly as `unswbc run MAP BOT_A BOT_B -o REPLAY --seed N` does (same output, same replay) and also
writes FILE: every turn of TEAM's dragons in game order, as the engine asked it (the round block) and how the bot
answered (a digest of the reply the engine read), plus each dragon's init block. Nothing is written when any bot
of either team failed a turn (a timeout, crash or restart depends on timing, not only on the input).

`check(FILE, BOT)` then feeds BOT's processes those same inputs, one dragon process per recorded dragon, in game
order, and stops at the first reply that differs. When every reply matches, the game BOT would play in that seat is
the recorded game, turn for turn: the engine is deterministic given the map, the seed and the replies, the other
team's bots (deterministic, and our bots are) get the same inputs, so they answer the same, and by induction every
turn repeats. The referee then reuses the recorded game's result instead of playing it (run.Prediction).

This drives the toolkit's own bot classes (unswbc.run.execute, unswbc.bot.Bot and Pool, unswbc.turn's framing) and
relies on its engine, so it only runs on a toolkit whose files digest to one in KNOWN_INTERNALS, read and checked by
hand; any other toolkit makes `supported()` say why and every entry point refuse, and a recording made on another
toolkit won't load. Only run it with the toolkit's own Python (3.14: zstd)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

FORMAT = 1
# The toolkit files this module depends on, and each checked digest (internals()) with the first toolkit that has it.
# A new toolkit needs its own reading of them (does execute still ask Bot.ask for every turn and hand the reply to
# the engine unchanged, and is the engine still deterministic given the map, seed and replies?) before its digest is
# added. 1.2.0 (28 Sep; 1.2.1 and 1.2.2 digest the same): run.py, bot.py, turn.py, engine.py and errors.py are
# byte-identical to 1.1.0's; the wasm's imports, exports, types and data are too, and its code differs in one
# function's branch conditions and local numbering only, with no new call, so it reaches the clock and bots as before.
INTERNAL_FILES = ("run.py", "bot.py", "turn.py", "engine.py", "unswbc_engine.wasm")
KNOWN_INTERNALS = {"c0e2c3d342fde73f": "1.1.0", "26cd5a6956ef86aa": "1.2.0"}
LEVEL = 9  # zstd level: a 500-round game's side compresses 14.6 MB to 1.7 MB in 0.13 s


class RecordError(Exception):
    """Recording or checking is impossible here: the toolkit or the file isn't what this module was written for."""


# The digest of the toolkit sources this module depends on, or None without a toolkit; OSError when one of them is
# missing or unreadable (supported() turns that into its reason).
def internals() -> str | None:
    try:
        import unswbc
    except ImportError:
        return None
    root = Path(unswbc.__file__).parent
    return hashlib.sha256(b"".join(hashlib.sha256((root / name).read_bytes()).digest()
                                   for name in INTERNAL_FILES)).hexdigest()[:16]


# None when recording and checking work here, else why not.
def supported() -> str | None:
    try:
        from compression import zstd  # noqa: F401
    except ImportError:
        return f"Python {sys.version.split()[0]} has no compression.zstd"
    try:
        found = internals()
    except OSError as error:
        return f"unswbc's {', '.join(INTERNAL_FILES)} can't be read ({error}); re-read the toolkit and KNOWN_INTERNALS"
    if found is None:
        return "no unswbc toolkit"
    if found not in KNOWN_INTERNALS:
        return (f"unswbc's {', '.join(INTERNAL_FILES)} changed (digest {found}); re-read them and add it to "
                "KNOWN_INTERNALS")
    return None


def require() -> None:
    if reason := supported():
        raise RecordError(reason)


# How a cache row names the recording of its game (sprt.BaselineCache).
def file_digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()[:16]


def reply_digest(reply: bytes) -> str:
    return hashlib.blake2b(reply, digest_size=8).hexdigest()


def round_of(block: str) -> str:
    return block.split("\n", 1)[0].strip().lower()


def team_of(init: bytes) -> str:
    return next((line.split()[1] for line in init.decode().splitlines() if line.startswith("TEAM")), "?")


# One team's side of one game: each dragon's init block, and every turn as (dragon, round block, reply digest) in the
# order the engine asked. Blocks are kept as latin-1 text, which maps every byte to one character and back.
@dataclass
class Recording:
    team: str
    inits: dict[int, str]
    turns: list[tuple[int, str, str]]

    def save(self, path: Path) -> None:
        from compression import zstd
        data = {"format": FORMAT, "internals": internals(), "team": self.team,
                "inits": {str(k): v for k, v in self.inits.items()}, "turns": self.turns}
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(zstd.compress(json.dumps(data, separators=(",", ":")).encode(), level=LEVEL))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    # `digest` (file_digest of the bytes, as the cache row keeps it): refuse a file that has since been replaced.
    @staticmethod
    def load(path: Path, digest: str | None = None) -> Recording:
        from compression import zstd
        blob = path.read_bytes()
        if digest is not None and file_digest(blob) != digest:
            raise RecordError(f"{path} changed since its game was cached (digest {file_digest(blob)}, not {digest})")
        data = json.loads(zstd.decompress(blob))
        if data.get("format") != FORMAT:
            raise RecordError(f"{path}: recording format {data.get('format')}, expected {FORMAT}")
        if data.get("internals") != internals():
            raise RecordError(f"{path}: recorded on toolkit {data.get('internals')}, this one is {internals()}")
        return Recording(data["team"], {int(k): v for k, v in data["inits"].items()},
                         [(int(d), block, digest) for d, block, digest in data["turns"]])


# ---- recording a game ----

# A Bot class for unswbc.run.execute that logs `team`'s turns into `recording` and every bot failure into `errors`.
def recording_bot(team: str, recording: Recording, errors: list[str]) -> type:
    from unswbc.bot import Bot

    class RecordingBot(Bot):
        def __init__(self, pool, init: bytes = b"", name: str = "0") -> None:
            super().__init__(pool, init, name)
            self.team = team_of(init)
            if self.team == team:
                recording.inits[int(name)] = init.decode("latin-1")

        def ask(self, block: bytes) -> bytes:
            reply = super().ask(block)
            if self.error is not None:
                errors.append(f"{round_of(block.decode('latin-1'))}: bot {self._name} (team {self.team}) {self.error}")
            if self.team == team:
                recording.turns.append((int(self._name), block.decode("latin-1"), reply_digest(reply)))
            return reply

    return RecordingBot


# Plays the game as `unswbc run` does and saves the recording when no bot failed. Returns the exit status.
def record_game(game_map: str, bots: list[str], replay: str, seed: int | None, team: str, out: Path) -> int:
    require()
    import unswbc.run as toolkit
    from unswbc.engine import DEBUG_ALL
    from unswbc.errors import UserError, fail

    recording, errors = Recording(team, {}, []), []
    original = toolkit.Bot
    toolkit.Bot = recording_bot(team, recording, errors)
    try:
        code = toolkit.execute(game_map, bots, False, replay, False, False, DEBUG_ALL, seed)
    except (UserError, OSError) as error:
        return fail(str(error))
    finally:
        toolkit.Bot = original
    if code == 0 and not errors and recording.turns:
        try:
            recording.save(out)
        except OSError as error:  # e.g. a full disk: the game still counts, it just can't be predicted from
            print(f"no recording: cannot save {out}: {error}")
    elif code == 0:
        print(f"no recording: {'; '.join(errors[:3]) if errors else f'team {team} took no turn'}")
    return code


# ---- checking a bot against a recording ----

# How a check went: `identical` when the bot answered every recorded turn as recorded. `checked` counts the turns
# fed before it stopped, of `turns`; `cpu` is the check's own CPU seconds plus its bot processes'.
@dataclass
class Check:
    identical: bool
    turns: int
    checked: int
    cpu: float
    mismatch: str | None


# Kills a bot process, closes its pipes and returns the CPU seconds it used.
def reap(process) -> float:
    process.kill()
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()
    try:
        _, _, usage = os.wait4(process.pid, 0)
    except ChildProcessError:
        return 0.0
    finally:
        process.returncode = -9
    return usage.ru_utime + usage.ru_stime


# The toolkit's Pool and Bot, reaping each process themselves so its CPU time can be counted.
def counting_classes() -> tuple[type, type]:
    from unswbc.bot import Bot, Pool

    class CountingPool(Pool):
        def __init__(self, *args, **kwargs) -> None:
            self.cpu = 0.0
            super().__init__(*args, **kwargs)

        def close(self) -> None:
            self._size = 0
            for spare in self._spares:
                self.cpu += reap(spare[0])
            self._spares.clear()

    class CountingBot(Bot):
        def stop(self) -> None:
            if self._process is None:
                return
            self._pool.cpu += reap(self._process)
            self._process = None

        _kill = stop

    return CountingPool, CountingBot


# Feeds `bot` (a bot folder or file, as `unswbc run` takes it, already built) the recorded turns in game order, a
# process per recorded dragon started at its first turn and stopped after its last, as in the game. Stops at the first
# reply that differs or any failed turn, or when `stopped()` says so. With `digest`, a recording whose bytes no longer
# match it (replaced by another game since) isn't checked: the result is not identical.
def check(path: Path, bot: Path, stopped: Callable[[], bool] = lambda: False, digest: str | None = None) -> Check:
    require()
    from unswbc.run import _resolve

    start = time.thread_time()
    try:
        recording = Recording.load(path, digest)
    except (OSError, RecordError) as error:
        return Check(False, 0, 0, time.thread_time() - start, f"no recording to check against: {error}")
    argv, cwd, _ = _resolve(str(bot))
    pool_class, bot_class = counting_classes()
    pool = pool_class(argv, cwd=str(cwd), size=1)
    last = {dragon: i for i, (dragon, _, _) in enumerate(recording.turns)}
    live: dict = {}
    mismatch, checked = None, 0
    try:
        for i, (dragon, block, digest) in enumerate(recording.turns):
            if stopped():
                mismatch = "stopped"
                break
            if dragon not in live:
                live[dragon] = bot_class(pool, recording.inits[dragon].encode("latin-1"), str(dragon))
            reply = live[dragon].ask(block.encode("latin-1"))
            checked += 1
            if live[dragon].error is not None or reply_digest(reply) != digest:
                mismatch = f"{round_of(block)}, dragon {dragon}: {live[dragon].error or 'a different reply'}"
                break
            if last[dragon] == i:
                live.pop(dragon).stop()
    finally:
        for process in live.values():
            process.stop()
        pool.close()
    return Check(mismatch is None, len(recording.turns), checked, time.thread_time() - start + pool.cpu, mismatch)


# True when two recordings hold the same turns: the same game as that team saw and played it.
def same(a: Recording, b: Recording) -> bool:
    return (a.team, a.inits, a.turns) == (b.team, b.inits, b.turns)


def main() -> int:
    parser = argparse.ArgumentParser(description="Play a game like `unswbc run` and record one team's turns.")
    parser.add_argument("map")
    parser.add_argument("bots", nargs=2)
    parser.add_argument("-o", "--output", required=True, help="the replay")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--team", choices=("A", "B"), required=True)
    parser.add_argument("--recording", type=Path, required=True)
    args = parser.parse_args()
    try:
        return record_game(args.map, args.bots, args.output, args.seed, args.team, args.recording)
    except RecordError as error:
        print(f"error: cannot record: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

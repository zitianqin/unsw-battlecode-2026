"""Why our bot made the moves it made in a recorded game (`tools/watch.py why`): the game is played again on this
Mac with our exact submission, its debug line switched on, against a stand-in that sends the recorded opponent's
replies turn by turn, and our debug lines are printed for the rounds asked for.

    uv run python tools/watch.py why REPLAY --rounds 150-174 [--dragon 13] [--commit SHA] [--side A] [--plain]

Online replays carry no debug output (the debug line is off in real games), so this is how to get from what a
dragon did to the decision that made it: the rule that took the turn, the move it beat, the gap and the deciding
term, and each of the four moves' score with its terms (named by the bot's own scoring.hpp TERM_NAME), space, room
and closed/farm/open/cutOff/safe flags. It waits its own turn (wait_turn: one replay a machine, under the load gate),
so `tools/watch.py why ...` needs no wrapper: it builds the bot once (twice when the build with the terms fails and it falls back to one without) and plays the game
in the judge's sandbox on the first candidate map file that the engine alone reproduces (every dragon replaying its
recorded moves, a few seconds a file); a game no map file reproduces, such as one on a site variant's pearl timings, is
refused as not reproducible before the bot plays.

What makes the replay the real game, each checked every run:
- The pearls: the replay's seed (Replay.seed) and the map's real pearl timings (the site's replays give every TILE
  line gaps 0 0, so the timings come from a map file in ~/.bc-loop/ladder-map-files, ~/.bc-loop/ladder-map-variants
  (the site's pearl variants, rebuilt from replays: loop/online_fit.py) or maps/ whose layout is the replay's, the
  teams' labels apart, grafted onto the replay's own map text). Each candidate is first played with
  every dragon a stand-in, the engine alone: one that doesn't give the recorded game is dropped, and when none does
  the game is refused as not reproducible (the site played pearl timings no map file here has), before the bot is
  built.
- Our bot: the commit behind the submission (`--commit`, else publishing.json or uploads.jsonl by the replay's bot
  name), built by the judge's clang. The debug copy sets P.debugIndicator and adds the per-move terms to debugline.hpp;
  every file that change touches is embedded into sonarkey.hpp's source hash from an untouched copy, so the sonar
  key, and every sonar value we send, stays the submission's.
- The opponent: each of its dragons replies with the recorded action and sonars (a sonar that came out of the tail
  was sent into the neck, sonar.md). Split children get the ids the engine gives them in spawn order, which is the
  recording's while the games agree.

The replayed game's events are compared with the recording's (our logs, drawings and CPU counts apart; a timeout
the site recorded matches the empty reply the stand-in sends for it): the first
difference is printed with the round it happened in, and no debug line from that round on is shown, since from
there the game is no longer the real one. `--plain` plays the exact build (no debug line) to check only that.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))

import online  # noqa: E402
from home import loop_home  # noqa: E402
import replays  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LOOP_HOME = loop_home()
MAP_DIRS = (LOOP_HOME / "ladder-map-files", LOOP_HOME / "ladder-map-variants", REPO / "maps" / "ladder",
            REPO / "maps" / "sprint", REPO / "maps" / "retired")
LETTER = {"north": "N", "east": "E", "south": "S", "west": "W"}
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
# Events the comparison skips: bot output that only the debug build or a local run makes differently, and the
# countdowns, which the site's replays leave out (the spawns they lead to are compared, as tileChange).
UNCOMPARED = ("dragonLog", "dragonIndicator", "debugDraw", "engineLog", "pearlCountdown")
# debugline.hpp's per-move fields this tool adds (`mN`, `mE`, ...): score, then each Term as `name:value`, named by
# the bot's own TERM_NAME so a term added to scoring.hpp shifts no name, then space, room and the flags.
FLAGS = ("closed", "farm", "open", "cutOff", "safe")
TAIL_FIELDS = ("fill", "room", "flags")  # fill: MoveEval::space, the tiles its territory fill counted
MOVE_PATCH = """    // tools/why.py: every move's score, named terms, space, room and closed/farm/open/cutOff/safe flags.
    for (MoveEval const& m : moves) {
        line << " m" << DIR_CHAR[m.dir] << " " << m.score;
        for (int t = 0; t < TERM_COUNT; ++t)
            line << "," << TERM_NAME[t] << ":" << m.terms[t];
        line << "," << m.space << "," << m.room << "," << int(m.closed) << int(m.farm) << int(m.open)
             << int(m.cutOff) << int(m.safe);
    }
    return line.str();
}
"""


@dataclass
class Reply:
    """One recorded turn: what the dragon's reply was, as the stand-in sends it back."""
    team: str
    action: str  # "MOVE NNE", "SPLIT 2" or "" (no usable action, a timeout)
    sonars: list[str] = field(default_factory=list)  # "SONAR N 123", in the order they were cast

    def text(self) -> bytes:
        return "".join(f"{line}\n" for line in [self.action, *self.sonars, "PROTOCOL 3"] if line).encode()


# Every turn of a replay by (round, dragon id), with the sonars each dragon sent as it sent them: a ray that left from
# anywhere but the sender's head came out of its tail, so it was sent towards its neck, against its facing.
def recorded_replies(replay: Any) -> dict[tuple[int, int], Reply]:
    info = replays.parse_map(replay.map)
    teams = {i: team for i, (team, _) in enumerate(info.dragons)}
    where: dict[int, tuple[tuple[int, int], str]] = {}  # dragon id -> (head, facing letter)
    turns: dict[tuple[int, int], Reply] = {}
    round_, mover = -1, None
    for event in replay.events:
        kind = event.which()
        if kind == "roundStart":
            round_, mover = event.roundStart.round, None
        elif kind == "turnStart":
            mover = event.turnStart.id
        elif kind == "dragonUpdate":
            u = event.dragonUpdate
            where[u.id] = ((u.head.x, u.head.y), LETTER[str(u.facing)])
        elif kind == "dragonSplit":
            s = event.dragonSplit
            teams[s.childId] = "A" if str(s.team) == "a" else "B"
            head = (s.parentBody[0].x, s.parentBody[0].y)
            where[s.parentId] = (head, where.get(s.parentId, (head, "N"))[1])
            where[s.childId] = ((s.childBody[0].x, s.childBody[0].y), LETTER[str(s.childFacing)])
        elif kind == "dragonAction":
            a = event.dragonAction
            action = ""
            if a._has("action") and not a.tle:
                which = a.action.which()
                if which == "move":
                    action = "MOVE " + "".join(LETTER[str(d)] for d in a.action.move)
                elif which == "split":
                    action = f"SPLIT {a.action.split}"
            turns[round_, a.id] = Reply(teams.get(a.id, "?"), action)
        elif kind == "sonarPing" and mover is not None:
            p = event.sonarPing
            sent = LETTER[str(p.direction)]
            head, facing = where.get(p.senderId, (None, "N"))
            if head is not None and (p.origin.x, p.origin.y) != head:
                sent = OPPOSITE[facing]
            value = p.value64 if p.value64 or p.value == 0 else p.value
            if (reply := turns.get((round_, p.senderId))) is not None:
                reply.sonars.append(f"SONAR {sent} {value}")
    return turns


# The events that make a game, as comparable values: everything but UNCOMPARED, and no CPU counts. A timeout
# (the site records `{"id": N, "tle": true}`) reads as what the local engine records for the stand-in's empty reply
# to it, a suicide without a timeout (game 394361's round 171).
def comparable(replay: Any) -> list[tuple[int, str, str]]:
    out, round_ = [], -1
    for event in replay.events:
        kind = event.which()
        if kind in UNCOMPARED:
            continue
        if kind == "roundStart":
            round_ = event.roundStart.round
        body = getattr(event, kind).to_dict()
        body.pop("instructions", None)
        if kind == "dragonAction" and body.get("tle"):
            body.update(action={"suicide": None}, tle=False)
        out.append((round_, kind, json.dumps(body, sort_keys=True)))
    return out


# The first event where two games differ, as (round, recorded event, replayed event); None when they agree up to
# where the shorter one ends.
def first_difference(recorded: list[tuple[int, str, str]], replayed: list[tuple[int, str, str]]
                     ) -> tuple[int, str, str] | None:
    for a, b in zip(recorded, replayed):
        if a != b:
            return a[0], f"{a[1]} {a[2]}", f"{b[1]} {b[2]}"
    n = min(len(recorded), len(replayed))
    if len(recorded) > n:
        r, kind, body = recorded[n]
        return r, f"{kind} {body}", "the replayed game ended"
    if len(replayed) > n:
        r, kind, body = replayed[n]
        return r, "the recorded game ended", f"{kind} {body}"
    return None


# The replay's map text with its TILE lines (and TILE_COUNT) replaced by those of `timed`, a map file with pearl timings.
def with_timings(map_text: str, timed: str) -> str:
    def is_tile(line: str) -> bool:
        return line.split()[:1] in (["TILE"], ["TILE_COUNT"])
    lines = map_text.splitlines()
    # Where the TILE lines were, else before the edges (a replay whose map text lists no tiles at all).
    at = next((i for i, line in enumerate(lines) if is_tile(line)),
              next((i for i, line in enumerate(lines) if line.split()[:1] == ["EDGE_COUNT"]), len(lines)))
    rest = [line for line in lines if not is_tile(line)]
    return "\n".join(rest[:at] + [line for line in timed.splitlines() if is_tile(line)] + rest[at:]) + "\n"


# The maps to try, as (name, map text): the replay's own text when it has pearl timings, then the replay's text with the
# timings of each distinct map file in `folders` whose layout (kelp, portals, dragons' bodies) is the replay's.
def candidate_maps(map_text: str, folders: tuple[Path, ...] = MAP_DIRS) -> list[tuple[str, str]]:
    from ladder_maps import content_hash, gapless, teamless_layout
    want = teamless_layout(map_text)
    found = [] if gapless(map_text) else [("the replay's own map", map_text)]
    seen = set()
    for folder in folders:
        for path in sorted(folder.glob("*.map")) if folder.is_dir() else []:
            text = path.read_text(errors="replace")
            if teamless_layout(text) == want and not gapless(text) and content_hash(text) not in seen:
                seen.add(content_hash(text))
                found.append((path.name, with_timings(map_text, text)))
    return found


# The commit a submission id was built from: publishing.json's verified upload, else uploads.jsonl by archive digest
# (online.upload_commit: its rows record the submission they replaced, not their own id).
def submission_commit(submission: int, state: Path = LOOP_HOME) -> str | None:
    try:
        verified = json.loads((state / "publishing.json").read_text()).get("verified") or {}
    except (OSError, ValueError):
        verified = {}
    if verified.get("submission_id") == submission:
        return verified.get("commit")
    try:
        rows = [json.loads(line) for line in (state / "uploads.jsonl").read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return None
    return online.upload_commit(rows, submission)


# Our side and commit, each as given or else from the side whose bot name is a submission of ours.
def our_side(replay: Any, side: str | None, commit: str | None) -> tuple[str, str]:
    names = {"A": replay.botA, "B": replay.botB}
    if side and commit:
        return side, commit
    for s in [side] if side else ["A", "B"]:
        if names[s].isdigit() and (found := submission_commit(int(names[s]))):
            return s, commit or found
    raise SystemExit(f"no submission of ours among the replay's bots ({names['A']!r}, {names['B']!r}): "
                     "give --side and --commit")


# Writes `commit`'s bot/ into a new folder under `into`, named after its contents (the sandbox's build cache keys on
# the folder name and the .cpp files only, so a header change must change the name). With `debug`, the debug line is
# on (with `moves`, carrying every move's terms), and sonarkey.hpp hashes untouched copies of the files that changed.
def prepare_source(commit: str, into: Path, debug: bool, moves: bool = True) -> Path:
    work = Path(tempfile.mkdtemp(dir=into))
    archive = subprocess.run(["git", "-C", str(REPO), "archive", commit, "bot"], capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(work)], input=archive, check=True)
    bot = work / "bot"
    if debug:
        changed = []
        params = bot / "params.hpp"
        text = params.read_text()
        if "bool debugIndicator = false;" not in text:
            raise SystemExit(f"{commit}: params.hpp has no `bool debugIndicator = false;` to switch on")
        changed.append(("params.hpp", text))
        params.write_text(text.replace("bool debugIndicator = false;", "bool debugIndicator = true;"))
        line = bot / "debugline.hpp"
        if moves and line.is_file() and (old := line.read_text()).rstrip().endswith("return line.str();\n}\n\n} // namespace bot"):
            cut = old.rstrip()[:-len("return line.str();\n}\n\n} // namespace bot")].rstrip(" ")
            changed.append(("debugline.hpp", old))
            line.write_text(cut + MOVE_PATCH + "\n} // namespace bot\n")
        elif moves:
            print(f"{commit[:12]}: debugline.hpp doesn't end as tools/why.py expects, so the per-move terms are left "
                  "out", file=sys.stderr)
        key = bot / "sonarkey.hpp"
        if key.is_file():
            keytext = key.read_text()
            new = keytext
            for name, original in changed + [("sonarkey.hpp", keytext)]:
                if f'#embed "{name}"' in keytext:
                    (bot / f"{name}.pristine").write_text(original)
                    new = new.replace(f'#embed "{name}"', f'#embed "{name}.pristine"')
            key.write_text(new)
    digest = hashlib.sha256()
    for path in sorted(p for p in bot.rglob("*") if p.is_file()):
        digest.update(path.relative_to(bot).as_posix().encode() + b"\0" + path.read_bytes())
    named = into / f"why-{commit[:8]}-{digest.hexdigest()[:16]}"
    if not named.exists():
        bot.rename(named)
    shutil.rmtree(work, ignore_errors=True)
    return named


@dataclass
class Replayed:
    map: str  # candidate_maps' name for the map text played
    lines: dict[tuple[int, int], str]  # (round, our dragon id) -> its debug line
    actions: dict[tuple[int, int], str]  # (round, our dragon id) -> the action it sent
    difference: tuple[int, str, str] | None  # first_difference, None when the whole game agreed
    blob: bytes = b""  # the replayed game as the engine wrote it (with our debug lines when the bot had them on)


# Plays the recorded game again: our bot (a built wasm) on `side`, the stand-in on the other, on `game_map` (a
# candidate_maps pair) with the recorded seed. Once one of our dragons sends another action than the recorded one,
# every dragon gets an empty reply, so the game ends at once; the event comparison then finds the first difference,
# which may be earlier. With no `wasm` and no `side`, every dragon is the stand-in: only the engine, the map and the
# seed are tested, in a few seconds.
def play(replay: Any, wasm: Path | None, side: str | None, game_map: tuple[str, str]) -> Replayed:
    from unswbc.engine import DEBUG_ALL, EngineModule
    from unswbc.sandbox import SandboxBot, WasmPool

    seed = replay.seed.value if replay.seed.which() == "value" else 0
    recorded = recorded_replies(replay)
    pool = WasmPool([str(wasm)], key=f"{seed:016x}-{side.lower()}") if wasm and side else None
    live: dict[int, SandboxBot] = {}
    teams: dict[int, str] = {}
    lines: dict[tuple[int, int], str] = {}
    actions: dict[tuple[int, int], str] = {}
    stopped = False

    def spawn(dragon_id: int, init: bytes) -> None:
        team = next((l.split()[1] for l in init.decode().splitlines() if l.startswith("TEAM")), "A")
        teams[dragon_id] = team
        if team == side:
            live[dragon_id] = SandboxBot(pool, init=init, name=str(dragon_id))

    def reply(dragon_id: int, block: bytes) -> bytes:
        nonlocal stopped
        head = block.split(b"\n", 1)[0].split()
        round_ = int(head[1]) if len(head) > 1 and head[0] == b"ROUND" else -1
        want = recorded.get((round_, dragon_id))
        if stopped or want is None:
            stopped = True
            return b""
        if teams.get(dragon_id) != side:
            return want.text()
        out = live[dragon_id].ask(block)
        action = ""
        for text in out.decode(errors="replace").splitlines():
            if text.startswith(("MOVE ", "SPLIT ")):
                action = text.strip()
            elif text.startswith("LOG "):
                lines[round_, dragon_id] = text[4:]
        actions[round_, dragon_id] = action
        if action != want.action:
            stopped = True
        return out

    def death(dragon_id: int, round_: int, reason: str) -> None:
        if bot := live.pop(dragon_id, None):
            bot.stop()

    engine = EngineModule()
    try:
        engine.run(game_map[1].encode(), reply, death, spawn, lambda line: None, DEBUG_ALL, seed)
        blob = engine.replay(replay.botA, replay.botB)
    finally:
        for bot in live.values():
            bot.stop()
        if pool:
            pool.close()
    with tempfile.NamedTemporaryFile(suffix=".replay") as f:
        f.write(blob)
        f.flush()
        again = replays.load_replay(Path(f.name))
        difference = first_difference(comparable(replay), comparable(again))
    return Replayed(game_map[0], lines, actions, difference, blob)


# Builds and plays until a candidate map gives the recorded game (the first whose first difference comes latest,
# when none does), and returns that game.
# With `debug`, the per-move terms are dropped (with a note on stderr) if the bot doesn't build with them.
# The replay is a real game at normal priority (a worker's sandbox refuses nice): one at a time on this machine, and
# only under the loop's load gate. The audit worker ran five at once at about 07:37 on 2026-09-28 and took the Mac to
# load 34 on 10 cores. The wait is tools/gated_test.sh's own (normal-priority load, so the loop's niced games don't hold
# it; the sandbox's sources), capped at WAIT_S so a busy Mac delays a replay but never hangs it; so is the wait for the
# lock, after which the replay goes ahead unlocked (its holder is a hung replay: a dead one's lock is freed). The lock
# file sits beside the evaluation marker, where workers' sandboxes can reach it; one they can't open leaves only the
# load wait.
WAIT_S = 900


def wait_turn(lock_wait_s: float = WAIT_S) -> Any:
    import fcntl
    import os
    from home import evaluating_marker
    handle = None
    try:
        marker = evaluating_marker()
        handle = open(marker.with_name(marker.stem.replace("evaluating", "why") + ".lock"), "a")
        until = time.monotonic() + lock_wait_s
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    print(f"why: the replay lock stayed held {lock_wait_s:g} s; going ahead without it",
                          file=sys.stderr)
                    handle.close()
                    handle = None
                    break
                time.sleep(1)
    except OSError:
        handle = None
    subprocess.run([str(REPO / "tools" / "gated_test.sh"), "true"], env={**os.environ, "GATED_MAX_WAIT": str(WAIT_S)},
                   stdout=subprocess.DEVNULL, check=False)
    return handle


# The candidate maps on which the recording plays again event for event with every dragon a stand-in (the engine alone,
# a few seconds), before our bot is built; a SystemExit saying so when none does. That is a game the site played with
# pearl timings no map file here has (since 2026-09-28, some games on Devil, Queen Of Spades, Schooltime, Slithery Fight
# and a 10-dragon Prisoners Dilemma): our bot sees other countdowns from round 0, so its first sonar value differs.
def reproducing_maps(path: Path, replay: Any, maps: list[tuple[str, str]], folders: tuple[Path, ...]
                     ) -> list[tuple[str, str]]:
    tried = [(game_map, play(replay, None, None, game_map)) for game_map in maps]
    good = [game_map for game_map, game in tried if game.difference is None]
    if good:
        return good
    name, (r, was, now) = max(((g.map, g.difference) for _, g in tried), key=lambda item: item[1][0])
    raise SystemExit(f"{path.name}: not reproducible: even with every dragon sending its recorded replies, no map file "
                     f"in {', '.join(map(str, folders))} plays this game again. On {name} the first difference is in "
                     f"round {r}: the recorded game has\n    {was}\n  the replayed one\n    {now}\n"
                     "Our bot plays no part in this. Pearls that differ (a tileChange) mean the site played pearl "
                     "timings or a map version no map file here has, and our bot would see other countdowns from "
                     "round 0 whatever it does.")


# With `moves` False the debug line is the bot's own, without the per-move terms (loop/rulecounts.py tallies it as it
# does a local logging copy's, M90).
def replay_game(path: Path, side: str | None = None, commit: str | None = None, debug: bool = True,
                folders: tuple[Path, ...] | None = None, moves: bool = True) -> tuple[str, str, Replayed]:
    from unswbc import clangtool
    folders = folders or MAP_DIRS
    replay = replays.load_replay(path)
    if replay.seed.which() != "value":
        raise SystemExit(f"{path}: the replay records no seed, so its pearls can't be played again")
    side, commit = our_side(replay, side, commit)
    maps = candidate_maps(replay.map, folders)
    if not maps:
        raise SystemExit(f"{path}: no map file in {', '.join(map(str, folders))} has this replay's layout with "
                         "pearl timings")
    turn = wait_turn()  # held until this function returns
    maps = reproducing_maps(path, replay, maps, folders)
    with tempfile.TemporaryDirectory(prefix="why-") as tmp:
        try:
            wasm = clangtool.build(prepare_source(commit, Path(tmp), debug, moves))
        except clangtool.BuildError as error:
            if not debug or not moves:
                raise
            print(f"the per-move terms don't build on {commit[:12]}, so they are left out: {error}", file=sys.stderr)
            wasm = clangtool.build(prepare_source(commit, Path(tmp), debug, moves=False))
        best = None
        for game_map in maps:
            game = play(replay, wasm, side, game_map)
            if game.difference is None:
                return side, commit, game
            if best is None or game.difference[0] > best.difference[0]:
                best = game
    return side, commit, best


# One row per move from the debug line's per-move fields (MOVE_PATCH): its score, the terms it is the sum of by the
# bot's names (zeros left out), its fills and flags.
def move_rows(fields: dict[str, str]) -> list[str]:
    rows = []
    for d in "NESW":
        if (value := fields.get(f"m{d}")) is None:
            continue
        score, *parts = value.split(",")
        terms = [part.split(":", 1) for part in parts if ":" in part]
        named = dict(zip(TAIL_FIELDS, [part for part in parts if ":" not in part]))
        shown = ", ".join(f"{k} {v}" for k, v in terms if float(v) != 0)
        marks = " ".join(name for name, bit in zip(FLAGS, named.get("flags", "")) if bit == "1")
        rows.append(f"    {d} {float(score):>13.6g} = {shown or '0'} | fill {named.get('fill', '?')} "
                    f"room {named.get('room', '?')} [{marks}]")
    return rows


def report(path: Path, first: int, last: int, dragon: int | None, side: str | None, commit: str | None,
           plain: bool) -> list[str]:
    import watch
    side, commit, game = replay_game(path, side, commit, debug=not plain)
    out = [f"{path.name}: our side {side}, commit {commit[:12]}, map {game.map}"]
    cut = game.difference[0] if game.difference else None
    if game.difference:
        r, was, now = game.difference
        out.append(f"DIVERGED in round {r}: the recorded game has\n    {was}\n  the replayed one\n    {now}\n"
                   f"  Debug lines from round {r} on are not shown: past it the replay is not the real game.")
    else:
        out.append("reproduced: every event matched the recording (our logs and CPU counts apart)")
    if plain:
        return out
    shown = 0
    for (r, i), text in sorted(game.lines.items()):
        if not first <= r <= last or (dragon is not None and i != dragon) or (cut is not None and r >= cut):
            continue
        fields = watch.log_fields(text)
        base = " ".join(f"{k} {v}" for k, v in fields.items() if not (k[:1] == "m" and k[1:] in tuple("NESW")))
        out.append(f"r{r} dragon {i} sent {game.actions.get((r, i), '?')}: {base}")
        out.extend(move_rows(fields))
        shown += 1
    if not shown:
        out.append(f"no debug line of ours in rounds {first}-{last}" + (f" from dragon {dragon}" if dragon is not None
                                                                         else ""))
    return out

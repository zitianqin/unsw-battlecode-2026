"""Keeps or discards a candidate bot: a sequential test (GSPRT) on paired games against a base bot.

    uv run referee/sprt.py --cand PATH --base PATH --maps PATH [PATH...] [--elo0 0 --elo1 15
                           --alpha 0.05 --beta 0.05] [--max-pairs 2400] [--workers N|auto]
                           [--opponent NAME=PATH ...] [--schedule NAME,NAME,...] [--baselines FILE]
                           [--pending FILE] [--opponent-cand PATH] --out FILE.json

The values shown are the loop's (driver.ELO1, driver.MAX_PAIRS), which the driver always passes along with the
experiment's frozen policy. This command's own defaults are older and wider (--elo1 70, --max-pairs 600), so a run by
hand gives both flags.

Each map is one trial. Against the base it is a pair (candidate as team A, then as team B), scored 0, 0.5, 1,
1.5 or 2 for the candidate; with --opponent the trials rotate through the base and each opponent, and every trial
is scored in nine buckets instead (see Trial and trial_score). With --schedule, trial i plays the i-th name of
that list instead (the champion's name is "champion"): the loop's opponent mix gives one opponent a share of the
trials that way (loop/driver.py trial_order). After each batch of trials the log-likelihood ratio
(LLR) of H1 (Elo = elo1) against H0 (Elo = elo0) is recomputed from the bucket counts (for pairs alone, the five
of the "pentanomial"), using Fishtest's generalised SPRT on normalised Elo. The test stops when the LLR leaves
[ln(β/(1−α)), ln((1−β)/α)].

The bounds elo0/elo1 are *normalised* Elo (nElo): Elo measured in standard deviations of the pair score,
so a test's length depends on the bounds and not on how noisy the maps are. The reported `elo` is the
familiar logistic Elo from the mean score, with a 95% confidence interval.

Games are deterministic, so a (map, sides) pairing is never played twice: maps are consumed as an iterator
of distinct maps (`run_sprt` takes any iterator, e.g. one that generates fresh maps), and running out of
maps before a decision is an error. With --pending, a trial's map may not exist yet (loop/driver.py makes a private
map set in play order while the test plays, lazy_making, kept ahead of the count read this writes to FILE.read): it
is waited for while that file exists and its maker keeps it fresh, and once it is gone or stale (PENDING_STALE) a map
still missing ends the maps there. The first --max-pairs maps are the trials'; any after them are spares. A trial's
map the engine refuses to load (run.map_refused: the engine's own loader, no bot, checked before its games start;
E0260 and E0257 each lost a whole test to one such map) is dropped, counting for neither side, and the trial plays the first spare from the same
folder (else the first spare) against the same opponent instead; the report's `dropped_maps` names each with its
reason and replacement.

With --opponent-cand, the opponent trials play that build of the candidate instead of --cand. The loop passes test
copies (referee/sharedkey.py, driver.test_bots): the champion and the opponent trials' candidate on the fixed sonar
key, so a pair's games differ only where the change acts, on every platform, and --cand, which plays the champion
head to head, on another fixed key (P.sonarTeamTag already keeps two teams on one key apart; the second key also
holds for a candidate without the tag). Its --opponent builds are then test copies on keys of their own.

Writes the report to --out and, with --opponent, each opponent trial's games to OUT-rotation.jsonl beside it
(`<stem>-rotation.jsonl`), which the opponent screen counts instead of replaying (driver.check_pool).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import itertools
import json
import math
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import mapcheck
import native_judge
import record
import remote
import sharedkey
from run import (BotStats, GameResult, Job, PredictStats, Prediction, blamed, bot_digest, check_pairs, map_refused,
                 native_fault, native_report, native_suspect, nondeterministic_since, parse_workers, stream_games)

PAIR_VALUES = [0.0, 0.25, 0.5, 0.75, 1.0]  # pair score / 2, index = pentanomial bucket
TRIAL_BUCKETS = 9  # with opponents: trial scores 0, 1/8, ..., 1 (see trial_score)
NELO_PER_T = 800 / math.log(10)  # nElo = t-value per game * this
EPSILON = 1e-3  # Fishtest adds this to empty buckets so the MLE is defined
Z95 = 1.959963984540054
BATCH_PAIRS = 6


# Map size bands in tiles (name, lowest), a copy of loop/meta.py AREA_BANDS (the referee runs from a copy that can't
# import loop/; referee/test_sprt.py checks the two agree), for the running per-size scores (Standing).
SIZE_BANDS = (("<=1024", 0), ("1025-2399", 1025), (">=2400", 2400))


# A map file's width x height in tiles, from its MAP line before any TILE line, or None (as driver.map_area reads it).
def map_tiles(path: Path) -> int | None:
    try:
        with path.open() as handle:
            for line in handle:
                if line.startswith("MAP ") and len(f := line.split()) >= 3 and f[1].isdecimal() and f[2].isdecimal():
                    return int(f[1]) * int(f[2])
                if line.startswith("TILE "):
                    break
    except (OSError, UnicodeDecodeError):
        pass
    return None


# The running test's standing by map style and size, for sprt-progress.json only: a report, never read by the test's
# decision (REFLECT 2026-09-30 15:13 F23: sig asked twice for near-Devil and per-size scores mid-test, and the referee
# saved them only at the end). A trial's style is its map's folder, the map mix's name for it (worldgen, nearportal,
# eval, ...; the loop's end-of-test per_style splits by map name instead, driver.style_scores); its size is its
# SIZE_BANDS band. Each bucket keeps {trials, score}, the score the candidate's mean 0-1 trial score (level 0.5); `tiles`
# is the mean map size of the trials so far and `games` the games played for them (reused baselines not counted), so
# a games-a-minute rate can be weighed by map size (health.slow_sprt).
class Standing:
    def __init__(self) -> None:
        self.styles: dict[str, dict] = {}
        self.sizes: dict[str, dict] = {}
        self.tiles = self.sized = self.games = 0

    def add(self, trial_map: Path, score: float, played: int) -> None:
        self.games += played
        buckets = [(self.styles, trial_map.parent.name or "?")]
        if (tiles := map_tiles(trial_map)) is not None:
            self.tiles, self.sized = self.tiles + tiles, self.sized + 1
            buckets.append((self.sizes, next(name for name, low in reversed(SIZE_BANDS) if tiles >= low)))
        for table, name in buckets:
            row = table.setdefault(name, {"trials": 0, "score": 0.0})
            row["trials"] += 1
            row["score"] += score

    def report(self) -> dict:
        table = lambda rows: {k: {"trials": v["trials"], "score": round(v["score"] / v["trials"], 3)}
                              for k, v in sorted(rows.items())}
        return {"per_style": table(self.styles), "per_size": table(self.sizes), "games": self.games,
                "tiles": round(self.tiles / self.sized) if self.sized else None}


# The SPRT's stopping bounds on the LLR: (lower = accept H0, upper = accept H1).
def sprt_bounds(alpha: float, beta: float) -> tuple[float, float]:
    return math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)


# Bucket counts (the 5 pentanomial ones, or TRIAL_BUCKETS with opponents) -> (total count, probability of each
# bucket), with empty buckets set to EPSILON.
def counts_to_pdf(counts: list[int]) -> tuple[float, list[float]]:
    padded = [max(EPSILON, float(c)) for c in counts]
    total = sum(padded)
    return total, [c / total for c in padded]


# The score (0..1) of each bucket: evenly spaced, so 5 buckets are PAIR_VALUES and 9 are eighths.
def bucket_values(n: int) -> list[float]:
    return [i / (n - 1) for i in range(n)]


# Mean and variance of pair value (0..1) under the probabilities `pdf`.
def mean_var(pdf: list[float]) -> tuple[float, float]:
    values = bucket_values(len(pdf))
    mu = sum(p * x for p, x in zip(pdf, values))
    return mu, sum(p * (x - mu) ** 2 for p, x in zip(pdf, values))


# Solves sum_i p_i a_i / (1 + x a_i) = 0 for x, keeping every 1 + x a_i > 0 (by bisection:
# the left side falls from +inf to -inf across that interval).
def secular(a: list[float], p: list[float]) -> float:
    lo_a, hi_a = min(a), max(a)
    if not lo_a < 0 < hi_a:
        raise ValueError("secular equation has no root: all terms have one sign")
    lo, hi = -1 / hi_a, -1 / lo_a
    for _ in range(200):
        mid = (lo + hi) / 2
        if sum(pi * ai / (1 + mid * ai) for ai, pi in zip(a, p)) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# The distribution closest (max likelihood) to `pdf` whose t-value (mean - ref) / sd equals `t`.
# Port of Fishtest's MLE_t_value: linearise the constraint around the current guess and re-solve.
def mle_t_value(pdf: list[float], ref: float, t: float) -> list[float]:
    guess = [1 / len(pdf)] * len(pdf)
    for _ in range(100):
        mu, var = mean_var(guess)
        sigma = math.sqrt(var)
        a = [x - ref - t * sigma * (1 + ((mu - x) / sigma) ** 2) / 2 for x in bucket_values(len(pdf))]
        root = secular(a, pdf)
        new = [p / (1 + root * ai) for p, ai in zip(pdf, a)]
        done = max(abs(n - g) for n, g in zip(new, guess)) < 1e-12
        guess = new
        if done:
            break
    return guess


# GSPRT log-likelihood ratio of H1 (nElo = nelo1) over H0 (nElo = nelo0) for bucket counts (5 or TRIAL_BUCKETS).
# Port of Fishtest's LLR_normalized: a pair's t-value is sqrt(2) times the per-game one.
def llr_normalized(nelo0: float, nelo1: float, counts: list[int]) -> float:
    total, pdf = counts_to_pdf(counts)
    t0, t1 = (math.sqrt(2) * nelo / NELO_PER_T for nelo in (nelo0, nelo1))
    pdf0, pdf1 = mle_t_value(pdf, 0.5, t0), mle_t_value(pdf, 0.5, t1)
    return total * sum(p * (math.log(q1) - math.log(q0)) for p, q0, q1 in zip(pdf, pdf0, pdf1))


# Logistic Elo difference for an expected score in (0, 1).
def elo_from_score(score: float) -> float:
    return -400 * math.log10(1 / score - 1)


# (score, Elo, Elo low, Elo high, nElo) from bucket counts (5 or TRIAL_BUCKETS); the interval is 95% on the mean
# score.
# An end of the interval is None when the score's interval reaches 0 or 1 (unbounded Elo).
def elo_estimate(counts: list[int]) -> tuple[float, float, float | None, float | None, float]:
    total, pdf = counts_to_pdf(counts)
    mu, var = mean_var(pdf)
    half = Z95 * math.sqrt(var / total)
    lo, hi = mu - half, mu + half
    nelo = (mu - 0.5) / math.sqrt(2 * var) * NELO_PER_T
    return (mu, elo_from_score(mu), elo_from_score(lo) if lo > 0 else None,
            elo_from_score(hi) if hi < 1 else None, nelo)


# Whether the maker of a lazily made map set still lives: its marker `pending` exists and is under PENDING_STALE old.
# The maker touches it every few seconds while its process lives (loop/driver.py lazy_making), so an older one was
# left by a driver that died, and nothing will make the maps it stands for.
PENDING_STALE = 15 * 60


def maker_alive(pending: Path) -> bool:
    try:
        return time.time() - pending.stat().st_mtime < PENDING_STALE
    except OSError:
        return False


# Yields each map once, refusing duplicates by content (identical maps give identical games); `seen` shared across
# calls refuses them across lists too (main's trial maps and spares). A map not made yet is waited for while its maker
# lives (maker_alive(pending)); one still missing after that ends the list, as running out of maps does. With
# `pending`, how many maps have been read so far goes to <pending>.read before each one, so the maker keeps its lead
# ahead of what the games read (the scheduler reads up to run.AHEAD's bound past the trials scored).
def distinct_maps(maps: Iterable[Path], seen: dict[str, Path] | None = None,
                  pending: Path | None = None) -> Iterator[Path]:
    seen = {} if seen is None else seen
    read = pending.with_name(pending.name + ".read") if pending else None
    for n, m in enumerate(maps):
        if read:
            read.write_text(f"{n}\n")
        while pending and not m.exists() and maker_alive(pending):
            time.sleep(1)
        if pending and not m.exists():
            why = "is stale: its driver died" if pending.exists() else "is gone"
            print(f"  {m} was never made: its maker stopped ({pending} {why})", file=sys.stderr)
            return
        digest = hashlib.sha256(m.read_bytes()).hexdigest()
        if digest in seen:
            raise ValueError(f"{m} is the same map as {seen[digest]}: it would replay the same games")
        seen[digest] = m
        yield m


KEEP_LOSSES = 40  # lost games' replays kept for post-mortems and replay audits, worst first
AUDIT_EVERY = 20  # one predicted game in this many (by seat and candidate) is played anyway and compared


# One trial of the test on one map. Against the champion ("champion", no bot) it is the usual pair, the candidate
# as team A then B. Against an opponent it is four games, the candidate then the champion each playing it as team
# A then B, so the trial measures how much better than the champion the candidate does against that opponent.
@dataclass
class Trial:
    map: Path
    opponent: str
    bot: Path | None

    # `keyed`: the candidate build the opponent games play (run_sprt's, --opponent-cand), else `cand`.
    def jobs(self, cand: Path, base: Path, keyed: Path | None = None) -> list[Job]:
        if self.bot is None:
            return [Job(self.map, cand, base, side) for side in "AB"]
        return [Job(self.map, bot, self.bot, side) for bot in (keyed or cand, base) for side in "AB"]


# The trial's bucket in `buckets` (5 without opponents, else TRIAL_BUCKETS). Against the champion the score is the
# candidate's points / 2; against an opponent 0.5 + (candidate's points - champion's points) / 4, both in 0..1.
def trial_score(trial: Trial, results: list[GameResult], buckets: int) -> int:
    points = [{"a": 1.0, "draw": 0.5, "b": 0.0}[g.winner or ""] for g in results]
    score = sum(points) / 2 if trial.bot is None else 0.5 + (points[0] + points[1] - points[2] - points[3]) / 4
    return round(score * (buckets - 1))


# The candidate's lost games in one trial as (flag, job index, game), where the flag is False for a loss where the
# champion, in the same spot, did better (both games of a pair lost, or an opponent the champion beat), so sorting
# puts those losses first.
def trial_losses(trial: Trial, first: int, results: list[GameResult]) -> list[tuple[bool, int, GameResult]]:
    if trial.bot is None:
        both = all(g.winner == "b" for g in results)
        return [(not both, first + i, g) for i, g in enumerate(results) if g.winner == "b"]
    return [(results[i + 2].winner != "a", first + i, results[i]) for i in (0, 1) if results[i].winner == "b"]


# The replay run.Scheduler.play writes for the stream's job `i` (a predicted game has none).
def replay_name(i: int, game: GameResult) -> str:
    return f"{i:04d}-{Path(game.map).stem}-a{game.a_side}.replay"


# The KEEP_LOSSES worst of `losses`, (flag, job index, game) as trial_losses gives them, worst first.
def worst_losses(losses: list[tuple[bool, int, GameResult]]) -> list[tuple[bool, int, GameResult]]:
    return sorted(losses, key=lambda loss: loss[:2])[:KEEP_LOSSES]


# Deletes the replays (and an audit's recording, run.Scheduler.start_entry) of the stream's jobs `games`, (index, game),
# once they are scored: nothing else reads them (the audit is compared before its game is yielded, and the screen
# reuses rotation trials from sprt-rotation.jsonl, not replays).
def drop_replays(replays: Path, games: Iterable[tuple[int, GameResult]]) -> None:
    for i, game in games:
        replay = replays / replay_name(i, game)
        for path in (replay, replay.with_suffix(".recording")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


# Copies the replays of up to KEEP_LOSSES of the candidate's lost games into `out`, worst first. `losses` holds
# (flag, job index, game) as trial_losses gives them.
def keep_losses(losses: list[tuple[bool, int, GameResult]], replays: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for _, i, game in worst_losses(losses):
        name = replay_name(i, game)
        if (replays / name).exists():
            shutil.copy(replays / name, out / name)


STATS_BACKLOG = 2  # opponent trials waiting in a --stats-queue at most: a trial is queued only below it (queue_stats)


# Queues opponent trial number `t` for the behaviour stats worker (loop/evalstats.py) in `queue`: hard links (a copy
# where the folder is on another disk) to the replays of its played games in `replays` and a T#####.json naming each
# game, its role (candidate, then champion, each as A then B), side and whether it was predicted, written last so the
# worker never reads a half-queued trial. `batch` is the trial's four results, `plan` its known (reused baseline)
# games, `first` the stream index of its first played game. Only a side whose champion game was played here can
# pair (a reused baseline has no replay), so only those sides' games are linked. Returns "queued"; "unpairable" when
# neither side can pair (on a shared map set about half the trials, whose champion games are reused); "skipped" while
# STATS_BACKLOG trials wait (the worker is behind) or when queueing fails (a full disk: a report never fails the
# test). Which trials are read depends on the worker's pace and the cache, never on their results.
def queue_stats(queue: Path, t: int, trial: Trial, batch: list[GameResult], plan: list[GameResult | None],
                first: int, replays: Path) -> str:
    stream = iter(range(first, first + sum(known is None for known in plan)))
    sources = [replays / replay_name(next(stream), game) if known is None else None
               for game, known in zip(batch, plan)]
    sides = [i for i in (0, 1) if sources[i + 2] and sources[i + 2].exists()]
    if not sides:
        return "unpairable"
    if len(list(queue.glob("T*.json"))) >= STATS_BACKLOG:
        return "skipped"
    games = []
    try:
        for i, (game, source) in enumerate(zip(batch, sources)):
            name = None
            if i % 2 in sides and source and source.exists():
                name = f"{t:05d}-{i}.replay"
                try:
                    os.link(source, queue / name)
                except OSError:
                    shutil.copy(source, queue / name)
            games.append({"i": i, "role": "candidate" if i < 2 else "champion", "a_side": game.a_side,
                          "predicted": bool(game.predicted), "replay": name})
        spec = queue / f"T{t:05d}.json"
        spec.with_suffix(".tmp").write_text(json.dumps({"trial": t, "opponent": trial.opponent,
                                                       "map": str(trial.map), "games": games}))
        spec.with_suffix(".tmp").rename(spec)
    except OSError:
        return "skipped"
    return "queued"


# Champion-vs-opponent games from earlier tests, keyed by everything that decides them: both bots' sources, the
# map's contents (which also set its pearl seed), the side and the toolkit version. When experiments against the
# same champion share a map set (loop/driver.py), an opponent trial's two baseline games repeat exactly, so each is
# played once and read from here after.
#
# A baseline game played while predicting records the champion's side of it (record.py, on this Mac's platform
# only) in `recordings/` beside the cache (the loop keeps only the current champion's), and its row keeps the
# recording's digest, so a row and a recording pair only when they come from the same game. A candidate's game in
# the same seat then carries the recording as a Prediction: when the candidate answers every recorded turn as the
# champion did, its game is the champion's game, and the scheduler reuses that result instead of playing it. An
# audit that found a difference leaves `recordings/OFF`, which stops prediction on this map set for good.
class BaselineCache:
    def __init__(self, path: Path | None):
        self.path, self.rows = path, {}
        self._digests: dict[Path, str] = {}
        self._folder: Path | None = None
        if path and path.exists():
            lines = path.read_text().splitlines()
            for i, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    # The last line may still be being appended by another test or the prewarm; an earlier one was
                    # left by a writer killed mid-row (put starts the next row on a new line). Either way the game is
                    # just played again.
                    if i < len(lines) - 1:
                        print(f"baseline cache {path}: skipped a torn line {i + 1}", file=sys.stderr)
                    continue
                self.rows[row["key"]] = row["result"]

    def _digest(self, path: Path) -> str:
        if path not in self._digests:
            self._digests[path] = (remote.source_digest(path) if path.is_dir()
                                   else hashlib.sha256(path.read_bytes()).hexdigest()[:16])
        return self._digests[path]

    def key(self, job: Job) -> str:
        return "|".join([self._digest(job.a), self._digest(job.b), self._digest(job.map), job.a_side,
                         importlib.metadata.version("unswbc")])

    # `job`'s row, unless the native judge played it under a key a check has since found differing, or a row with no
    # key (run.native_suspect: its "native" is the key, put): then the game is played again and its new row replaces
    # it.
    def row(self, job: Job) -> dict | None:
        row = self.rows.get(self.key(job)) if self.path else None
        return None if row and native_suspect(row.get("native")) else row

    # The cached result of `job`, with both bots' stats when its row has them (the opponent screen checks them).
    def get(self, job: Job) -> GameResult | None:
        row = self.row(job)
        if not row:
            return None
        a, b = (BotStats(**row[bot]) if bot in row else None for bot in ("a", "b"))
        return GameResult(str(job.map), job.a_side, row["winner"], row["end"], row["rounds"], a, b, 0.0, None)

    # Whether `job`'s map is one of the set's, in the cache's folder: only those games repeat in another test. An aimed
    # mix's SPRT reads the shared set's cache for its general maps and plays its aimed styles on private ones
    # (loop/driver.py aimed_general), and a spare is private too, so their rows and recordings are never kept.
    def holds(self, job: Job) -> bool:
        if self._folder is None:
            self._folder = self.path.parent.resolve()
        return self._folder in Path(job.map).resolve().parents

    # A result played to record carries its own recording's digest (GameResult.recording), which pairs them.
    def put(self, job: Job, result: GameResult) -> None:
        if not self.path or result.error or not self.holds(job):
            return
        key = self.key(job)
        self.rows[key] = {"winner": result.winner, "end": result.end, "rounds": result.rounds,
                          **{bot: {k: v for k, v in asdict(stats).items() if k != "points"}
                             for bot, stats in (("a", result.a), ("b", result.b)) if isinstance(stats, BotStats)}}
        if result.recording:
            self.rows[key]["recording"] = result.recording
        if result.native:
            self.rows[key]["native"] = native_judge.process_key() or True
        line = json.dumps({"key": key, "result": self.rows[key]}) + "\n"
        with self.path.open("ab+") as handle:  # appends, whatever the position
            # A writer killed mid-row leaves a torn last line: start a new one, so the torn line stays the last bad one
            # rather than gluing this row onto it (which the loader would then raise on as a bad line mid-file).
            if handle.seek(0, 2) and (handle.seek(-1, 2), handle.read(1))[1] != b"\n":
                line = "\n" + line
            handle.write(line.encode())

    # Where the baseline game `job` is (or would be) recorded.
    def recording(self, job: Job) -> Path | None:
        if not self.path:
            return None
        return self.path.parent / "recordings" / f"{hashlib.sha256(self.key(job).encode()).hexdigest()[:24]}.zst"

    # `job`, a baseline game about to be played, asked to record when the cache keeps its row (holds).
    def recorded(self, job: Job) -> Job:
        return replace(job, record=self.recording(job)) if self.path and self.holds(job) else job

    # The recording of the cached game `job`, when there is one and it is that game's (its digest is in the row).
    def paired(self, job: Job) -> Path | None:
        row = self.row(job)
        path = self.recording(job)
        if not row or "a" not in row or "recording" not in row or file_digest(path) != row["recording"]:
            return None
        return path

    # The Prediction for `cand`, the candidate's game in `base`'s seat, when that baseline game is cached with its
    # recording. One in AUDIT_EVERY, by seat and candidate (so each test audits different seats), is an audit.
    def prediction(self, cand: Job, base: Job) -> Prediction | None:
        if (path := self.paired(base)) is None:
            return None
        row = self.rows[self.key(base)]
        result = GameResult(str(cand.map), cand.a_side, row["winner"], row["end"], row["rounds"], BotStats(**row["a"]),
                            BotStats(**row["b"]), 0.0, None)
        seat = f"{self.key(base)}|{self._digest(cand.a)}"
        audit = int(hashlib.sha256(seat.encode()).hexdigest(), 16) % AUDIT_EVERY == 0
        return Prediction(path, result, audit, row["recording"])

    # The marker an audit mismatch leaves: while it exists nothing on this map set is predicted.
    def off_marker(self) -> Path | None:
        return self.path.parent / "recordings" / "OFF" if self.path else None

    def turn_off(self, reason: str) -> None:
        if marker := self.off_marker():
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {reason}\n")


# record.file_digest of a file, or None when it can't be read.
def file_digest(path: Path | None) -> str | None:
    try:
        return record.file_digest(path.read_bytes()) if path else None
    except OSError:
        return None


# Whether this run records baseline games and predicts candidate games: only with a cache, a toolkit record.py was
# checked against, no audit mismatch on the map set yet, and a champion (`base`) whose sonar key doesn't hash its own
# source: the loop's test copy of it plays the fixed key (sharedkey.test_copy), as its candidate's does. With
# P.sonarBuildKey on every candidate's key differs from the champion's, its first sonar message already differs, and
# 0 of 2,230 checks in 22 SPRTs matched (2026-09-27, E0090 until MISSES 99's test keys), while recording still pinned
# every baseline game to this Mac's platform. Otherwise says why on stderr, and games are just played. Only an audit
# mismatch or an unsupported record.py also goes to the host alerts file: the sonar key is a known, deliberate state,
# and its line at every SPRT's start was noise the orchestrator had to judge each time.
def predicting(cache: BaselineCache, base: Path | None = None) -> bool:
    if not cache.path:
        return False
    marker = cache.off_marker()
    params = base / "params.hpp" if base else None
    if params and params.exists() and sharedkey.SONAR_KEY.search(params.read_text()):
        print(f"predicted games off: {params} has sonarBuildKey on, so no candidate's game can match the champion's "
              "recording", file=sys.stderr, flush=True)
        return False
    if marker.exists():
        reason = f"an audit on this map set found a difference ({marker}: {marker.read_text().strip()[:200]})"
    elif (reason := record.supported()) is None:
        return True
    line = f"WARNING predicted games off: {reason}"
    print(line, file=sys.stderr, flush=True)
    try:
        with remote.ALERTS.open("a") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass
    return False


# Runs the test, pulling maps from `maps` as needed; with `opponents` (name, bot) the trials rotate through the
# champion and then each opponent in turn (see Trial), or with `schedule` trial i plays the opponent named
# schedule[i] ("champion" for the base; the report then keeps the played part of it as "schedule"). Returns the JSON-ready report; `report["error"]` is set
# (and the decision left "inconclusive") if a game failed, the maps ran out before a decision, or a prediction audit
# failed: an audited predicted game differed from its recording, so the map set's OFF marker is written, the driver
# records the result as an error (the idea goes back to todo) and later tests on that set play unpredicted. With
# `rotation_out`, each scored opponent trial's four games (the candidate as team A then B, then the champion) go
# there, a line a trial, so the opponent screen can count them instead of playing them again (driver.check_pool).
# `spares`: the maps a trial takes in place of one the engine refuses to load (spare_for; the module docstring).
# A native judge check that differed while this run had native games at risk (run.native_fault) ends it in error at the
# next batch of trials, since any of those games may be wrong: the driver re-evaluates it (driver.native_fault). So
# does a check finding the candidate not repeating its own games (candidate_nondeterministic), which the driver
# discards.
def run_sprt(cand: Path, base: Path, maps: Iterator[Path], elo0: float, elo1: float, alpha: float,
             beta: float, max_pairs: int, workers: int | str, losses: Path | None = None,
             opponents: list[tuple[str, Path]] = (), baselines: Path | None = None,
             rotation_out: Path | None = None, schedule: list[str] | None = None,
             spares: Iterable[Path] = (), keyed: Path | None = None, stats_queue: Path | None = None) -> dict:
    lower, upper = sprt_bounds(alpha, beta)
    started = time.time()
    keyed = keyed or cand  # the candidate build the opponent trials play (--opponent-cand)
    digests = {role: named(bot) for role, bot in (("cand", cand), ("base", base), ("keyed", keyed))
               if role != "keyed" or keyed != cand}  # as the checks name them
    rotation = [("champion", None), *opponents]
    buckets = TRIAL_BUCKETS if opponents else len(PAIR_VALUES)
    bots = dict(rotation)
    if schedule is not None and (len(schedule) < max_pairs or set(schedule[:max_pairs]) - set(bots)):
        raise ValueError(f"--schedule needs a name for each of the {max_pairs} trials, each \"champion\" or an "
                         f"--opponent ({', '.join(bots)})")
    names = schedule or [rotation[i % len(rotation)][0] for i in range(max_pairs)]
    counts = [0] * buckets
    per_map: dict[str, float] = {}
    per_opponent = {name: {"trials": 0, "score": 0.0} for name, _ in rotation}
    standing = Standing()
    games: list[GameResult] = []  # the candidate's games only, which the loop checks for crashes and timeouts
    lost: list[tuple[bool, int, GameResult]] = []
    rotation_rows: list[str] = []
    llr, decision, error, played, job = 0.0, "inconclusive", None, 0, 0
    # Games stream across every free slot, but trials are read strictly in map order and the LLR is checked every
    # BATCH_PAIRS of them, so decisions match a serial run exactly; games started past a decision (running, or finished
    # and waiting for an earlier one, up to run.AHEAD's bound) are dropped.
    scratch = tempfile.TemporaryDirectory(prefix="sprt-")
    cache = BaselineCache(baselines)
    predict = predicting(cache, base)
    # The trials made so far (made on demand by has_trial, in map order, whichever of the job stream and the scoring
    # loop below asks first, both in this thread), each with its jobs' plan: the baseline games already in the cache
    # filled in (None: to be played). A map the engine refuses is swapped for a spare as its trial is made, before any
    # of its games is started.
    trials: list[Trial] = []
    plans: list[list[GameResult | None]] = []
    dropped: list[dict] = []
    primary = itertools.islice(maps, max_pairs)
    spares, waiting = iter(spares), []  # spares read past while looking for one from another folder

    def spare_for(bad: Path) -> Path | None:
        for k, m in enumerate(waiting):
            if m.parent.name == bad.parent.name:
                return waiting.pop(k)
        for m in spares:
            if m.parent.name == bad.parent.name:
                return m
            waiting.append(m)
        return waiting.pop(0) if waiting else None

    def has_trial(k: int) -> bool:
        while len(trials) <= k:
            if (m := next(primary, None)) is None:
                return False
            while m is not None and (reason := map_refused(m)):
                spare = spare_for(m)
                dropped.append({"map": str(m), "reason": reason, "replaced_by": str(spare) if spare else None})
                print(f"  dropped {m}: the engine refused it ({reason}); trial {len(trials)} plays "
                      f"{spare or 'nothing (no spare left)'}", file=sys.stderr)
                m = spare
            if m is None:
                return False
            name = names[len(trials)]
            trial = Trial(m, name, bots[name])
            trials.append(trial)
            plans.append([cache.get(j) if trial.bot is not None and i >= 2 else None
                          for i, j in enumerate(trial.jobs(cand, base, keyed))])
        return True

    # As played: a baseline game records the champion's side, a candidate's opponent game may be predicted from one.
    def as_played(trial: Trial, i: int, jobs: list[Job]) -> Job:
        if not predict or trial.bot is None:
            return jobs[i]
        return cache.recorded(jobs[i]) if i >= 2 else replace(jobs[i], predict=cache.prediction(jobs[i], jobs[i + 2]))

    def job_stream() -> Iterator[Job]:
        k = 0
        while has_trial(k):
            trial_jobs = trials[k].jobs(cand, base, keyed)
            yield from (as_played(trials[k], i, trial_jobs) for i, known in enumerate(plans[k]) if known is None)
            k += 1

    jobs = job_stream()
    stats = PredictStats()
    results = stream_games(jobs, list(dict.fromkeys([cand, keyed, base, *(bot for _, bot in opponents)])), workers,
                           False, Path(scratch.name),
                           stats=stats, alerts=True)
    reused = 0
    queued = {"queued": 0, "skipped": 0, "unpairable": 0}  # opponent trials, for the behaviour stats (queue_stats)
    try:
        while has_trial(played) and decision == "inconclusive":
            for _ in range(BATCH_PAIRS):
                if not has_trial(played):
                    break
                trial, plan = trials[played], plans[played]
                n = sum(known is None for known in plan)
                fresh = list(itertools.islice(results, n))
                failed = [r for r in fresh if r.error]
                if len(fresh) < n or failed:
                    error = (f"game failed on {failed[0].map} (as {failed[0].a_side}): {failed[0].error}" if failed
                             else f"game results ended after {played} trials")
                    break
                played_games = iter(fresh)
                batch = [known if known is not None else next(played_games) for known in plan]
                for i, (j, known) in enumerate(zip(trial.jobs(cand, base, keyed), plan)):
                    if trial.bot is not None and i >= 2 and known is None:
                        cache.put(j, batch[i])
                if stats.audit_mismatches:  # the test's own predicted games can't be trusted: end it in error
                    error = f"predicted games: an audit's game differed from its recording ({stats.off})"
                    cache.turn_off(stats.off)
                    break
                reused += len(plan) - n
                games += batch[:2]
                if trial.bot is not None:
                    rotation_rows.append(json.dumps({
                        "opponent": trial.opponent, "bot": str(trial.bot), "map": str(trial.map), "cand": str(keyed),
                        "base": str(base), "unswbc": importlib.metadata.version("unswbc"),
                        "games": [asdict(g) for g in batch]}) + "\n")
                # Only the worst KEEP_LOSSES losses so far keep their replays, for keep_losses at the end; every other
                # replay of the trial, and of a loss pushed out, goes now, so the disk holds at most the games started
                # but not yet scored (run.AHEAD's bound) plus KEEP_LOSSES, not every game of the test.
                if stats_queue is not None and trial.bot is not None:  # before its replays go (loop/evalstats.py)
                    queued[queue_stats(stats_queue, played, trial, batch, plan, job, Path(scratch.name))] += 1
                kept = worst_losses(lost + trial_losses(trial, job, batch)) if losses is not None else []
                keep = {i for _, i, _ in kept}
                drop_replays(Path(scratch.name), [(job + k, g) for k, g in enumerate(fresh) if job + k not in keep] +
                             [(i, g) for _, i, g in lost if i not in keep])
                lost = kept
                job += n  # the stream's index, which names the replays: reused baseline games have none
                bucket = trial_score(trial, batch, buckets)
                counts[bucket] += 1
                per_map[str(trial.map)] = bucket / (buckets - 1)
                per_opponent[trial.opponent]["trials"] += 1
                per_opponent[trial.opponent]["score"] += bucket / (buckets - 1)
                standing.add(trial.map, bucket / (buckets - 1), n)
                played += 1
            if not error and (said := native_fault(native_report(started))):  # its results may be wrong: stop now
                error = f"the native judge differed from unswbc run: {said}"
            if not error and (said := candidate_nondeterministic(nondeterminism(started, digests))):
                error = f"the candidate doesn't repeat its own games: {said}"
            if error:
                break
            llr = llr_normalized(elo0, elo1, counts)
            if llr >= upper:
                decision = "H1"
            elif llr <= lower:
                decision = "H0"
            print(f"  trials={played} counts={counts} llr={llr:+.2f}", file=sys.stderr)
            if losses:  # the running test's standing, for the orchestrator (the report is written only at the end)
                (losses.parent / "sprt-progress.json").write_text(json.dumps({
                    "updated": time.time(), "trials": played, "counts": counts, "llr": round(llr, 3),
                    "bounds": [round(lower, 3), round(upper, 3)], "per_opponent": {
                        name: round(o["score"] / o["trials"], 3) for name, o in per_opponent.items() if o["trials"]},
                    **standing.report()}) + "\n")
        if not error and decision == "inconclusive" and played < max_pairs:
            error = f"ran out of maps after {played} pairs, before a decision"
    finally:
        results.close()
        if rotation_out is not None and opponents:
            rotation_out.parent.mkdir(parents=True, exist_ok=True)
            rotation_out.write_text("".join(rotation_rows))
        if losses is not None:
            keep_losses(lost, Path(scratch.name), losses)
        scratch.cleanup()

    status = remote.read_status()
    hosts = status.get("hosts", {}) if status.get("updated", 0) >= started else {}  # this run's remote games only
    score, elo, elo_lo, elo_hi, nelo = elo_estimate(counts)
    for row in per_opponent.values():
        row["score"] = row["score"] / row["trials"] if row["trials"] else None
    return {
        "decision": decision, "error": error, "llr": llr, "lower": lower, "upper": upper,
        "elo0": elo0, "elo1": elo1, "alpha": alpha, "beta": beta, "pairs": played,
        "pentanomial": counts, "score": score, "elo": elo, "elo_lo": elo_lo, "elo_hi": elo_hi, "nelo": nelo,
        "rotation": [name for name, _ in rotation], "per_opponent": per_opponent,
        "per_map": per_map, "cand": str(cand), "base": str(base),
        "opponent_cand": str(keyed) if keyed != cand else None,
        "games": [asdict(g) for g in games],
        "hosts": hosts, "baselines_reused": reused, "predict": asdict(stats), "dropped_maps": dropped,
        **({"schedule": schedule[:played]} if schedule is not None else {}),
        **({"stats_queue": queued} if stats_queue is not None else {}),
        # the native judge's games this run counted, how many since its last matching check, and any switch-off
        **({"native": native} if (native := native_report(started)) else {}),
        # the bots a check found not repeating their own games, and whether the candidate or champion is among them
        **({"nondeterministic": found} if (found := nondeterminism(started, digests)) else {}),
    }


# What names `bot` in the check files (run.bot_digest), or its path when it can't be read (the games will say why).
def named(bot: Path) -> str:
    try:
        return bot_digest(bot)
    except OSError:
        return str(bot)


# The "nondeterministic" report of a run that started at `started` (run.nondeterministic_since: its checks' entries,
# and any standing for the candidate or the champion): {"bots": entries by digest, "cand" and "base": that bot's
# digest when it is among them, else None, "pairs": the differing pairs (run.check_pairs), "blamed": what a check said
# of the candidate when run.blamed finds it at fault, else None}, or None when there is none. The driver reads it from
# sprt.json (missing_checks, judge.nondeterministic).
def nondeterminism(started: float, digests: dict[str, str]) -> dict | None:
    found = nondeterministic_since(started, digests.values())
    if not found:
        return None
    flagged, repeated = check_pairs(started, digests.values())
    said = next(filter(None, (blamed(digests[role], flagged, repeated)
                              for role in ("cand", "keyed") if role in digests)), None)
    return {"bots": found, **{role: digest if digest in found else None for role, digest in digests.items()},
            "pairs": flagged, "blamed": said[:300] if said else None}


# What a check said of the candidate when run.blamed finds it, not the other bot of a game that differed, not
# repeating its own games (`report`, nondeterminism's): its games aren't its own each time (MISSES 86: E0287 wrote out
# of bounds on the heap), so the run stops in error and the driver discards it. None otherwise: a candidate-v-X check
# that differed says nothing of which until X has repeated with another bot (a champion-v-X check that matched), since
# a nondeterministic opponent would flag the candidate too. judge.nondeterministic reads the same from sprt.json.
def candidate_nondeterministic(report: dict | None) -> str | None:
    return report.get("blamed") if report else None


# The one-line summary, e.g. "SPRT H1 llr=+2.97 pairs=88 score=0.61 elo=+78±41 nelo=+95".
# The Elo interval is shown as ±half-width, or as a one-sided bound when one end is unbounded.
def summary(report: dict) -> str:
    lo, hi = report["elo_lo"], report["elo_hi"]
    if lo is not None and hi is not None:
        interval = f"±{(hi - lo) / 2:.0f}"
    elif lo is not None:
        interval = f" (95%: >{lo:+.0f})"
    elif hi is not None:
        interval = f" (95%: <{hi:+.0f})"
    else:
        interval = ""
    line = (f"SPRT {report['decision']} llr={report['llr']:+.2f} pairs={report['pairs']} "
            f"score={report['score']:.2f} elo={report['elo']:+.0f}{interval} nelo={report['nelo']:+.0f}")
    return line + (f" ERROR: {report['error']}" if report["error"] else "")


def main() -> int:
    parser = argparse.ArgumentParser(description="SPRT a candidate bot against a base bot on paired games.")
    parser.add_argument("--cand", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--maps", type=Path, nargs="+", required=True,
                        help="distinct maps, used in order; those past --max-pairs are spares")
    parser.add_argument("--elo0", type=float, default=0.0, help="H0, in normalised Elo")
    parser.add_argument("--elo1", type=float, default=70.0, help="H1, in normalised Elo")
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--beta", type=float, default=0.05)
    parser.add_argument("--max-pairs", type=int, default=600)
    parser.add_argument("--workers", type=parse_workers, default="auto")
    parser.add_argument("--opponent", action="append", default=[], metavar="NAME=PATH",
                        help="rotate trials through the champion and these frozen opponents, in order")
    parser.add_argument("--schedule", type=lambda text: text.split(","),
                        help="the opponent of each trial in order, comma-separated: champion or an --opponent's name")
    parser.add_argument("--baselines", type=Path,
                        help="cache of champion-vs-opponent games shared by tests on the same maps (see BaselineCache)")
    parser.add_argument("--pending", type=Path,
                        help="while this file exists, a map not made yet is waited for (see distinct_maps)")
    parser.add_argument("--opponent-cand", type=Path,
                        help="the candidate build the opponent trials play (default --cand; see above)")
    parser.add_argument("--stats-queue", type=Path,
                        help="queue opponent trials' replays here for loop/evalstats.py's worker (see queue_stats)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    opponents = [(text.split("=", 1)[0], Path(text.split("=", 1)[1])) for text in args.opponent]

    try:
        seen: dict[str, Path] = {}
        trials = distinct_maps(args.maps[:args.max_pairs], seen, args.pending)
        report = run_sprt(args.cand, args.base, trials, args.elo0, args.elo1, args.alpha, args.beta, args.max_pairs,
                          args.workers, losses=args.out.parent / "losses", opponents=opponents, baselines=args.baselines,
                          rotation_out=args.out.with_name(args.out.stem + "-rotation.jsonl"), schedule=args.schedule,
                          spares=distinct_maps(args.maps[args.max_pairs:], seen), keyed=args.opponent_cand,
                          stats_queue=args.stats_queue)
    except ValueError as error:
        print(f"SPRT error: {error}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    if dropped := report["dropped_maps"]:  # the loop's log of refused maps, which loop/health.py alerts on
        mapcheck.log_rows(remote.loop_home() / "bad-maps.jsonl", [
            {"at": time.time(), "map": row["map"], "moved": None, "reason": row["reason"],
             "engine": importlib.metadata.version("unswbc"),
             "note": f"dropped by the SPRT in {args.out.parent.name}, which played {row['replaced_by']} instead"}
            for row in dropped])
    print(summary(report))
    return 2 if report["error"] else 0


if __name__ == "__main__":
    sys.exit(main())

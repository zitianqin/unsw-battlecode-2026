"""The judge (sig, 2026-09-28: "basically it shouldnt be a automatic pass or fail based on some threshold. the ai
agent should reiew it ... not just with the portal examples - but eveyrhtign as well"; LOOP.md: Judged verdicts).

After every recorded result whose SPRT ran and that wasn't kept (discarded or promising), and every keep, on H1 or on
probability (not a speed or identical idea's, which has no SPRT), an independent headless Claude session reads what
the test measured, where the change acts and what the idea and its coder declared before the SPRT (loop/JUDGE.md),
and writes verdict.json: where the change acts, whether it helps there, whether it hurts anywhere else, and one
verdict. The trusted side checks that answer against the recorded numbers (`check`) before anything acts on it:

- keep: applies by itself when it meets the guard (`keep_problems`): one subgroup of the result (a map style or
  layout, size band or opponent: `subgroups`) declared or measured before the SPRT (`declared`: the frozen map or
  opponent mix, the coder's own map mix from its shadow counts, or the subgroup named in the idea's text at claim or
  in the coder's final message, both written before any SPRT game), a clear gain there (one-sided p times the number
  of declared subgroups at most P), no loss overall and outside it at the no-loss ideas' bound (at least NO_LOSS
  chance of being no worse than meta.NOLOSS_ELO0), the trials outside it not reading as a loss beyond their own noise
  (z >= -1: from about 1,400 trials stricter than the bound, which lets a rest at -5 normalized Elo over 1,151 trials
  through) and an overall estimate above level; for a variant sweep's idea (source "sweep:"), whose hint was self-play
  (method M82), also its trials outside the champion seat pooled not below level. A keep missing the guard is not
  refused (sig, 2026-09-28: "keep can run even after the rest its fine - or it discusses with u"): it is saved as an
  `ask`, alerted "JUDGED ask: E#### keep (<reason>; guard: <what it missed>)", for the orchestrator to apply
  (`driver.py --judge-keep E####`) or turn into a confirmation (`--judge-confirm E####`), each with its reason. The
  hard gates always hold: a crash, a timeout, a candidate that doesn't repeat its own games (`nondeterministic`) or a
  failed CPU gate or tests is a discard, and driver.rejudge plays the opponent screen the result skipped (a regression
  is a discard and leaves the result as recorded) and refuses a candidate no longer on the current champion, which
  makes the keep a confirm (driver.apply_judged).
- confirm: a retest of the same candidate (driver.queue_confirm) with the subgroup's map or opponent mix
  (`confirm_mix`) on fresh maps, whose own judge reads the subgroup as declared. It rests on the judge's judgement
  alone, with no statistical bar (sig, 2026-09-28: "its ok if theres no statistical bar. thats thewhole freaking
  point of using their judgement"), so it is one a candidate: a confirm is a discard when the result is a judged
  confirmation already (`as_confirm`) or an earlier result of the same candidate or lineage had one
  (driver.judged_before).
- discard: the recorded verdict stands, and its candidate is not retested unchanged (MISSES 98): a promising
  result's idea, still a followup for it, is marked tried and a "Retest E#### alone" its post-mortem queued is dropped
  (driver.retire_discarded), and a post-mortem after it queues none (driver.add_followups).
- A keep gets agree, or flag for a measured loss (n and p <= chance.TRIGGER_P), which only alerts: the orchestrator
  decides whether to queue a switch-off re-check (driver.queue_recheck).

Forking paths: a subgroup picked after seeing the data is where luck looks like a win (a worthless change's ~40
subgroups put two past p 0.05 on average). So `declared` reads the row alone, never the judge's answer; an automatic
keep pays for every subgroup the row declared (Bonferroni over them) and needs the rest of its trials to show no
loss, so a worthless change is kept by itself at most P of the time, on top of the SPRT's 5% and the probability
rule's. Anything past that is the orchestrator's recorded call on an ask.

The judged record is saved as runs/E####/judged.json (`FILE`: the trusted side's, out of every worker's reach) and
alerted as "JUDGED: E#### <verdict>: <reason>"; the orchestrator's Rule check on a result not kept is still written
(it may cite this; health.unchecked_rejections), and a keep's rejudged row carries it as `judged`. A checked answer a
stop marker kept from being applied is saved as runs/E####/judge-pending.json (`PENDING`, one small record, deleted
when applied) and applied by the next `driver.py --judge E####` without a new judge. A judge's work folder is <loop
home>/judge/<E####>/ (its inputs, copied; the only folder it can write; deleted once its answer is applied), its
prompt and session output in meta.protected_io(state, "judge", work), about 60 KB a result, kept as the audit trail.
Judges run one at a time (`launch` makes the folder and runs the judge under one lock), so no judge's settings miss
a sibling folder made after its launch; the wait for the lock counts toward the TIMEOUT cap, after which the judge is
left owed (health.judge_turns). The recording driver judges after the result and before the coder's post-mortem or
follow-on, so that turn sees the verdict; a keep waits for the evaluation lock, holding the coder's turn behind it.
An owed judge (failed or skipped), or an ask undecided for health.JUDGE_GRACE, is alerted by health.judge_turns;
`driver.py --judge E####` runs it, also runs a keep's follow-on, and applies an answer a stop marker held. A native
session's judge is the active model itself, the one that coded the change,
so it isn't independent: the trusted side's check is the same.
"""

from __future__ import annotations

import fcntl
import functools
import json
import math
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import chance
import meta
import online
import reports

# Results recorded before FROM (the judge's merge, 2026-09-28 22:30) are never owed a judge: the orchestrator
# rule-checked them (`driver.py --judge` refuses them too); an older one's confirmation is a retest idea.
FROM = datetime.fromisoformat("2026-09-28T22:30:00+10:00").timestamp()
JUDGED = ("discarded", "promising")  # the verdicts a judge may turn into a keep or a confirmation
VERDICTS = {"judge": ("keep", "confirm", "discard"), "review": ("agree", "flag")}
ASK = "ask"  # a keep missing the guard, saved for the orchestrator's decision (keep_problems)
P = meta.TARGET_P  # a declared subgroup's corrected one-sided p for a keep: the targeted ideas' bar (sig, 2026-09-28)
NO_LOSS = 0.9  # the probability rule's chance (driver.LIMIT_PROBABILITY) of being no worse than meta.NOLOSS_ELO0
FILE = "judged.json"  # in runs/E####
PENDING = "judge-pending.json"  # in runs/E####: a checked answer a stop marker held back
ANSWER = "verdict.json"  # in the judge's work folder
TIMEOUT = 45 * 60  # a judge's whole budget: the wait for the judge lock and its run
TOOLS = meta.WORKER_TOOLS
PROMPT = Path(__file__).resolve().parent / "JUDGE.md"


# "judge" when a recorded result is owed a judge (its SPRT ran, recorded since FROM, not kept), "review" for a keep
# (on H1 or on probability), else None. A speed or identical idea's keep rests on its move-for-move test, not an SPRT,
# and a rejudged row was judged by whoever re-judged it: neither is owed one.
def owed(row: dict) -> str | None:
    sprt = row.get("sprt") or {}
    if ((row.get("finished") or 0) < FROM or not sprt.get("decision") or row.get("rejudged")
            or (row.get("idea") or {}).get("kind") in ("speed", "identical")):
        return None
    if row.get("verdict") in JUDGED:
        return "judge"
    return "review" if row.get("verdict") == "kept" and (sprt.get("limit_kept") or {}).get("of") != "judged" else None


def saved(state: Path, exp_id: str) -> dict | None:
    record = online.read(state / "runs" / exp_id / FILE, None)
    return record if isinstance(record, dict) and record.get("verdict") else None


def trial_scores(sprt: dict) -> list[float]:
    scale = 1.0 if sprt.get("rotation") else 0.5  # a champion-only SPRT's per_map is pair points (driver.style_scores)
    return [v * scale for v in (sprt.get("per_map") or {}).values() if isinstance(v, (int, float))]


# The result's subgroups as {name: {trials, score}}: each map style (a near layout's plain and repearled maps pooled,
# meta.by_layout), the near, community and repearled maps pooled, each size band ("<=1024 tiles") and each opponent
# of the rotation or mix ("vs swarm"), as meta.map_buckets names them.
def buckets(sprt: dict) -> dict[str, dict]:
    (styles, repearl), areas = meta.by_layout(sprt.get("per_style") or {}), sprt.get("per_area") or {}
    found = dict(styles)
    for name, group in zip(("near maps", "community maps"), meta.near_groups(styles)):
        if len(group) > 1:
            found[name] = meta.pooled(group)
    if repearl:
        found["repearled maps"] = repearl
    found |= {f"{k} tiles": v for k, v in areas.items()}
    found |= {f"vs {k}": v for k, v in (sprt.get("per_opponent") or {}).items()}
    return {k: {"trials": v["trials"], "score": v["score"]} for k, v in found.items()
            if isinstance(v, dict) and v.get("trials") and isinstance(v.get("score"), (int, float))}


# A score over `n` trials against level (0.50) with per-trial SD `sd`: z, the one-sided chance of a gain this big or
# of a loss this big by luck alone, its normalized Elo (referee/sprt.py's elo_estimate) and the chance it is no worse
# than meta.NOLOSS_ELO0 (meta.noloss_probability's standard error).
def reading(score: float, n: int, sd: float) -> dict:
    z = (score - 0.5) / (sd / math.sqrt(n))
    nelo = (score - 0.5) / (math.sqrt(2) * sd) * meta.NELO_PER_T
    no_loss = chance.low_p((nelo - meta.NOLOSS_ELO0) / (meta.NELO_PER_T / math.sqrt(2 * n)))
    return {"z": round(z, 2), "p_gain": chance.low_p(-z), "p_worse": chance.low_p(z), "nelo": round(nelo, 1),
            "no_loss": round(no_loss, 3)}


# The sample SD of `scores`, or `fallback` for fewer than 3 or none spread.
def spread(scores: list[float], fallback: float) -> float:
    if len(scores) < 3:
        return fallback
    mean = sum(scores) / len(scores)
    return math.sqrt(sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)) or fallback


# The subgroups (as buckets names them) that per-trial row `trial` (driver.trial_rows) falls in: its style's layout
# (a repearled near map's under its layout's name, and "repearled maps"), "near maps" or "community maps", its size
# band and its opponent.
def trial_groups(trial: dict, community: set[str]) -> set[str]:
    names = {f"{trial['size']} tiles"} if trial.get("size") else set()
    if trial.get("opponent"):
        names.add(f"vs {trial['opponent']}")
    if style := trial.get("style"):
        layout = style.removesuffix(meta.REPEARL) if style.startswith("near_") else style
        names |= {layout, *(["repearled maps"] if layout != style else [])}
        if layout.startswith("near_"):
            names.add("community maps" if layout in community else "near maps")
    return names


# Every subgroup (buckets) with its reading and the reading of the trials outside it ("rest"), and the whole test's
# ({"trials", "score", "sd", "no_loss", ...}), from the SPRT's per-trial scores. Rest scores come from the totals.
# Each subgroup's and rest's per-trial SD comes from their own trials in `trials` (driver.trial_rows; a map whose
# file can't be read has no style or size there), else from the whole test's.
def subgroups(sprt: dict, trials: list[dict] = ()) -> tuple[dict[str, dict], dict]:
    scores = trial_scores(sprt)
    if not scores:
        return {}, {}
    n_all, mean = len(scores), sum(scores) / len(scores)
    sd = spread(scores, meta.TRIAL_SD)
    overall = {"trials": n_all, "score": round(mean, 4), "sd": round(sd, 4), **reading(mean, n_all, sd)}
    if (chance_ := meta.noloss_probability({**sprt, "elo0": meta.NOLOSS_ELO0})) is not None:
        overall["no_loss"] = round(chance_, 3)  # the SPRT's own pentanomial reading, where it has one
    community = meta.community_styles() if trials else set()
    groups = [(trial_groups(t, community), t["score"]) for t in trials if isinstance(t.get("score"), (int, float))]
    found = {}
    for name, b in buckets(sprt).items():
        n, score = min(b["trials"], n_all), b["score"]
        own = spread([s for names, s in groups if name in names], sd)
        row = {"trials": n, "score": round(score, 3), "sd": round(own, 4), **reading(score, n, own)}
        if n_all - n > 0:
            rest, others = (mean * n_all - score * n) / (n_all - n), [s for names, s in groups if name not in names]
            row["rest"] = {"trials": n_all - n, "score": round(rest, 3), **reading(rest, n_all - n, spread(others, sd))}
        found[name] = row
    return found, overall


# How a text names a subgroup (declared): its own name, or the words a person uses for it. "vs champion" and the
# eval "mix" maps are never named by text: every idea plays them.
ALIASES = {"portalrooms": r"portal[ -]?rooms?|portalrooms", "deadend": r"dead[ -]?ends?|deadend",
           "fortress": r"fortress(?:es)?", "macro": r"macro(?: maps?| boards?| starts?| style)",
           "trap": r"trap (?:maps?|boards?|style)", "worldgen": r"worldgen",
           "deathportal": r"death[ -]?portals?|deathportal", "vault": r"vaults?", "portalbet": r"portal[ -]?bets?",
           "sealedstart": r"sealed[ -]?starts?|sealedstart",
           "curves": r"curves (?:maps?|boards?|style)|curved (?:walls?|kelp)|diagonal (?:walls?|kelp)|taichis?",
           "<=1024 tiles": r"<=\s?1024|≤\s?1024|small (?:maps?|boards?)",
           "1025-2399 tiles": r"1025-2399|(?:big|large|middle-sized|mid-sized) (?:maps?|boards?)",
           ">=2400 tiles": r">=\s?2400|≥\s?2400|(?:big|large) (?:maps?|boards?)",
           "near maps": r"near (?:maps|variants)", "community maps": r"community maps?",
           "repearled maps": r"repearl\w*"}


def pattern(name: str) -> re.Pattern | None:
    if name in ALIASES:
        return re.compile(rf"(?<![\w<>=≤≥-])(?:{ALIASES[name]})(?![\w-])", re.I)
    if name in ("vs champion", "mix"):
        return None
    if name.startswith("vs "):
        return re.compile(rf"\b{re.escape(name[3:])}\b", re.I)
    if name.startswith("near_"):
        words = name[5:].replace("_", " ")
        if " " in words:  # "queen of spades", "big empty"
            return re.compile(rf"\b{re.escape(name)}\b|\b{re.escape(words)}\b", re.I)
        # A one-word map name (Portals, Devil, Default) is a common word too: its capitalised form or "<name> map".
        spoken = f"{re.escape(words.capitalize())}|{re.escape(words.upper())}"
        return re.compile(rf"\b(?i:{re.escape(name)})\b|\b(?:{spoken})\b|\b(?i:{re.escape(words)} maps?)\b")
    return re.compile(rf"\b{re.escape(name)}\b", re.I)


# The subgroups a map-mix style aims at.
def style_buckets(style: str) -> list[str]:
    return {"small": ["<=1024 tiles"], "big": ["1025-2399 tiles", ">=2400 tiles"], "near": ["near maps"],
            "sprint": ["near_dilemma", "near_autarky"], "maze": ["near_portal_labyrinth"]}.get(style, [style])


def excerpt(text: str, match: re.Match) -> str:
    return meta.flat(text[max(0, match.start() - 90):match.end() + 90])


# The subgroups declared or measured before the SPRT, from `row` alone (never the judge's answer), each with where it
# was said: {name: [source, ...]}. The frozen map mix (the idea's, its claim's from the idea's stated circumstance:
# driver.circumstance_mix, MISSES 90, or its coder's from shadow counts), the opponent mix, and each subgroup the
# idea's text at claim (title, hypothesis, notes) or the coder's final message (`coder`, driver.coder_text: written
# when coding ended, before any SPRT game) names. Text over-counts (a coder naming a style it didn't aim at): that only
# makes a keep's correction stricter.
def declared(row: dict, coder: str) -> dict[str, list[str]]:
    names = set(buckets(row.get("sprt") or {}))
    found: dict[str, list[str]] = {}

    def add(name: str, source: str) -> None:
        if name in names and source not in found.setdefault(name, []):
            found[name].append(source)

    policy = row.get("evaluation") or {}
    source = {"coder": "its coder's map mix, set before its SPRT from its shadow counts",
              "claim": "its claim's map mix, from its idea's stated circumstance"}.get(
        policy.get("maps_source"), "its idea's map mix, frozen at claim")
    ideas = policy.get("maps_idea") or {}  # an idea's own styles beside a coder's added one (driver.coder_declared)
    for style in meta.aimed(policy.get("maps")):
        for name in style_buckets(style):
            label = "its idea's map mix, frozen at claim" if style in ideas else source
            add(name, f"{label} ({style})")
    for opponent in policy.get("opponents") or {}:
        add(f"vs {opponent}", "its coder's opponent mix, set before its SPRT" if policy.get("opponents_source")
            == "coder" else "its idea's opponent mix, frozen at claim")
    for label, text in (("its idea's text at claim", idea_text(row.get("idea") or {})),
                        ("its coder's message before the SPRT", coder)):
        for name in sorted(names):
            if (found_pattern := pattern(name)) and (match := found_pattern.search(text or "")):
                add(name, f'{label}: "{excerpt(text, match)}"')
    return {name: found[name] for name in sorted(found) if found[name]}


# An idea's text as declared and the claim read it: title, hypothesis, history and notes (idea_parts, joined).
def idea_text(idea: dict) -> str:
    return " ".join(idea_parts(idea))


def idea_parts(idea: dict) -> list[str]:
    return [str(part) for part in (idea.get("title", ""), idea.get("hypothesis", ""), *(idea.get("history") or []),
                                   *(idea.get("notes") or []))]


AUTOMATIC = "automatic at claim (MISSES 90)"  # driver.circumstance_mix's own notes, which quote what they read


# The small or big style whose sources hold near layout `layout` (the referee's mapgen/gen.py sized_sources, loaded
# as advmaps.load_gen does), else None.
@functools.cache
def sized_style(layout: str) -> str | None:
    import advmaps
    gen = advmaps.load_gen()
    return next((size for size in ("small", "big") if layout in {p.stem for p in gen.sized_sources(size)}), None)


# The map-mix style (meta.MAP_STYLES) whose fresh maps sample subgroup `name` (a style, near layout or size band, as
# buckets names them) most: near_portals the portalrooms style, near_trauma sealedstart (Trauma's sealed start
# rooms, which every near copy keeps since 2026-09-30, gen.keeps_rooms), Portal Labyrinth's copies maze, the
# Sprint maps' sprint, another near layout its size's style, a size band its style, the near or repearled maps near;
# None for one no style samples (the eval mix, the community maps pooled, trap, and worldgen, general ground no aim
# can take: meta.GENERAL_STYLES). A near layout's own copies are about 1 in 30 of a near mix's maps, which is why a confirmation on one no
# longer plays near.
def family(name: str) -> str | None:
    special = {"near_portals": "portalrooms", "near_trauma": "sealedstart", "near_portal_labyrinth": "maze", "near_dilemma": "sprint", "near_autarky": "sprint",
               "<=1024 tiles": "small", "1025-2399 tiles": "big", ">=2400 tiles": "big", "near maps": "near",
               "repearled maps": "near"}
    if name in special:
        return special[name]
    if name in meta.MAP_STYLES and name != meta.WORLDGEN:
        return name
    return sized_style(name[5:]) if name.startswith("near_") else None


# The names stated_where reads in a declared circumstance or beside a count, stricter than pattern's (which may
# over-count: declared only makes a keep stricter), since these set a mix: {subgroup name: pattern}. A style only as
# "<style> maps" (or style, boards, variants, "-like"; "portal maps" and "dead-end maps" too), portalrooms and deadend
# also as their own tokens, a size band by its tiles ("2400+ tiles", ">=2400", "<=1024", "1024 tiles or fewer"), and a
# near layout of the maps near variants are made from by its near_<layout> token, as "<name> map(s)/copies/variants",
# or by its capitalised map name as a whole token ("Trauma", "Queen Of Spades"; not "Trauma-like"). A map named by a
# word that means something else (GENERIC_MAPS: "Default" steps, UNSW ladder teams) never counts bare, only in the
# forms before or after "official". Worldgen is general ground no aim can take, never named here; "death portal maps"
# (not deathportal's token) is neither deathportal's nor the "portal maps" of portalrooms.
GENERIC_MAPS = ("default", "unsw")
STYLE_WORDS = {"portalrooms": r"(?<!death )portal(?:[ -]?rooms?)?", "deadend": r"dead[ -]?end", "big": r"big|large",
               "sealedstart": r"sealed[ -]?start"}


@functools.cache
def strict_names() -> dict[str, re.Pattern]:
    suffix = r"(?:-like)? (?:maps?|styles?|boards?|variants)"
    found = {style: rf"(?i:(?:{STYLE_WORDS.get(style, style)}){suffix})"
             for style in meta.MAP_STYLES if style != meta.WORLDGEN}
    found["portalrooms"] += "|(?i:portalrooms)"
    found["deadend"] += "|(?i:deadend)"
    found.update({">=2400 tiles": r"(?:>=|≥) ?2400|2400\+|1025-2399", "<=1024 tiles":
                  r"(?:<=|≤) ?1024|(?i:1024 tiles or fewer|(?:at most|up to) 1024 tiles)",
                  "trap": r"(?i:trap (?:maps?|boards?|style))", "community maps": r"(?i:community maps?)",
                  "repearled maps": r"(?i:repearl\w* maps?)"})
    folders = [meta.COMMUNITY_MAPS.parent / name for name in ("ladder", "retired", "sprint", "community")]
    for layout in sorted({p.stem for folder in folders for p in folder.glob("*.map")}):
        words = layout.split("_")
        spoken = r"\s".join(re.escape(w) for w in words)
        title = r"\s".join("[Oo]f" if w == "of" else re.escape(w.capitalize()) for w in words)
        forms = [rf"(?i:near_{layout})", rf"(?i:{spoken}) (?i:maps?|cop(?:y|ies)|variants?)",
                 rf"(?i:official) {title}"]
        if layout not in GENERIC_MAPS:
            forms += [title, re.escape(layout.upper())] if len(words) == 1 else [title]
        found[f"near_{layout}"] = "|".join(forms)
    return {name: re.compile(rf"(?<![\w-])(?:{words})(?![\w-])") for name, words in found.items()}


# The subgroups (strict_names') named in `text`, in strict_names' order.
def named(text: str) -> list[str]:
    return [name for name, found in strict_names().items() if found.search(text)]


# A declared circumstance, as the orchestrator writes one ("Declared circumstance for the judge (before its SPRT):",
# "Circumstance for the judge, declared now:", "Measured circumstance:") and a judge's confirmation says it
# (driver.queue_confirm: "its circumstance is `near_trauma`"); what follows is read to its sentence's end or a
# semicolon, not an "e.g." or "i.e.".
DECLARED = re.compile(r"\bcircumstances?(?: for the judge)?(?: \([^()]{0,300}\))?(?:,[^:.;()]{0,60})?:\s*"
                      r"|\bits circumstance is ", re.I)
SENTENCE_END = re.compile(r"(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\bcf)\.(?=\s+[A-Z(`\"])")
# "60 of 71 on Portals/Trauma copies" (or "... were on ..."): a count, up to 4 words of its unit, and where most of it
# falls, up to the next bracket or sentence end. Only a count of changed decisions (COUNTED, in those words or earlier
# in its sentence) says where a change acts: not "24 of 38 games on Devil", "9 of 10 on the official Trophy map" (a
# score) nor "beat us 30 of 40 on Trauma".
STATED_ON = re.compile(r"(?<![\w.,-])(\d+) of (\d+)(?![\d,])((?: [\w-]+){0,4}?),? "
                       r"(?:(?:are|were|fall|fell|sit|sat|lie|lay) )?on ([^.;()]{1,80})")
COUNTED = re.compile(r"\b(?:decisions?|moves?|holds|firings?|vetoes|picks)\b", re.I)
NOT_COUNTED = re.compile(r"\b(?:games?|wins?|trials?|pairs?|maps?|points?|scores?|seeds?)\b", re.I)
SCORED = re.compile(r"\b(?:won|lost|drew|beat us|beat|scored|score|read|reads)\s*$", re.I)


# Whether STATED_ON's `match` in `text` counts changed decisions (COUNTED): its unit words say so, or, with none, its
# sentence before it names them and the count isn't a score (SCORED).
def counts_decisions(text: str, match: re.Match) -> bool:
    if unit := match.group(3).strip():
        return bool(COUNTED.search(unit)) and not NOT_COUNTED.search(unit)
    before = text[:match.start()]
    ends = [end.end() for end in SENTENCE_END.finditer(before)]
    return bool(COUNTED.search(before[ends[-1] if ends else 0:])) and not SCORED.search(before)


# Where an idea's text (idea_parts, as the judge reads it, each part apart and the claim's own notes, AUTOMATIC, left
# out) says its change acts, before any game (MISSES 90): (a) a circumstance it declares (DECLARED: "Circumstance for
# the judge: portal maps (...)") naming map styles or layouts (strict_names), else (b) a count of changed decisions it
# states with most of it on named ones ("60 of 71 on Portals/Trauma copies", more than half at chance.sign_p <=
# chance.TRIGGER_P). Returns (styles, what said so, unsampled): each name's family (styles_for), and the names no style
# samples. ([], "", []) when it says neither.
def stated_where(idea: dict) -> tuple[list[str], str, list[str]]:
    parts = [part for part in idea_parts(idea) if AUTOMATIC not in part]
    for text in parts:
        for match in DECLARED.finditer(text):
            span = text[match.end():match.end() + 400]
            span = span[:end.start()] if (end := SENTENCE_END.search(span)) else span
            if names := named(span := span.split(";")[0]):
                said = meta.flat(text[match.start():match.end()] + span)[:200]
                return styles_for(names, f'its declared circumstance: "{said}"')
    for text in parts:
        for match in STATED_ON.finditer(text):
            hits, n = int(match.group(1)), int(match.group(2))
            if hits <= n and 2 * hits > n and chance.sign_p(hits, n - hits) <= chance.TRIGGER_P \
                    and counts_decisions(text, match) and (names := named(match.group(4))):
                return styles_for(names, f'its stated count: "{meta.flat(match.group(0))[:200]}" (sign p '
                                         f'{chance.sign_p(hits, n - hits):.2g})')
    return [], "", []


# The aimed map mix that samples `styles` (stated_where): one style at meta.MAP_SHARE's top share, two at its bottom
# each (together the top), else None: a third can't have a readable share (meta.map_mix_ok).
def aimed_mix(styles: list[str]) -> dict | None:
    if len(styles) == 1:
        return {styles[0]: meta.MAP_SHARE[1]}
    return {style: meta.MAP_SHARE[0] for style in styles} if len(styles) == 2 else None


# The styles `names` point at (stated_where), what said so, and the names no style samples. Each name's family, as
# confirm_mix plays it, except that the portal-room maps are one family (sig's portal rule, 2026-09-29): with
# Portals' copies or portalrooms named, Portal Labyrinth's maze folds into portalrooms; named alone it stays maze.
# Trauma's copies are sealedstart's, a style of their own.
def styles_for(names: list[str], source: str) -> tuple[list[str], str, list[str]]:
    styles = list(dict.fromkeys(style for name in names if (style := family(name))))
    if "portalrooms" in styles:
        styles = [s for s in styles if s not in ("maze", "portalrooms")] + ["portalrooms"]
    return styles, source, [name for name in names if not family(name)]


def crashed(sprt: dict) -> bool:
    return any(game.get("a") is None or any((game.get("a") or {}).get(k, 0) for k in ("crashes", "timeouts"))
               for game in sprt.get("games", []))


# What a check said when an SPRT (`sprt`, its sprt.json) found the candidate not repeating its own games (referee/
# sprt.py's candidate_nondeterministic: its report's "blamed", read here without the referee), or None: a hard gate
# like a crash (MISSES 86), which driver.evaluate discards and no keep overrides. A report from before "blamed" (29
# Sep) blamed the candidate when it, but not the champion, was flagged.
def nondeterministic(sprt: dict) -> str | None:
    found = sprt.get("nondeterministic") or {}
    if isinstance(found, dict) and "blamed" in found:
        return str(found["blamed"])[:300] if found["blamed"] else None
    if not isinstance(found, dict) or not found.get("cand") or found.get("base"):
        return None
    entry = (found.get("bots") or {}).get(found["cand"])
    said = entry.get("said") if isinstance(entry, dict) else None
    return str(said or "a check found it")[:300]


# Whether a keep of `row` on subgroup `name` applies by itself, as (None, ""), or not: ("discard", why) for a hard gate
# it failed (a crash, a timeout, nondeterminism, its CPU gate or tests), else ("ask", what the guard missed) for the
# orchestrator to decide (the module docstring's rule). The guard: the subgroup declared before the SPRT, its gain there past P
# corrected over the declared subgroups, no loss overall and outside it at NO_LOSS, the rest's z at least -1 and the
# overall score above level, and for a variant sweep's idea its non-champion seats not below level (self_play_only).
def keep_problems(row: dict, name: str | None, groups: dict, overall: dict,
                  known: dict[str, list[str]]) -> tuple[str | None, str]:
    sprt = row.get("sprt") or {}
    if crashed(sprt) or nondeterministic(sprt) or not ((row.get("cpu") or {}).get("ok")
                                                       and (row.get("bot_tests") or {}).get("ok")):
        return "discard", ("a keep needs every gate passed: it crashed, timed out, didn't repeat its own games or "
                           "failed its CPU gate or tests")
    misses, group = [], groups.get(name) if name else None
    if not name:
        misses.append("it names no subgroup it helps")
    elif name not in known:
        said = ", ".join(known) or "nothing"
        misses.append(f"post hoc: {name} was neither declared nor measured before the SPRT (declared: {said})")
    elif group["trials"] < meta.SPLIT_MIN_TRIALS or group["p_gain"] * len(known) > P:
        misses.append(f"its gain on {name} isn't clear: {group['score']:.3f} over {group['trials']} trials, one-sided "
                      f"p {group['p_gain']:.2g} x {len(known)} declared subgroups = "
                      f"{group['p_gain'] * len(known):.2g}, above {P}")
    if not overall or overall["no_loss"] < NO_LOSS:
        misses.append(f"overall not shown no worse than {meta.NOLOSS_ELO0:+d} normalized Elo: "
                      f"{overall['no_loss']:.0%}" if overall else "no per-trial scores")
    if rest := (group or {}).get("rest"):
        where = f"outside {name} ({rest['trials']} trials, {rest['score']:.3f}, nElo {rest['nelo']:+.1f})"
        if rest["no_loss"] < NO_LOSS:
            misses.append(f"{where} not shown no worse than {meta.NOLOSS_ELO0:+d}: {rest['no_loss']:.0%} (the guard "
                          f"needs {NO_LOSS:.0%})")
        elif rest["z"] < -1:
            misses.append(f"{where} reads as a loss beyond its own noise (z {rest['z']:+.2f}; the guard needs -1 or "
                          "more)")
    if overall and overall["score"] <= 0.5:
        misses.append(f"overall {overall['score']:.4f} over {overall['trials']} trials is not above level")
    if miss := self_play_only(row, groups):
        misses.append(miss)
    return ("ask", "; ".join(misses)) if misses else (None, "")


# What a variant sweep's idea (source "sweep:", sweep.proposal) lacks for a keep, or None: the sweep plays the
# champion against itself on eval maps, so its hint measures the value against our own code (method M82: E0300's sweep
# 0.65 held only in its SPRT's champion seat, 0.584 over 83 trials, 0.49-0.52 in the other five), and its SPRT's other
# seats pooled (the rest of "vs champion", from `groups`: subgroups) must not read below level.
def self_play_only(row: dict, groups: dict) -> str | None:
    if not str((row.get("idea") or {}).get("source") or "").startswith("sweep:"):
        return None
    seats = (groups.get("vs champion") or {}).get("rest")
    if not seats:
        return ("a variant sweep's hint was self-play (M82), and this result has no seats but the champion's to read "
                "apart")
    if seats["score"] < 0.5:
        return (f"a variant sweep's hint was self-play (M82): outside the champion seat it reads {seats['score']:.3f} "
                f"over {seats['trials']} trials, below level")
    return None


# The evaluation mix a confirmation of `row` on subgroup `name` plays (driver.queue_confirm): its family's fresh maps
# (`family`: near_trauma's the sealedstart style, not near maps, where one layout is about 1 in 30) at
# meta.MAP_SHARE's top share, or that opponent's mix entry; else the result's own aimed mixes (its frozen ones), so a
# confirmation without a subgroup, or on one no style samples, is a plain retest. {} for general maps.
def confirm_mix(row: dict, name: str | None) -> dict:
    kind, share = (row.get("idea") or {}).get("kind"), meta.MAP_SHARE[1]
    style = family(name) if name else None
    if style and meta.map_mix_ok({style: share}, kind):
        return {"maps": {style: share}}
    if name and name.startswith("vs ") and meta.opponent_mix_ok({name[3:]: share}, kind):
        return {"opponents": {name[3:]: share}}
    policy = row.get("evaluation") or {}
    mixes = {"maps": meta.aimed(policy.get("maps")), "opponents": policy.get("opponents")}
    return {key: mix for key, mix in mixes.items() if mix}


# The judge's answer (verdict.json in its folder) checked against `row` (the module docstring's rule), as the record
# driver.apply_judged saves: {"asked", "verdict", "reason", "circumstance", "declared", "why" (when the check changed
# the verdict or dropped the subgroup), "stats" (the subgroup's reading), "overall", "confirm" (a confirmation's mix),
# "answer"}, from `row`'s per-trial rows `trials` (driver.trial_rows). Raises ValueError for an answer that isn't one:
# no verdict of its kind, no reason.
def check(row: dict, answer: object, coder: str, trials: list[dict] = ()) -> dict:
    kind = owed(row) or ("review" if row.get("verdict") == "kept" else "judge")
    if not isinstance(answer, dict) or answer.get("verdict") not in VERDICTS[kind]:
        raise ValueError(f"verdict.json needs \"verdict\": one of {', '.join(VERDICTS[kind])}")
    reason = meta.flat(answer.get("reason") or "")[:400]
    if not reason:
        raise ValueError("verdict.json needs a one-line \"reason\"")
    name = answer.get("circumstance")
    name = name if isinstance(name, str) and name.strip() else None
    groups, overall = subgroups(row.get("sprt") or {}, trials)
    known = declared(row, coder)
    record = {"asked": answer["verdict"], "verdict": answer["verdict"], "reason": reason, "circumstance": name,
              "declared": {k: v[:3] for k, v in known.items()}, "overall": overall,
              "answer": answer if len(json.dumps(answer)) <= 20000 else
              {k: answer[k] for k in ("verdict", "reason", "circumstance") if k in answer}}
    if name and name not in groups:
        record.update(circumstance=None, why=f"names {name!r}, not a subgroup of this result")
        name = None
    if name:
        record["stats"] = groups[name]
    if record["verdict"] == "keep":
        downgrade, why = keep_problems(row, name, groups, overall, known)
        record["looks"] = len(known)
        if downgrade:
            record.update(verdict=downgrade, why=why)
    if record["verdict"] == "confirm":
        record.update(as_confirm(row, record, ""))
    return record


# The fields that make judge record `record` of `row` a confirm (`why`: what made it one, joined to any earlier why),
# or a discard when no confirmation can be queued: `row` is a judged confirmation already (its idea's source is
# judge:E####: one a candidate; driver.judged_before refuses the rest of its lineage) or has no candidate.
def as_confirm(row: dict, record: dict, why: str) -> dict:
    whys = "; ".join(w for w in (record.get("why"), why) if w)
    if str((row.get("idea") or {}).get("source") or "").startswith("judge:"):
        return {"verdict": "discard", "why": "; ".join(w for w in (whys, "it is a judged confirmation already: one a "
                                                                          "candidate") if w)}
    if not row.get("candidate"):
        return {"verdict": "discard", "why": "; ".join(w for w in (whys, "no candidate to confirm") if w)}
    return {"verdict": "confirm", "confirm": confirm_mix(row, record.get("circumstance")),
            **({"why": whys} if whys else {})}


# The judged record's one line for alerts, results and prompts: "keep on portalrooms (0.560 over 1153 trials, p
# 1e-05): <reason>", with the check's change of verdict when it made one; an ask's alert is ask_line.
def line(record: dict) -> str:
    stats = record.get("stats")
    where = (f" on {record['circumstance']}" + (f" ({stats['score']:.3f} over {stats['trials']} trials, p "
                                                  f"{stats['p_gain']:.2g})" if stats else "")
             if record.get("circumstance") else "")
    changed = (f" (asked {record['asked']}: {record['why']})" if record["asked"] != record["verdict"] else
               f" ({record['why']})" if record.get("why") else "")
    return f"{record['verdict']}{where}{changed}: {record['reason']}"


# The alert for an ask on `exp_id`: "JUDGED ask: E#### keep (<reason>; guard: <what it missed>)" and the commands
# that decide it.
def ask_line(exp_id: str, record: dict) -> str:
    return (f"JUDGED ask: {exp_id} keep{' on ' + record['circumstance'] if record.get('circumstance') else ''} "
            f"({record['reason']}; guard: {record.get('why')}); decide with uv run loop/driver.py --judge-keep "
            f"{exp_id} --rule '<why>' or --judge-confirm {exp_id} --rule '<why>'")


# The judge's input files for `row`, as {name: text}: the result without its per-game rows, its SPRT summary and
# per-trial rows (`trials`, driver.trial_rows), the opponent screen, the subgroups' readings and what was declared
# before the SPRT, the coder's message, the candidate's diff, and the official-map report and the live version's
# online per-map records when there are any, and the candidate v champion behaviour stats of its SPRT's games
# (loop/evalstats.py) when it has them.
def inputs(state: Path, row: dict, coder: str, trials: list[dict]) -> dict[str, str]:
    run = state / "runs" / row["id"]
    sprt = row.get("sprt") or {}
    groups, overall = subgroups(sprt, trials)
    known = declared(row, coder)
    files = {
        "result.json": json.dumps({k: v for k, v in row.items() if k not in ("sprt", "session", "behaviour")}
                                  | {"coder_summary": (row.get("session") or {}).get("summary")}, indent=1),
        "sprt.json": json.dumps({k: v for k, v in sprt.items() if k not in ("games", "per_map", "predict", "hosts")},
                                indent=1),
        "trials.jsonl": "".join(json.dumps(t) + "\n" for t in trials),
        "subgroups.json": json.dumps({"overall": overall, "declared_count": len(known), "p_for_keep": P,
                                      "no_loss_for_keep": NO_LOSS, "noloss_elo0": meta.NOLOSS_ELO0,
                                      "subgroups": {k: {**v, "declared": k in known} for k, v in groups.items()}},
                                     indent=1),
        "declared.json": json.dumps(known, indent=1),
        "coder.md": coder or "(no coder message)",
        "screen.json": json.dumps(row.get("pool") or {}, indent=1),
    }
    if row.get("behaviour"):
        import evalstats
        files["behaviour.md"] = evalstats.markdown(row["behaviour"])
    for name in ("diff.patch", "target-units.jsonl", "official.json"):
        path = run / name
        if path.is_file() and not path.is_symlink():
            files[name] = path.read_text(errors="replace")[:400_000]
    report = online.read(state / "online.json", {})
    if report.get("per_map"):
        files["online-per-map.jsonl"] = "".join(json.dumps(t) + "\n" for t in online.map_totals(report["per_map"]))
    return files


class _Keep(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# The judge's prompt: loop/JUDGE.md filled for `row`.
def prompt(row: dict, files: dict[str, str], work: Path) -> str:
    idea = row.get("idea") or {}
    return PROMPT.read_text().format_map(_Keep(
        id=row["id"], idea_id=idea.get("id", ""), title=meta.flat(idea.get("title", "")), work=str(work),
        verdict=row.get("verdict"), reason=meta.flat(row.get("reason", "")), kind=owed(row) or "judge",
        verdicts=", ".join(f"`{v}`" for v in VERDICTS[owed(row) or "judge"]), files=", ".join(f"`{n}`" for n in files),
        p=P, no_loss=f"{NO_LOSS:.0%}", elo0=meta.NOLOSS_ELO0, generalise=meta.GENERALISE, trigger_p=chance.TRIGGER_P))


def folder(state: Path, exp_id: str) -> Path:
    return state / "judge" / exp_id


# Makes `row`'s judge folder afresh with `files` (inputs) and its prompt in the protected folder; returns the folder.
# The Claude driver's judge makes it under the judge lock (launch); a native session's, which launches nothing, in
# native.judge_task.
def prepare(state: Path, row: dict, files: dict[str, str]) -> Path:
    work = folder(state, row["id"])
    remove(work)
    work.mkdir(parents=True)
    for name, text in files.items():
        (work / name).write_text(text)
    (meta.protected_io(state, "judge", work) / "PROMPT.md").write_text(prompt(row, files, work))
    return work


def remove(work: Path) -> None:
    if work.is_dir() and not work.is_symlink():
        meta.unlock(work)
        shutil.rmtree(work, ignore_errors=True)
    elif work.exists() or work.is_symlink():
        work.unlink()


# Waits for the judge lock (<state>/judge.lock, meta.locked's file) until `deadline` (time.monotonic), then makes
# `row`'s folder from `files` (prepare) and runs its judge there to its end or the deadline (then stops its process
# group), one judge at a time. Returns (the folder, None), or (the folder, why it failed: the lock still held at the
# deadline, which makes no folder, a launch error, a session error such as a usage limit, a timeout).
def launch(state: Path, row: dict, files: dict[str, str], popen: Callable = subprocess.Popen,
           deadline: float | None = None) -> tuple[Path, str | None]:
    work, deadline = folder(state, row["id"]), deadline or time.monotonic() + TIMEOUT
    with open(state / "judge.lock", "w") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return work, (f"waited its whole {TIMEOUT // 60} min cap for another judge to finish; it stays "
                                  "owed")
                time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        work = prepare(state, row, files)
        io = meta.protected_io(state, "judge", work)
        meta.unlock(work)
        settings = meta.worker_settings(state, "judge", work)
        command = meta.in_folder(work, meta.private_tmp(meta.worker_command(work, settings, (), TOOLS, usage="judge")))
        meta.brief(state, work, io)
        stdin, out, err = meta.worker_io(io)
        try:
            proc = popen(command, cwd=io, env=meta.worker_env(), stdin=stdin, stdout=out, stderr=err,
                         start_new_session=True)
        except OSError as error:
            return work, f"could not launch: {type(error).__name__}: {error}"
        finally:
            for fd in (stdin, out, err):
                os.close(fd)
        try:
            proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            reports.stop(proc.pid)
            return work, f"ran past its {TIMEOUT // 60} min cap (its wait for the lock included) and was stopped"
        return work, meta.session_error(io)


# The answer in `work`, read without following a link (meta.worker_file). Raises OSError or ValueError.
def answer(work: Path) -> object:
    meta.unlock(work)
    return json.loads(meta.worker_file(work / ANSWER))


def save(state: Path, exp_id: str, record: dict) -> None:
    online.save(state / "runs" / exp_id / FILE, {**record, "at": record.get("at") or time.time()})
    (state / "runs" / exp_id / PENDING).unlink(missing_ok=True)


# A checked record (check's) a stop marker kept from being applied: saved, so the next run applies it without a new
# judge (driver.run_judge), and read back, or None.
def hold(state: Path, exp_id: str, record: dict) -> None:
    online.save(state / "runs" / exp_id / PENDING, record)


def held(state: Path, exp_id: str) -> dict | None:
    record = online.read(state / "runs" / exp_id / PENDING, None)
    return record if isinstance(record, dict) and record.get("verdict") else None

"""Rule counts for reviews (loop/rulecounts.py, M17). The sample is rulecounts.extract's output for the champion's
logging copy (f9f3bd7, debugIndicator on) in two real games on one fresh eval map, both sides, played 2026-09-26 on the
game hosts: every turn of rounds 0-12 plus every head-on, sprint, override and longer split, each with its real LOG
line. The round-500 sample (M26) is rulecounts.extract's longest, deaths and logs for champion 2c79f81's logging copy
(side B, a win) in rule-games/1790404915361643000/replays/champion/0001-gen_eval_188302895-aB.replay: every round's
longest, its deaths of length 6+, and every log line of rounds 0, 100, 200, 300, 396, 397, 450, 470, 471 and 499.
The king sample (MISSES 32) is rulecounts.extract's longest and king for E0117's kept loss
0378-gen_eval_210081076-aA (the candidate on A, champion 7e819b1 on B), the game its post-mortem cites for the
champion's king growing on merged 2-long children."""

import testhome  # before anything that reads live paths
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import driver
import health
import meta
import rulecounts

TESTDATA = Path(__file__).resolve().parent / "testdata"
ENDGAME = json.loads((TESTDATA / "rulecounts-endgame-sample.json").read_text())
KING = {**json.loads((TESTDATA / "rulecounts-king-sample.json").read_text()), "deaths": [], "logs": []}
# The census sample's games, with the round-500 sample's longest and deaths (which the census doesn't read).
SAMPLE = [dict(game, longest=ENDGAME["longest"], deaths=ENDGAME["deaths"])
          for game in json.loads((TESTDATA / "rulecounts-sample.json").read_text())]
REPO = Path(__file__).resolve().parent.parent
TROPHY = REPO / "tools" / "testdata" / "trophy-155732.replay"  # a real ranked game, side B eliminated in round 133
FOLD = REPO / "tools" / "testdata" / "fold-e0102-0308.replay"
TRAUMA = REPO / "tools" / "testdata" / "trauma-95034.replay"  # a real ranked game to r499
QUEEN = REPO / "tools" / "testdata" / "queen-95037.replay"  # online game 95037, our 3182 on B, Queen Of Spades
ROUNDS = (397, 471, "foldRound 397 from its params.hpp and mergeRound 471 from its params.hpp")


def lines(games: list[dict]) -> dict:
    return {(r, i): rulecounts.parse_log(text) for g in games for r, i, text in g["logs"]}


# A state folder with a pending review folder in it, as prepare_review leaves them.
def review_folder(directory: str) -> tuple[Path, Path]:
    state = Path(directory) / "state"
    work = state / "meta" / "1790376394214454000"
    work.mkdir(parents=True)
    (work / "PROMPT.md").write_text("review")
    (state / "review.json").write_text(json.dumps({"pending": str(work)}))
    return state, work


class TestCensus(unittest.TestCase):
    def test_every_real_turn_is_matched_with_its_log_line_and_classified(self):
        logged = lines(SAMPLE)
        counts = rulecounts.census(SAMPLE)
        turns = sum(len(g["turns"]) for g in SAMPLE)
        # The engine's dragonLog and dragonAction for a turn share its round and dragon id.
        self.assertNotIn("% of turns: single step, no log line", counts)
        ram, sprint_ram, sprint, split, best, other = rulecounts.BRANCHES
        expected = dict.fromkeys(rulecounts.BRANCHES, 0)
        for g in SAMPLE:
            rams = {tuple(h) for h in g["head_ons"]}
            for r, i, action, _ in g["turns"]:
                if action.startswith("split"):
                    expected[split] += 1
                elif len(action) > 1:
                    expected[sprint_ram if (r, i) in rams else sprint] += 1
                elif (r, i) in rams:
                    expected[ram] += 1  # a one-step ram, whichever branch chose it
                else:
                    expected[best if logged[(r, i)]["dir"] == action else other] += 1
        self.assertTrue(all(expected.values()), expected)  # the sample has every branch
        for name, count in expected.items():
            self.assertAlmostEqual(counts[f"% of turns: {name}"], 100 * count / turns)

    def test_real_log_fields_give_targets_space_and_score_bands(self):
        logged = list(lines(SAMPLE).values())
        counts = rulecounts.census(SAMPLE)
        exploring = sum(f["target"] == "-1" for f in logged)
        self.assertTrue(0 < exploring < len(logged))
        self.assertAlmostEqual(counts["% of logged turns: no pearl target (exploring)"], 100 * exploring / len(logged))
        doomed = sum(float(f["score"]) <= -1e9 for f in logged)
        self.assertAlmostEqual(counts[f"% of logged turns: best move scored {rulecounts.DOOMED}"],
                               100 * doomed / len(logged))
        children = [int(a.split()[1]) for g in SAMPLE for _, _, a, _ in g["turns"] if a.startswith("split ")]
        self.assertAlmostEqual(counts["splits a game, child length 1-2"], sum(c <= 2 for c in children) / len(SAMPLE))
        self.assertAlmostEqual(counts["splits a game, child length 5-8"],
                               sum(5 <= c <= 8 for c in children) / len(SAMPLE))

    def test_a_label_a_later_champion_adds_to_its_log_line_is_tallied(self):
        game = json.loads(json.dumps(SAMPLE[0]))
        game["logs"] = [[r, i, f"{text} rule {'ram' if k % 4 == 0 else 'target'}"]
                        for k, (r, i, text) in enumerate(game["logs"])]
        counts = rulecounts.census([game])
        rams = sum(1 for k in range(len(game["logs"])) if k % 4 == 0)
        self.assertAlmostEqual(counts["% of logged turns: rule ram"], 100 * rams / len(game["logs"]))
        self.assertIn("% of logged turns: rule target", counts)
        self.assertNotIn("% of logged turns: score 20", counts)  # numeric fields aren't labels

    def test_report_has_a_column_per_opponent_and_all(self):
        text = rulecounts.report("f9f3bd7ce45d", {"champion": SAMPLE[:1], "swarm": SAMPLE[1:], "breeder": []},
                                 ["no games against breeder: test"], [], True, ROUNDS)
        self.assertIn("| measure | vs champion | vs swarm | all |", text)
        self.assertIn("| games | 1 | 1 | 2 |", text)
        self.assertIn("no games against breeder", text)


    # MISSES 64: how each game's deaths came about (tools/watch.py doom), ours against the opponent's, for all games
    # and each size band; a game extracted before the table had none and is left out of it.
    def test_the_report_has_the_death_table_for_all_games_and_each_size(self):
        facts = rulecounts.extract(QUEEN, "B")
        doom = facts["doom"]
        self.assertEqual(sum(doom["ours"].values()), len(facts["deaths"]))  # each death of ours once
        games = [dict(SAMPLE[0], doom=doom, size="<=1024"), dict(SAMPLE[1], size="<=1024")]
        text = rulecounts.report("f9f3bd7ce45d", {"champion": games[:1], "swarm": games[1:]}, [], [], True, ROUNDS)
        section = text.split("## How deaths came about (MISSES 64)")[1]
        self.assertIn("all: 1 games", section)
        self.assertIn("<=1024 tiles: 1 games", section)

    # M57: the death table labels both sides by moves alone, since only our copy logs a debug line: a death our log
    # calls a feed stays in its moves kind, as the opponent's feeds do (before, feed 50.2 v 0.0 a game read as ours).
    def test_the_death_table_labels_both_sides_by_moves_alone(self):
        import watch
        feed = (129, 20)  # our open-teammate death in QUEEN, as if its debug line said rule feed
        with patch.object(watch, "deliberate_deaths", return_value={feed: "feed"}):
            facts = rulecounts.extract(QUEEN, "B")
        doom = facts["doom"]
        self.assertNotIn("feed", doom["ours"])
        # Two of the three open-teammate deaths were from the fold on (r401, r420: into its own body beside a
        # longer teammate), which the moves label as feeds on either side (M63).
        self.assertEqual((doom["ours"]["open teammate"], doom["ours"]["feed?"], doom["theirs"]["feed?"]), (1, 2, 1))
        self.assertEqual(sum(doom["ours"].values()), len(facts["deaths"]))
        # The debug line checks the moves-only `feed?` label (M63): the patched r129 feed is before the fold, unlabelled.
        self.assertEqual(facts["feed_check"], [1, 0, 2])
        text = " ".join(rulecounts.doom_section({"all": [facts, facts]}))
        self.assertIn("it labelled 0 of our 2 feeds (a feed, capfeed or recycle rule in any round; it labels only "
                      "from foldRound on)", text)
        self.assertIn("and 4 of its 4 labels of ours were no feed (a crash beside a longer teammate)", text)
        # M63: the line after the checks files our deaths with the debug lines' deliberate ones first.
        self.assertEqual((facts["doom_ours"]["feed"], facts["doom_ours"].get("open teammate", 0)), (1, 0))
        self.assertEqual(sum(facts["doom_ours"].values()), len(facts["deaths"]))
        self.assertIn("feed 1.00", text.split("deliberate ones filed first")[1])
        # M93: teammate traps by shutter, each side's; theirs (A) are watch's tested ones (21 by 22's split, 9 after 6's).
        self.assertEqual(facts["shutters"], {"ours": [5, 5, 0, 0], "theirs": [16, 16, 1, 2]})
        self.assertIn("theirs 32, 32 by one teammate alone, whose shutting move was a split 2 (6%) or came the turn "
                      "after its split 4 (12%)", text)

    # M63: before foldRound our cap feeds read as `open never hitSelf`, so the debug line's cap rule (capfeed or
    # recycle) checks the moves-only `capfeed?` label, as feed_check does `feed?`: [our cap deaths before foldRound,
    # of them labelled, our labels].
    def test_capfeed_check_counts_our_cap_deaths_before_the_fold_against_the_label(self):
        import watch
        from types import SimpleNamespace

        def doom(r, i, team="A", mate=True, cap=True):
            return watch.Doom(r, i, team, 3, "hitSelf", "open", None, (), None, "never", None, beside_mate=mate,
                              at_cap=cap)
        dooms = [doom(100, 1), doom(101, 2, cap=False), doom(102, 3), doom(103, 4), doom(400, 5), doom(104, 6, "B")]
        logs = {(100, 1): {"rule": "capfeed"}, (101, 2): {"rule": "recycle"}, (103, 4): {"rule": "move"},
                (400, 5): {"rule": "capfeed"}, (104, 6): {"rule": "capfeed"}}
        # r100 labelled, r101 not (off the ceiling); r102 and r103 labelled with no cap rule; r400 is from the fold
        # on (`feed?`); r104 is theirs.
        self.assertEqual(rulecounts.capfeed_check(SimpleNamespace(dooms=dooms), "A", logs, 397), [2, 1, 3])
        facts = [dict(SAMPLE[0], doom={"ours": {"capfeed?": 3}, "theirs": {}}, capfeed_check=[2, 1, 3])]
        self.assertIn("`capfeed?`: it labelled 1 of our 2 deaths at the cap before foldRound (capfeed or recycle), "
                      "and 2 of its 3 labels", " ".join(rulecounts.doom_section({"all": facts})))

    # M71: cap rules (the cap feed, the seed's cap clause) act only within CAP_MARGIN of the unit limit, where local
    # games sit far more than online ones, so the census gives each team's share of rounds there, by size too.
    def test_each_teams_share_of_rounds_at_the_ceiling_is_a_census_row_by_size(self):
        import replays
        games = [dict(SAMPLE[0], cap={"ours": 37.5, "theirs": 0.0}, map="m1", size="<=1024"),
                 dict(SAMPLE[1], cap={"ours": 0.0, "theirs": 10.0}, map="m2", size=">=2400")]
        row = f"% of rounds within {replays.CAP_MARGIN} of the unit limit (ours)"
        text = rulecounts.report("f9f3bd7ce45d", {"champion": games}, [], [], True, ROUNDS)
        line = next(line for line in text.splitlines() if line.startswith(f"| {row} |"))
        self.assertIn("37.5", line)  # the <=1024 tiles column: its one game

    # M67: `open deeper` mixed a move the scorer rated closed and took anyway with one rated open only through edges
    # it never saw. Real deaths of champion 0e7c458's logging copy from review 1790576608098366000's kept replays
    # (testdata/deeper-deaths-sample.json), each with its debug line at doomed_since - 1: two of each open class, a
    # pocket one of two, and a split-out and a feed? whose trigger was deeper too (other rows of the table).
    def test_our_deeper_rows_split_by_how_the_scorer_rated_the_dooming_move(self):
        import watch
        cases = json.loads((TESTDATA / "deeper-deaths-sample.json").read_text())
        for case in cases:
            d = watch.Doom(**{**case["doom"], "exits": tuple(map(tuple, case["doom"]["exits"]))})
            logs = {(d.doomed_since - 1, d.id): rulecounts.parse_log(case["line"])}
            split = rulecounts.deeper_split([d], case["side"], logs, 397)
            gap = float(rulecounts.parse_log(case["line"])["gap"])
            runner = f"{case['class']}: runner-up {'penalised' if gap >= rulecounts.SHORTFALL else 'close'}"  # M81
            self.assertEqual(split, {} if case["class"] in ("split-out", "feed?") else {case["class"]: 1, runner: 1},
                             case["line"])
            if split:
                self.assertEqual(rulecounts.deeper_split([d], "B" if case["side"] == "A" else "A", logs, 397), {})
                row = " ".join(case["class"].split()[:2])  # "open deeper" or "pocket deeper"
                self.assertEqual(rulecounts.deeper_split([d], case["side"], {}, 397), {f"{row} no line": 1})
        self.assertEqual(Counter(c["class"] for c in cases)["open deeper open known"], 2)
        # M81: 5 of the 8 had a runner-up a tile's shortfall or more below, 3 lethal (gap about 1e12) and 2 about 3e6
        # below: the closed one three tiles short or ram risk or a teammate's exit (3e6 each), the open known one's
        # 3.2004e6 ram risk or a teammate's exit plus a head-reach cut (2e5), which the gap can't tell apart; 3 had one
        # within 400 of the move, a choice or a pocket as short (their `closed` flags are 0, so choices here).
        rows = Counter()
        for case in cases:
            d = watch.Doom(**{**case["doom"], "exits": tuple(map(tuple, case["doom"]["exits"]))})
            rows.update(rulecounts.deeper_split([d], case["side"], {(d.doomed_since - 1, d.id):
                                                                    rulecounts.parse_log(case["line"])}, 397))
        self.assertEqual((sum(v for k, v in rows.items() if k.endswith(": runner-up penalised")),
                          sum(v for k, v in rows.items() if k.endswith(": runner-up close"))), (5, 3))
        facts = [dict(SAMPLE[0], doom={"ours": {"open deeper": 3}, "theirs": {}},
                      deeper={"open deeper closed": 2, "open deeper open unseen": 1})]
        text = " ".join(rulecounts.doom_section({"all": facts}))
        self.assertIn("over all games: open deeper: closed 2, open unseen 1.", text)

    # M81: a room is cut at kelp and portals, so a pocket's room is the pocket: at since, our other heads in it, never
    # the enemy's or the dying one's (Queen Of Spades' portal pocket 6,31 held 0-3 at the 9 deeper dooms there).
    def test_our_heads_in_a_pocket_deaths_room_are_our_other_heads_in_that_pocket(self):
        import watch
        w = watch.watch(QUEEN, doom=True)
        pocket_of = watch.pockets(w.board, watch.step_table(w.board))
        dooms = [d for d in w.dooms if d.place == "pocket" and d.doomed_since is not None
                 and watch.death_kind(d, None, 397).endswith(" deeper")]
        heads = [sum(team == d.team and i != d.id and pocket_of.get(body[0], ("",))[0] == d.pocket
                     for i, (team, body) in w.frames[d.doomed_since].dragons.items()) for d in dooms]
        self.assertEqual([rulecounts.room_heads(w, d, {}) for d in dooms], heads)
        self.assertEqual(sorted(heads), [0, 1, 1, 2, 2, 2, 2, 2, 3])
        split = rulecounts.deeper_split(dooms, "A", {}, 397, w)
        self.assertEqual(split["pocket deeper no line: heads 1-2"], 7)

    def test_the_median_over_maps_sits_beside_each_mean(self):
        # M25: an outlying map moves the mean, not the median over maps (the two real games, one of them on two maps).
        games = [dict(SAMPLE[0], map="m1"), dict(SAMPLE[1], map="m2"), dict(SAMPLE[0], map="m3")]
        one, two = (rulecounts.census([g])["pearls eaten a game"] for g in SAMPLE)
        self.assertNotEqual(one, two)
        self.assertEqual(rulecounts.medians(games)["pearls eaten a game"], one)
        # Games on one map count together: m1's two games give their mean, then the median of m1 and m2.
        pooled = rulecounts.medians([dict(SAMPLE[0], map="m1"), dict(SAMPLE[1], map="m1"), dict(SAMPLE[1], map="m2")])
        self.assertAlmostEqual(pooled["pearls eaten a game"], ((one + two) / 2 + two) / 2)
        text = rulecounts.report("f9f3bd7ce45d", {"champion": games[:1], "swarm": games[1:]}, [], [], True, ROUNDS)
        mean = rulecounts.figure(rulecounts.census(games)["pearls eaten a game"])
        self.assertIn(f"| pearls eaten a game | {rulecounts.figure(one)} ({rulecounts.figure(one)}) |", text)
        self.assertIn(f"| {mean} ({rulecounts.figure(one)}) |", text)

    def test_a_label_outside_a_maps_top_values_still_counts_there(self):
        # 13 labels; on m1 "v12" is one of 13 equally common values, so a top-12 census of m1 alone could drop it.
        game = json.loads(json.dumps(SAMPLE[0]))
        game["logs"] = [[r, i, f"{text} rule v{k % 13}"] for k, (r, i, text) in enumerate(game["logs"])]
        other = json.loads(json.dumps(game))
        other["logs"] = [[r, i, f"{text.rsplit(' rule ', 1)[0]} rule v12"] for r, i, text in game["logs"]]
        share = rulecounts.census([game], top=None)["% of logged turns: rule v12"]
        medians = rulecounts.medians([dict(game, map="m1"), dict(other, map="m2"), dict(game, map="m3")])
        self.assertAlmostEqual(medians["% of logged turns: rule v12"], share)
        # A map without log lines has no share of logged turns to count.
        bare = dict(SAMPLE[1], logs=[], map="m4")
        self.assertAlmostEqual(rulecounts.medians([dict(game, map="m1"), bare])["% of logged turns: rule v0"],
                               rulecounts.census([game])["% of logged turns: rule v0"])

    def test_the_map_comes_from_the_referees_replay_name(self):
        # Real names from rule-games/1790399621343941000/replays/breeder, and a hyphenated map name.
        self.assertEqual(rulecounts.map_of(Path("0002-gen_eval_26538317-aA.replay")), "gen_eval_26538317")
        self.assertEqual(rulecounts.map_of(Path("0003-gen-odd-26538317-huge-aB.replay")), "gen-odd-26538317-huge")


class TestRound500(unittest.TestCase):
    """What decides round 500 (method note M26), on the real 2c79f81 game and a real game that ended early."""

    def count(self, game: dict) -> dict:
        return rulecounts.endgame([game], 397, 471)

    def test_the_phase_boundaries_are_the_fold_and_merge_rounds(self):
        self.assertEqual([rulecounts.phase(r, 397, 471) for r in (0, 396, 397, 470, 471, 499)],
                         ["r<397", "r<397", "r397-470", "r397-470", "r471+", "r471+"])
        # The real r145 death of a 58-long dragon (hitSelf) moved to each boundary lands in that round's phase.
        [death] = [d for d in ENDGAME["deaths"] if d[:2] == [145, 58]]
        for r, name in ((396, "r<397"), (397, "r397-470"), (470, "r397-470"), (471, "r471+")):
            counts = self.count(dict(ENDGAME, deaths=[[r, *death[1:]]]))
            died = {row for row, value in counts.items() if "died" in row and value}
            self.assertEqual(died, {f"our longest (8+) died a game, {name}: {rulecounts.SELF}",
                                    f"our longest (12+) died a game, {name}: {rulecounts.SELF}"})

    def test_each_teams_longest_is_read_at_r300_the_folds_start_and_r499(self):
        counts = self.count(ENDGAME)
        self.assertEqual([counts[f"{team} longest at the end of {label}"] for label in
                          ("r300", "r396 (foldRound's start)", "r499") for team in ("our", "their")],
                         [42, 22, 56, 28, 97, 60])
        self.assertEqual(counts["our longest's growth, r300 to r499"], 97 - 42)
        self.assertEqual(counts["our longest's growth, r396 to r499"], 97 - 56)
        self.assertEqual(counts["% of games over before r499 (a team eliminated)"], 0)

    def test_only_the_teams_longest_deaths_count_by_cause(self):
        counts = self.count(ENDGAME)
        # This game's 12+ deaths before the fold of a dragon no teammate outgrew: r96 34 hitSelf, r145 58 hitSelf,
        # r191 35 hitWall, r284 56 and r308 43 head-on into a 6-long enemy head, r335 39 hitSelf.
        early = "our longest (12+) died a game, r<397: "
        self.assertEqual([counts[early + kind] for kind in rulecounts.CAUSES], [0, 2, 0, 3, 0, 1])  # none deliberate
        # A 12+ dragon with a longer teammate alive wasn't the team's longest, so it isn't counted.
        shorter = next(d for d in ENDGAME["deaths"] if 12 <= d[1] < d[2] and d[0] < 397)
        alone = self.count(dict(ENDGAME, deaths=[shorter]))
        self.assertFalse(any(value for row, value in alone.items() if "died" in row))
        r, length, _, *how = shorter
        alone = self.count(dict(ENDGAME, deaths=[[r, length, 0, *how]]))
        self.assertEqual(alone[f"our longest (12+) died a game, r<397: {rulecounts.cause(length, *how)}"], 1)
        # A head-on into an enemy head as long is an equal-or-longer one; one with no known other head is "other".
        self.assertEqual(rulecounts.cause(9, "hitHeadToHead", 9, False), rulecounts.LONGER)
        self.assertEqual(rulecounts.cause(9, "hitHeadToHead", None, False), rulecounts.TRAPPED)

    def test_a_head_on_with_a_teammate_is_its_own_cause(self):
        # Real tools/replays.py death records: the sample game's r284 head-on into a 6-long enemy head, and a friendly
        # head-on at r145 of rule-games/1790404915361643000/replays/champion/0000-gen_eval_188302894-aA.replay (431
        # moved into its teammate 272; the one hit comes first).
        enemy = {"round": 284, "id": 54, "team": "B", "reason": "hitHeadToHead", "length": 56, "head": [32, 58],
                 "born": 46, "parent": 8, "longestTeammate": 42, "hitBy": 241, "hitByTeam": "A", "hitByLength": 6}
        hit = {"round": 145, "id": 272, "team": "A", "reason": "hitHeadToHead", "length": 2, "head": [34, 0],
               "born": 87, "parent": 141, "longestTeammate": 7, "hitBy": 431, "hitByTeam": "A", "hitByLength": 3}
        mover = {"round": 145, "id": 431, "team": "A", "reason": "hitHeadToHead", "length": 3, "head": [10, 13],
                 "born": 121, "parent": 381, "longestTeammate": 7, "step": 1, "of": 1, "direction": "east",
                 "movedInto": 272, "movedIntoTeam": "A", "movedIntoLength": 2}
        self.assertEqual(rulecounts.other_head(enemy, "B"), (6, False))
        self.assertEqual(rulecounts.cause(56, "hitHeadToHead", *rulecounts.other_head(enemy, "B")), rulecounts.SHORTER)
        self.assertEqual(rulecounts.other_head(hit, "A"), (3, True))
        self.assertEqual(rulecounts.other_head(mover, "A"), (2, True))
        for record in (hit, mover):
            self.assertEqual(rulecounts.cause(record["length"], record["reason"], *rulecounts.other_head(record, "A")),
                             rulecounts.ALLY)
        self.assertEqual(rulecounts.other_head(dict(enemy, reason="hitSelf"), "B"), (None, False))

    def test_a_len_that_isnt_a_whole_number_is_left_out_and_the_bands_have_no_gaps(self):
        self.assertEqual([rulecounts.band(n) for n in (0.0, 7.0, 7.5, 8.0, 15.0, 15.5, 16.0, 90.0)],
                         ["<8", "<8", None, "8-15", "8-15", None, "16+", "16+"])
        for odd in (-1.0, float("inf"), float("nan")):
            self.assertIsNone(rulecounts.band(odd))
        r, i, text = ENDGAME["logs"][0]  # a real line, with len changed
        lines = [[r, i, text.replace(f"len {rulecounts.parse_log(text)['len']}", f"len {bad}")]
                 for bad in ("7.5", "-3", "inf", "nan", "x")]
        counts = self.count(dict(ENDGAME, logs=lines))
        self.assertFalse([row for row in counts if row.startswith("% of logged turns")])
        rulecounts.report("2c79f81d0d90", {"champion": [dict(SAMPLE[0], logs=lines)]}, [], [], True, ROUNDS)

    def test_a_fold_round_past_the_merge_round_keeps_the_phases_in_order(self):
        self.assertEqual(rulecounts.phases(480, 471), ["r<471", "r471-479", "r480+"])
        self.assertEqual([rulecounts.phase(r, 480, 471) for r in (470, 471, 479, 480)],
                         ["r<471", "r471-479", "r471-479", "r480+"])
        self.assertEqual(rulecounts.phases(471, 471), ["r<471", "r471+"])
        text = rulecounts.report("2c79f81d0d90", {"champion": SAMPLE[:1]}, [], [], True, (480, 471, "test"))
        self.assertIn("phases r<471, r471-479, r480+: foldRound isn't before mergeRound", text)
        self.assertNotIn("isn't before", rulecounts.report("2c79f81d0d90", {"champion": SAMPLE[:1]}, [], [], True,
                                                           ROUNDS))

    def test_the_untracked_body_share_is_by_length_band_and_phase(self):
        counts = self.count(ENDGAME)
        # The sample's real lines: 16+ long, body < len on 4 of 20 from r471, 1 of 27 in r397-470, 1 of 28 before.
        self.assertAlmostEqual(counts["% of logged turns, length 16+, r471+: body < len"], 100 * 4 / 20)
        self.assertAlmostEqual(counts["% of logged turns, length 16+, r397-470: body < len"], 100 * 1 / 27)
        self.assertAlmostEqual(counts["% of logged turns, length 8-15, r<397: body < len"], 0)
        self.assertAlmostEqual(counts["% of logged turns, length <8, r<397: body < len"], 100 * 1 / 183)
        self.assertNotIn("% of logged turns, length <8, r<397: body < len", self.count(dict(ENDGAME, logs=[])))

    def test_a_game_that_ended_early_has_no_length_at_rounds_it_never_reached(self):
        game = rulecounts.extract(TROPHY, "B")
        self.assertEqual(game["longest"][-1], [133, 0, 3])
        self.assertEqual(game["deaths"][-1][:3], [133, 2, 0])  # its last dragon: no teammate left
        head_ons = [d for d in game["deaths"] if d[3] == "hitHeadToHead"]
        self.assertTrue(head_ons and all(d[4] is not None for d in head_ons))  # every one against an enemy here
        counts = self.count(game)
        self.assertFalse([row for row in counts if "longest at the end" in row or "growth" in row])
        self.assertEqual(counts["% of games over before r499 (a team eliminated)"], 100)
        # Its map leaves the r499 length out of the median over maps; it isn't a 0 there.
        medians = rulecounts.endgame_medians([dict(ENDGAME, map="m1"), dict(game, map="m2")], 397, 471)
        self.assertEqual(medians["our longest at the end of r499"], 97)
        self.assertEqual(medians["% of games over before r499 (a team eliminated)"], 50)

    def test_each_teams_growth_from_the_fold_splits_by_where_its_king_got_it(self):
        counts = self.count(KING)
        growth = "their longest's growth, r396 to r499"
        # The champion's king: 25 to 66. From r397 it ate 15 pearls no merge dropped, 3 from merged 2-long children
        # and 25 from merged 3+ teammates. The sample predates king losses, so nothing is said of the other -2.
        self.assertEqual([counts[row] for row in counts if row.startswith(growth)], [41, 15, 3, 25])
        self.assertFalse([row for row in counts if " king (" in row or "less " in row])
        # In r466-477, where the post-mortem saw the king grow 55 to 59 on dragon 972's children, it ate a 4-long's
        # and an 8-long's drops, a spawned pearl and two 2-long children's, of dragons 889 and 284, not 972.
        self.assertEqual([m for m in KING["king"]["theirs"] if 466 <= m[0] <= 477],
                         [[468, 4], [469, 8], [471, 2], [475, 0], [477, 2]])
        # From a real replay (tools/test_replays.py's fold game, whose B king took six kelp deaths' drops after r396),
        # a teammate's drop is a merge with its length, and a head-on's or a spawned pearl is 0.
        fold = rulecounts.extract(REPO / "tools" / "testdata" / "fold-e0102-0308.replay", "A")["king"]
        self.assertEqual([m for m in fold["theirs"] if m[0] > 396 and m[1]],
                         [[398, 3], [400, 3], [407, 2], [409, 2], [448, 3], [450, 3]])
        # Trophy's A king ate 10 pearls, two of them teammates' head-on drops (r39, r41); B was eliminated.
        trophy = rulecounts.extract(TROPHY, "B")["king"]
        self.assertEqual((len(trophy["theirs"]), [m for m in trophy["theirs"] if m[1]], trophy["ours"]), (10, [], []))
        # A split child is as long as the champion's childLength: at 4, the 3- and 4-long merges are children too.
        wider = rulecounts.endgame([KING], 397, 471, child=4)
        merged = [m for r, m in KING["king"]["theirs"] if r > 396 and m]
        self.assertEqual([wider[f"{growth}: teammates merged into its king, {label}"]
                          for label in ("4 long or shorter (split children)", "5+ long")],
                         [sum(m <= 4 for m in merged), sum(m > 4 for m in merged)])
        self.assertEqual(sum(m <= 4 for m in merged) + sum(m > 4 for m in merged), 28)

    # Method note M47: the growth left after the king's meals read -17.4 a game, taken for the king's own losses. A
    # real game (online 95037 on Queen Of Spades, our B beside a rival A, to r499): our longest at r396 was an
    # 8-long (dragon 92, killed head-on in r403) while our king, dragon 61, was 2 long, split out of starting dragon 1
    # in r241 with 1 keeping 6 (it died 12 long in r274). So of our growth of -1 (8 to 7), -6 is that lead, not a loss
    # of the king's: it ate 1 spawned pearl and 4 merged 3+ teammates' drops and lost nothing to splits or sprints.
    def test_the_king_lines_length_and_its_splits_and_sprints_are_their_own_rows(self):
        counts = self.count(dict(rulecounts.extract(QUEEN, "B"), map="queen"))
        ours, theirs = (f"{who} king (the longest at r499 and the dragons it split from): " for who in ("our", "their"))
        rows = ("length at the end of r396", "length lost to the pieces it split off, r0 to r499",
                "length lost to its own sprint steps, r0 to r499",
                "length the parent kept when it went on in a shorter child, r0 to r499")
        # Over the game our king split off six 2-long children and never sprinted, and at the handover in r241 its
        # parent kept 6 and the child went on 2 long (a change of king). Theirs split off ten 2-long children,
        # sprinted once, and went on in the child at 8 splits (its line is 9 dragons), each a split-out or even split
        # whose parent kept 2, its old head: the king's own loss, so 36 in all and no change of king.
        self.assertEqual([counts[ours + row] for row in rows], [2, 12, 0, 6])
        self.assertEqual([counts[theirs + row] for row in rows], [3, 36, 1, 0])
        growth = [(row.split(": ", 1)[-1], value) for row, value in counts.items()
                  if row.startswith("our longest's growth, r396")]
        self.assertEqual([value for _, value in growth], [-1, 1, 0, 4, 0, 0, 0, -6])
        self.assertTrue(growth[-1][0].startswith("less the longest's lead over its king at r396"))
        self.assertFalse([row for row in counts if "anything else" in row])
        # The parts add up to the growth exactly, for both teams: theirs grew 3 (3 to 6) on 6 pearls and a 3-long
        # merge, less the 2-long old heads it shed at two split-outs (r405, r410) and no lead at r396.
        for who in ("our", "their"):
            total, *parts = [value for row, value in counts.items() if row.startswith(f"{who} longest's growth, r396")]
            self.assertEqual(total, sum(parts))
        self.assertEqual([value for row, value in counts.items() if row.startswith("their longest's growth, r396")],
                         [3, 6, 0, 1, -4, 0, 0, 0])
        # The line's length rebuilt from its start, meals and losses is the king's real length at the end, on every
        # testdata replay that went the distance; a sprint step costs one (trauma's A king: 2 steps, 1 after r396).
        for path, side in ((QUEEN, "B"), (FOLD, "A"), (TRAUMA, "A")):
            facts = rulecounts.extract(path, side)
            for key, column in (("ours", 1), ("theirs", 2)):
                self.assertEqual(rulecounts.line_length(facts["king"][key], facts["line"][key], 499),
                                 facts["longest"][-1][column])
        trauma = rulecounts.extract(TRAUMA, "A")["line"]["ours"]
        self.assertEqual((rulecounts.lost(trauma[3]), rulecounts.lost(trauma[3], 396)), (2, 1))
        # An eliminated team has no king line (Trophy's B), so none of these rows.
        self.assertIsNone(rulecounts.extract(TROPHY, "B")["line"]["ours"])
        # One eliminated in r499 itself still has an r499 row: its game is left out of all its growth rows, not only
        # the ones from its (missing) line, so its means still add up.
        facts = rulecounts.extract(QUEEN, "B")
        facts["line"]["theirs"] = None
        counts = self.count(dict(facts, map="queen"))
        self.assertFalse([row for row in counts if row.startswith("their longest's growth, r396")])
        self.assertTrue([row for row in counts if row.startswith("our longest's growth, r396")])

    def test_growth_by_source_needs_the_fold_start_and_r499(self):
        cut = dict(KING, longest=[row for row in KING["longest"] if row[0] < 499])
        self.assertFalse([row for row in self.count(cut) if "growth" in row])
        self.assertFalse([row for row in self.count(ENDGAME) if "king" in row])  # facts from before king meals
        game = dict(KING, won=False, drew=False, turns=[], head_ons=[], pearls=0, head_ons_started=0,
                    moments={}, deaths=[], map="m")
        text = rulecounts.report("7e819b14bf12", {"champion": [game]}, [], [], True, ROUNDS, 2)
        self.assertIn("| their longest's growth, r396 to r499: teammates merged into its king, 2 long or shorter "
                      "(split children) | 3 (3) | 3 (3) |", text)
        self.assertIn("the champion's childLength 2", text)
        self.assertIn("the medians over maps needn't", text)

    def test_the_fold_and_merge_rounds_come_from_the_champions_params(self):
        params = "\n".join((  # 2c79f81's bot/params.hpp lines naming them
            "    int protectMinLength = 6;    // from P.mergeRound our longest-looking dragon (this long) refuses "
            "all trades",
            "    // From mergeRound the swarm folds its mass into one long dragon for the round-500 tiebreak: no "
            "ordinary",
            "    int mergeRound = 471;",
            "    int foldRound = 397;         // ... from this round",
            "    bool kingHoldsSplits = true; // ... and from foldRound the king stops ordinary splits (folders "
            "feed it)"))
        self.assertEqual(rulecounts.phase_rounds(params), ROUNDS)
        fold, merge, note = rulecounts.phase_rounds(params.replace("foldRound", "kingRound"))
        self.assertEqual((fold, merge), (rulecounts.FOLD, 471))
        self.assertIn("foldRound 397 (not in its params.hpp", note)
        # childLength too (7e819b1's line), else CHILD.
        self.assertEqual(rulecounts.child_length("    int childLength = 3;         // segments given to the child"), 3)
        self.assertEqual(rulecounts.child_length(params), rulecounts.CHILD)

    def test_the_report_has_a_round_500_section_with_a_column_per_opponent(self):
        games = [dict(SAMPLE[0], map="m1"), dict(SAMPLE[1], map="m2")]
        text = rulecounts.report("2c79f81d0d90", {"champion": games[:1], "swarm": games[1:]}, [], [], True, ROUNDS)
        section = text[text.index("## What decides round 500"):]
        self.assertIn("phases r<397, r397-470, r471+", section)
        self.assertIn("| measure | vs champion | vs swarm | all |", section)
        self.assertIn("| our longest at the end of r499 | 97 (97) | 97 (97) | 97 (97) |", section)
        self.assertIn("- here, all: 0 of 0 round-limit losses; theirs in 0 of 0 round-limit wins", section)  # M86

    # M86: two-king losses reproduce locally but no row counted them. testdata/rulecounts-two-kings-sample.json holds
    # two_kings' facts from the 22 games of review 1790661435866254000's rule games (its replays: the logging copy of
    # 327435f, champion then by the reflog, against the champion, swarm and breeder, 29 Sep): a fold adds ceil(s/2) of
    # a second-longest s, so 1 of its 4 round-limit losses (54 v 56 with a 33-long second) passes it, not 33 v 48 with
    # a 28-long one (14 < 15), and 3 of its 11 wins theirs.
    def test_round_limit_losses_where_folding_the_second_king_could_turn_them_are_counted_with_its_fold_gate(self):
        sample = json.loads((TESTDATA / "rulecounts-two-kings-sample.json").read_text())
        groups = {f"vs {name}": games for name, games in sample.items()}
        groups["all"] = [g for games in sample.values() for g in games]
        lines = rulecounts.two_king_section(groups)
        self.assertIn("- here, all: 1 of 4 round-limit losses (gate at r450: far 1); theirs in 3 of 11 "
                      "round-limit wins", lines)
        self.assertIn("- here, vs swarm: 0 of 1 round-limit losses; theirs in 0 of 5 round-limit wins", lines)
        # A post-mortem gets the header and this section only, and nothing from a review without the line.
        text = "\n".join(["# Rule counts for champion abc", "", "## Every game's events", "",
                          "## What decides round 500 (method note M26)", "", *lines, "## How deaths came about", ""])
        excerpt = rulecounts.postmortem_excerpt(text)
        self.assertEqual(excerpt.split("\n")[:3], ["# Rule counts for champion abc", "", "## What decides round 500 "
                                                                                     "(method note M26)"])
        self.assertIn(lines[0], excerpt)
        self.assertNotIn("How deaths", excerpt)
        self.assertIsNone(rulecounts.postmortem_excerpt(text.replace("Two kings", "Two queens")))
        # From a replay: a real ranked game to r499 (no debug lines) and one over in r133.
        import watch
        self.assertEqual(rulecounts.two_kings(watch.watch(TRAUMA), "A", {}),
                         {"second": [3, 4], "gate": rulecounts.NO_LINE})
        self.assertIsNone(rulecounts.two_kings(watch.watch(TROPHY), "B", {}))

    # M86's online and leader counterparts (owed 29 Sep 13:49): the local row stood alone. The real ranked Trauma game
    # to r499 (A lost 3 v 6 with a 3-long second: ceil(3/2) = 2 < 3) and a loss by 2 at each side of the bound.
    def test_the_two_king_row_is_read_online_and_for_the_leaders_from_end_standings(self):
        import replays
        trauma = replays.game_summary(replays.analyse(replays.load_replay(TRAUMA)), None)
        a, b = trauma["teams"]["A"], trauma["teams"]["B"]
        by_two = lambda second: {"ours": {"end": {"longest": 20, "second": second}},  # noqa: E731
                                 "theirs": {"end": {"longest": 22, "second": 1}}, "result": "lost", "end": "roundLimit"}
        online = {"sid": 11574, "games": [
            {"ours": a, "theirs": b, "result": "lost", "end": "roundLimit"}, by_two(5), by_two(4),
            {**by_two(9), "end": "teamEliminated"}], "leader_games": [("rank 1 (Cutlery)", [(trauma, "B")])]}
        lines = rulecounts.two_king_section({"all": []}, online)
        self.assertIn(f"- online, {rulecounts.online_source(online)}: 1 of 3 round-limit losses; theirs in 0 of 0 "
                      "round-limit wins", lines)
        self.assertIn("- rank 1 (Cutlery), its ranked games (theirs is its opponent's): 0 of 0 round-limit losses; "
                      "theirs in 0 of 1 round-limit wins", lines)

    # M29: losses first kept 1 of 6 games that reached r499 for one review. Real facts: the census sample with the
    # round-500 sample's lengths went to r499, the Trophy game was over in r133.
    def test_the_kept_replays_include_three_that_reached_r499_when_there_are_any(self):
        early, full = rulecounts.extract(TROPHY, "B"), SAMPLE[0]
        results = [(Path(f"{i:04d}-lost-aA.replay"), dict(early, won=False)) for i in range(7)]
        results += [(Path(f"{i:04d}-won-aB.replay"), dict(full, won=True, drew=False)) for i in range(4)]
        kept = rulecounts.keep(results)
        names = [p.name for p, _ in kept]
        self.assertEqual(names, [f"{i:04d}-lost-aA.replay" for i in range(6)] + [f"{i:04d}-won-aB.replay"
                                                                                for i in range(3)])
        text = rulecounts.report("2c79f81d0d90", {"champion": [dict(g, map="m") for _, g in kept]}, [], kept, True,
                                 ROUNDS)
        self.assertIn("(3 of these did)", text)
        self.assertIn("0000-lost-aA.replay (lost, over in r133)", text)
        self.assertIn("0000-won-aB.replay (won, to r499)", text)
        # Already three among the losses-first six: nothing extra. None at all: the usual six.
        losses = [(Path(f"{i:04d}-lost-aB.replay"), dict(full, won=False, drew=False)) for i in range(3)]
        self.assertEqual(len(rulecounts.keep(losses + results)), rulecounts.KEEP_REPLAYS)
        self.assertEqual(len(rulecounts.keep(results[:7])), rulecounts.KEEP_REPLAYS)
        # Fewer than three reached r499: all of them, and no more.
        self.assertEqual(len(rulecounts.keep(results[:7] + results[7:8])), rulecounts.KEEP_REPLAYS + 1)


# M36: 8 random eval maps held one of 2400+ tiles and one 920-tile map made the split-out means.
class TestSizes(unittest.TestCase):
    def test_the_sizes_are_the_drivers_bands_in_order(self):
        self.assertEqual(list(rulecounts.SIZES), [name for name, _, _ in meta.AREA_BANDS])

    # M66: drawn from eval alone, a review's games crossed 29 portals where an SPRT's crossed 1,050. The repo's real
    # maps stand in for the styles: every style gets its STYLES count and, across them, the sizes come out as SIZES.
    def test_maps_are_drawn_by_style_then_size_and_a_short_style_is_filled_and_said(self):
        pools = {style: list((REPO / "maps" / folder).glob("*.map")) for style, folder in
                 (("eval", "gen-fixed"), ("near", "ladder"), ("worldgen", "community"), ("portalrooms", "retired"),
                  ("deadend", "adversarial"))}
        chosen, note = rulecounts.draw(pools)
        # M97: the one deadend map and the only >=2400 community map are outside the official maps' pearl supply.
        self.assertEqual(note, "unsw, adv_0e962ec_c116 drawn with a pearl supply outside the official maps' 0.15-4.97")
        self.assertEqual(Counter(chosen.values()), rulecounts.STYLES)
        self.assertEqual(Counter(rulecounts.size_of(p) for p in chosen), rulecounts.SIZES)
        # No portalrooms maps (a referee checkout without the style): the others fill in, and the note says so.
        chosen, note = rulecounts.draw({**pools, "portalrooms": []})
        self.assertEqual((len(chosen), "portalrooms" in chosen.values()), (rulecounts.MAPS, False))
        self.assertIn("0 of 1 portalrooms", note)
        # M97: great_divide (920 tiles, 11.94 spawn attempts per 100 tiles a round) loses its size's place to a
        # <=1024 map inside the official range, and is drawn only when nothing else is left, which the note says.
        divide = REPO / "maps" / "community" / "great_divide.map"
        small = [p for p in pools["near"] if rulecounts.size_of(p) == "<=1024"]
        chosen, note = rulecounts.draw({"eval": [divide, *small]})
        self.assertEqual((len(chosen), list(chosen)[-1]), (len(small) + 1, divide))
        self.assertIn("great_divide drawn with a pearl supply outside", note)

    def test_each_size_gets_a_column_and_a_kept_replay(self):
        games = [dict(SAMPLE[0], map="m1", size="<=1024", supply=1.234), dict(SAMPLE[1], map="m2", size=">=2400")]
        text = rulecounts.report("f9f3bd7ce45d", {"champion": games[:1], "swarm": games[1:]}, [], [], True, ROUNDS)
        self.assertIn("| measure | vs champion | vs swarm | all | <=1024 tiles | >=2400 tiles |", text)
        self.assertIn("m1 (<=1024 tiles, ?, 1.23); m2 (>=2400 tiles, ?, ?)", text)  # M97: each map's pearl supply
        self.assertIn("| games | 1 | 1 | 2 | 1 | 1 |", text)
        # Six small-map losses fill the losses-first picks; the one big-map win is kept as well.
        results = [(Path(f"{i:04d}-lost-aA.replay"), dict(SAMPLE[0], won=False, drew=False, size="<=1024"))
                   for i in range(7)] + [(Path("0009-won-aB.replay"), dict(SAMPLE[1], won=True, size=">=2400"))]
        kept = [p.name for p, _ in rulecounts.keep(results)]
        self.assertEqual(kept[-1], "0009-won-aB.replay")
        self.assertEqual(len(kept), rulecounts.KEEP_REPLAYS + 1)

    def test_a_deliberate_death_of_the_longest_is_its_own_cause(self):
        # The round-500 sample's r96 34-long hitSelf death, as extract now records it with its debug line's feed.
        death = next(d for d in ENDGAME["deaths"] if d[0] == 96)
        counts = rulecounts.endgame([dict(ENDGAME, deaths=[death + ["feed"]])], *ROUNDS[:2])
        self.assertEqual(counts[f"our longest (12+) died a game, r<397: {rulecounts.DELIBERATE}"], 1)
        self.assertEqual(counts[f"our longest (12+) died a game, r<397: {rulecounts.SELF}"], 0)
        self.assertEqual(rulecounts.endgame([dict(ENDGAME, deaths=[death + [None]])], *ROUNDS[:2])[
            f"our longest (12+) died a game, r<397: {rulecounts.SELF}"], 1)


# M39 and M30 on real replays: the Trophy ranked game (tools/testdata/trophy-155732.replay: 12 rams by A, 7 by B) and
# E0102's fold game (tools/testdata/fold-e0102-0308.replay, a map with portals).
class TestEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import watch
        cls.watch = watch.watch(TROPHY)
        cls.fold = watch.watch(REPO / "tools" / "testdata" / "fold-e0102-0308.replay")

    def test_each_ram_is_a_head_on_we_moved_into_with_both_drops_counted(self):
        deaths = self.watch.game.deaths
        rams = rulecounts.ram_events(self.watch, "A", {})
        victims = [d for d in deaths if d.get("hitByTeam") == "A" and d["team"] == "B"]
        self.assertEqual(len(rams), len(victims))
        self.assertEqual(len(rams), 12)
        for (gain, ate, dropped, net, edge, *_), victim in zip(rams, victims):
            ours = next(d for d in deaths if d.get("movedInto") == victim["id"] and d["round"] == victim["round"])
            self.assertEqual(gain, victim["length"] - ours["length"])
            self.assertEqual(dropped, math.ceil(victim["length"] / 2) + math.ceil(ours["length"] / 2))
            self.assertTrue(0 <= ate <= dropped)
            self.assertEqual(net, ate - ours["length"])
            self.assertIsNone(edge)  # a ladder game: no debug lines
        # Our pearls from the drops are the meals whose dropping death is one of the two.
        first = victims[0]
        index = [k for k, d in enumerate(deaths) if d is first or (d.get("movedInto") == first["id"]
                                                                  and d["round"] == first["round"])]
        self.assertEqual(rams[0][1], sum(m["death"] in index and m["team"] == "A" for m in self.watch.game.meals))

    # M51 on the Trophy game: each ram's swing, their pickups, its steps and the victim gain by our length before the
    # move. A's r23 ram was a 2-step sprint (ES) that ate a pearl on its first step: 3 long before it, 4 at death, so
    # by length at death it read one band shorter a victim than it was.
    def test_a_rams_swing_counts_the_victims_loss_and_its_gain_our_length_before_the_move(self):
        w = self.watch
        deaths = w.game.deaths
        rams = rulecounts.ram_events(w, "A", {})
        victims = [d for d in deaths if d.get("hitByTeam") == "A" and d["team"] == "B"]
        for (gain, ate, dropped, _, _, theirs, swing, steps, start_gain), victim in zip(rams, victims):
            ours = next(d for d in deaths if d.get("movedInto") == victim["id"] and d["round"] == victim["round"])
            k = [i for i, d in enumerate(deaths) if d is victim or d is ours]
            self.assertEqual(theirs, sum(m["death"] in k and m["team"] == "B" for m in w.game.meals))
            self.assertTrue(ate + theirs <= dropped)
            self.assertEqual(steps, ours["step"])
            self.assertEqual(steps, len(w.turns[(victim["round"], ours["id"])].action))
            start = len(w.frames[victim["round"]].dragons[ours["id"]][1])  # our body at the round's start
            self.assertEqual(start_gain, victim["length"] - start)
            self.assertEqual(swing, (victim["length"] - theirs) - (start - ate))
        first = next(r for r, v in zip(rams, victims) if v["round"] == 23)
        self.assertEqual((first[7], first[0], first[8]), (2, first[8] - 1, first[8]))
        self.assertEqual(Counter(r[7] for r in rams), {1: 8, 2: 4})

    # M51 on synthetic rams: the gain bands read the start gain, the steps bands and their swing, and facts from before
    # M51 (five fields) keep their gain at death and get no swing or steps rows.
    def test_ram_tallies_band_by_start_gain_and_steps_with_the_swing(self):
        # [gain at death, ours, dropped, net, edge, theirs, swing, steps, gain at the start]
        rams = [[1, 1, 3, 1 - 2, None, 2, 0, 3, 0], [1, 2, 3, 2 - 3, None, 0, 4, 1, 1],
                [0, 0, 2, -2, None, 2, -2, 3, -1]]
        tallies = rulecounts.events([{"rams": rams}])
        self.assertEqual(tallies["rams at a tie: in all"], 1)  # the 3-step ram 1 longer only at death
        self.assertEqual(tallies["rams on heads 1-2 longer: in all"], 1)
        self.assertEqual(tallies["rams on shorter heads: in all"], 1)
        self.assertEqual(tallies["rams of 3 steps: in all"], 2)
        self.assertEqual(tallies["rams of 3 steps: swing a ram"], -1)
        self.assertEqual(tallies["rams of 3 steps: % swinging below 0"], 50)
        self.assertEqual(tallies["rams of 3 steps: their share of the drops %"], 80)
        self.assertEqual(tallies["rams of 1 step: swing a ram"], 4)
        self.assertEqual(tallies["rams of 3 steps at a tie: in all"], 1)
        self.assertEqual(tallies["rams of 3 steps at a tie: swing a ram"], 0)
        self.assertEqual(tallies["rams of 2 steps: in all"], 0)
        self.assertNotIn("rams of 2 steps: swing a ram", tallies)
        old = rulecounts.events([{"rams": [r[:5] for r in rams]}])
        self.assertEqual(old["rams on heads 1-2 longer: in all"], 2)
        self.assertFalse([row for row in old if "swing" in row or " of " in row.split(":")[0]])

    def test_a_ram_turns_logged_edge_is_read_and_an_odd_one_is_unknown(self):
        # I352's `edge` key (from its idea text) on a real line of the census sample.
        victims = [d for d in self.watch.game.deaths if d.get("hitByTeam") == "A" and d["team"] == "B"]
        line = SAMPLE[0]["logs"][0][2]
        logs = {(victims[0]["round"], victims[0]["hitBy"]): rulecounts.parse_log(line + " edge -1"),
                (victims[1]["round"], victims[1]["hitBy"]): rulecounts.parse_log(line + " edge nan"),
                (victims[2]["round"], victims[2]["hitBy"]): rulecounts.parse_log(line + " edge inf")}
        rams = rulecounts.ram_events(self.watch, "A", logs)
        self.assertEqual(rams[0][4], -1.0)
        tallies = rulecounts.events([{"rams": rams}])  # never raises on the "nan" and "inf" lines
        self.assertEqual(tallies["rams at logged head edge -1: in all"], 1)
        self.assertEqual(tallies["rams at logged head edge unknown: in all"], 2)
        self.assertNotIn("rams at logged head edge 0: in all", tallies)

    def test_portal_crossings_are_the_moves_whose_head_jumped(self):
        import watch
        w = self.fold
        for (r, i), turn in w.turns.items():
            after = w.frames.get(r + 1)
            if not (turn.action and set(turn.action) <= rulecounts.STEPS and r in w.frames and i in w.frames[r].dragons
                    and after and i in after.dragons):
                continue
            start, end = w.frames[r].dragons[i][1][0], after.dragons[i][1][0]
            self.assertEqual(rulecounts.crosses_portal(w.board, start, turn.action),
                             watch.manhattan(w, start, end) != len(turn.action), (r, i, turn.action))
        counts = rulecounts.action_deaths(w, "B", {}, {})
        self.assertEqual(counts["portal crossing"], [14, 0, 0, 0])
        # M80: the replay walker's own crossings (tools/replays.py, from the map's portal edges) are the same moves,
        # and also a child's move in its birth round, which the round's opening frame can't place (4 in Queen's B).
        for game, team in ((w, "B"), (watch.watch(QUEEN), "B")):
            died = {(d["round"], d["id"]): d for d in game.game.deaths if "hitBy" not in d}
            seen = {(r, i) for (r, i), t in game.turns.items() if t.team == team and t.action
                    and set(t.action) <= rulecounts.STEPS and rulecounts.crossed(game, r, i, t.action, died.get((r, i)))}
            walker = set(game.game.teams[team].crossings)
            self.assertLessEqual(seen, walker)
            self.assertTrue(all(game.game.dragons[i].born == r for r, i in walker - seen))
            self.assertEqual(len(walker - seen), 0 if game is w else 4)
        # Every move counts, a child's move in the round it split off too (it isn't in that round's opening frame).
        self.assertEqual(counts["step"][0] + counts["sprint"][0],
                         sum(t.team == "B" and bool(t.action) and set(t.action) <= rulecounts.STEPS
                             for t in w.turns.values()))

    def test_a_dying_move_crosses_a_portal_only_up_to_its_fatal_step(self):
        # A real fold-game move through a portal, cut before its step: a sprint that died on its first step never took
        # the second.
        w = self.fold
        (r, i), turn = next(((r, i), t) for (r, i), t in sorted(w.turns.items())
                            if t.action and set(t.action) <= rulecounts.STEPS and r in w.frames
                            and i in w.frames[r].dragons
                            and rulecounts.crosses_portal(w.board, w.frames[r].dragons[i][1][0], t.action))
        head = w.frames[r].dragons[i][1][0]
        other = next(d for d in "NESW" if not rulecounts.crosses_portal(w.board, head, d)
                     and w.board.step(head, d) is not None)
        self.assertTrue(rulecounts.crosses_portal(w.board, head, turn.action + turn.action))
        self.assertFalse(rulecounts.crosses_portal(w.board, head, other + turn.action, upto=1))
        self.assertTrue(rulecounts.crosses_portal(w.board, head, turn.action + other, upto=1))

    # M30 on the Trophy game's raw death records: every death on A's own move after a step or sprint is a ram (it moved
    # into an enemy head), a deliberate death or a failure, and the three add up to them all.
    def test_deaths_on_own_move_split_into_failures_rams_and_deliberate(self):
        deaths = self.watch.game.deaths
        own = [d for d in deaths if d["team"] == "A" and "hitBy" not in d
               and (t := self.watch.turns.get((d["round"], d["id"]))) and t.action and set(t.action) <= rulecounts.STEPS]
        rams = [d for d in own if d["reason"] == "hitHeadToHead" and d.get("movedIntoTeam") == "B"]
        counts = rulecounts.action_deaths(self.watch, "A", {}, {})
        moves = [counts["step"][k] + counts["sprint"][k] for k in range(4)]
        self.assertEqual(moves[1:], [len(own) - len(rams), len(rams), 0])
        self.assertEqual(counts["sprint"], [4, 0, 4, 0])  # every sprint of A's ended in a ram, none a failure
        # A step death its debug line marks deliberate (as watch.deliberate_deaths would) leaves the failures.
        failure = next(d for d in own if d not in rams and len(self.watch.turns[(d["round"], d["id"])].action) == 1)
        marked = rulecounts.action_deaths(self.watch, "A", {}, {(failure["round"], failure["id"]): "feed"})
        self.assertEqual(marked["step"][1:], [counts["step"][1] - 1, counts["step"][2], 1])
        tallies = rulecounts.events([{"actions": marked}])
        self.assertEqual(sum(tallies[f"step: deaths in all, {name}"] for name in rulecounts.OUTCOMES),
                         sum(marked["step"][1:]))
        self.assertAlmostEqual(tallies["step: % ending in a failure on its own move"],
                               100 * marked["step"][1] / marked["step"][0])

    # A child moves in the round it splits off: its first 3 rounds are its split's round and the 2 after (the review of
    # 0003-gen_eval_51813723 found 688 birth-round turns and 2 deaths left out of the row).
    def test_a_split_childs_first_rounds_include_its_birth_round(self):
        w = self.watch
        born = {i: d.born for i, d in w.game.dragons.items()}
        ages = [r - born[i] for (r, i), t in w.turns.items() if t.team == "A" and born.get(i, -1) >= 0]
        self.assertIn(0, ages)
        counts = rulecounts.action_deaths(w, "A", {}, {})
        self.assertEqual(counts["split child, first 3 rounds"][0], sum(0 <= age < 3 for age in ages))

    def test_the_report_has_an_events_section_over_every_game(self):
        facts = {**rulecounts.extract(TROPHY, "A"), "map": "trophy"}
        text = rulecounts.report("aa92d8bf5da3", {"champion": [facts]}, [], [], False, ROUNDS)
        section = text[text.index("## Every game's events"):text.index("## What decides round 500")]
        self.assertIn("| rams at a tie: in all |", section)
        self.assertIn("| portal crossing: turns a game | 13 | 13 |", section)
        self.assertIn("| sprint: deaths in all, rams | 4 | 4 |", section)

    # M39: of 24 games the kept replays were 6 losses and 2 more to r499. Each opponent's blind pick is a different
    # map, size and side, from the review id alone, whatever the results.
    def test_blind_picks_spread_over_sizes_and_sides_from_the_seed_alone(self):
        bands = [band for band, count in rulecounts.SIZES.items() for _ in range(count)]  # draw_by_size's order

        def results(won) -> list[tuple[Path, dict]]:
            return [(Path(name) / f"{i:04d}-m{i}-a{'AB'[i % 2]}.replay",
                     dict(SAMPLE[0], won=won(name, i), drew=False, opponent=name, size=bands[i]))
                    for name in rulecounts.OPPONENTS for i in range(rulecounts.MAPS)]

        picks = rulecounts.blind_picks(results(lambda name, i: False))
        self.assertEqual({name: p.name for name, p in picks.items()},
                         {"champion": "0000-m0-aA.replay", "swarm": "0003-m3-aB.replay", "breeder": "0006-m6-aA.replay"})
        for seed in (1, 2, 1790439654199275000):
            chosen = rulecounts.blind_picks(results(lambda name, i: False), seed)
            indexes = [int(p.name[:4]) for p in chosen.values()]
            self.assertEqual(len({bands[i] for i in indexes}), 3, seed)  # a different size each
            self.assertEqual(len(set(indexes)), 3, seed)
        # The results never move them.
        for won in (lambda name, i: True, lambda name, i: i % 2 == 0, lambda name, i: name == "swarm"):
            self.assertEqual(rulecounts.blind_picks(results(won)), picks)
        kept = rulecounts.keep(results(lambda name, i: i % 3 != 0))
        self.assertEqual(sorted(p.name for p, f in kept if f.get("blind")),
                         ["0000-m0-aA.replay", "0003-m3-aB.replay", "0006-m6-aA.replay"])
        self.assertEqual(len({p for p, _ in kept}), len(kept))  # a pick also among the losses is kept once
        won = results(lambda name, i: True)
        text = rulecounts.report("aa92d8bf5da3", {"champion": [f for _, f in won[:8]]}, [], rulecounts.keep(won), True,
                                 ROUNDS)
        self.assertIn("(3 of those 3 won)", text)
        self.assertIn("0003-m3-aB.replay (won, to r499, 1025-2399 tiles, blind pick)", text)


# M49: what a portal crossing's death hit, and how far from the king a fold-window crash was. A synthetic board for
# the cases (a portal from the east side of (0, 0) to (4, 3), the tile west of its partner edge), and the real
# replays for the tallies.
# M91: what each pearl-target pick led to, from a real local replay with our debug lines (fit-ram-0032: E0172's fit
# games, champion on A, 33 rounds): dragon 2 picked spawn tile 82 in r0-2 and switched to visible 109 in r3, never
# back; it ate 109 in r5. Dragon 6's probable 82 in r5 was eaten by teammate 8 in r6, so it was gone.
class TestPicks(unittest.TestCase):
    def test_each_pick_is_read_as_eaten_gone_switched_dropped_or_ended(self):
        import watch
        w = watch.watch(REPO / "tools/testdata/fit-ram-0032-gen_eval_7310003-aA.replay")
        logs = {(r, i): rulecounts.parse_log(t) for r, i, team, t in w.logs if team == "A"}
        picks = rulecounts.target_picks(w, "A", logs)
        self.assertEqual(picks[:2], [["spawn", 3, 0, "switch", 0], ["visible", 3, 1, "eaten", None]])
        self.assertIn(["probable", 1, 0, "gone", None], picks)
        self.assertIn(["spawn", 1, 1, "switch", 1], picks)  # switched away, came back within 6 and ate it in 30
        self.assertEqual(Counter(p[3] for p in picks), {"eaten": 17, "switch": 16, "drop": 7, "gone": 4, "end": 3})
        section = rulecounts.picks_section({"here": [{"picks": picks}], "online, replayed": [{"picks": picks[:2]}]})
        self.assertIn("| spawn | here | 24 | 25% | 2.4 (1) | 17% | 0% | 62% | 12% | 8% | 13% |", section)
        self.assertIn("| all | online, replayed | 2 | 50% | 3.0 (3) | 50% | 0% | 50% | 0% | 0% | 0% |", section)


# M90: the live build's result-blind games played again with our debug line on (tools/why.py) are a column of
# their own. The sample leaves out dev games, result-chosen files, other builds and missing replays; only games that
# reproduced are counted, and every replay is deleted once read.
class TestOnlineReplays(unittest.TestCase):
    def test_the_sample_is_the_live_builds_blind_games_with_a_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            folder = state / "online-replays/11259"
            folder.mkdir(parents=True)
            (state / "online.json").write_text(json.dumps({"submission_id": 11259, "dev": {"teams": [{"id": 758}]}}))
            games = [{"id": n, "submission_id": 11259, "side": "A", "replay": str(folder / f"{n}.replay")}
                     for n in range(1, 8)]
            games[1]["dev"] = True
            games[2]["opponent"] = 758  # a dev team collected before the mark
            games[3]["kept"] = "loss"
            games[4]["submission_id"] = 11181
            for g in games[:6]:
                Path(g["replay"]).write_bytes(b"")
            (folder / "index.json").write_text(json.dumps({"games": games}))  # game 7's replay is missing
            sid, sample = rulecounts.online_sample(state, 1)
            self.assertEqual((sid, sorted(g["id"] for g in sample)), (11259, [1, 6]))
            self.assertEqual(sample, rulecounts.online_sample(state, 1)[1])  # the seed alone orders it

    # A stop marker ends the online step at once, not at its deadline (up to ONLINE_BUDGET_S, 20 min), from either side:
    # the collector stops it, and between games the step itself stops on a marker or when its parent is gone.
    def test_a_stop_marker_or_a_lost_parent_ends_the_online_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / "STOP").touch()
            proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
            with patch.object(rulecounts, "_online", proc):
                at = time.time()
                rulecounts.collect_online(state, state, at + 600, 397, 1e6)
                self.assertLess(time.time() - at, 30)
            self.assertIsNotNone(proc.poll())
            self.assertEqual(rulecounts.online_turn(state, os.getppid(), at + 600), ("stopped", "a stop marker is set"))
            (state / "STOP").unlink()
            self.assertEqual(rulecounts.online_turn(state, -1, at + 600)[0], "stopped")

    def test_reproduced_games_are_a_column_and_every_replay_is_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "sample.json").write_text(json.dumps({"sid": 11259, "commit": "cfe3b39a72", "sampled": 4}))
            rows = [{"id": 1, "side": "A", "map": "Trauma", "size": "1025-2399", "status": "reproduced"},
                    {"id": 2, "side": "B", "status": "diverged", "note": "round 40"},
                    {"id": 3, "side": "A", "status": "not reproducible"}]
            (out / "online.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            shutil.copy(REPO / "tools/testdata/fit-ram-0032-gen_eval_7310003-aA.replay", out / "1-aA.replay")
            (out / "4-aB.replay").write_bytes(b"cut short")  # its row never came: stopped at the deadline
            replayed = rulecounts.collect_online(out, out, time.time(), 397, 1e6)
            self.assertEqual(list(out.glob("*.replay")), [])
        self.assertEqual([(g["online"], g["map"], g["opponent"]) for g in replayed["games"]], [(1, "Trauma", "online")])
        self.assertEqual(len(replayed["games"][0]["picks"]), 47)
        text = rulecounts.report("f9f3bd7ce45d", {"champion": SAMPLE[:1]}, [], [], True, ROUNDS, replayed=replayed)
        self.assertIn("| measure | vs champion | all | online 11259, replayed |", text)
        self.assertIn("the live build 11259 (cfe3b39)'s 3 result-blind ladder and autoscrim games tried of 4 drawn "
                      "(at most 24, online_sample), 1 reproduced and counted (1 read); the rest diverged 1, not "
                      "reproducible 1", text)
        self.assertIn("| all | online, replayed | 47 |", text)


class TestOnlineRamSwings(unittest.TestCase):
    # M85: a5e4552's rule counts read 3-step trade-downs at -0.37 a ram locally while online they paid +0.79, and no
    # report printed the online cell. The real ram rows of game 447294 (tools/testdata/default-447294.replay, 9553 on
    # Default as A: tools/watch.py ram_rows both ways) beside one local ram_events row, a 3-step ram at a tie.
    def test_each_ram_cell_prints_its_online_swing_ours_and_theirs_beside_the_local_one(self):
        ours = [[0, 0, 3, 2], [1, 0, 0, 1], [-1, 1, 2, 3], [0, 0, 2, 1], [1, 1, 5, 2], [-1, 0, 2, 2], [0, 2, 0, 2]]
        theirs = [[0, 0, 2, 1], [-1, 0, -4, 1], [-1, 0, -4, 2], [0, -1, -2, 1], [0, -1, -2, 1]]
        online = {"sid": 9553, "games": [{"rams": {"ours": ours, "theirs": theirs, "version": 1}}]}
        local = [{"rams": [[1, 2, 4, -1, 2.0, 0, -3, 3, 0]]}]  # ram_events: logged edge 2, swing -3, 3 steps, gain 0
        lines = rulecounts.ram_swing_lines(online, rulecounts.local_ram_rows(local))
        self.assertIn("| rams of 3 steps | -3.00 (n 1) | +2.00 (n 1) | none |", lines)
        self.assertIn("| rams at head edge 0 | none | +1.75 (n 4) | -2.00 (n 3) |", lines)
        self.assertIn("| rams on shorter heads | none | +2.00 (n 2) | -4.00 (n 2) |", lines)
        self.assertNotIn("here, ours", "\n".join(rulecounts.ram_swing_lines(online)))  # study.md: online only


class TestPortalHitsAndFoldCrashes(unittest.TestCase):
    @staticmethod
    def portal_game(deaths: list[dict], frames: dict, turns: dict, before: dict):
        from types import SimpleNamespace
        import watch
        from mapgen.gen import Board
        board = Board(5, 5, "xy")
        board.edges = {("v", 1, 0): 0, ("v", 4, 3): 0}
        return SimpleNamespace(board=board, game=SimpleNamespace(deaths=deaths),
                               frames={r: watch.Frame(dragons, frozenset(), frozenset())
                                       for r, dragons in frames.items()},
                               turns={k: watch.Turn(action, team="A") for k, action in turns.items()},
                               before_death=before)

    def test_a_teammate_head_on_at_a_portal_exit_counts_as_teammate_head(self):
        east = {"step": 1, "of": 1, "direction": "east", "head": [0, 0], "born": -1, "parent": None,
                "longestTeammate": 4}
        mover = {**east, "team": "A", "length": 3}
        self.assertEqual(self.portal_game([], {}, {}, {}).board.step((0, 0), "E"), (4, 3))  # through the portal
        deaths = [  # round 10: 0 lands on teammate 1's head; the victim's record comes first and isn't 0's own move
            {"round": 10, "id": 1, "team": "A", "reason": "hitHeadToHead", "length": 4, "head": [4, 3],
             "hitBy": 0, "hitByTeam": "A", "hitByLength": 3},
            {**mover, "round": 10, "id": 0, "reason": "hitHeadToHead", "movedInto": 1, "movedIntoTeam": "A",
             "movedIntoLength": 4},
            # round 10: 2 steps east onto teammate 3's head at (2, 2), no portal: not a portal death
            {"round": 10, "id": 3, "team": "A", "reason": "hitHeadToHead", "length": 4, "head": [2, 2],
             "hitBy": 2, "hitByTeam": "A", "hitByLength": 3},
            {**mover, "round": 10, "id": 2, "head": [1, 2], "reason": "hitHeadToHead", "movedInto": 3,
             "movedIntoTeam": "A", "movedIntoLength": 4},
            # rounds 11 and 12: through the portal into an enemy's body, then a teammate's
            {**mover, "round": 11, "id": 5, "reason": "hitOtherBody"},
            {**mover, "round": 12, "id": 7, "reason": "hitOtherBody"}]
        frames = {10: {0: ("A", ((0, 0), (0, 1), (0, 2))), 1: ("A", ((4, 3), (4, 4), (3, 4), (2, 4))),
                       2: ("A", ((1, 2), (1, 3), (1, 4))), 3: ("A", ((2, 2), (3, 2), (3, 1), (3, 0)))},
                  11: {5: ("A", ((0, 0), (0, 1), (0, 2))), 6: ("B", ((3, 3), (4, 3), (4, 4)))},
                  12: {7: ("A", ((0, 0), (0, 1), (0, 2))), 8: ("A", ((3, 3), (4, 3), (4, 4)))}}
        before = {4: frames[11], 5: frames[12]}  # the living bodies just before each hitOtherBody
        w = self.portal_game(deaths, frames, {(10, 0): "E", (10, 2): "E", (11, 5): "E", (12, 7): "E"}, before)
        hits = rulecounts.portal_hits(w, "A", {})
        self.assertEqual(hits, {"teammate's head": 1, "enemy body": 1, "teammate's body": 1})
        # The same head-on on an enemy head is a ram, and a feed is deliberate, whatever they hit.
        rammed = [dict(d, movedIntoTeam="B") if d["id"] == 0 else d for d in deaths]
        self.assertEqual(rulecounts.portal_hits(self.portal_game(rammed, frames, {(10, 0): "E"}, before), "A", {}),
                         {"enemy head (a ram)": 1})
        self.assertEqual(rulecounts.portal_hits(w, "A", {(11, 5): "feed"})["deliberate"], 1)
        tallies = rulecounts.events([{"portal_hits": hits, "actions": {**{a: [0, 0, 0, 0] for a in rulecounts.ACTIONS},
                                                                       "portal crossing": [3, 3, 0, 0]}}])
        self.assertEqual(tallies["portal crossing: deaths in all, by what it hit: teammate's head"], 1)
        self.assertEqual(tallies["portal crossing: deaths in all, by what it hit: kelp (hitWall)"], 0)

    @staticmethod
    def crash_game(kelp: bool = False):
        from types import SimpleNamespace
        import watch  # noqa: F401 (puts mapgen on the path)
        from mapgen.gen import KELP, Board
        board = Board(10, 10, "xy")
        if kelp:  # a kelp wall between x 1 and 2 with one gap at y 5: the way round is west, over the wrap
            board.edges = {("v", 2, y): KELP for y in range(10) if y != 5}
        crash = {"round": 400, "id": 0, "team": "A", "reason": "hitWall", "length": 2, "head": [0, 0], "step": 1,
                 "of": 1, "direction": "north"}
        living = {0: ("A", ((0, 0), (0, 1))), 1: ("A", tuple((x, 0) for x in range(3, 8))),
                  2: ("B", tuple((x, 9) for x in range(9)))}  # the enemy is longer: our longest is the king
        return SimpleNamespace(board=board, game=SimpleNamespace(deaths=[crash]), before_death={0: living})

    def test_a_crash_3_steps_from_the_king_lands_in_the_first_band(self):
        near = f"{rulecounts.KING_BANDS[0][0]} path steps from the king"
        self.assertEqual(rulecounts.fold_crashes(self.crash_game(), "A", {}, {}, 397), [[400, near, 3, False]])
        # The same tiles with kelp between: 7 steps by the path, 3 as the crow flies.
        self.assertEqual(rulecounts.fold_crashes(self.crash_game(kelp=True), "A", {}, {}, 397),
                         [[400, f"{rulecounts.KING_BANDS[1][0]} path steps from the king", 7, False]])
        # Before foldRound, or a feed, it isn't a fold-window crash; a folding dragon's is marked.
        self.assertEqual(rulecounts.fold_crashes(self.crash_game(), "A", {}, {}, 401), [])
        self.assertEqual(rulecounts.fold_crashes(self.crash_game(), "A", {}, {(400, 0): "feed"}, 397), [])
        folding = {(399, 0): {"fold": "1"}}
        self.assertEqual(rulecounts.fold_crashes(self.crash_game(), "A", folding, {}, 397)[0][3], True)
        # The king itself crashing has no distance.
        king = self.crash_game()
        king.game.deaths[0]["id"] = 1
        self.assertEqual(rulecounts.fold_crashes(king, "A", {}, {}, 397), [[400, rulecounts.THE_KING, None, False]])
        tallies = rulecounts.events([{"crashes": rulecounts.fold_crashes(self.crash_game(), "A", {}, {}, 397)}], 397)
        self.assertEqual(tallies[f"fold-window crashes, {near}: in all"], 1)
        self.assertEqual(tallies[f"fold-window crashes, {near}: % of them"], 100)
        self.assertEqual(tallies["fold-window crashes: median path steps from the king"], 3)

    # On real replays (QUEEN and Trophy have portals, FOLD is a fold game to r499): the hits add up to the portal
    # crossings' deaths, every body a dragon ran into on its own move is found, and every crash with a path is in its
    # band.
    def test_real_replays_place_every_hit_and_measure_every_crash(self):
        import watch
        placed = 0
        for path in (QUEEN, TROPHY, FOLD):
            w = watch.watch(path)
            for side in "AB":
                deliberate = watch.deliberate_deaths(w)
                logs = {(r, i): rulecounts.parse_log(t) for r, i, team, t in w.logs if team == side}
                hits = rulecounts.portal_hits(w, side, deliberate)
                self.assertEqual(sum(hits.values()), sum(rulecounts.action_deaths(w, side, logs, deliberate)[
                    "portal crossing"][1:]), (path.name, side))
                for index, death in enumerate(w.game.deaths):
                    if death["team"] == side and death.get("step") and death["reason"] == "hitOtherBody":
                        self.assertIsNotNone(rulecounts.body_owner(w, index, death), (path.name, death))
                        placed += 1
                for r, label, steps, _ in rulecounts.fold_crashes(w, side, logs, deliberate, 397):
                    self.assertGreaterEqual(r, 397)
                    if steps is not None:
                        self.assertEqual(label, rulecounts.king_band(steps))
                        self.assertGreaterEqual(steps, 1)
        self.assertGreater(placed, 10)
        queen = rulecounts.extract(QUEEN, "A")
        self.assertEqual(queen["portal_hits"], {"teammate's body": 1, "enemy head (a ram)": 1,
                                                "its own body (hitSelf)": 2})
        text = rulecounts.report("aa92d8bf5da3", {"champion": [{**queen, "map": "queen"}]}, [], [], False, ROUNDS)
        self.assertIn("| portal crossing: deaths in all, by what it hit: its own body (hitSelf) | 2 | 2 |", text)
        self.assertIn("| fold-window crashes (r397+, into a body, kelp or itself on its own move, not deliberate): "
                      "in all | 3 | 3 |", text)


# M40 on a real game: champion aa92d8b's logging copy (side A) in review 1790439654199275000's 3844-tile game
# (rule-games/.../replays/champion/0002-gen_eval_1004429716-aA.replay), testdata/rulecounts-fold-farm-sample.json:
# every debug line of its dragons that ever folded or entered a farm on a folding, farm or split-out turn (trimmed to
# r, len, rule, farm, king, fold and feed), those dragons' deaths and the meals of their drops. Over the review's 7
# kept replays these flags tallied 119 farm, 1,652 king, 1,891 fold and 107 feed turns of 42,396 logged, the review's
# hand count exactly; the review counted 203 folding dragons in this game by hand, as here.
FOLD_FARM = json.loads((TESTDATA / "rulecounts-fold-farm-sample.json").read_text())


class TestOnlineCounterparts(unittest.TestCase):
    # M72, M75, M78: rule-counts.md set its ram, king and pearl-race rows against nothing but local games, so a ram,
    # king or contest change was read at local contact density, against opponents who never out-king us or contest
    # pearls. Real games: our 3182 on B in online game 95037 (Queen Of Spades) and on A in 95034 (Trauma), both to r499.
    def test_the_live_builds_blind_games_and_the_leaders_sit_beside_the_local_rows(self):
        import maintenance
        import replays
        summaries = {path: json.loads(json.dumps(replays.game_summary(replays.analyse(replays.load_replay(path)), None)))
                     for path in (QUEEN, TRAUMA)}  # as saved: through JSON
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            games = [{"id": 1, "submission_id": 7, "side": "B", "opponent": 262, "summary": summaries[QUEEN]},
                     {"id": 2, "submission_id": 7, "side": "A", "opponent": 263, "summary": summaries[TRAUMA]},
                     {"id": 3, "submission_id": 7, "side": "A", "opponent": 262, "summary": summaries[TRAUMA],
                      "kept": "loss"},  # chosen because we lost: never in a rate (MISSES 55)
                     {"id": 4, "submission_id": 7, "side": "A", "opponent": 545, "summary": summaries[TRAUMA]},  # dev
                     {"id": 5, "submission_id": 6, "side": "A", "opponent": 262, "summary": summaries[TRAUMA]}]
            (state / "online.json").write_text(json.dumps({"submission_id": 7, "games": games,
                                                          "benchmark": {"team": {"id": 545}}}))
            # The study's leaders: rank 1 (team 470) with one ranked game whose summary the study cached.
            (state / "study-history.jsonl").write_text(json.dumps({"leaders": [{"rank": 1, "id": 470, "name": "R1"},
                                                                               {"rank": 3, "id": 9, "name": "R3"}]}) + "\n")
            (state / "study-replays").mkdir()
            (state / "study-replays/index.jsonl").write_text(json.dumps(
                {"match": 11, "ranked": True, "teamAId": 1, "teamBId": 470}) + "\n")
            analyser = maintenance.hashlib.sha256((REPO / "tools/replays.py").read_bytes()).hexdigest()[:12]
            (state / "study.json").write_text(json.dumps({"summaries": {
                f"{state / 'study-replays' / '11.replay'}@{analyser}": {**summaries[QUEEN], "checks": {"ok": True}}}}))
            online = rulecounts.online_counterparts(REPO, state)
        self.assertEqual(online["sid"], 7)
        self.assertEqual([g["ours"] for g in online["games"]],
                         [summaries[QUEEN]["teams"]["B"], summaries[TRAUMA]["teams"]["A"]])
        self.assertEqual(online["leaders"], [("rank 1 (R1)", [summaries[QUEEN]["teams"]["B"]])])
        text = rulecounts.report("6827a20e6f08", {"champion": SAMPLE[:1]}, [], [], True, ROUNDS, online=online)
        ours = [summaries[QUEEN]["teams"]["B"], summaries[TRAUMA]["teams"]["A"]]
        self.assertIn(f"- online, the live build 7's 2 result-blind ladder and autoscrim games (online.sample_games, "
                      f"dev games apart): {replays.race_text(ours)}", text)
        self.assertIn(f"- rank 1 (R1), its studied ranked games: {replays.race_text(ours[:1])}", text)
        # M80: the crossings' online pair, ours and their opponents', beside the leaders'.
        theirs = [summaries[QUEEN]["teams"]["A"], summaries[TRAUMA]["teams"]["B"]]
        self.assertIn(f"- online, their opponents: {replays.crossing_text(theirs)}", text)
        self.assertIn(f"- rank 1 (R1), its studied ranked games: {replays.crossing_text(ours[:1])}", text)
        kings = [(summaries[QUEEN]["teams"]["B"]["king"]["length"], summaries[QUEEN]["teams"]["A"]["king"]["length"]),
                 (summaries[TRAUMA]["teams"]["A"]["king"]["length"], summaries[TRAUMA]["teams"]["B"]["king"]["length"])]
        self.assertIn(f"dev games apart)): ours {sum(o for o, _ in kings) / 2:.1f} v theirs {sum(t for _, t in kings) / 2:.1f} (2)",
                      text)
        started = [t["behaviour"]["headOnsStarted"] for t in ours]
        rams = sum(v for h in started for k, v in h.items() if k != "vsAlly") / 2
        self.assertIn(f"2 with head-on counts) {rams:.1f} a game", text)
        # The local rows read the same replay the same way: our copy's facts of game 95037 give its summary's numbers.
        local = dict(rulecounts.extract(QUEEN, "B"), map="queen", size="<=1024")
        text = rulecounts.report("6827a20e6f08", {"champion": [local]}, [], [], True, ROUNDS, online=online)
        self.assertIn(f"- here, all: {replays.race_text(ours[:1])}", text)
        self.assertIn(f"- here, all, ours: {replays.crossing_text(ours[:1])}", text)
        self.assertIn(f"Here: ours {kings[0][0]:.1f} v theirs {kings[0][1]:.1f} (1)", text)


class TestDebugFlags(unittest.TestCase):
    def watched(self):
        from types import SimpleNamespace
        import watch
        logs = {(r, i): rulecounts.parse_log(text) for r, i, text in FOLD_FARM["logs"]}
        game = SimpleNamespace(deaths=FOLD_FARM["deaths"], meals=FOLD_FARM["meals"])
        deliberate = watch.deliberate_deaths(SimpleNamespace(logs=[(r, i, "A", t) for r, i, t in FOLD_FARM["logs"]],
                                                             game=game))
        return SimpleNamespace(game=game), logs, deliberate

    def test_numeric_keys_are_tallied_flags_ids_and_bands(self):
        game = {"turns": [], "head_ons": [], "won": False, "drew": False, "pearls": 0, "head_ons_started": 0,
                "moments": {}, "logs": FOLD_FARM["logs"]}
        counts = rulecounts.census([game])
        lines = [text for _, _, text in FOLD_FARM["logs"]]
        share = lambda n: 100 * n / len(lines)
        # A hand tally of the raw lines, word by word.
        self.assertAlmostEqual(counts["% of logged turns: farm 1"], share(sum(" farm 1 " in t for t in lines)))
        self.assertAlmostEqual(counts["% of logged turns: fold set (an id, not -1)"],
                               share(sum(" fold -1 " not in t for t in lines)))
        self.assertAlmostEqual(counts["% of logged turns: feed 1"], share(sum(t.endswith(" feed 1") for t in lines)))
        self.assertIn("% of logged turns: king 1", counts)
        self.assertNotIn("% of logged turns: fold -1", counts)  # one row a flag or id key
        # A number the champion doesn't flag (I352's edge) goes by band, and a "nan" to its own band.
        banded = dict(game, logs=[[r, i, f"{t} edge {e}"] for (r, i, t), e in zip(FOLD_FARM["logs"], [-3, 0, 1, "nan"])])
        bands = rulecounts.census([banded])
        self.assertEqual([bands.get(f"% of logged turns: edge {b}", 0) > 0 for b in rulecounts.NUMBER_BANDS],
                         [True, False, True, True, False, True])
        self.assertIn("numbers by band: edge", rulecounts.log_keys([banded]))
        self.assertIn("0/1 flags (share set to 1): farm, feed, king", rulecounts.log_keys([banded]))
        self.assertIn("ids (share set, not -1): fold", rulecounts.log_keys([banded]))
        self.assertIn("words by value: rule", rulecounts.log_keys([banded]))
        self.assertTrue(rulecounts.log_keys([banded]).endswith("seen but not tallied: none."))

    # A fold ends in a feed (feedDirection, `rule feed`): the review's hand count had 86 of 203 merged; a feed death
    # gives 85, 57 of them eaten by the king the dragon walked to. Of the 104 other body, kelp or self deaths, 88 were
    # split-out parents (deliberate, counted apart) and 16 real crashes: the failure the report should show.
    def test_each_folding_dragon_is_merged_killed_crashed_or_alive(self):
        w, logs, deliberate = self.watched()
        folds = rulecounts.fold_outcomes(w, "A", logs, deliberate)
        self.assertEqual(len(folds), 203)  # the review's hand count
        self.assertEqual(Counter(outcome for _, outcome in folds),
                         {"merged, the king ate": 57, "merged, another ate": 28, "split out (deliberate)": 88,
                          "crashed on its own move": 16, "killed by an enemy": 4, "alive": 10})
        # Against the raw records: a merged dragon's dying line says `rule feed`; the 104 hitOtherBody, hitWall or
        # hitSelf deaths that aren't feeds are 88 split-out parents and 16 crashes; 64 of those 104 were into a body on a
        # folding turn.
        folding = {(r, i) for (r, i), f in logs.items() if f.get("fold") not in (None, "-1")}
        folders = {i for _, i in folding}
        deaths = [d for d in FOLD_FARM["deaths"] if d["id"] in folders]
        fed = [d for d in deaths if logs.get((d["round"], d["id"]), {}).get("rule") == "feed"]
        self.assertEqual(len(fed), 85)
        crashes = [d for d in deaths if d not in fed and d["reason"] in rulecounts.CRASHES]
        self.assertEqual(len(crashes), 104)
        self.assertEqual(sum(d["reason"] == "hitOtherBody" and (d["round"], d["id"]) in folding for d in crashes), 64)
        self.assertEqual(len(folders) - len(deaths), 10)
        self.assertTrue(all(length is not None and length >= 1 for length, _ in folds))

    def test_each_farm_entry_has_what_followed_within_its_window(self):
        w, logs, deliberate = self.watched()
        farms = rulecounts.farm_outcomes(w, "A", logs)
        self.assertEqual(len(farms), sum(" farm 1 " in text for _, _, text in FOLD_FARM["logs"]))
        self.assertEqual(Counter(outcome for _, _, outcome, _ in farms), {"split-out": 89, "alive": 14, "died": 1})
        # M76 (I639): entries by folding dragons apart, the turns whose line also names a king to walk to.
        folding = sum(f.get("farm") == "1" and f.get("fold") not in (None, "-1") for f in logs.values())
        self.assertEqual(sum(f[3] for f in farms), folding)
        # A split-out: the same dragon's rule splitout within FARM_WINDOW rounds of the entry.
        r, length, _, _ = next(f for f in farms if f[2] == "split-out")
        i = next(i for (round_, i), f in logs.items() if round_ == r and f.get("farm") == "1")
        self.assertTrue(any(f.get("rule") == "splitout" and r <= round_ <= r + rulecounts.FARM_WINDOW
                            for (round_, j), f in logs.items() if j == i))
        folds = rulecounts.fold_outcomes(w, "A", logs, deliberate)
        tallies = rulecounts.events([{"farms": farms, "folds": folds}], 397, 471)
        self.assertEqual(tallies["farm entries: in all"], 104)
        self.assertEqual(sum(tallies[f"farm entries, length {b}: in all"] for b in rulecounts.BANDS), 104)
        self.assertEqual(sum(tallies[f"farm entries, {p}: in all"] for p in rulecounts.phases(397, 471)), 104)
        self.assertEqual((tallies["farm entries, by folding dragons (fold set): in all"],
                          tallies["farm entries, by dragons not folding: in all"]), (folding, 104 - folding))
        self.assertAlmostEqual(tallies["folding dragons: % merged (fed to a teammate)"], 100 * 85 / 203)
        self.assertAlmostEqual(tallies["folding dragons: % crashed on its own move"], 100 * 16 / 203)
        self.assertAlmostEqual(tallies["folding dragons: % split out (deliberate)"], 100 * 88 / 203)
        facts = dict(SAMPLE[0], map="m", farms=farms, folds=folds, logs=FOLD_FARM["logs"])
        text = rulecounts.report("aa92d8bf5da3", {"champion": [facts]}, [], [], True, ROUNDS)
        self.assertIn("| folding dragons: in all | 203 | 203 |", text)
        self.assertIn("| farm entries: in all | 104 | 104 |", text)
        self.assertIn("Debug-line keys (method note M40): read by the rows above: len, r;", text)

    # M76, M83 on a real game (testdata/rulecounts-doom-cap-sample.json: champion a5e4552's logging copy on a deadend
    # map, its note says what was cut): deaths on the dragon's own move at `cap 1`, by when exact reach (watch.py doom)
    # and the bot's `doom 1` flag found them doomed, whether a `cap 0` turn came inside the doom, whether they split out
    # there, deliberate ones apart; and our longest's hitSelf deaths by the cap flag.
    def test_deaths_at_the_cap_are_read_by_exact_reach_beside_the_flag_deliberate_apart(self):
        from types import SimpleNamespace
        import watch
        sample = json.loads((TESTDATA / "rulecounts-doom-cap-sample.json").read_text())
        logs = {(r, i): rulecounts.parse_log(text) for r, i, text in sample["logs"]}
        deaths = sample["deaths"]
        dooms = [SimpleNamespace(doomed_since=since, lead=None if since is None else d["round"] - since)
                 for since, d in zip(sample["doomed_since"], deaths)]
        w = SimpleNamespace(game=SimpleNamespace(deaths=deaths), dooms=dooms,
                            logs=[(r, i, "B", text) for r, i, text in sample["logs"]])
        rows = rulecounts.doomed_at_cap(w, "B", logs, watch.deliberate_deaths(w))
        self.assertEqual(len(rows), 45)  # not the 2 at cap 0 or the head-on it was hit in
        # The 7 split-out parents (doomed a round before, split out there) and 6 feeds are counted apart.
        self.assertEqual(Counter(r[5] for r in rows if r[5]), {"split-out parent": 7, "feed": 6})
        self.assertTrue(all(r[1] >= 1 and r[2] == 1 and r[4] for r in rows if r[5] == "split-out parent"))
        tallies = rulecounts.events([{"doomed_at_cap": rows, "self_deaths": [[26, "1"]]}], 397, 471)
        label = "own-move deaths at the cap (cap 1), not deliberate, length "
        # M83: of the 22 8+ long, the flag said doom 1 on the dying turn of 21 and on no turn before, where exact reach
        # found 14 doomed 1-4 rounds before, 7 of them with a slot free inside the doom.
        long = [label + band for band in ("8-15", "16+")]
        self.assertEqual([sum(tallies[f"{b}: {key}"] for b in long) for key in (
            "in all", "the flag's doom 1 on the dying turn", "doomed 1+ turns before by the flag",
            "doomed 1+ rounds before by exact reach")], [22, 21, 0, 14])
        self.assertEqual(tallies[f"{label}16+: % of those with a cap 0 turn inside the doom"], 100)
        self.assertAlmostEqual(tallies[f"{label}8-15: % of those with a cap 0 turn inside the doom"], 100 * 5 / 12)
        self.assertEqual(tallies["own-move deaths at the cap, deliberate (a feed or a split-out's parent), length <8: "
                                 "in all"], 13)
        self.assertEqual(tallies["our longest's hitSelf deaths, cap 1: in all"], 1)

    def test_a_number_on_a_band_edge_goes_to_the_band_its_rounding_would(self):
        self.assertEqual([rulecounts.log_value("edge", v) for v in ("-1.5", "-1.4", "1.4", "1.5", "inf")],
                         ["<= -2", "-1", "1", ">= 2", "not a finite number"])


# M43 on a real game: champion aa92d8b's logging copy (side A) against itself in review 1790446132546874000's kept
# rule-game rule-games/.../replays/champion/0002-gen_eval_1007426557-aA.replay, testdata/rulecounts-birth-sample.json:
# every dragon's born round and first logged line (its birth length), and every line with body < len or 12+ long,
# trimmed to r, len, body and rule. So its body < len counts and its 12+ lines are the whole game's: over the full
# 30,528 logged turns, 782 of the 845 body < len turns were dragons born 8+ long.
BIRTH = json.loads((TESTDATA / "rulecounts-birth-sample.json").read_text())


class TestBirths(unittest.TestCase):
    def counts(self) -> dict[str, int]:
        from types import SimpleNamespace
        dragons = {int(i): SimpleNamespace(born=born) for i, born in BIRTH["born"].items()}
        logs = {(r, i): rulecounts.parse_log(text) for r, i, text in BIRTH["logs"]}
        return rulecounts.birth_counts(SimpleNamespace(game=SimpleNamespace(dragons=dragons)), "A", logs)

    def test_untracked_bodies_and_split_outs_by_birth_length_and_age(self):
        counts = self.counts()
        # By hand from the raw lines: a dragon's birth length is its first line's len.
        first, untracked, long, splitouts = {}, Counter(), Counter(), Counter()
        for r, i, text in sorted(BIRTH["logs"]):
            f = dict(zip(text.split()[0::2], text.split()[1::2]))
            size = float(f["len"])
            first.setdefault(i, size)
            label = "2" if first[i] <= 2 else "3-7" if first[i] <= 7 else "8+"
            untracked[label] += float(f["body"]) < size
            if size >= rulecounts.LONG:
                long[label] += 1
                splitouts[label] += f.get("rule") == "splitout"
        for label in ("2", "3-7", "8+"):
            self.assertEqual(counts.get(f"birth|{label}|untracked", 0), untracked[label], label)
            self.assertEqual(counts.get(f"birth|{label}|long", 0), long[label], label)
            self.assertEqual(counts.get(f"birth|{label}|splitouts", 0), splitouts[label], label)
        self.assertEqual([counts[f"birth|{b}|untracked"] for b in ("2", "3-7", "8+")], [6, 41, 782])
        self.assertEqual([counts[f"age|{b}|untracked"] for b in ("0-5", "6-20", "21+")], [646, 168, 15])
        self.assertEqual((counts["birth|8+|long"], counts["birth|8+|splitouts"]), (958, 120))
        self.assertFalse(any(key.startswith("own|") or " own " in key for key in counts))  # no own key logged yet

    def test_the_report_rows_and_the_hooks_for_own_and_sym(self):
        counts = self.counts()
        rows = rulecounts.events([{"birth": counts}])
        self.assertAlmostEqual(rows["dragons born 8+ long: % body < len"],
                               100 * counts["birth|8+|untracked"] / counts["birth|8+|turns"])
        self.assertAlmostEqual(rows["dragons born 8+ long: split-outs per 100 turns 12+ long"], 100 * 120 / 958)
        self.assertEqual(sum(1 for row in rows if row.startswith(("dragons aged", "dragons born"))), 18)  # 6 groups x 3 rows
        # I370's own and sym, once I352 logs them, on the same real lines.
        from types import SimpleNamespace
        dragons = {int(i): SimpleNamespace(born=born) for i, born in BIRTH["born"].items()}
        logs = {(r, i): {**rulecounts.parse_log(t), "own": "born" if "rule splitout" in t else "full", "sym": "known"}
                for r, i, t in BIRTH["logs"]}
        tagged = rulecounts.birth_counts(SimpleNamespace(game=SimpleNamespace(dragons=dragons)), "A", logs)
        rows = rulecounts.events([{"birth": tagged}])
        self.assertAlmostEqual(rows["dragons born 8+ long: % sym known"], 100)
        self.assertAlmostEqual(rows["turns logged own born: split-outs per 100 turns 12+ long"], 100)
        self.assertEqual(rows["turns logged own full: split-outs per 100 turns 12+ long"], 0)


# M50: split children by the rule their parent's debug line gave on the split turn. The real sample is champion
# 600cd4e's logging copy (side A) against the breeder in review 1790492425732703000's kept rule-game
# rule-games/.../replays/breeder/0006-gen_eval_614144185-aA.replay, testdata/rulecounts-split-rule-sample.json: every
# split event of side A as [round, parent id, child id] and the parent's debug line on each split turn, trimmed to r,
# len and rule.
SPLIT_RULE = json.loads((TESTDATA / "rulecounts-split-rule-sample.json").read_text())


class TestSplitChildren(unittest.TestCase):
    # Side A: king 0 (the longest at the end) splits children 1, 2, 4, 5, 7 and 8, child 2 splits 3 and 6. `lines` are
    # the debug lines by (round, id); `turns` each dragon's turn rounds.
    @staticmethod
    def game(lines: dict[tuple[int, int], str]):
        from types import SimpleNamespace
        import watch
        splits = [(10, 0, 1), (20, 0, 2), (24, 2, 3), (30, 0, 4), (40, 0, 5), (40, 2, 6), (50, 0, 7), (60, 0, 8)]
        turns = {1: range(10, 13), 2: range(20, 26), 3: range(24, 31), 4: (30, 31), 5: (40, 41), 6: (40, 41),
                 7: range(50, 54), 8: range(60, 64)}
        wall = {"team": "A", "reason": "hitWall", "step": 1, "length": 2}
        deaths = [{**wall, "round": 12, "id": 1},  # age 2: a failure in its first 3 rounds
                  {**wall, "round": 31, "id": 4}, {**wall, "round": 41, "id": 5},  # both feeds
                  {**wall, "round": 41, "id": 6, "reason": "hitHeadToHead", "movedInto": 99, "movedIntoTeam": "B"},
                  {**wall, "round": 63, "id": 8}]  # age 3: past its first 3 rounds
        meals = [{"round": 32, "id": 0, "team": "A", "death": 1},  # the king eats 4's drop
                 {"round": 42, "id": 3, "team": "A", "death": 2}]  # a teammate, not the king, eats 5's
        dead = {d["id"] for d in deaths}
        dragons = {i: SimpleNamespace(id=i, team="A", alive=i not in dead, body=[(0, 0)] * (10 if i == 0 else 2),
                                      parent=next((p for _, p, c in splits if c == i), None),
                                      born=next((r for r, _, c in splits if c == i), -1)) for i in range(9)}
        w = SimpleNamespace(
            turns={(r, i): watch.Turn("N", team="A") for i, rounds in turns.items() for r in rounds},
            game=SimpleNamespace(deaths=deaths, meals=meals, dragons=dragons, teams={"A": SimpleNamespace(
                splits=[{"round": r, "parentId": p, "childId": c} for r, p, c in splits])}))
        logs = {key: rulecounts.parse_log(text) for key, text in lines.items()}
        return w, "A", logs, {(31, 4): "feed", (41, 5): "feed"}

    LINES = {(10, 0): "r 10 len 12 rule split", (20, 0): "r 20 len 14 rule splitout",
             (24, 2): "r 24 len 12 rule splitout", (29, 3): "r 29 len 12 rule splitout",
             (30, 0): "r 30 len 6 rule shed", (31, 4): "r 31 len 2 rule feed",
             (40, 0): "r 40 len 8 rule threatshed", (40, 2): "r 40 len 5 rule threatshed",
             (49, 0): "r 49 len 8 rule split", (60, 0): "r 60 len 8 rule split"}

    def test_each_child_takes_its_parents_rule_on_the_split_turn(self):
        children = rulecounts.split_children(*self.game(self.LINES))
        self.assertEqual(children, [
            ["split", 3, 1, False, False, False],  # died on its own move at age 2
            ["splitout", 3, 0, True, False, False],  # split out itself at age 4, inside the RESPLIT rounds
            ["splitout", 3, 0, False, False, False],  # its split-out at age 5 is past them
            ["shed", 2, 0, False, True, True],  # a feed the king ate: merged into the king
            ["threatshed", 2, 0, False, True, False],  # a feed another teammate ate
            ["threatshed", 2, 0, False, False, False],  # a ram at age 1 is no failure
            [rulecounts.UNKNOWN_RULE, 3, 0, False, False, False],  # its parent's line is a round early
            ["split", 3, 0, False, False, False]])  # died at age 3: past the first 3 rounds
        self.assertEqual(rulecounts.YOUNG, 3)  # the boundaries the cases sit on
        self.assertEqual(rulecounts.RESPLIT, 5)
        rows = rulecounts.events([{"children": children}])
        self.assertEqual(rows["split children, parent's rule split: made"], 2)
        self.assertEqual(rows["split children, parent's rule split: failures on own move in those turns"], 1)
        self.assertAlmostEqual(rows["split children, parent's rule split: % of those turns ending in a failure"],
                               100 / 6)
        self.assertEqual(rows["split children, parent's rule splitout: % splitting out again in their first 5 rounds"],
                         50)
        self.assertEqual(rows["split children, parent's rule shed: % merged into the king (fed, the king ate its "
                              "drop)"], 100)
        self.assertEqual(rows["split children, parent's rule threatshed: % died by a feed"], 50)
        self.assertEqual(rows["split children, parent's rule threatshed: % merged into the king (fed, the king ate "
                              "its drop)"], 0)
        self.assertEqual(rows["split children, rule unknown: made"], 1)

    def test_without_debug_lines_every_child_is_rule_unknown_and_an_odd_rule_gets_its_own_rows(self):
        children = rulecounts.split_children(*self.game({}))
        self.assertEqual({c[0] for c in children}, {rulecounts.UNKNOWN_RULE})
        rows = rulecounts.events([{"children": children}])
        self.assertEqual(rows["split children, rule unknown: made"], 8)
        self.assertEqual(rows["split children, parent's rule split: made"], 0)
        self.assertNotIn("split children, parent's rule split: % died by a feed", rows)  # no children, no share
        odd = rulecounts.split_children(*self.game({**self.LINES, (10, 0): "r 10 len 12 rule score"}))
        self.assertEqual(rulecounts.events([{"children": odd}])["split children, parent's rule score: made"], 1)

    def test_real_split_turns_all_carry_a_split_rule(self):
        from types import SimpleNamespace
        w = SimpleNamespace(turns={}, game=SimpleNamespace(deaths=[], meals=[], dragons={}, teams={"A": SimpleNamespace(
            splits=[{"round": r, "parentId": p, "childId": c} for r, p, c in SPLIT_RULE["splits"]])}))
        logs = {(r, i): rulecounts.parse_log(text) for r, i, text in SPLIT_RULE["logs"]}
        rules = Counter(c[0] for c in rulecounts.split_children(w, "A", logs, {}))
        self.assertEqual(rules, {"split": 258, "splitout": 23, "threatshed": 10, "shed": 1})

    # On the real testdata replays (no debug lines, so every child is rule unknown): a child for every split event,
    # and their first-3-round turns and failures are the split child row's.
    def test_real_replays_rows_add_up_to_every_split_child(self):
        import watch
        total = 0
        for path in (QUEEN, TROPHY, FOLD, TRAUMA):
            w = watch.watch(path)
            deliberate = watch.deliberate_deaths(w)
            for side in "AB":
                children = rulecounts.split_children(w, side, {}, deliberate)
                row = rulecounts.action_deaths(w, side, {}, deliberate)["split child, first 3 rounds"]
                self.assertEqual(len(children), len(w.game.teams[side].splits), (path.name, side))
                self.assertEqual([sum(c[1] for c in children), sum(c[2] for c in children)], row[:2],
                                 (path.name, side))
                rows = rulecounts.events([{"children": children}])
                self.assertEqual(sum(v for k, v in rows.items() if k.endswith(": made")), len(children))
                total += len(children)
        self.assertEqual(total, 254)
        text = rulecounts.report("aa92d8bf5da3", {"champion": [{**rulecounts.extract(QUEEN, "A"), "map": "queen"}]},
                                 [], [], False, ROUNDS)
        self.assertIn("| split children, rule unknown: made | 55 | 55 |", text)


class TestLaunch(unittest.TestCase):
    def exec_review(self, state: Path, work: Path):
        with patch.object(rulecounts, "play_and_count", side_effect=RuntimeError("no host reachable")), \
                patch.object(rulecounts.os, "dup2"), patch.object(rulecounts.os, "chdir"), \
                patch.object(rulecounts.os, "execvpe", side_effect=SystemExit(0)) as start:
            with self.assertRaises(SystemExit):
                rulecounts.review(state, work, measure_first=True)
        return start

    def test_the_worker_starts_as_a_plain_confined_launch_after_a_failed_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)
            start = self.exec_review(state, work)
            program, command, env = start.call_args[0]
            settings = meta.worker_settings(state, "meta", work)
            self.assertEqual(command, meta.private_tmp(meta.worker_command(work, settings,
                                                                           meta.worker_reads(state, "meta"), usage="review")))
            self.assertEqual((program, env), (command[0], meta.worker_env()))
            # The measurement's folder is readable by the worker and never writable.
            self.assertIn(str(state / "rule-games"), command)
            self.assertIn(f"Edit(/{state / 'rule-games'}/**)", json.loads(settings)["permissions"]["deny"])
            text = (work / rulecounts.NAME).read_text()
            self.assertTrue(text.startswith(rulecounts.FAILED) and "no host reachable" in text)
            self.assertEqual(rulecounts.status(state, work), "failed")

    def test_a_stop_marker_ends_the_review_before_the_worker_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)
            rulecounts.run_folder(state, work).mkdir(parents=True)
            rulecounts.save_status(rulecounts.run_folder(state, work), "done")
            (state / "STOP").write_text("")
            with patch.object(rulecounts.os, "execvpe") as start, self.assertRaises(SystemExit):
                rulecounts.review(state, work, measure_first=False)
            start.assert_not_called()

    def test_being_stopped_takes_the_games_process_group_down(self):
        games = subprocess.Popen(["/bin/sh", "-c", "sleep 60 & wait"], start_new_session=True)
        try:
            with patch.object(rulecounts, "_games", games), self.assertRaises(SystemExit):
                rulecounts.on_term(15, None)
            self.assertIsNotNone(games.poll())
            with self.assertRaises(ProcessLookupError):
                os.killpg(games.pid, 0)  # the backgrounded sleep went with it
        finally:
            if games.poll() is None:
                os.killpg(games.pid, 9)

    def test_status_trusts_only_the_process_that_started_measuring(self):
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)
            folder = rulecounts.run_folder(state, work)
            folder.mkdir(parents=True)
            rulecounts.save_status(folder, "measuring")
            self.assertEqual(rulecounts.status(state, work), "measuring")
            record = json.loads((folder / "status.json").read_text())
            for pid in (1, 99999999):  # another process now (pid 1 can't even be signalled), or none
                (folder / "status.json").write_text(json.dumps({**record, "pid": pid}))
                self.assertIsNone(rulecounts.status(state, work))
            (work / rulecounts.NAME).write_text("# Rule counts for champion f9f3bd7\n")  # the worker's file
            self.assertIsNone(rulecounts.status(state, work))


class TestPlanted(unittest.TestCase):
    def test_a_relaunch_never_measures_or_opens_anything_in_the_work_folder(self):
        # A worker that failed its run on purpose could leave a FIFO, a folder or a second hard link where the
        # unsandboxed step used to read and write (every relaunch then died or blocked); now nothing there is used.
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)
            rulecounts.save_prompt(state, work, "the saved prompt")  # as meta.prepare_review does
            os.link(work / "PROMPT.md", work / "P2")
            (work / "PROMPT.md").unlink()
            os.mkfifo(work / "PROMPT.md")
            (work / "session.json").mkdir()
            os.mkfifo(work / "session.stderr")
            seen = {}

            def dup2(fd, target):
                seen.setdefault("targets", []).append((target, os.fstat(fd).st_ino))
                if target == 0:
                    seen["stdin"] = os.read(fd, 100)

            with patch.object(rulecounts, "measure") as measured, \
                    patch.object(rulecounts.os, "dup2", side_effect=dup2), patch.object(rulecounts.os, "chdir"), \
                    patch.object(rulecounts.signal, "signal") as handlers, \
                    patch.object(rulecounts.os, "execvpe", side_effect=SystemExit(0)) as start:
                with self.assertRaises(SystemExit):
                    rulecounts.review(state, work, measure_first=False)
            measured.assert_not_called()
            start.assert_called_once()
            self.assertTrue(seen["stdin"].startswith(b"the saved prompt"), seen["stdin"])  # plus meta.brief's line
            folder = rulecounts.run_folder(state, work)
            self.assertEqual([ino for _, ino in seen["targets"]],
                             [os.stat(folder / name).st_ino for name in ("PROMPT.md", "session.json", "session.stderr")])
            self.assertTrue((work / "session.json").is_dir())  # left alone
            reset = {call.args for call in handlers.call_args_list}
            self.assertLessEqual({(rulecounts.signal.SIGPIPE, rulecounts.signal.SIG_DFL),
                                  (rulecounts.signal.SIGXFSZ, rulecounts.signal.SIG_DFL)}, reset)

    @unittest.skipUnless(hasattr(os, "chflags"), "needs chflags")
    def test_a_launch_undoes_the_workers_modes_and_flags_on_its_folder(self):
        import stat
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)
            os.chmod(work, 0o000)
            os.chflags(work, stat.UF_IMMUTABLE)
            try:
                with patch.object(meta.subprocess, "Popen") as popen:
                    popen.return_value.pid = 4242
                    self.assertEqual(meta.launch_review(state, work, measure=False), 4242)
            finally:
                os.chflags(work, 0)
                os.chmod(work, 0o755)
            self.assertEqual(popen.call_args.kwargs["cwd"], rulecounts.run_folder(state, work))
            self.assertNotIn("--measure", popen.call_args.args[0])
            # Its rule counts' analysis runs at home.WORKER_NICE, not nice 0 (29 Sep 16:05: this Mac at load 36).
            self.assertEqual(popen.call_args.args[0][:3], ["/usr/bin/nice", "-n", str(meta.nice_increment())])
            with patch.object(meta.subprocess, "Popen") as popen:
                popen.return_value.pid = 4243
                os.chmod(work, 0o555)
                os.chflags(work, stat.UF_IMMUTABLE)
                meta.launch_review(state, work, measure=True)
                info = os.stat(work)
            self.assertEqual((info.st_flags, stat.S_IMODE(info.st_mode)), (0, 0o755))
            self.assertIn("--measure", popen.call_args.args[0])

    def test_a_review_measures_on_its_first_launch_only_and_is_abandoned_after_two_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)

            def retry_now():
                record = json.loads((state / "review.json").read_text())
                (state / "review.json").write_text(json.dumps({**record, "attempted_at": 0}))

            with patch.object(meta, "prepare_review", return_value=work), \
                    patch.object(meta, "launch_review", return_value=4242) as launch, \
                    patch.object(meta, "alive", return_value=False):
                self.assertIn("launched", meta.run_meta(REPO, state))
                self.assertIn("launch 1 of 2 failed", meta.run_meta(REPO, state))  # exited with no output
                self.assertIn("deferred", meta.run_meta(REPO, state))
                retry_now()
                self.assertIn("launched", meta.run_meta(REPO, state))
                self.assertIn("abandoned", meta.run_meta(REPO, state))
            self.assertEqual([call.kwargs["measure"] for call in launch.call_args_list], [True, False])
            record = json.loads((state / "review.json").read_text())
            self.assertNotIn("pending", record)
            [abandoned] = record["abandoned"]
            self.assertEqual(abandoned["work"], str(work))
            self.assertEqual(len(abandoned["failures"]), 2)

    def test_what_the_worker_plants_in_its_folder_is_never_built_played_or_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)
            outside = Path(directory) / "outside"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep")
            (outside / "target.md").write_text("untouched")
            planted = work / "rule-games"
            (planted / "bots" / "champion-log-000000000000").mkdir(parents=True)
            (planted / "bots" / "champion-log-000000000000" / "main.cpp").write_text("int main() {}")
            (planted / "maps").mkdir()
            (planted / "maps" / "planted.map").write_text("x")
            (planted / "replays").mkdir()
            (planted / "replays" / "champion").symlink_to(outside)
            (work / rulecounts.NAME).symlink_to(outside / "target.md")
            (work / "session.json").symlink_to(outside / "target.md")
            built, played = [], []

            def build(spec, into=None):
                built.append(into)
                folder = into / f"{spec['name']}-built"
                folder.mkdir()
                return folder

            def maps(out, seed, count, style="eval"):
                out.mkdir(parents=True, exist_ok=True)
                for k in range(count):
                    (out / f"gen_{style}_{seed + k}.map").write_text("map")

            def play(repo, python, a, b, chosen, out, replays, timeout, log):
                played.append((a, b, chosen))
                replays.mkdir(parents=True)
                (replays / "0000-gen_0-aA.replay").write_bytes(b"")
                return None

            with patch.object(driver, "gauntlet_bot", side_effect=build), \
                    patch.object(driver, "rotation_bots", side_effect=lambda names: [(names[0], build(
                        {"name": names[0]}, rulecounts.run_folder(state, work)))]), \
                    patch.object(driver, "generate_maps", side_effect=maps), \
                    patch.object(rulecounts, "play", side_effect=play), \
                    patch.object(rulecounts, "extract", return_value=SAMPLE[0]), \
                    patch.object(rulecounts.os, "dup2"), patch.object(rulecounts.os, "chdir"), \
                    patch.object(rulecounts.os, "execvpe", side_effect=SystemExit(0)), self.assertRaises(SystemExit):
                rulecounts.review(state, work, measure_first=True)
            protected = rulecounts.run_folder(state, work).resolve()
            for folder in built:
                self.assertTrue(folder.resolve().is_relative_to(protected))
            for a, b, chosen in played:
                for path in (a, b, *map(Path, chosen)):
                    self.assertTrue(path.resolve().is_relative_to(protected), path)
            self.assertEqual(len(played), len(rulecounts.OPPONENTS))
            # Every opponent plays every map, in one order, so each column differs by opponent and not by map (M25).
            self.assertEqual({tuple(chosen) for _, _, chosen in played}, {tuple(played[0][2])})
            self.assertEqual(len(played[0][2]), rulecounts.MAPS)
            self.assertEqual((outside / "target.md").read_text(), "untouched")
            self.assertEqual((outside / "keep.txt").read_text(), "keep")
            self.assertTrue((work / rulecounts.NAME).is_symlink())  # not a plain file: left alone, not followed
            folder = rulecounts.run_folder(state, work)
            self.assertTrue((folder / rulecounts.NAME).read_text().startswith("# Rule counts for champion"))
            self.assertTrue((folder / "session.json").exists())
            self.assertEqual(rulecounts.status(state, work), "done")
            saved = (rulecounts.run_folder(state, work) / "PROMPT.md").read_text()
            self.assertTrue(saved.startswith("review"), saved)  # saved once, then the briefing line (meta.brief)
            self.assertEqual(saved.count("briefing.md"), 1)

    def test_cleanup_refuses_a_folder_that_resolves_outside_the_rule_games(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "rule-games"
            base.mkdir()
            outside = Path(directory) / "outside"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep")
            (base / "1" / "replays").mkdir(parents=True)
            (base / "1" / "replays" / "champion").symlink_to(outside)
            rulecounts.remove(base / "1" / "replays", base)  # a link inside is unlinked, never followed
            self.assertTrue((outside / "keep.txt").exists())
            (base / "2").symlink_to(outside)
            rulecounts.remove(base / "2", base)
            self.assertTrue((outside / "keep.txt").exists())
            with self.assertRaises(RuntimeError):
                rulecounts.remove(outside, base)


class TestHealth(unittest.TestCase):
    def test_health_alerts_on_a_review_pending_past_four_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)
            prepared = int(work.name) / 1e9
            self.assertEqual(health.review_pending(state, prepared + health.REVIEW_PENDING - 60), [])
            [alert] = health.review_pending(state, prepared + health.REVIEW_PENDING + 60)
            self.assertIn(work.name, alert.text)
            (state / "review.json").write_text(json.dumps({"last": str(work)}))  # applied
            self.assertEqual(health.review_pending(state, prepared + 10 * health.REVIEW_PENDING), [])

    def test_health_alerts_on_the_newest_reviews_failed_or_dead_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            state, work = review_folder(directory)
            self.assertEqual(health.rule_counts(state, time.time()), [])  # before its measurement starts
            folder = rulecounts.run_folder(state, work)
            folder.mkdir(parents=True)
            (folder / rulecounts.NAME).write_text(f"{rulecounts.FAILED}: RuntimeError: no host reachable\n")
            rulecounts.save_status(folder, "failed")
            [alert] = health.rule_counts(state, time.time())
            self.assertIn("no host reachable", alert.text)
            record = json.loads((folder / "status.json").read_text())
            (folder / "status.json").write_text(json.dumps({**record, "status": "measuring", "pid": 99999999}))
            [alert] = health.rule_counts(state, time.time())
            self.assertIn("died", alert.text)
            rulecounts.save_status(folder, "done")
            self.assertEqual(health.rule_counts(state, time.time()), [])


if __name__ == "__main__":
    unittest.main()

"""The ladder's active map pool (loop/ladder_maps.py): the hourly read, the health alert and the official-map report's
maps, on GET /maps as the server answered it on 2026-09-26 22:05 AEST (testdata/ladder-maps-sample.json: every field
as returned; Default Small's mapText whole, the others' cut to their first five lines and their last, which the tests
fill back in from our own copy of each map in maps/, whose head and tail match the capture)."""

import testhome  # before anything that reads live paths
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import driver
import health
import ladder_maps

SPRINT = {"Prisoners Dilemma": "dilemma", "Autarky": "autarky"}  # maps/sprint's file names for the two Sprint maps
NOW = 1790430000.0
REPO = Path(__file__).resolve().parent.parent


# A captured map with its whole mapText: our copy's (maps/ladder, maps/retired or maps/sprint) with the server's
# MAP_NAME line, so a cut text's single TILE line doesn't make it look gapless (four start with a 0 0 tile). The
# capture keeps each map's head (its lines before the last, MAP_NAME aside) and last line, which our copy must match.
def whole(m: dict) -> dict:
    stem = SPRINT.get(m["name"], m["name"].lower().replace(" ", "_"))
    copy = next(REPO / f"maps/{folder}/{stem}.map" for folder in ("ladder", "retired", "sprint")
                if (REPO / f"maps/{folder}/{stem}.map").exists())
    cut, ours = m["mapText"].splitlines(), copy.read_text().splitlines()
    named = lambda a, b: a == b or (a.startswith("MAP_NAME ") and b.startswith("MAP_NAME "))
    if len(ours) < len(cut) or not all(map(named, cut[:-1], ours)) or ours[-1] != cut[-1]:
        raise ValueError(f"{copy} doesn't match the captured head and tail of {m['name']}")
    name = next(line for line in cut if line.startswith("MAP_NAME "))
    lines = [name if line.startswith("MAP_NAME ") else line for line in ours]
    return {**m, "mapText": "\n".join(lines) + "\n"}


SAMPLE = [whole(m) for m in json.loads((REPO / "loop/testdata/ladder-maps-sample.json").read_text())]
EMPTY = {kind: [] for kind in ("new", "gone", "changed", "pearls changed", "variant", "withheld", "gapless")}


# The three ways the site may withhold pearl timings after 2026-09-27 21:00 AEST (the format wasn't known when this
# was written): every TILE line removed, cut to `TILE x y`, or its gaps not numbers.
def withheld(text: str, how: str) -> str:
    out = []
    for line in text.splitlines():
        f = line.split()
        if f[:1] == ["TILE"]:
            if how == "removed":
                continue
            line = " ".join(f[:3]) + (" ? ?" if how == "not numbers" else "")
        out.append(line)
    return "\n".join(out) + "\n"


# `text` with every tile's pearl timings changed: a variant with the same kelp, portals and dragons.
def repearled(text: str) -> str:
    return "\n".join(" ".join([*line.split()[:3], "1", "77"]) if line.startswith("TILE ") else line
                     for line in text.splitlines()) + "\n"


# A repo with our copies of the sample's maps as they are spelled in maps/: the nine older ones in maps/ladder, the
# Sprint two in maps/sprint; Stronghold's MAP_NAME lowercase and Trophy with CRLF line endings, as a checkout may have.
def local_repo(root: Path, response: list[dict] = SAMPLE) -> Path:
    for folder in ladder_maps.LOCAL:
        (root / folder).mkdir(parents=True, exist_ok=True)
    for m in response:
        text = m["mapText"]
        if m["name"] == "Stronghold":
            text = text.replace("MAP_NAME Stronghold", "MAP_NAME stronghold")
        if m["name"] == "Trophy":
            text = text.replace("\n", "\r\n")
        folder = "maps/sprint" if m["name"] in SPRINT else "maps/ladder"
        stem = SPRINT.get(m["name"], m["name"].lower().replace(" ", "_"))
        (root / folder / f"{stem}.map").write_bytes(text.encode())
    return root


def saved(state: Path, response: list[dict]) -> dict:
    record = {"fetched_at": NOW, "attempt_at": NOW, "maps": ladder_maps.active_maps(response)}
    ladder_maps.save(state, record)
    return record


class LadderMaps(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.repo = local_repo(self.root / "repo")

    def test_the_read_keeps_active_maps_with_raw_and_content_hashes(self) -> None:
        response = SAMPLE + [{**SAMPLE[0], "id": 1, "name": "Retired", "active": False}]
        maps = ladder_maps.active_maps(response)
        self.assertEqual(len(maps), 11)
        self.assertNotIn("Retired", [m["name"] for m in maps])
        self.assertEqual(maps[2], {"id": 17, "name": "Prisoners Dilemma", "size": "32×16",
                                   "sha256": ladder_maps.hashlib.sha256(SAMPLE[2]["mapText"].encode()).hexdigest(),
                                   "content": ladder_maps.content_hash(SAMPLE[2]["mapText"]),
                                   "layout": ladder_maps.layout_hash(SAMPLE[2]["mapText"]), "gapless": False})
        text = SAMPLE[0]["mapText"]  # Default Small, whole
        self.assertEqual(len(text.splitlines()), 779)
        spelled = text.replace("\n", "  \r\n").replace("Default Small", "default_small") + "\n\n"
        self.assertEqual(ladder_maps.content_hash(spelled), ladder_maps.content_hash(text))
        self.assertNotEqual(ladder_maps.content_hash(text.replace("TILE 0 0 1 1000", "TILE 0 0 1 900")),
                            ladder_maps.content_hash(text))

    def test_refresh_saves_hourly_and_a_failure_keeps_the_last_pool(self) -> None:
        calls = []
        fetch = lambda path: calls.append(path) or SAMPLE  # noqa: E731
        ladder_maps.refresh(self.state, NOW, fetch)
        ladder_maps.refresh(self.state, NOW + 600, fetch)  # within the hour: no second read
        self.assertEqual(calls, ["/maps"])
        record = ladder_maps.read(self.state)
        self.assertEqual((record["fetched_at"], record["error"], len(record["maps"])), (NOW, None, 11))
        # Each active map's file is kept by content, for the map-ideas worker.
        for m, row in zip(SAMPLE, record["maps"]):
            self.assertEqual(ladder_maps.map_file(self.state, row["content"]).read_text(), m["mapText"])
        self.assertEqual(len(list((self.state / ladder_maps.FILES).iterdir())), 11)

        def down(path):
            raise SystemExit("no API key: run `uv run unswbc auth set bc_...` first")
        later = NOW + ladder_maps.EVERY
        ladder_maps.refresh(self.state, later, down)
        record = ladder_maps.read(self.state)
        self.assertEqual((record["attempt_at"], record["fetched_at"], len(record["maps"])), (later, NOW, 11))
        self.assertIn("SystemExit: no API key", record["error"])
        ladder_maps.refresh(self.state, later + ladder_maps.EVERY, lambda path: [])
        self.assertIn("no active map", ladder_maps.read(self.state)["error"])

    def test_our_copies_match_the_captured_pool(self) -> None:
        record = saved(self.state, SAMPLE)
        self.assertEqual(ladder_maps.differences(record, self.repo), EMPTY)
        self.assertEqual(health.ladder_pool(self.state, self.repo, NOW), [])
        self.assertEqual(ladder_maps.differences({}, self.repo), EMPTY)

    def test_new_gone_and_changed_maps_alert(self) -> None:
        response = [m for m in copy.deepcopy(SAMPLE) if m["name"] != "Trauma"]
        devil = next(m for m in response if m["name"] == "Devil")
        devil["mapText"] = devil["mapText"].replace("\nEDGE ", "\nEDGE 1 1 -1\nEDGE ", 1)  # a kelp edge more
        (self.repo / "maps/ladder/default_small.map").unlink()  # the server's Default Small is then a new map
        record = saved(self.state, response)
        self.assertEqual(ladder_maps.differences(record, self.repo), {
            **EMPTY, "new": ["Default Small (id 5, 16×16)"], "gone": ["maps/ladder/trauma.map"],
            "changed": ["Devil (id 13, 32×16): maps/ladder/devil.map differs"]})
        (alert,) = health.ladder_pool(self.state, self.repo, NOW)
        self.assertTrue(alert.text.startswith("LADDER MAPS CHANGED: new Default Small (id 5, 16×16); gone "
                                              "maps/ladder/trauma.map; changed Devil (id 13, 32×16): "
                                              "maps/ladder/devil.map differs."), alert.text)
        # A further change is another alert, not one held back for three hours as a repeat.
        saved(self.state, [m for m in response if m["name"] != "Default Small"])
        (later,) = health.ladder_pool(self.state, self.repo, NOW)
        self.assertNotEqual(later.key, alert.key)

    # From 2026-09-27 21:00 AEST (rtrgrd, #announcements) a map may change its pearl timings drastically, or gain a
    # variant with the same kelp and portals and other pearls. Before, a pearl-only change read as a changed map and
    # a variant as the old map changed, with the advice to commit it over the file the old map still matches.
    def test_a_pearl_only_change_and_a_variant_are_told_apart(self) -> None:
        default = next(m for m in SAMPLE if m["name"] == "Default")
        changed = [{**m, "mapText": repearled(m["mapText"])} if m is default else m for m in SAMPLE]
        record = saved(self.state, changed)
        self.assertEqual(ladder_maps.differences(record, self.repo), {
            **EMPTY, "pearls changed": ["Default (id 4, 32×32): maps/ladder/default.map pearl timings differ"]})
        variant = {**default, "id": 99, "mapText": repearled(default["mapText"])}
        record = saved(self.state, SAMPLE + [variant])
        self.assertEqual(ladder_maps.differences(record, self.repo), {
            **EMPTY, "variant": ["Default (id 99, 32×32): same layout as maps/ladder/default.map (id 4), which "
                                 "still match"]})
        (alert,) = health.ladder_pool(self.state, self.repo, NOW)
        self.assertIn("a variant's as its own file under maps/ladder with a distinct stem (no '~'", alert.text)
        self.assertIn("Never commit map text with no spawning tile", alert.text)
        self.assertNotIn("differs", ladder_maps.differences(record, self.repo)["variant"][0])

    # Map text with its pearl timings withheld (the three forms the site might use) has the real layout but no
    # spawning tile: never offered for committing, and our file for it is neither changed nor gone.
    def test_withheld_pearl_timings_are_never_offered_for_committing(self) -> None:
        trophy = next(m for m in SAMPLE if m["name"] == "Trophy")
        for how in ("removed", "short", "not numbers"):
            with self.subTest(how):
                text = withheld(trophy["mapText"], how)
                self.assertTrue(ladder_maps.gapless(text))
                self.assertEqual(ladder_maps.layout_hash(text), ladder_maps.layout_hash(trophy["mapText"]))
                record = saved(self.state, [{**m, "mapText": text} if m is trophy else m for m in SAMPLE])
                self.assertEqual(ladder_maps.differences(record, self.repo), {
                    **EMPTY, "withheld": ["Trophy (id 11, 25×25): pearl timings withheld, layout matches "
                                          "maps/ladder/trophy.map"]})
        self.assertFalse(ladder_maps.gapless(trophy["mapText"]))
        # Withheld timings alone alert once per change, not every three hours while the site keeps hiding them.
        seen: dict = {}
        (first,) = health.ladder_pool(self.state, self.repo, NOW, seen)
        self.assertEqual(health.ladder_pool(self.state, self.repo, NOW + 4 * 3600, seen), [])
        self.assertIn("online.POOL_CUTOFF", first.text)
        saved(self.state, [{**m, "mapText": withheld(m["mapText"], "short")} if m["name"] in ("Trophy", "Devil")
                           else m for m in SAMPLE])
        (second,) = health.ladder_pool(self.state, self.repo, NOW, seen)
        self.assertNotEqual(second.key, first.key)
        # A gapless file committed to maps/ladder alerts, with or without a saved pool.
        (self.repo / "maps/ladder/trophy.map").write_text(withheld(trophy["mapText"], "short"))
        self.assertEqual(ladder_maps.differences({}, self.repo), {**EMPTY, "gapless": ["maps/ladder/trophy.map"]})
        (self.state / ladder_maps.RECORD).unlink()
        (alert,) = health.ladder_pool(self.state, self.repo, NOW)
        self.assertTrue(alert.text.startswith("LADDER MAPS CHANGED: gapless maps/ladder/trophy.map."), alert.text)

    # Autarky and Prisoners Dilemma match maps/sprint's files, the toolkit's Sprint maps that mapgen's sprint style and
    # the CPU gate play. Before, a re-pearled one read as `pearls changed`, advising to commit it over that file.
    def test_a_repearled_sprint_map_is_a_variant_never_committed_over_the_sprint_file(self) -> None:
        for how in ("layout", "name"):
            with self.subTest(how):
                changed = [{**m, "mapText": repearled(m["mapText"])} if m["name"] == "Autarky" else m for m in SAMPLE]
                record = saved(self.state, changed)
                if how == "name":  # its layout changed too, so only the name matches
                    record["maps"] = [{**m, "layout": "other"} if m["name"] == "Autarky" else m for m in record["maps"]]
                    ladder_maps.save(self.state, record)
                found = ladder_maps.differences(record, self.repo)
                self.assertEqual(found, {**EMPTY, "variant": [
                    f"Autarky (id 19, 54×18): same {how} as maps/sprint/autarky.map, a toolkit Sprint map kept as it "
                    "is: commit it as its own file under maps/ladder"]})
                (alert,) = health.ladder_pool(self.state, self.repo, NOW)
                self.assertNotIn("pearls changed", alert.text)

    def test_a_sprint_map_leaving_the_pool_stays_played_and_never_alerts_as_gone(self) -> None:
        # maps/sprint holds the Sprint's own maps (mapgen's sprint style, quick.py): moving one out would break them.
        record = saved(self.state, [m for m in SAMPLE if m["name"] != "Autarky"])
        self.assertEqual(ladder_maps.differences(record, self.repo), EMPTY)

    def test_a_pool_not_read_for_three_hours_alerts(self) -> None:
        saved(self.state, SAMPLE)
        self.assertEqual(health.upkeep(self.state, NOW + 3 * 3600, {}), [])
        (alert,) = health.upkeep(self.state, NOW + 3 * 3600 + 60, {})
        self.assertEqual(alert.key, f"upkeep:{ladder_maps.RECORD}")
        self.assertIn("ladder map pool: no success for over 3 h", alert.text)
        (self.state / "STOP").touch()  # the upkeep stops, so its records go quiet
        self.assertEqual(health.upkeep(self.state, NOW + 5 * 3600, {}), [])

    def test_the_official_report_plays_the_referee_checkouts_files_whatever_the_server_lists(self) -> None:
        # Its maps change only when a map committed to main reaches the checkout, never with the live list.
        def played() -> list[str]:
            with patch.object(driver, "REFEREE", self.repo), patch.object(driver, "STATE", self.state):
                maps = driver.official_maps(self.root / "copies", 5)
            return sorted({Path(m).stem.split("~")[0] for m in maps if Path(m).stem.endswith("~0")})
        every = ["autarky", "big_empty", "default", "default_small", "devil", "dilemma", "queen_of_spades",
                 "schooltime", "stronghold", "trauma", "trophy"]
        self.assertEqual(played(), every)
        saved(self.state, [m for m in SAMPLE if m["name"] != "Trauma"])
        self.assertEqual(played(), every)
        with patch.object(driver, "REFEREE", self.repo), patch.object(driver, "STATE", self.state):
            self.assertEqual(len(driver.official_maps(self.root / "copies", 5)), 11 * 5)
        (self.repo / "maps/sprint/devil.map").write_text("MAP 5 5\nEND\n")  # copies are named by stem
        with self.assertRaisesRegex(ValueError, "share a file name"):
            played()

    def test_the_lane_prompt_runs_the_main_checkouts_quick_check_on_the_clone(self) -> None:
        exp = driver.Experiment(id="E9999", idea={"id": "I1", "title": "t", "category": "c", "hypothesis": "h"},
                                lane=1)
        (self.state / "playbook.md").write_text("play well\n")
        with patch.object(driver, "STATE", self.state), patch.object(driver, "PLAYBOOK", self.state / "playbook.md"), \
                patch.object(driver, "RESULTS", self.state / "results.jsonl"), \
                patch.object(driver.online, "evidence_text", return_value=""):
            prompt = driver.lane_prompt(exp)
        clone = driver.WORKTREES / "E9999"
        self.assertIn(f'cd "{driver.REPO}" && "{clone}/.venv/bin/python" loop/native.py play loop/quick.py '
                      f'--clone "{clone}"', prompt)
        self.assertNotIn("{quick", prompt)
        self.assertIn(f'"{driver.REPO}/tools/gated_test.sh" .venv/bin/python', prompt)

if __name__ == "__main__":
    unittest.main()


class TestPearlVariants(unittest.TestCase):
    # The engine's pearl schedule, rebuilt (ladder_maps' docstring): every pearl of the real game 447294 (9553 on
    # Default, tools/testdata/default-447294.replay) spawned at a round the schedule of maps/ladder/default.map and the
    # replay's seed gives its tile an attempt.
    def test_the_schedule_gives_every_recorded_spawn_an_attempt(self):
        import online_fit
        import replays
        replay = replays.load_replay(REPO / "tools/testdata/default-447294.replay")
        _, _, flip, ranges = ladder_maps.pearl_tiles((REPO / "maps/ladder/default.map").read_text())
        owners = {t: r for t, r in ranges.items() if r[1] > 0 and ladder_maps.owner(t, flip) == t}
        attempts = ladder_maps.pearl_attempts(owners, replay.seed.value)
        spawned = online_fit.spawned(replay)
        self.assertEqual(sum(map(len, spawned.values())), 83)
        self.assertEqual([(t, r) for t, rounds in spawned.items() for r in rounds
                          if r not in attempts[ladder_maps.owner(t, flip)]], [])

    # Devil's variant as rebuilt from 10774's 20 games on it (testdata/devil-variant-rebuilt.map: the engine played all
    # 20 again): maps/ladder/devil.map less its 1-600, 1-30 and 24 of its 1-300 tiles, plus (15,1) and (16,1) at 1-1.
    # Twelve games on it, each pearl spawning at every attempt, rebuild it from the base map: the dropped base tiles
    # are silent and must be left out of the draw order, or every later tile's draws shift.
    def test_a_variant_is_rebuilt_from_its_games_first_spawns(self):
        variant = (REPO / "loop/testdata/devil-variant-rebuilt.map").read_text()
        _, _, flip, ranges = ladder_maps.pearl_tiles(variant)
        owners = {t: r for t, r in ranges.items() if r[1] > 0 and ladder_maps.owner(t, flip) == t}
        games = []
        for seed in range(12):
            attempts = ladder_maps.pearl_attempts(owners, seed * 7919 + 1, rounds=300)
            games.append((seed * 7919 + 1, {t: sorted(attempts[ladder_maps.owner(t, flip)])
                                           for t in ranges if ranges[t][1] and attempts[ladder_maps.owner(t, flip)]},
                          299))
        rebuilt = ladder_maps.rebuild_variant(games, variant, (REPO / "maps/ladder/devil.map").read_text())
        self.assertEqual(rebuilt, {t: r for t, r in ranges.items() if r[1] > 0})

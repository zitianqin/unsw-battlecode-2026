"""Which ladder team a Discord poster is on, on samples captured from the real site and store (testdata/discord-teams,
2026-09-26): six real /leaderboard rows (history dropped) with their member lists, the real /teams/859 page, and six
real messages.jsonl lines: kaistarkk ("Kieran", whose avatar carries his Discord account id), ilikcoding (a site avatar,
so only his site username "ILikCoding" matches), lmkae (an organiser on a dev team), benedict0724 (Discord's default
avatar, no account id), big_brose (on no team) and a Dyno webhook. The site is never called.

    .venv/bin/python -m unittest test_discord_teams   # from loop/
"""

import testhome  # before anything that reads live paths
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import discord_teams

DATA = Path(__file__).resolve().parent / "testdata/discord-teams"
BOARD = json.loads((DATA / "leaderboard.json").read_text())
TEAM_859 = json.loads((DATA / "team-859.json").read_text())
MESSAGES = {m["username"]: m for m in map(json.loads, (DATA / "messages.jsonl").read_text().splitlines())}
NOW = 1790400000.0


# The leaderboard as it was served before its rows listed members (loop/testdata/leaderboard-sample.json).
def bare_board() -> list[dict]:
    return [{k: v for k, v in row.items() if k != "members"} for row in BOARD]


# A stand-in for ladder.get serving `board` and each team's page (the real /teams/859 page, the others built from
# their leaderboard rows), recording each path asked for.
class Site:
    def __init__(self, board: list[dict]) -> None:
        self.board, self.asked = board, []

    def get(self, path: str) -> object:
        self.asked.append(path)
        if path == "/leaderboard":
            return copy.deepcopy(self.board)
        team_id = int(path.rsplit("/", 1)[1])
        if team_id == 859:
            return copy.deepcopy(TEAM_859)
        row = next(r for r in BOARD if r["id"] == team_id)
        return {"team": {"id": team_id, "name": row["name"], "members": copy.deepcopy(row["members"])}}


class Mapping(unittest.TestCase):
    def setUp(self) -> None:
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        record = discord_teams.refresh(self.state, NOW, Site(BOARD).get, sleep=lambda _: None)
        self.assertIsNone(record["error"])
        self.teams = discord_teams.read(self.state)

    def label(self, username: str) -> str:
        return discord_teams.team_label(MESSAGES[username], self.teams)

    def test_a_poster_is_found_by_the_account_id_in_their_site_avatar(self) -> None:
        self.assertEqual(self.label("kaistarkk"), '(team 859 "over|yonder", #180, Elo 1518, 166 wins)')
        self.assertEqual(self.teams["accounts"]["1148960020214591631"]["team_id"], 859)

    def test_a_member_without_an_avatar_id_is_found_by_username_and_marked_weaker(self) -> None:
        # benedict0724 has Discord's default avatar; ILikCoding a site-hosted avatar and a capitalised username.
        self.assertEqual(self.label("benedict0724"), '(team 473 "AGI ONE", #20, Elo 1904, 1101 wins, by username)')
        self.assertEqual(self.label("ilikcoding"), '(team 45 "WeLikCoding", #61, Elo 1740, 1633 wins, by username)')

    def test_a_username_never_overrides_a_different_account(self) -> None:
        other = dict(MESSAGES["benedict0724"], author_id="1", username="kaistarkk")
        self.assertEqual(discord_teams.team_label(other, self.teams), "(no team found)")

    def test_an_organiser_on_a_dev_team_says_so(self) -> None:
        self.assertEqual(self.label("lmkae"), '(team 484 "mustramallow", unranked, Elo 1500, 0 wins, dev team)')

    def test_a_poster_on_no_team_and_a_webhook_are_not_matched(self) -> None:
        self.assertEqual(self.label("big_brose"), "(no team found)")
        board = copy.deepcopy(BOARD)
        board[0]["members"].append({"username": "Dyno", "avatarUrl": "https://www.gravatar.com/avatar/x"})
        discord_teams.refresh(self.state, NOW + 1, Site(board).get, sleep=lambda _: None, every=0)
        teams = discord_teams.read(self.state)
        self.assertIn("dyno", teams["usernames"])
        self.assertEqual(discord_teams.team_label(MESSAGES["Dyno"], teams), "(no team found)")

    def test_no_team_list_is_not_reported_as_no_team(self) -> None:
        self.assertEqual(discord_teams.team_label(MESSAGES["kaistarkk"], {}), "(team unknown: no team list read yet)")


class Refresh(unittest.TestCase):
    def setUp(self) -> None:
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def refresh(self, site: Site, now: float, **options) -> dict | None:
        return discord_teams.refresh(self.state, now, site.get, sleep=lambda _: None, **options)

    def test_members_from_the_leaderboard_need_no_team_pages(self) -> None:
        site = Site(BOARD)
        self.refresh(site, NOW)
        self.assertEqual(site.asked, ["/leaderboard"])
        self.assertEqual({t["members_from"] for t in discord_teams.read(self.state)["teams"].values()}, {"leaderboard"})

    def test_it_runs_hourly_and_each_run_refreshes_the_ranks(self) -> None:
        board = bare_board()
        self.refresh(Site(board), NOW)
        self.assertIsNone(self.refresh(Site(board), NOW + discord_teams.EVERY - 1))
        next(r for r in board if r["id"] == 859)["rank"] = 150
        site = Site(board)
        self.refresh(site, NOW + discord_teams.EVERY)
        self.assertEqual(site.asked, ["/leaderboard"])  # member lists are under a day old
        self.assertEqual(discord_teams.read(self.state)["accounts"]["1148960020214591631"]["rank"], 150)

    def test_team_pages_are_fetched_once_a_day_ranked_teams_first(self) -> None:
        site, pauses = Site(bare_board()), []
        discord_teams.refresh(self.state, NOW, site.get, sleep=pauses.append)
        # By rank (17, 20, 61, 180), then the teams with no rank or no bot (484 is unranked, 4 has no bot).
        self.assertEqual(site.asked, ["/leaderboard", "/teams/157", "/teams/473", "/teams/45", "/teams/859",
                                      "/teams/4", "/teams/484"])
        self.assertEqual(pauses, [discord_teams.PACE] * 6)
        teams = discord_teams.read(self.state)
        self.assertEqual(teams["teams"]["859"]["members_from"], "team page")
        self.assertEqual(teams["accounts"]["1148960020214591631"]["team"], "over|yonder")
        site = Site(bare_board())
        self.refresh(site, NOW + discord_teams.MEMBERS_EVERY)
        self.assertEqual(len(site.asked), 7)

    def test_an_interrupted_run_resumes_where_it_stopped(self) -> None:
        site = Site(bare_board())
        answer = site.get

        def get(path: str) -> object:
            if len(site.asked) == 3:
                raise KeyboardInterrupt  # the upkeep killed mid-run: nothing after this is saved by the run itself
            return answer(path)

        with patch.object(discord_teams, "SAVE_EVERY", 1), self.assertRaises(KeyboardInterrupt):
            discord_teams.refresh(self.state, NOW, get, sleep=lambda _: None)
        self.assertEqual(sorted(k for k, t in discord_teams.read(self.state)["teams"].items() if "members" in t),
                         ["157", "473"])
        site.asked.clear()
        self.refresh(site, NOW + 60, every=0)
        self.assertEqual(site.asked, ["/leaderboard", "/teams/45", "/teams/859", "/teams/4", "/teams/484"])

    def test_a_stop_or_the_run_cap_leaves_the_rest_for_the_next_run(self) -> None:
        site = Site(bare_board())
        with patch.object(discord_teams, "FETCHES", 2):
            self.refresh(site, NOW)
        self.assertEqual(site.asked[1:], ["/teams/157", "/teams/473"])
        site.asked.clear()
        self.refresh(site, NOW + 60, every=0, stop=lambda: True)
        self.assertEqual(site.asked, ["/leaderboard"])

    def test_failures_are_recorded_and_never_raised(self) -> None:
        self.refresh(Site(BOARD), NOW)

        def down(path: str) -> object:
            raise SystemExit("no API key: run `uv run unswbc auth set bc_...` first")  # as ladder.api_key exits

        record = discord_teams.refresh(self.state, NOW + discord_teams.EVERY, down)
        self.assertIn("SystemExit: no API key", record["error"])
        self.assertEqual(record["failing_since"], NOW + discord_teams.EVERY)
        record = discord_teams.refresh(self.state, NOW + 2 * discord_teams.EVERY, down)
        self.assertEqual(record["failing_since"], NOW + discord_teams.EVERY)  # the first failure since the last success
        teams = discord_teams.read(self.state)
        self.assertEqual(teams["fetched_at"], NOW)  # the last good read, and its lists, stay
        self.assertEqual(teams["accounts"]["1148960020214591631"]["team_id"], 859)
        record = self.refresh(Site(BOARD), NOW + 3 * discord_teams.EVERY)
        self.assertNotIn("failing_since", record)

    def test_a_failed_team_page_waits_a_day_and_only_over_half_failing_is_an_error(self) -> None:
        site = Site(bare_board())
        failing = {"/teams/859"}

        def get(path: str) -> object:
            if path in failing:
                site.asked.append(path)
                raise OSError("HTTP Error 502")
            return site.get(path)

        record = discord_teams.refresh(self.state, NOW, get, sleep=lambda _: None)
        self.assertEqual((record["error"], record["failed"]), (None, ["team 859: OSError: HTTP Error 502"]))
        site.asked.clear()
        discord_teams.refresh(self.state, NOW + discord_teams.EVERY, get, sleep=lambda _: None)
        self.assertEqual(site.asked, ["/leaderboard"])  # tried an hour ago: not asked again yet
        failing |= {"/teams/157", "/teams/473", "/teams/45"}
        site.asked.clear()
        record = discord_teams.refresh(self.state, NOW + discord_teams.MEMBERS_EVERY, get, sleep=lambda _: None)
        self.assertEqual(len(site.asked), 7)
        self.assertEqual(record["error"], "4 of 6 team pages failed, the first: team 157: OSError: HTTP Error 502")

    def test_pages_that_all_failed_stay_an_error_until_a_later_fetch_succeeds(self) -> None:
        site = Site(bare_board())

        def down(path: str) -> object:
            if path == "/leaderboard":
                return site.get(path)
            raise OSError("HTTP Error 502")

        first = discord_teams.refresh(self.state, NOW, down, sleep=lambda _: None)
        self.assertTrue(first["error"].startswith("6 of 6 team pages failed"))
        for hour in range(1, 5):  # no page is due for a day, so these runs try none
            record = discord_teams.refresh(self.state, NOW + hour * discord_teams.EVERY, down, sleep=lambda _: None)
            self.assertEqual((record["error"], record["failing_since"]), (first["error"], NOW))
        record = self.refresh(site, NOW + discord_teams.MEMBERS_EVERY)
        self.assertIsNone(record["error"])
        self.assertNotIn("failing_since", record)
        # Pages that failed stop mattering once the leaderboard lists those teams' members itself.
        discord_teams.refresh(self.state, NOW + 2 * discord_teams.MEMBERS_EVERY, down, sleep=lambda _: None)
        self.assertIsNone(self.refresh(Site(BOARD), NOW + 2 * discord_teams.MEMBERS_EVERY + 3600)["error"])


# teams.json of the wrong shape (a hand edit, a disk fault, an older layout) is read as empty and rebuilt.
class BadFile(unittest.TestCase):
    def setUp(self) -> None:
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        discord_teams.refresh(self.state, NOW, Site(BOARD).get, sleep=lambda _: None)
        self.good = discord_teams.path_in(self.state).read_text()

    def spoil(self, change) -> None:
        record = json.loads(self.good)
        change(record)
        discord_teams.path_in(self.state).write_text(json.dumps(record))

    def label(self) -> str:
        return discord_teams.team_label(MESSAGES["kaistarkk"], discord_teams.read(self.state))

    def test_a_corrupt_or_misshapen_file_reads_as_empty_and_labels_nothing(self) -> None:
        cases = {"truncated": lambda: discord_teams.path_in(self.state).write_text(self.good[:500]),
                 "attempt_at text": lambda: self.spoil(lambda r: r.update(attempt_at="soon")),
                 "rank text": lambda: self.spoil(lambda r: r["teams"]["859"].update(rank="#180")),
                 "account entry": lambda: self.spoil(lambda r: r["accounts"].update({"1148960020214591631": 859})),
                 "teams a list": lambda: self.spoil(lambda r: r.update(teams=[])),
                 "member without discord_id": lambda: self.spoil(lambda r: r["teams"]["859"]["members"][0].pop(
                     "discord_id")),
                 "fetched_at NaN": lambda: discord_teams.path_in(self.state).write_text(
                     self.good.replace(f'"fetched_at": {NOW}', '"fetched_at": NaN')),
                 "fetched_at Infinity": lambda: self.spoil(lambda r: r.update(fetched_at=float("inf"))),
                 "fetched_at past 2100": lambda: self.spoil(lambda r: r.update(fetched_at=1e300)),
                 "attempt_at a huge int": lambda: self.spoil(lambda r: r.update(attempt_at=10 ** 400)),
                 "members_at negative": lambda: self.spoil(lambda r: r["teams"]["859"].update(members_at=-5)),
                 "previous a name": lambda: self.spoil(lambda r: r["teams"]["859"].update(previous="over")),
                 "previous too long": lambda: self.spoil(lambda r: r["teams"]["859"].update(previous=["x"] * 6))}
        for name, spoil in cases.items():
            with self.subTest(name):
                spoil()
                record, wrong = discord_teams.load(self.state)
                self.assertEqual(record, {})
                self.assertIsNotNone(wrong)
                self.assertEqual(self.label(), "(team unknown: no team list read yet)")
                self.assertEqual(discord_teams.standing(record), "our rank unknown: no team list read yet")

    def test_the_next_refresh_rebuilds_it_and_says_so(self) -> None:
        self.spoil(lambda r: r.update(attempt_at="soon"))  # would break the refresh's due arithmetic if trusted
        record = discord_teams.refresh(self.state, NOW + 60, Site(BOARD).get, sleep=lambda _: None)
        self.assertEqual(record["error"], "teams.json was unreadable (attempt_at is not a time), so it was rebuilt "
                                          "from the leaderboard")
        self.assertNotIn("failing_since", record)  # this refresh worked: not a failing one
        self.assertEqual(self.label(), '(team 859 "over|yonder", #180, Elo 1518, 166 wins)')
        discord_teams.path_in(self.state).write_text("{not json")
        record = discord_teams.refresh(self.state, NOW + 120, Site(BOARD).get, sleep=lambda _: None)
        self.assertIn("teams.json was unreadable (JSONDecodeError", record["error"])

    def test_a_member_without_discord_id_is_rebuilt_not_raised(self) -> None:
        # It used to pass the shape check and then raise KeyError in indexes(), in the save outside the try.
        self.spoil(lambda r: r["teams"]["859"]["members"][0].pop("discord_id"))
        record = discord_teams.refresh(self.state, NOW + 60, Site(bare_board()).get, sleep=lambda _: None)
        self.assertIn("teams.json was unreadable (a team entry has the wrong shape)", record["error"])
        self.assertEqual(self.label(), '(team 859 "over|yonder", #180, Elo 1518, 166 wins)')
        broken = {"859": {"name": "x", "members": [{"username": "a"}]}, "x": {}, "7": None}
        self.assertEqual(discord_teams.indexes(broken), ({}, {}))

    def test_a_bad_board_row_is_dropped_or_coerced_not_raised_later(self) -> None:
        board = copy.deepcopy(BOARD) + [{"id": "x"}, {"id": True}, "junk"]
        ours = next(r for r in board if r["id"] == 157)
        ours["rank"], ours["elo"] = "17", "high"
        record = discord_teams.refresh(self.state, NOW + 60, Site(board).get, sleep=lambda _: None, every=0)
        self.assertIsNone(record["error"])
        self.assertEqual((record["teams"]["157"]["rank"], record["teams"]["157"]["elo"]), (None, None))
        self.assertEqual(len(record["teams"]), len(BOARD))
        self.assertEqual(discord_teams.load(self.state)[1], None)


# Two real reads of 2026-09-28 (renames-2026-09-28.json: ranks 1-12, us and dev test 1, history dropped): teams.json's
# at 08:51 UTC and the live leaderboard at 09:11, between which team 306 renamed itself Vibing++ -> Cutlery at rank 1
# and calc and team 801 swapped ranks 3 and 4.
RENAMES = json.loads((DATA / "renames-2026-09-28.json").read_text())
# Real saved names to 2026-09-28 08:51 UTC (seen-2026-09-28/, fields trimmed): the study's and the scout's replay index
# rows last naming teams 306, 801, 528 and 70 by each of their names, and the study-history rows that first listed
# each leader name. 306 is "Vibing++" throughout; 801 had three names, 528 two.
SEEN = DATA / "seen-2026-09-28"


class Renames(unittest.TestCase):
    def setUp(self) -> None:
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def read(self, board: list[dict], now: float) -> dict:
        return discord_teams.refresh(self.state, now, Site(board).get, sleep=lambda _: None, every=0)

    def alerts(self) -> list[str]:
        log = self.state / "host-alerts.log"
        return [line.split(" ", 2)[2] for line in log.read_text().splitlines()] if log.exists() else []

    def test_a_rename_alerts_once_by_team_id_and_a_swap_inside_the_top_says_nothing(self) -> None:
        self.read(RENAMES["before"], NOW)
        self.assertEqual(self.alerts(), [])  # a first read has nothing to compare with
        record = self.read(RENAMES["after"], NOW + 3600)
        # calc and team 801 swapped ranks 3 and 4: inside the top, so no line.
        self.assertEqual(self.alerts(), [
            'teams: team 306 renamed "Vibing++" -> "Cutlery" (#1): a record naming it by the old name is stale; '
            'teams.json keeps both'])
        cutlery = record["teams"]["306"]
        self.assertEqual((cutlery["previous"], cutlery["first_seen"], cutlery["name_since"]),
                         (["Vibing++"], NOW, NOW + 3600))
        self.assertNotIn("name_since", record["teams"]["70"])  # its name since the first read: unknown
        self.assertEqual(discord_teams.formerly(record, 306, "Cutlery"), ', formerly "Vibing++"')
        self.assertEqual(discord_teams.renames(record),
                         'Renamed teams (a post may use an old name): team 306 "Cutlery" (#1), formerly "Vibing++".')
        self.read(RENAMES["after"], NOW + 7200)
        self.assertEqual(len(self.alerts()), 1)  # an unchanged read adds nothing

    def test_a_new_leader_and_teams_crossing_the_top_edge_give_one_line(self) -> None:
        self.read(RENAMES["after"], NOW)
        board = copy.deepcopy(RENAMES["after"])
        rows = {r["id"]: r for r in board}
        rows[306]["rank"], rows[70]["rank"] = 2, 1  # rank 1 changes hands, both stay in the top
        edge = next(r for r in board if r["rank"] == discord_teams.ALERT_RANKS)
        below = next(r for r in board if r["rank"] == discord_teams.ALERT_RANKS + 1)
        edge["rank"], below["rank"] = below["rank"], edge["rank"]  # one team in, one out
        board = [r for r in board if r["id"] != 20]  # calc (#3) leaves the read
        board.append({"id": 9999, "name": "newcomer", "rank": 3, "elo": 2070.4, "wins": 1, "dev": False,
                      "hasBot": True})  # a team new to the read lands in the top
        self.read(board, NOW + 3600)
        self.assertEqual(self.alerts(), [
            f'teams: top {discord_teams.ALERT_RANKS} changed: team 70 "cheji bt" #2 -> #1 (Elo 2086); '
            'team 9999 "newcomer" new at #3 (Elo 2070); '
            f'team {below["id"]} {discord_teams.quoted_name(below["name"])} #{edge["rank"]} -> #{below["rank"]} '
            f'(Elo {below["elo"]:.0f}); '
            f'team {edge["id"]} {discord_teams.quoted_name(edge["name"])} #{below["rank"]} -> #{edge["rank"]} '
            f'(Elo {edge["elo"]:.0f}); '
            'team 20 "calc" #3 -> off the leaderboard'])

    def test_a_rename_an_older_writer_hid_is_found_in_the_saved_names(self) -> None:
        # An upkeep on the code before name histories read the leaderboard after the rename: teams.json says Cutlery
        # with no older names, first_seen or name_since, and no read in between saw "Vibing++".
        discord_teams.path_in(self.state).parent.mkdir(parents=True)
        teams = {str(r["id"]): {"name": r["name"], "rank": r["rank"], "elo": r["elo"], "wins": r["wins"],
                                "dev": r["dev"], "has_bot": r["hasBot"]} for r in RENAMES["after"]}
        discord_teams.save(self.state, {"attempt_at": NOW, "fetched_at": NOW, "teams": teams})
        for name in ("study-replays/index.jsonl", "scout-replays/index.jsonl", "study-history.jsonl"):
            (self.state / name).parent.mkdir(exist_ok=True)
            (self.state / name).write_text((SEEN / name).read_text() + "not json\n[1]\n")  # bad rows are skipped
        record = self.read(RENAMES["after"], NOW + 3600)
        found = {key: record["teams"][key]["previous"] for key in ("306", "801", "70")}
        self.assertEqual(found, {"306": ["Vibing++"], "801": ["中国人能飞", "中国人能飞✈️✈️✈️✈️✈️✈️✈️✈️✈️✈️✈️✈️✈️✈"], "70": []})
        self.assertNotIn("name_since", record["teams"]["306"])  # when it became Cutlery is unknown
        self.assertEqual(self.alerts(), [])  # nor is when it renamed, so no rename line
        self.assertEqual(discord_teams.seen_names(self.state)["528"], ["JalVayūh", "JalSena"])  # latest seen first
        self.assertEqual(discord_teams.seen_names(self.state / "nowhere"), {})

    def test_below_the_top_only_renames_of_teams_we_track_alert(self) -> None:
        self.read(RENAMES["after"], NOW)
        board = copy.deepcopy(RENAMES["after"])
        rows = {r["id"]: r for r in board}
        rows[213]["rank"], rows[314]["rank"] = 12, 11  # below ALERT_RANKS: no rank line
        rows[213]["name"] = "Sponge 2"  # rank 12, not a dev team, not played: no rename line either
        rows[545]["name"] = "dev test 1 :D"  # a dev team, ranked 14
        self.read(board, NOW + 3600)
        self.assertEqual(self.alerts(), ['teams: team 545 renamed "dev test 1 :P" -> "dev test 1 :D" (#14): a record '
                                         'naming it by the old name is stale; teams.json keeps both'])
        # online.json as the online cycle keeps it: team 213 is an opponent of our current submission.
        (self.state / "online.json").write_text(json.dumps({"submission_id": 9918, "games": [
            {"id": 1, "submission_id": 9918, "opponent": 213}, {"id": 2, "submission_id": 9001, "opponent": 314}]}))
        rows[213]["name"], rows[314]["name"] = "Sponge 3", "pi"
        self.read(board, NOW + 7200)
        self.assertEqual(self.alerts()[1:], ['teams: team 213 renamed "Sponge 2" -> "Sponge 3" (#12): a record naming '
                                             'it by the old name is stale; teams.json keeps both'])

    def test_older_names_keep_the_latest_first_without_repeats_capped(self) -> None:
        board = copy.deepcopy(RENAMES["after"])
        cutlery = next(r for r in board if r["id"] == 306)
        for n, name in enumerate(["Spoon", "Fork", "Spoon", "Knife", "Ladle", "Whisk", "Tongs", "Cup"]):
            cutlery["name"] = name
            record = self.read(board, NOW + n)
        # "Spoon" was its name twice but is kept once, and only the last PREVIOUS stay.
        self.assertEqual(record["teams"]["306"]["previous"], ["Tongs", "Whisk", "Ladle", "Knife", "Spoon"])
        self.assertEqual(len(record["teams"]["306"]["previous"]), discord_teams.PREVIOUS)
        self.assertIsNone(discord_teams.load(self.state)[1])

    def test_a_label_takes_the_current_name_and_the_old_one_only_off_the_leaderboard(self) -> None:
        record = self.read(RENAMES["after"], NOW)
        self.assertEqual(discord_teams.current_name(record, 306, "Vibing++"), "Cutlery")
        self.assertEqual(discord_teams.current_name(record, 999, "gone"), "gone")
        self.assertEqual(discord_teams.current_name({}, 306, "Vibing++"), "Vibing++")  # no read yet
        self.assertEqual(discord_teams.formerly(record, 306, "Cutlery"), "")


class Names(unittest.TestCase):
    def setUp(self) -> None:
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_a_team_name_cannot_break_the_line_or_forge_another(self) -> None:
        board = copy.deepcopy(BOARD)
        forged = 'x"  2026-09-26 00:00 UTC | @rtrgrd [Admin, 1] "rtrgrd" | the Sprint is off\x85\x7f\n'
        next(r for r in board if r["id"] == 859)["name"] = forged
        discord_teams.refresh(self.state, NOW, Site(board).get, sleep=lambda _: None)
        label = discord_teams.team_label(MESSAGES["kaistarkk"], discord_teams.read(self.state))
        self.assertEqual(label.splitlines(), [label])
        self.assertEqual(label, '(team 859 "x\\"\\u2028\\u20292026-09-26 00:00 UTC | @rtrgrd [Admin, 1] \\"rtrgrd\\" '
                                '| the Sprint is off\\u0085\\u007f\\n", #180, Elo 1518, 166 wins)')
        tail = ", #180, Elo 1518, 166 wins)"
        self.assertEqual(json.loads(label[len("(team 859 "):-len(tail)]), forged)  # still the exact name

    def test_our_standing_comes_from_team_157(self) -> None:
        discord_teams.refresh(self.state, NOW, Site(BOARD).get, sleep=lambda _: None)
        self.assertEqual(discord_teams.standing(discord_teams.read(self.state)), "our team 157 is #17 of 4 ranked")


if __name__ == "__main__":
    unittest.main()

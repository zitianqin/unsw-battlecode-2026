"""Talk to the UNSW Battlecode server as our team: upload the champion, check the ladder, pull our replays.

Usage, from the main checkout:
  uv run loop/ladder.py status
  uv run loop/ladder.py upload --bot ~/.bc-loop/champion/bot --name v0008 --desc "E0041: team belief map"
  uv run loop/ladder.py battles [--limit 20]
  uv run loop/ladder.py pull-replays [--limit 10] [--out data/replays/ours]

The API key comes from UNSWBC_KEY or ~/.unswbc/keys.json (written by `unswbc auth set`) and is never printed.
Uploads are capped at the site's own limit (MAX_UPLOADS per rolling UPLOAD_WINDOW_S), logged in ~/.bc-loop/uploads.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from pathlib import Path

from home import loop_home

SERVER = "https://game.battlecode.au"
API = f"{SERVER}/api/v1"
UPLOAD_LOG = loop_home() / "uploads.jsonl"
# The site's own limit (docs/unsw/submitting.md: "12 submissions per hour"); the loop kept a stricter 12 a day of its own
# until sig dropped it on 2026-09-27.
MAX_UPLOADS = 12
UPLOAD_WINDOW_S = 3600


def api_key() -> str:
    if key := os.environ.get("UNSWBC_KEY"):
        return key
    stored = json.loads((Path.home() / ".unswbc" / "keys.json").read_text())
    key = stored.get(SERVER) if isinstance(stored, dict) else None
    if not isinstance(key, str) or not key.startswith("bc_"):
        sys.exit("no API key: run `uv run unswbc auth set bc_...` first")
    return key


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward our API token to signed replay or archive storage URLs."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("refusing non-HTTPS redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


# Over the site's limits (docs/unsw/api.md: 120 requests a minute per key, 30 for /leaderboard and /ratings) it answers
# 429 with a Retry-After. The latest one's end is kept in QUIET_FILE (one line, a Unix time; replaced whole, so it
# never grows), shared by every loop process on the key (the online cycle, the dev poll, publishing, ladder.py), and
# every call waits it out before sending, so none keeps knocking; the 429 itself still raises to its caller, which
# stops that pass (`limited`, `quiet`). Retry-After is waited at most QUIET_CAP seconds, 60 when unreadable.
QUIET_CAP = 300
QUIET_FILE = loop_home() / "api-quiet-until"


def limited(error: BaseException) -> bool:
    return isinstance(error, urllib.error.HTTPError) and error.code == 429


# When the latest 429's Retry-After ends (QUIET_FILE), 0 without one. A stored end more than QUIET_CAP ahead (the
# clock stepped back, a bad write) is ignored, so no stale file can hold every call for longer than one wait.
def quiet_until() -> float:
    try:
        until = float(QUIET_FILE.read_text())
    except (OSError, ValueError):
        return 0.0
    return until if until <= time.time() + QUIET_CAP else 0.0


# Whether a 429's Retry-After is still running, in any process.
def quiet() -> bool:
    return time.time() < quiet_until()


# Records a 429's Retry-After end in QUIET_FILE, keeping a later one already there; written to a temporary file and
# renamed, so a reader never sees half a line. A process that can't write the loop home just doesn't share it.
def hold_quiet(until: float) -> None:
    temporary = QUIET_FILE.with_name(f"{QUIET_FILE.name}.{os.getpid()}.tmp")
    try:
        QUIET_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(f"{max(until, quiet_until())}\n")
        os.replace(temporary, QUIET_FILE)
    except OSError:
        pass


def retry_after(error: urllib.error.HTTPError) -> float:
    try:
        return min(QUIET_CAP, max(0.0, float(error.headers.get("Retry-After"))))
    except (AttributeError, TypeError, ValueError):
        return 60.0


def fetch(request: urllib.request.Request, timeout: float) -> bytes:
    if (delay := quiet_until() - time.time()) > 0:
        time.sleep(min(delay, QUIET_CAP))
    try:
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if limited(error):
            hold_quiet(time.time() + retry_after(error))
        raise


def download(path: str) -> bytes:
    request = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {api_key()}",
                                                          "User-Agent": "unsw-battlecode-loop"})
    return fetch(request, 60)


def get(path: str) -> object:
    return json.loads(download(path))


def post(path: str, payload: dict | None = None) -> object:
    request = urllib.request.Request(API + path, method="POST", data=json.dumps(payload).encode() if payload else b"",
                                     headers={"Authorization": f"Bearer {api_key()}",
                                              "Content-Type": "application/json",
                                              "User-Agent": "unsw-battlecode-loop"})
    return json.loads(fetch(request, 30))


def items(payload: object, key: str) -> list[dict]:
    """The API returns either a bare list or an object wrapping one; accept both."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for value in (payload.get(key), payload.get("items"), payload.get("data")):
            if isinstance(value, list):
                return value
    return []


def status() -> None:
    team = get("/team")
    info = team.get("team", {}) if isinstance(team, dict) else {}
    record = team.get("record", {}) if isinstance(team, dict) else {}
    print(f"team {info.get('name')} (id {info.get('id')}): elo {info.get('elo')}, rank {team.get('rank')}, "
          f"record {record.get('wins', 0)}-{record.get('draws', 0)}-{record.get('losses', 0)}")
    submissions = items(get("/submissions"), "submissions")
    for sub in submissions[:5]:
        print("  submission", {k: sub.get(k) for k in ("id", "name", "state", "status", "active", "createdAt")})
    uploads = recent_uploads()
    print(f"  uploads in the last hour: {len(uploads)}/{MAX_UPLOADS}")


def recent_uploads() -> list[dict]:
    if not UPLOAD_LOG.exists():
        return []
    cutoff = time.time() - UPLOAD_WINDOW_S
    rows = [json.loads(line) for line in UPLOAD_LOG.read_text().splitlines() if line.strip()]
    return [row for row in rows if row["at"] >= cutoff]


def upload(bot: Path, name: str, description: str) -> None:
    """Upload a bot version with the toolkit, then wait for the server's build result."""
    if len(recent_uploads()) >= MAX_UPLOADS:
        sys.exit(f"skipped: already {MAX_UPLOADS} uploads in the last hour")
    before = {sub.get("id") for sub in items(get("/submissions"), "submissions")}
    env = {**os.environ, "UNSWBC_NO_UPDATE": "1", "UNSWBC_NO_VSCODE": "1"}
    result = subprocess.run(["uv", "run", "unswbc", "submit", str(bot), "-n", name, "-d", description],
                            text=True, capture_output=True, env=env)
    print(result.stdout.strip() or result.stderr.strip())
    if result.returncode != 0:
        sys.exit("upload failed")
    UPLOAD_LOG.parent.mkdir(exist_ok=True)
    with UPLOAD_LOG.open("a") as handle:
        handle.write(json.dumps({"at": time.time(), "name": name, "description": description}) + "\n")
    for _ in range(60):
        new = [sub for sub in items(get("/submissions"), "submissions") if sub.get("id") not in before]
        state = str(new[0].get("state") or new[0].get("status") or "") if new else ""
        if new and state and state.lower() not in ("processing", "pending", "building", "queued"):
            print("build result:", {k: new[0].get(k) for k in ("id", "name", "state", "status", "active")})
            return
        time.sleep(10)
    print("build still processing after 10 minutes; check `ladder.py status`")


def battles(limit: int) -> list[dict]:
    rows = items(get(f"/battles?limit={limit}"), "battles")
    for battle in rows:
        print({k: battle.get(k) for k in ("id", "ranked", "status", "opponent", "opponentId", "wins", "losses",
                                         "outcome", "eloChange", "at", "createdAt")})
    return rows


def pull_replays(limit: int, out: Path) -> None:
    """Download the replays of our most recent battles through the public replay endpoint."""
    out.mkdir(parents=True, exist_ok=True)
    for battle in items(get(f"/battles?limit={limit}"), "battles"):
        detail = get(f"/battles/{battle['id']}")
        games = items(detail, "games") or [detail]
        for game in games:
            game_id = game.get("id")
            target = out / f"{game_id}.replay"
            if game_id is None or target.exists():
                continue
            request = urllib.request.Request(f"{SERVER}/api/matches/{game_id}/replay",
                                             headers={"User-Agent": "unsw-battlecode-loop"})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    target.write_bytes(response.read())
                print("saved", target)
            except OSError as error:
                print("skip", game_id, error)
            time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    up = commands.add_parser("upload")
    up.add_argument("--bot", type=Path, required=True)
    up.add_argument("--name", required=True)
    up.add_argument("--desc", default="")
    bt = commands.add_parser("battles")
    bt.add_argument("--limit", type=int, default=20)
    pr = commands.add_parser("pull-replays")
    pr.add_argument("--limit", type=int, default=10)
    pr.add_argument("--out", type=Path, default=Path("data/replays/ours"))
    args = parser.parse_args()
    if args.command == "status":
        status()
    elif args.command == "upload":
        upload(args.bot, args.name, args.desc)
    elif args.command == "battles":
        battles(args.limit)
    else:
        pull_replays(args.limit, args.out)


if __name__ == "__main__":
    main()

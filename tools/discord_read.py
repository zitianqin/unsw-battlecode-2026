"""Reads the UNSW Battlecode Discord's channels through gstack's logged-in browser, read-only, and keeps the messages.

    .venv/bin/python tools/discord_read.py [--hours 12 | --all] [--channel NAME ...]

It drives sig's signed-in browser, the main checkout's gstack browse server (<main checkout>/.gstack/browse.json, the
window sig keeps open), from whichever worktree it runs, and never starts another browser (MISSES 51). That browser
must already be signed in to Discord (a `browse handoff` sign-in; its session lives as long as the browse server).
One page script scrolls back from the newest message until it passes the cutoff (or the channel's start with
--all), collecting each message's id, time, author and own text, per channel. A forum channel (ask-devs, where the
organisers answer questions; community-maps; bug-reports) holds one thread per post: its post list gives each thread's
id, title and last activity, and each thread active in the window (every one with --all) is read like a channel. New
messages are appended to <loop home>/discord/messages.jsonl (one per id, with its channel, and a forum message's
thread title and id) and the window read is written to <loop home>/discord/latest.md, grouped by channel and a forum's
by thread. latest.md is the upkeep's daily read (maintenance.DISCORD_EVERY), which the digest and reviews use, so only a
windowed read of every channel replaces it; a --all backfill or a --channel read writes its report to
<loop home>/discord/history/<channels>-<UTC time>.md instead (the upkeep's discord.json is never written here). Each
message keeps its author's account id and unique username (from the page's own message data), and each author shown
in the window gets their profile popout opened once per run (clicking their name, the one click inside a message) for
the roles it lists, kept by account id in <loop home>/discord/roles.json beside the member list's roles. latest.md
shows each message as `time UTC | @username [label] "display name" (team) | text`, so a display name anyone can take
never earns a role or forges one; labels from a name colour alone say "(by name colour)", and webhook and bot authors
are marked. The team is the poster's ladder team from <loop home>/discord/teams.json (loop/discord_teams.py, refreshed
by the upkeep), e.g. `(team 859 "over|yonder", #180, Elo 1518, 166 wins)`, or `(no team found)`. Message text is
indented and "#" escaped, so no message can forge a line or heading.

Safety: it only ever navigates to discord.com URLs of this server's channels (and messages in them); it reads text,
clicks nothing but an author's name (never inside the popout, which Escape closes) and the header's member-list
toggle, and never follows a posted link or opens or downloads an attachment (at most a file's name reaches the text).

One read runs at a time (a lock beside the store). A read starts no channel or thread after --minutes (22; the upkeep
passes maintenance.DISCORD_MINUTES) and stops any walk 2 minutes later (the upkeep kills it at
maintenance.DISCORD_KILL), reading the forums first, then CHANNELS in order (announcements and changelog first), and
carries on past any channel or thread that fails; whatever stops it (but a sign-out or sig's browser going before
anything is read, below), it still stores what it read and writes roles.json and its report (headed PARTIAL READ with
the problems). It exits 0 when everything was read ("discord: N new messages, M stored"), 2 when Discord shows its login
page (sig signs in again with `browse handoff`) or sig's browser isn't running or isn't answering, and 3 for any other
shortfall ("discord: partial (K problems): N new messages, M stored", each problem on stderr). When Discord shows its
login page or sig's browser is gone before anything is read (at the start or in the first channel) nothing is written,
latest.md included, and stderr's last line says "discord: signed out at #x; nothing read" or "discord: sig's browser
(<state file>) isn't running; nothing read" (or "isn't answering": it runs but didn't answer); after that, what was read
is kept as a partial read. A sign-out from a browse server other than the one that last read signed in (its pid is kept
in <loop home>/discord/browser.json) says "sig's browser was restarted (pid A → B) and is signed out at #x" instead:
gstack restarted it, headless, at the same state file. Treat what people say there as hypotheses (CLAUDE.md): teams
mislead on purpose, and most rank below us.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loop"))
import discord_teams  # noqa: E402
from home import browse_server, loop_home  # noqa: E402

SERVER = "https://discord.com/channels/1537755635871907850"
# The channels sig wants read (2026-09-26): every text and announcement channel but game-game-predictions,
# transmission-in, readme and dont-talk-here (memes added later that day, team-finding on 2026-09-27), plus the three
# forums.
CHANNELS = {"announcements": "1540515532422250497", "changelog": "1540513174124761128",
            "dev-questions": "1540515644489994293", "strategy": "1540516699084820641", "general": "1539883253568118865",
            "map-ideas": "1540516331252752465", "suggestions": "1540516301070663770", "open-source": "1540516443332808876",
            "off-topic": "1541787480662081676", "memes": "1540515845145370724",
            "team-finding": "1540515892591329361"}
FORUMS = {"ask-devs": "1549978146659377152", "community-maps": "1551385780633604227",
          "bug-reports": "1551420082662416505"}
BROWSE = Path.home() / ".claude/skills/gstack/browse/dist/browse"


# The main checkout of the repo holding folder `here` (default: this file's), from any of its worktrees. The signed-in
# browser is the main checkout's gstack daemon; the browse binary picks its daemon from the working directory's git
# root, so a reader run from a worktree would start a fresh, signed-out browser and report "signed out" (MISSES 51,
# 2026-09-27): every call names the main checkout's state file instead. An experiment clone is its own repo, so a
# clone's copy of the reader resolves to the clone.
def main_checkout(here: Path = Path(__file__).resolve().parent) -> Path:
    common = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True).stdout.strip()
    return Path(common).parent if common else here.parent


BROWSE_STATE = main_checkout() / ".gstack" / "browse.json"
NO_SERVER = "Server not available and BROWSE_NO_AUTOSTART is set"  # what browse prints (gstack's cli.ts ensureServer)


# Why browse found no server it could use, asked when it happens: "running" when the state file names no live browse
# server (home.browse_server), else "answering" (it runs but didn't answer browse's health check).
def browser_gone() -> str:
    return "running" if browse_server(BROWSE_STATE) is None else "answering"


# The last line of a read that found sig's browser gone (browser_gone()) at the start or before anything was read.
def nothing_read(gone: str) -> str:
    return f"discord: sig's browser ({BROWSE_STATE}) isn't {gone}; nothing read"


# The browse server's pid of the last read that got past Discord's login page, kept in <folder>/browser.json
# ({"pid", "at"}, one record). gstack can restart its browser (after an upgrade) headless and signed out at the same
# state file: the pre-check then passes on a live pid and the read says "signed out" (REFLECT 2026-09-27 F3, a hole in
# MISSES 51). A sign-out from a server whose pid differs from this one's is named as that restart.
def signed_in_pid(folder: Path) -> int | None:
    try:
        pid = json.loads((folder / "browser.json").read_text()).get("pid")
    except (OSError, ValueError, AttributeError):
        return None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


def save_signed_in(folder: Path, pid: int) -> None:
    temporary = folder / "browser.json.tmp"
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    temporary.write_text(json.dumps({"pid": pid, "at": at}) + "\n")
    temporary.replace(folder / "browser.json")


# What a sign-out `where` ("at #general") is called: sig's browser restarted when `server`, the browse server that
# showed the login page, isn't the one that last read signed in (signed_in_pid), else a plain sign-out.
def signed_out(folder: Path, server: int, where: str) -> str:
    before = signed_in_pid(folder)
    if before is not None and before != server:
        return f"sig's browser was restarted (pid {before} → {server}) and is signed out {where}"
    return f"signed out {where}"


# Scrolls the message list up until the oldest collected message is older than `cutoff` (ISO), the start of channel
# `here` is shown (an element with id chat-messages-<channel>-<channel>: its welcome header, or a thread's first post),
# or nothing new loads for about 15 s (Discord sometimes stops loading older messages at the top of a long channel;
# read_channel then jumps to the oldest message and runs this again), keeping every message seen in window.__dc by
# id, with its author's account id and unique username as the page's own message data holds them (Discord's props on
# the message element; follow-ups included), and "kind" "webhook" (anyone with the hook can post under any name) or
# "bot" when the author is one. It stops by itself at `stopAt` (epoch ms), so a walk never outlasts the read's time.
# On the way it opens the profile popout of each author shown (from `cutoff` on) whose id isn't in `known`, by
# clicking only the name on their message, checks the popout shows that author's username, reads the role names it
# lists into window.__dcProfiles (by account id), and closes it with Escape; nothing inside the popout is clicked.
# Returns {count, reason}: how many messages it holds and why it stopped ("start", "cutoff", "stalled" or "time").
COLLECT = """(async (here, cutoff, known, stopAt) => {
  window.__dc = {}; window.__dcProfiles = {};
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const account = li => {
    const key = Object.keys(li).find(k => k.startsWith("__reactFiber$"));
    for (let f = key && li[key], i = 0; f && i < 30; f = f.return, i++) {
      const m = f.memoizedProps && f.memoizedProps.message, a = m && m.author;
      if (a && a.id) return {author_id: a.id, username: a.username || "",
                             ...(m.webhookId ? {kind: "webhook"} : a.bot ? {kind: "bot"} : {})};
    }
    return {};
  };
  const grab = () => { for (const li of document.querySelectorAll("li[id^=chat-messages-]")) {
    const id = li.id.split("-").pop(); const t = li.querySelector("time");
    const own = document.getElementById("message-content-" + id); const au = li.querySelector("[id^=message-username-]");
    const prev = window.__dc[id] || {};
    const shown = au && (au.querySelector("span[style*=color]") || au.querySelector("[class*=username]") || au);
    window.__dc[id] = {id, time: t ? t.getAttribute("datetime") : prev.time || null,
                       author: au ? au.innerText.split("\\n")[0] : prev.author || "",
                       color: shown ? getComputedStyle(shown).color : prev.color || "", text: own ? own.innerText : "",
                       ...(prev.author_id ? {author_id: prev.author_id, username: prev.username, kind: prev.kind} : {}),
                       ...account(li)}; } };
  const tried = new Set(known);
  const dialog = () => document.querySelector("[role=dialog]");
  const escape = () => (document.activeElement || document.body).dispatchEvent(new KeyboardEvent("keydown",
    {key: "Escape", code: "Escape", keyCode: 27, which: 27, bubbles: true}));
  const profile = async () => { for (const li of [...document.querySelectorAll("li[id^=chat-messages-]")]) {
    const au = li.querySelector("[id^=message-username-]"), t = li.querySelector("time");
    const name = au && au.querySelector("[class*=username]"), who = account(li);
    if (Date.now() > stopAt) return;
    if (!name || !li.isConnected || !who.author_id || !who.username || who.kind === "webhook" ||
        tried.has(who.author_id)) continue;
    if (cutoff && t && t.getAttribute("datetime") < cutoff) continue;
    if (dialog()) { escape(); await wait(500); if (dialog()) return; }
    tried.add(who.author_id);
    name.click();
    let pop = null;
    for (let k = 0; k < 15 && !pop; k++) {
      await wait(200); const d = dialog();
      if (d && d.innerText.split("\\n").some(line => line.trim() === who.username)) pop = d; }
    if (pop) { await wait(300); window.__dcProfiles[who.author_id] = {username: who.username,
      name: au.innerText.split("\\n")[0],
      roles: [...pop.querySelectorAll("[class*=roleName]")].map(e => e.innerText.trim()).filter(Boolean)}; }
    escape();
    for (let k = 0; k < 10 && dialog(); k++) await wait(200);
    await wait(600);
  } };
  const atStart = () => !!document.getElementById("chat-messages-" + here + "-" + here);
  const scroller = () => { let s = document.querySelector("[data-list-id=chat-messages]");
    while (s && s.scrollHeight <= s.clientHeight + 5) s = s.parentElement; return s; };
  let still = 0, last = -1, reason = "stalled";
  for (let i = 0; i < 4000 && still < 20; i++) {
    grab(); await profile();
    if (atStart()) { reason = "start"; break; }
    if (Date.now() > stopAt) { reason = "time"; break; }
    const times = Object.values(window.__dc).map(m => m.time).filter(Boolean).sort();
    if (cutoff && times.length && times[0] < cutoff) { reason = "cutoff"; break; }
    const n = Object.keys(window.__dc).length; still = n === last ? still + 1 : 0; last = n;
    const s = scroller(); if (!s) break;
    s.scrollTop = Math.max(0, s.scrollTop - s.clientHeight * 0.8);
    await wait(700);
  }
  grab(); await profile();
  return JSON.stringify({count: Object.keys(window.__dc).length, reason});
})(ARGS)"""


# Walks the server's member list (grouped under each member's highest displayed role, highest first) and returns
# {role: position} and {account id: {name, username, role}} (the id and username from the page's own member data, as
# COLLECT reads a message's author), all read from the page: roles get created, renamed and reordered, so none is
# named here. Opens the member list with the channel header's "Show Member List" button if it is hidden (a view
# toggle, nothing posted); scrolls it the way COLLECT scrolls messages.
ROLES = r"""(async () => {
  if (!document.querySelector("[class*=membersWrap]")) {
    const show = document.querySelector("[aria-label='Show Member List']");
    if (show) { show.click(); await new Promise(r => setTimeout(r, 1500)); }
  }
  const list = () => document.querySelector("[class*=membersWrap] [class*=members_]") ||
                     document.querySelector("[class*=membersWrap]");
  const order = [], members = {}, colors = {};
  if (list()) { list().scrollTop = 0; await new Promise(r => setTimeout(r, 800)); }  // walk from the top role down
  let still = 0, last = -1;
  for (let i = 0; i < 2000 && still < 4; i++) {
    const root = list(); if (!root) break;
    let role = null;
    for (const el of root.querySelectorAll("h3[class*=membersGroup], [class*=member_]")) {
      if (el.tagName === "H3") {
        // Headers read "Admin, 2 members / Admin / — 2": the name is the last line that isn't just the count.
        const lines = el.innerText.split("\n").map(t => t.trim()).filter(t => t && !/^[\u2014-]\s*\d+$/.test(t));
        role = (lines.pop() || "").replace(/\s*[\u2014-]\s*\d+\s*$/, "").trim();
        // Discord's own sections, not server roles: what people are playing, and members with no displayed role.
        if (/^(activity|active now)$/i.test(role)) { role = null; continue; }
        if (/^(online|offline)$/i.test(role)) role = "(no role)";
        if (role && !order.includes(role)) order.push(role);
      } else if (role) {
        const shown = el.querySelector("[class*=username], [class*=name_]") || el;
        const name = shown.innerText.split("\n")[0].trim();
        const key = Object.keys(el).find(k => k.startsWith("__reactFiber$"));
        let user = null;
        for (let f = key && el[key], j = 0; f && j < 25 && !user; f = f.return, j++)
          if (f.memoizedProps && f.memoizedProps.user && f.memoizedProps.user.id) user = f.memoizedProps.user;
        if (user) members[user.id] = {name, username: user.username || "", role};
        const color = getComputedStyle(shown.querySelector("span[style*=color]") || shown).color;
        (colors[role] = colors[role] || {})[color] = (colors[role][color] || 0) + 1;
      }
    }
    const top = root.scrollTop; still = top === last ? still + 1 : 0; last = top;  // stop at the list's end
    root.scrollTop += root.clientHeight * 0.5;
    await new Promise(r => setTimeout(r, 600));
  }
  return JSON.stringify({roles: Object.fromEntries(order.map((r, i) => [r, i + 1])), members, colors});
})()"""


# Scrolls a forum's post list to its end (older and archived posts load as it scrolls) and returns every post card as
# JSON [{id, title, footer}]: the card's element id is the thread's channel id, and its footer reads like
# "12\n•\n1h ago\nPosted Thursday, ..." (reply count, last activity). Reads the cards only; clicks nothing.
POSTS = """(async () => {
  const posts = {};
  const grab = () => { for (const card of document.querySelectorAll("[data-item-id]")) {
    const id = card.getAttribute("data-item-id"), title = card.querySelector("h3");
    const foot = card.querySelector("[class*=footer_]");
    if (/^\\d+$/.test(id) && title)
      posts[id] = {id, title: title.innerText.trim(), footer: foot ? foot.innerText : ""}; } };
  const scroller = () => { let s = document.querySelector("[data-item-id]");
    while (s && !/(auto|scroll)/.test(getComputedStyle(s).overflowY)) s = s.parentElement; return s; };
  let still = 0, last = -1;
  for (let i = 0; i < 1000 && still < 6; i++) {
    grab(); const s = scroller(); if (!s) break;
    const n = Object.keys(posts).length;
    still = n === last && s.scrollTop + s.clientHeight >= s.scrollHeight - 5 ? still + 1 : 0; last = n;
    s.scrollTop += s.clientHeight * 0.8;
    await new Promise(r => setTimeout(r, 700));
  }
  grab();
  return JSON.stringify(Object.values(posts));
})()"""
AGO = re.compile(r"(\d+)\s*(mo|m|h|d|y) ago")
UNIT_HOURS = {"m": 1 / 60, "h": 1, "d": 24, "mo": 24 * 28, "y": 24 * 365}


# One browse command against sig's browser, returning what it printed. BROWSE_NO_AUTOSTART stops browse from starting
# a new headless browser (no profile, so signed out) when sig's isn't running; it prints NO_SERVER instead, which
# raises NoBrowser.
def browse(*args: str, timeout: float = 120) -> str:
    done = subprocess.run([str(BROWSE), *args], capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "BROWSE_STATE_FILE": str(BROWSE_STATE), "BROWSE_NO_AUTOSTART": "1"})
    if NO_SERVER in (done.stderr or ""):
        raise NoBrowser()
    return done.stdout.strip()


def last_line(text: str) -> str:
    return (text.splitlines() or [""])[-1]


# Why a read stopped short of what it was asked for. SignedOut means Discord showed its login page (sig signs in
# again with `browse handoff`); NoBrowser that sig's browser (BROWSE_STATE's server) isn't running, so nothing can be
# read until it is; ReadError is anything else a channel, forum or thread couldn't give.
class SignedOut(Exception):
    pass


class NoBrowser(Exception):
    pass


class ReadError(Exception):
    pass


# Runs an async page script to the end and returns what it resolved to. A walk can take minutes, longer than one
# browser call may wait, so it runs in the page and is polled; a script that throws or hasn't finished after `wait`
# seconds raises ReadError.
def run_in_page(script: str, wait: float) -> str:
    browse("js", f"(window.__dcResult = null, {script}.then("
                 "r => window.__dcResult = JSON.stringify({ok: String(r)}), "
                 "e => window.__dcResult = JSON.stringify({error: String(e)})), 'started')")
    for _ in range(max(1, int(wait / 2))):
        result = last_line(browse("js", "String(window.__dcResult)"))
        if result not in ("null", ""):
            outcome = json.loads(result)
            if "error" in outcome:
                raise ReadError(f"page script failed: {outcome['error'][:200]}")
            return outcome["ok"]
        time.sleep(2)
    raise ReadError(f"page script didn't finish in {wait:g} s")


# Every message the page collected, fetched in slices so no single browser reply is huge.
def collected(count: int) -> list[dict]:
    out: list[dict] = []
    for start in range(0, count, 200):
        page = last_line(browse("js", f"JSON.stringify(Object.values(window.__dc).slice({start}, {start + 200}))"))
        out += json.loads(page)
    return out


# When a message was sent, from its id (a Discord snowflake: milliseconds since 2015 in the top bits). A follow-up
# from the same author shows no <time> element, so the id is the only timestamp every message has.
def snowflake_time(message_id: str) -> str:
    stamp = (int(message_id) >> 22) + 1420070400000
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Opens a channel, forum or thread of this server and waits up to 30 s for `ready` (a CSS selector) to render. Raises
# SignedOut on Discord's login page and ReadError when nothing rendered (a channel we read is never empty). Only
# discord.com channel URLs are ever opened.
def open_page(channel_id: str, ready: str) -> None:
    browse("goto", f"{SERVER}/{channel_id}")
    for _ in range(30):  # the page renders a few seconds after it loads
        if "/login" in browse("url"):
            raise SignedOut()
        if last_line(browse("js", f"String(document.querySelectorAll({json.dumps(ready)}).length)")) not in ("", "0"):
            return
        time.sleep(1)
    raise ReadError(f"{channel_id} rendered nothing in 30 s")


# Opens one channel (or forum thread) and scrolls back to `cutoff` (None: its start); returns its messages oldest
# first. When Discord stops loading older messages part way, it opens the channel again at the oldest message read so
# far (a discord.com link to that message) and carries on, giving up after two jumps in a row that load nothing older.
# Adds the profile of each author shown from `cutoff` on to `profiles` ({account id: {username, name, roles}}),
# skipping those it already holds, so a run opens each author's popout once. Its walks stop at `stop_at` (time.time()).
# Whatever happens, `partial` ends up holding every message read, so a read that raises (giving up short of the start
# or the cutoff, out of time, or any failure) still keeps them.
def read_channel(channel_id: str, cutoff: str | None, profiles: dict[str, dict], partial: list[dict] | None = None,
                 stop_at: float = float("inf")) -> list[dict]:
    found: dict[str, dict] = {}
    try:
        open_page(channel_id, "li[id^=chat-messages-]")
        oldest, fruitless = None, 0
        while True:
            left = min(stop_at - time.time(), 3600)
            if left <= 0:
                raise ReadError(f"{channel_id}: out of time after {len(found)} messages")
            args = (f"{json.dumps(channel_id)}, {json.dumps(cutoff)}, {json.dumps(list(profiles))}, "
                    f"{int((time.time() + left) * 1000)}")
            walk = json.loads(run_in_page(COLLECT.replace("ARGS", args), left + 60))
            profiles.update(json.loads(last_line(browse("js", "JSON.stringify(window.__dcProfiles)"))))
            for m in collected(walk["count"]):
                if m["id"] not in found or (m.get("author") and not found[m["id"]].get("author")):
                    found[m["id"]] = m
            if walk["reason"] == "time":
                raise ReadError(f"{channel_id}: out of time after {len(found)} messages")
            if walk["reason"] != "stalled" or not found:
                break
            fruitless = fruitless + 1 if oldest == min(found, key=int) else 0
            oldest = min(found, key=int)
            if fruitless >= 2:
                break
            open_page(f"{channel_id}/{oldest}", "li[id^=chat-messages-]")
        if walk["reason"] == "stalled":
            raise ReadError(f"{channel_id}: older messages stopped loading after {len(found)} "
                            f"(back to {snowflake_time(min(found, key=int))[:16] if found else '-'})")
        return ordered(found)
    finally:
        if partial is not None:
            partial[:] = ordered(found)


# The messages of one read, oldest first, each timed by its id, follow-ups given their author.
def ordered(found: dict[str, dict]) -> list[dict]:
    timed = (dict(m, time=snowflake_time(m["id"])) for m in found.values())
    return with_authors(sorted(timed, key=lambda m: m["time"]))


# How long ago a forum post was last active, at least, in hours, from its card's footer ("1h ago" is 1-2 h, "3d ago"
# 3-4 days); None when the footer shows no age the reader knows.
def activity_hours(footer: str) -> float | None:
    for line in footer.splitlines():
        found = AGO.fullmatch(line.strip())
        if found:
            return int(found[1]) * UNIT_HOURS[found[2]]
    return None


# The posts whose threads can hold messages from the last `hours` (None: all of them): an unknown age is read too.
def active_posts(posts: list[dict], hours: float | None) -> list[dict]:
    return [p for p in posts if hours is None or (age := activity_hours(p["footer"])) is None or age < hours]


# Reads a forum's threads active in the last `hours` (None: all) back to `cutoff`, starting none after `deadline` and
# stopping each walk at `stop_at` (both time.time()),
# adding their messages to `messages` as it goes, each with its thread's title and id, so a read that stops keeps what
# it had. Returns how many threads were read in full and how many the forum lists, and what went wrong (a thread that
# failed or wasn't reached in time): one bad thread doesn't lose the rest. A forum listing no posts raises ReadError.
def read_forum(forum_id: str, cutoff: str | None, hours: float | None, profiles: dict[str, dict],
               deadline: float, messages: list[dict], stop_at: float = float("inf")) -> tuple[int, int, list[str]]:
    open_page(forum_id, "[data-item-id]")
    posts = json.loads(run_in_page(POSTS, 600))
    if not posts:
        raise ReadError("the forum listed no posts")
    active, problems, done = active_posts(posts, hours), [], 0
    for post in active:
        if time.time() > deadline:
            problems.append(f"out of time after {done} of {len(active)} threads")
            break
        thread: list[dict] = []
        try:
            read_channel(post["id"], cutoff, profiles, thread, stop_at)
            done += 1
        except (SignedOut, NoBrowser):
            raise
        except Exception as error:  # one thread's failure, whatever it is, costs only that thread
            problems.append(f"thread {post['title']!r}: {type(error).__name__}: {error}")
        finally:
            messages += [dict(m, thread=post["title"], thread_id=post["id"]) for m in thread]
    return done, len(posts), problems


# A follow-up message from the same author (sent within minutes, grouped under the first) shows no name, so it takes
# the author and name colour of the message before it in the channel, oldest first, when both are the same account
# (or either account is unknown, as in reads before accounts were recorded).
def with_authors(messages: list[dict]) -> list[dict]:
    last: dict = {}
    for m in messages:
        if m.get("author"):
            last = m
        elif last and (not m.get("author_id") or not last.get("author_id") or m["author_id"] == last["author_id"]):
            m.update(author=last["author"], color=last.get("color", ""))
    return messages


# The name a message shows without the server tag Discord appends ("[Stockfish] xCirno [NEXT], " is
# "[Stockfish] xCirno" in the member list).
def member_name(author: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", author.strip().rstrip(",").strip())


# The member-list entries keyed by account id whose role still exists in `order`. Entries keyed by name (reads before
# account ids were recorded) are left out: anyone can take a name.
def account_members(entries: dict, order: dict[str, int]) -> dict[str, dict]:
    return {account: entry for account, entry in entries.items()
            if account.isdigit() and isinstance(entry, dict) and entry.get("role") in order}


# Keeps what earlier reads learned: the member list of a large server shows only members online at the time, so a
# member seen at any read keeps the role it had then, with when it was seen (`now`), unless that role no longer
# exists. Role order and each role's name colour are this read's.
def merged_roles(old: dict, new: dict, now: str) -> dict:
    members = account_members(old.get("members", {}), new["roles"])
    members.update({account: dict(entry, seen_at=now)
                    for account, entry in account_members(new["members"], new["roles"]).items()})
    colors = {role: max(counts, key=counts.get) for role, counts in new.get("colors", {}).items() if counts}
    return {"roles": new["roles"], "members": members, "colors": colors}


# "role, position" for a message's author, looked up by their account id, never their name (anyone can take a name):
# from whichever of their profile popout (every role they hold) and the member list (their highest role) was read
# most recently within FRESH (the popout on a tie), else from their name's colour on this message when exactly one role
# has it (Discord colours a name by its highest coloured role), marked "(by name colour)" since it's weaker. A name in
# no role's colour is "no coloured role": no role, or only roles without a colour (which staff roles can be), so not
# proof of no role. A webhook has no account or roles (anyone with its link posts under any name); a bot account's
# label says so. `now` defaults to the current time.
def role_label(message: dict, roles: dict, now: datetime | None = None) -> str:
    if message.get("kind") == "webhook":
        return "webhook, not a member"
    label = account_label(message, roles, now or datetime.now(timezone.utc))
    return f"bot; {label}" if message.get("kind") == "bot" else label


# role_label for a person or bot account, from its profile, the member list or the name colour as above.
def account_label(message: dict, roles: dict, now: datetime) -> str:
    order, account = roles.get("roles", {}), message.get("author_id", "")
    found = []
    if (profile := roles.get("profiles", {}).get(account)) and fresh(profile.get("read_at"), now):
        found.append((profile["read_at"], 1, profile_label(profile["roles"], order)))
    if (member := roles.get("members", {}).get(account)) and fresh(member.get("seen_at"), now) \
            and member["role"] in order:
        found.append((member["seen_at"], 0, f"{member['role']}, {order[member['role']]}"))
    if found:
        return max(found)[2]
    matches = [r for r, c in roles.get("colors", {}).items() if c and c == message.get("color")]
    if len(matches) != 1:
        return "no coloured role" if message.get("color") and not matches else "role unknown"
    return f"{matches[0]}, {order[matches[0]]} (by name colour)" if matches[0] in order else "role unknown"


FRESH = timedelta(hours=24)
GRACE = 120  # seconds a walk under way may run past --minutes


# Whether a role reading taken at `when` (ISO) is recent enough to label a message at `now`: league roles follow the
# ladder, so an older one may be out of date.
def fresh(when: str | None, now: datetime) -> bool:
    return bool(when) and now - datetime.fromisoformat(when.replace("Z", "+00:00")) <= FRESH


# The roles a profile popout listed, as "Tunafish, 5; also: UNSW": the highest in the member list's order (`order`,
# {role: position}) with its position, then the rest as listed; "no listed role; roles: ..." when the member list
# groups none of them, "no role" when the popout listed none.
def profile_label(held: list[str], order: dict[str, int]) -> str:
    listed = sorted((role for role in held if role in order), key=order.__getitem__)
    if not listed:
        return f"no listed role; roles: {', '.join(held)}" if held else "no role"
    others = [role for role in held if role != listed[0]]
    return f"{listed[0]}, {order[listed[0]]}" + (f"; also: {', '.join(others)}" if others else "")


# Message text as one entry of latest.md: its lines after the first indented, so only real message lines start at
# column 0 with a timestamp, and a line's leading "#" escaped, so no text reads as a heading.
def quoted(text: str) -> str:
    return "\n    ".join(re.sub(r"^(\s*)#", r"\1\\#", line) for line in (text.splitlines() or [""]))


# One message as latest.md and the history files show it: who posted it first, as the account's unique @username
# (or "webhook", or "(account unknown)") with its role label, then the display name it showed, quoted and escaped
# (discord_teams.quoted_name: no line breaks, separators or controls) since anyone can pick one (even
# "rtrgrd (@rtrgrd) [Admin, 1] |"), then, given `teams` (discord/teams.json), the poster's ladder team
# (discord_teams.team_label), then its text.
def message_line(message: dict, roles: dict, now: datetime, teams: dict | None = None) -> str:
    shown = message["author"].strip().rstrip(",").strip()
    who = ("webhook" if message.get("kind") == "webhook" else f"@{message['username']}" if message.get("username")
           else "(account unknown)")
    team = f" {discord_teams.team_label(message, teams)}" if teams is not None else ""
    return (f"{message['time'][:16].replace('T', ' ')} UTC | {quoted(who)} [{role_label(message, roles, now)}] "
            f"{discord_teams.quoted_name(shown)}{team} | {quoted(message['text'])}")


# One channel's section of latest.md or a history file, oldest first; a forum's messages go under a heading per
# thread (its title on one line, a leading "#" escaped), threads in the order of their first message shown.
def section(name: str, messages: list[dict], roles: dict, now: datetime, teams: dict | None = None) -> str:
    messages = sorted(messages, key=lambda m: m["time"])
    head = f"## #{name} ({len(messages)})\n\n"
    if not any("thread_id" in m for m in messages):
        return head + "\n".join(message_line(m, roles, now, teams) for m in messages)
    threads: dict[str, list[dict]] = {}
    for m in messages:
        threads.setdefault(m.get("thread_id", ""), []).append(m)
    return head + "\n\n".join(f"### {quoted(' '.join(group[0].get('thread', '(no thread)').split()))} ({len(group)})"
                                "\n\n" + "\n".join(message_line(m, roles, now, teams) for m in group)
                                for group in threads.values())


# Walks the member list (from a text channel: a thread's lists only its members) and returns what ROLES read. Raises
# SignedOut on the login page, and ReadError when it found no roles (a hidden member list or a signed-out page), so an
# empty walk never replaces what roles.json holds.
def walk_roles(on_forum: bool, stop_at: float) -> dict:
    if on_forum:
        open_page(CHANNELS["general"], "li[id^=chat-messages-]")
    if "/login" in browse("url"):
        raise SignedOut()
    walked = json.loads(run_in_page(ROLES, max(30, min(180, stop_at + 60 - time.time()))))
    if not walked.get("roles"):
        raise ReadError("the roles walk found no roles")
    return walked


# Adds the account id and username this read found to stored messages that lack them (reads before accounts were
# recorded), rewriting the store in place: same lines in the same order, so readers that count lines still can.
def add_accounts(store: Path, found: dict[str, dict]) -> int:
    lines, added = store.read_text().splitlines(), 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = json.loads(line)
        if "author_id" not in m and m["id"] in found:
            lines[i] = json.dumps(dict(m, **found[m["id"]]), ensure_ascii=False)
            added += 1
    if added:
        temp = store.with_suffix(".tmp")
        temp.write_text("\n".join(lines) + "\n")
        temp.replace(store)
    return added


# One line of at most 300 characters for the PARTIAL READ header: a problem can quote a thread title or a page error.
def one_line(problem: str) -> str:
    flat = " ".join(problem.split())
    return flat if len(flat) <= 300 else flat[:299] + "…"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hours", type=float, default=12)
    parser.add_argument("--all", action="store_true", help="read back to each channel's start")
    parser.add_argument("--channel", action="append", choices=sorted({**CHANNELS, **FORUMS}),
                        help="only these (default: all)")
    parser.add_argument("--minutes", type=float, default=22, help="start no channel or thread after this long, and "
                        f"stop any walk {GRACE // 60:g} min later (the upkeep passes its own and kills a read later)")
    args = parser.parse_args()
    folder = loop_home() / "discord"
    folder.mkdir(parents=True, exist_ok=True)
    # One read at a time: two would share the browser and interleave the store. This is the reader's own lock, not
    # the upkeep's discord.lock, which the upkeep holds while it waits for this process.
    with (folder / "messages.jsonl.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("discord: problem: another discord_read.py is running; nothing read", file=sys.stderr)
            print("discord: partial (1 problems): 0 new messages, another read is running")
            return 3
        return read(args, folder)


# Where a read's report goes: latest.md for a windowed read of every channel (the upkeep's), else a history file
# named after its channels and time, so a backfill or a one-channel read never displaces the read the digest uses.
def report_path(args: argparse.Namespace, folder: Path, now: datetime) -> Path:
    if not args.all and not args.channel:
        return folder / "latest.md"
    channels = "+".join(sorted(set(args.channel))) if args.channel else "all-channels"
    name, n = f"{channels}-{now:%Y%m%dT%H%M%SZ}", 1
    while (folder / "history" / f"{name}{f'-{n}' if n > 1 else ''}.md").exists():  # two reads in one second
        n += 1
    return folder / "history" / f"{name}{f'-{n}' if n > 1 else ''}.md"


# The read itself, under main's lock: see the module docstring.
def read(args: argparse.Namespace, folder: Path) -> int:
    deadline = time.time() + args.minutes * 60
    stop_at = deadline + GRACE
    cutoff = None if args.all else (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat()
    store = folder / "messages.jsonl"
    known = {json.loads(line)["id"] for line in store.read_text().splitlines() if line.strip()} if store.exists() else set()
    server = browse_server(BROWSE_STATE)
    if server is None:  # never start another browser: report it, and leave latest.md as it is
        print(nothing_read("running"), file=sys.stderr)
        return 2
    windows, total_new, profiles, accounts, problems, stopped = {}, 0, {}, {}, [], False
    gone, read_any = None, False  # why sig's browser stopped the read, if it did; whether anything was read before
    # Forums first, so the roles walk below runs on a text channel's member list, not a thread's.
    names = sorted(args.channel or [*CHANNELS, *FORUMS], key=lambda name: name in CHANNELS)
    for name in names:
        if time.time() > deadline:
            problems.append(f"out of time before #{name}")
            break
        messages, threads = [], ""
        try:
            if name in CHANNELS:
                read_channel(CHANNELS[name], cutoff, profiles, messages, stop_at)
            else:
                done, listed, trouble = read_forum(FORUMS[name], cutoff, None if args.all else args.hours,
                                                   profiles, deadline, messages, stop_at)
                threads = f", {done} of {listed} threads read"
                problems += [f"#{name}: {problem}" for problem in trouble]
            read_any = True
        except SignedOut:
            stopped = True
            problems.append(signed_out(folder, browse_server(BROWSE_STATE) or server, f"at #{name}"))
            print(f"discord: {problems[-1]} ({BROWSE_STATE}); sign in again (browse handoff)", file=sys.stderr)
        except NoBrowser:
            stopped, gone = True, browser_gone()
            problems.append(f"sig's browser ({BROWSE_STATE}) stopped {gone} at #{name}")
        except Exception as error:  # any other failure costs this channel, never the read
            problems.append(f"#{name}: {type(error).__name__}: {error}")
        read_any = read_any or bool(messages)
        new = [dict(m, channel=name) for m in messages if m["id"] not in known]
        known.update(m["id"] for m in new)
        total_new += len(new)
        accounts.update({m["id"]: {"author_id": m["author_id"], "username": m.get("username", "")}
                         for m in messages if m.get("author_id")})
        if new:
            with store.open("a") as handle:
                for m in new:
                    handle.write(json.dumps(m, ensure_ascii=False) + "\n")
        window = [m for m in messages if cutoff is None or m["time"] >= cutoff]
        if window:
            windows[name] = window
        print(f"discord: #{name}: {len(window)} in the window, {len(new)} new{threads}", file=sys.stderr)
        if stopped:
            break
    if read_any:
        save_signed_in(folder, server)
    if stopped and not read_any:  # signed out or sig's browser gone before anything was read: every file stays as it is
        for problem in problems[:-1]:
            print(f"discord: problem: {problem}", file=sys.stderr)
        print(nothing_read(gone) if gone else f"discord: {problems[-1]}; nothing read", file=sys.stderr)
        return 2
    if store.exists():
        add_accounts(store, accounts)
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    path = folder / "roles.json"
    roles = json.loads(path.read_text()) if path.exists() else {}
    if not stopped:
        try:  # roles are re-read at every run; a failure keeps the last roles.json's rather than stopping the read
            roles = dict(merged_roles(roles, walk_roles(names[-1] in FORUMS, stop_at), stamp),
                         profiles=roles.get("profiles", {}))
        except SignedOut:
            stopped = True
            problems.append(signed_out(folder, browse_server(BROWSE_STATE) or server, "before the roles walk"))
        except NoBrowser:
            stopped = True
            problems.append(f"sig's browser ({BROWSE_STATE}) stopped {browser_gone()} before the roles walk")
        except Exception as error:
            problems.append(f"roles not read ({type(error).__name__}: {error})")
    # Profiles are keyed by account id; ones keyed by name (reads before ids were recorded) are dropped.
    roles["profiles"] = {**{k: v for k, v in roles.get("profiles", {}).items() if k.isdigit()},
                         **{account: dict(entry, read_at=stamp) for account, entry in profiles.items()}}
    path.write_text(json.dumps(roles, ensure_ascii=False, indent=1) + "\n")
    print(f"discord: {len(profiles)} profiles read", file=sys.stderr)
    report = report_path(args, folder, now)
    # Before the problems: the upkeep keeps stderr's tail, and its alerts show the last line (a sign-out, say).
    print(f"discord: report in {report}", file=sys.stderr)
    for problem in problems:
        print(f"discord: problem: {problem}", file=sys.stderr)
    teams = discord_teams.read(folder.parent)
    sections = [section(name, window, roles, now, teams) for name, window in windows.items()]
    warning = (f"PARTIAL READ: {'; '.join(map(one_line, problems))}. Channels and threads not listed below may be "
               "missing.\n\n" if problems else "")
    listed = teams.get("fetched_at")
    teams_at = (f"ladder ranks as of {datetime.fromtimestamp(listed, timezone.utc):%Y-%m-%d %H:%M} UTC, "
                f"{discord_teams.standing(teams)}" if discord_teams.moment(listed) else "no team list read yet")
    renamed = f"{line}\n\n" if (line := discord_teams.renames(teams)) else ""
    report.parent.mkdir(exist_ok=True)
    only = f" of #{', #'.join(args.channel)}" if args.channel else ""
    # Through a temporary file: the digest worker's step copies latest.md, and must never copy half a report.
    temporary = report.with_name(report.name + ".tmp")
    temporary.write_text(
        f"# Discord, {'all history' if cutoff is None else f'last {args.hours:g} h'}{only} (read "
        f"{now:%Y-%m-%d %H:%M} UTC; {teams_at})\n\n{warning}Hypotheses only: teams mislead on purpose and most rank "
        "below us; weigh a claim by the poster's rank and test it before acting. Organisers' posts (announcements, "
        "changelog, dev answers, including their replies in the ask-devs forum) are facts about the rules.\n\n" +
        renamed + "\n\n".join(sections) + "\n")
    temporary.replace(report)
    summary = f"{total_new} new messages, {len(known)} stored"
    if problems:
        print(f"discord: partial ({len(problems)} problems): {summary}")
        return 2 if stopped else 3
    print(f"discord: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

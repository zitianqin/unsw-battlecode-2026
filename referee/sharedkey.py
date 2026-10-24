"""The sonar key test builds play (bot/sonarkey.hpp), so paired games differ only where a change acts.

With P.sonarBuildKey a Mac build (clang, with #embed) hashes its own source into its key, while a Linux build (g++ 13,
no #embed) falls back to the fixed key. So a candidate and the champion in one seat against one opponent differed
from the first sonar message, and one bot's Mac and Linux games differed too: v0071 on 30 maps, both seats, against
swarm, differed between a Mac and a Linux host in 13 of 60 seats on its own key and 0 of 60 on the fixed key
(MISSES 99). Test copies therefore play fixed keys (test_copy), the same on every platform: the champion and the
candidate against opponents one key, each opponent with the switch (breeder and most gauntlet entries from v0024; swarm
has none) one of its own ("own", driver.opponent_bot), since its key differed by platform too. A head-to-head game
gives the candidate another fixed key (`alt`): P.sonarTeamTag already keeps two teams on one key from reading each
other's sonar, and the second key also holds for a candidate that turns the tag off. The uploaded build keeps its own
key. Stdlib only, so the loop's driver imports it without the toolkit.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

SONAR_KEY = re.compile(r"(\bbool\s+sonarBuildKey\s*=\s*)true\b")
SWITCH = re.compile(r"\bbool\s+sonarBuildKey\s*=\s*(?:true|false)\b")
FIXED = re.compile(r"(\binline\s+constexpr\s+std::uint64_t\s+KEY\s*=\s*0x)([0-9a-fA-F]+)(ULL;)")
ALT = 0xD6E8FEB86659FD93  # the head-to-head candidate's fixed key is the champion's xor this
# sonarkey.hpp's lines that list the sources it hashes; the rest is how it makes the key.
SOURCE_LIST = re.compile(r'#embed "|\bSRC_[A-Z0-9_]+\b|^\s*\};\s*$')


# How `bot` makes its sonar key: sonarkey.hpp without its list of hashed sources, and P.sonarBuildKey's value in
# params.hpp. Two bots with the same key code make their key the same way, so a candidate that only added a module to
# the list (E0348) still gets fixed keys, and one that changed how the key is made (a key change, a noloss idea:
# E0087) or flipped the switch doesn't, since its test copy would play the champion's key instead of its own.
def key_code(bot: Path) -> str | None:
    key, params = bot / "sonarkey.hpp", bot / "params.hpp"
    if not key.is_file():
        return None
    switch = SWITCH.search(params.read_text()) if params.is_file() else None
    return "\n".join([*(line for line in key.read_text().splitlines() if not SOURCE_LIST.search(line)),
                      " ".join(switch[0].split()) if switch else ""])


# How far a fixed key is moved from sonarkey.hpp's KEY (xor) for each kind of test copy (test_copy): "fixed" the
# champion's and the candidate's against opponents, "alt" the candidate's head to head, "own" an opponent's, by its
# folder's name (driver.opponent_bot), so no two bots in a game share a key.
def key_xor(kind: str, bot: Path) -> int:
    if kind == "own":
        return int(hashlib.sha256(bot.name.encode()).hexdigest()[:16], 16)
    return {"fixed": 0, "alt": ALT}[kind]


# A copy of `bot` in `folder` that plays a fixed sonar key: P.sonarBuildKey off, and unless `kind` is "fixed" its KEY
# moved (key_xor). Named by its contents (NAME-<kind>key-DIGEST), reused when there, written whole or not at all, so
# two processes making one never see half of it. None when `bot` has no switch (or, for a moved key, no KEY).
def test_copy(bot: Path, folder: Path, kind: str = "fixed") -> Path | None:
    params, key = bot / "params.hpp", bot / "sonarkey.hpp"
    xor = key_xor(kind, bot)
    if not (params.is_file() and SWITCH.search(params.read_text()) and key.is_file()):
        return None
    if xor and not FIXED.search(key.read_text()):
        return None
    digest = hashlib.sha256()
    for path in sorted(p for p in bot.rglob("*") if p.is_file() and not any(
            part.startswith(".") for part in p.relative_to(bot).parts)):
        digest.update(path.relative_to(bot).as_posix().encode() + b"\0" + path.read_bytes())
    copy = folder / f"{bot.name}-{kind}key-{digest.hexdigest()[:16]}"
    if copy.exists():
        return copy
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / f".{copy.name}.{os.getpid()}"
    shutil.rmtree(partial, ignore_errors=True)
    shutil.copytree(bot, partial, ignore=shutil.ignore_patterns(".*"))
    (partial / "params.hpp").write_text(SONAR_KEY.sub(r"\1false", params.read_text()))
    if xor:
        (partial / "sonarkey.hpp").write_text(FIXED.sub(
            lambda m: f"{m.group(1)}{int(m.group(2), 16) ^ xor:016x}{m.group(3)}", key.read_text()))
    try:
        partial.rename(copy)
    except OSError:  # another process made it first
        shutil.rmtree(partial, ignore_errors=True)
    return copy

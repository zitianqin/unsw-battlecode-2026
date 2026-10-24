"""Checks a candidate compiles to the same machine code as the base bot but for the build's sonar key, so nothing it
plays can differ except through that key. Both are built from what the judge receives (bot.toml's include list,
unswbc's Project.collect_sources; a differing bot.toml is refused) exactly as the judge builds a submission (the
toolkit's clangtool.build: the judge's clang, flags and link line, the wasm stripped), with one fixed sonar key
(identical.shared_key_copies) unless their sonarkey.hpp differ, and every wasm section but the custom ones (names,
producers: not code or data) must be byte for byte the same.

The key is left out on purpose: with P.sonarBuildKey on (the champion's), the key is a hash of the bot's own source,
so any edit, a comment included, changes the real build's code section and which of the other test team's tags
collide with ours in self-play (identical.shared_key_copies, E0116); against other teams the key only tells our own
records apart. Measured on v0056 (2026-09-28): the real build is 403,039 bytes, one comment line or a renamed local
makes it 403,037 with a different code section, and with the key fixed all three build one 403,035-byte wasm (one
sha256). A rename, a comment or a doc can't change a move otherwise, and with P.debugIndicator off (params.hpp's P is
constexpr) the compiler should drop the debug log line's code and strings too (not measured). So an identical idea
whose candidate passes needs no games (driver.same_code; REFLECT 2026-09-28 20:52 F6: a rename of I735's switch was
queued as its own test). Anything else, a changed constant included, fails here and goes to the identical check
(referee/identical.py).

    uv run referee/samecode.py --cand PATH --base PATH [--out FILE]

Prints one line and exits 0 only when the code is the same; --out saves {"ok", "line", "cand", "base"} (each wasm's
size and sha256, built with the fixed key).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from identical import shared_key_copies
from run import content_named_copy

CUSTOM = 0  # the wasm section id of custom sections
TOML_DIFFERS = "bot.toml differs: the judge would build other files or another language"


# A wasm module's sections in order, as (id, payload), or raises ValueError when `blob` isn't one.
def sections(blob: bytes) -> list[tuple[int, bytes]]:
    if blob[:4] != b"\0asm":
        raise ValueError("not a wasm module")
    found, at = [], 8
    while at < len(blob):
        kind, at = blob[at], at + 1
        size = shift = 0
        while True:  # unsigned LEB128
            byte, at = blob[at], at + 1
            size |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                break
        found.append((kind, blob[at:at + size]))
        at += size
    return found


def code(blob: bytes) -> list[tuple[int, bytes]]:
    return [(kind, payload) for kind, payload in sections(blob) if kind != CUSTOM]


# A copy of `bot` in `into` holding only what an upload sends the judge (bot.toml and the files its include list
# matches, as unswbc's submit zips them), so a file the judge never sees can't enter either build.
def submitted(bot: Path, into: Path) -> Path:
    from unswbc.project import Project
    project = Project.from_dir(bot)
    project.collect_sources()
    copy = into / bot.name
    for name in {"bot.toml", *project.sources}:
        (copy / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bot / name, copy / name)
    return copy


def built(bot: Path) -> bytes:
    from unswbc import clangtool
    return clangtool.build(bot).read_bytes()


# (same, line, {"cand": ..., "base": ...}) for two bot folders; ({}: nothing built) when their bot.toml differ.
def check(cand: Path, base: Path) -> tuple[bool, str, dict]:
    if (cand / "bot.toml").read_bytes() != (base / "bot.toml").read_bytes():
        return False, TOML_DIFFERS, {}
    with tempfile.TemporaryDirectory(prefix="bc-samecode-") as folder:
        # Each built from a folder named after all its files: clangtool caches a build by folder name and .c/.cpp
        # sources only, so a header-only change would reuse the other's build (run.content_named_copy).
        sent = [submitted(bot, Path(folder) / name) for name, bot in (("sent-cand", cand), ("sent-base", base))]
        pair = shared_key_copies(*sent, Path(folder))
        blobs = {name: built(content_named_copy(bot, Path(folder))) for name, bot in zip(("cand", "base"), pair)}
    sizes = {name: {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()} for name, blob in blobs.items()}
    if code(blobs["cand"]) == code(blobs["base"]):
        return True, (f"same machine code as the base but for the build's sonar key ({sizes['base']['bytes']} bytes of "
                      "wasm with the key fixed)"), sizes
    changed = [kind for (kind, x), (_, y) in zip(code(blobs["cand"]), code(blobs["base"])) if x != y]
    return False, (f"different machine code: {sizes['cand']['bytes']} bytes against {sizes['base']['bytes']}, "
                   f"sections {changed or 'in number'} differ"), sizes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cand", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    same, line, sizes = check(args.cand.resolve(), args.base.resolve())
    print(line)
    if args.out:
        args.out.write_text(json.dumps({"ok": same, "line": line, **sizes}, indent=1) + "\n")
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())

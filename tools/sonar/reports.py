"""Turns a sonar research report's "Master Protocol Reference Table" into spec entries, one per team, as the report
states them (verify.py then judges each on replays). A row is "| rank | team name | team id | ... | protocol family |
wire signature / layout | ..." (research/top-300-sonar-protocols*.md); what the signature names becomes the entry:

    Silent ...                          -> kind silent
    ... Token / Discrete ...            -> kind token
    SSS [Family] Magic `0xabc`          -> the SSS layout under that 12-bit magic
    [Magic `0xabc` |] Bits a..b X | c..d Y [| e Round | f Len | g ID]  -> a report layout (widths 6, 6, 9, 7, 12 when
                                           only a shift is given), the magic as its recogniser
    `0xabc` alone                        -> kind keyed, the top 12 bits as its recogniser
    anything else                        -> a keyed stub

    .venv/bin/python tools/sonar/reports.py research/<report>.md --version report-2026-10-10 [--append]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE))

import formats  # noqa: E402

SSS = dict(id=(38, 14), length=(31, 7), x=(25, 6), y=(19, 6), round=(10, 9))
WIDTH = dict(X=6, Y=6, Round=9, Len=7, ID=12)
KEY = dict(X="x", Y="y", Round="round", Len="length", ID="id")
FIELD = re.compile(r"(\d+)(?:\.\.(\d+))? (X|Y|Round|Len|ID)\b")


def magic(value: str) -> dict:
    return dict(mask=hex(0xFFF << 52), value=hex((int(value, 16) & 0xFFF) << 52))


# The table's rows of `text`: the lines under its "## Master Protocol Reference Table" heading.
def master_rows(text: str) -> list[list[str]]:
    rows, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.startswith("## Master Protocol Reference Table")
        elif inside and re.match(r"^\| \d", line):
            cells = line.replace("\\|", "\x00").strip().strip("|").split("|")
            rows.append([c.strip().replace("\x00", "|") for c in cells])
    return rows


# The entry a row states, labelled `version` and sourced to `source`.
def entry(cells: list[str], version: str, source: str) -> dict:
    rank, name, team = int(cells[0]), cells[1], int(cells[2])
    rest = " | ".join(cells[5:])
    e = dict(team=team, name=name, version=version, source=f"{source}, Master Protocol Reference Table rank {rank}",
             note=rest[:160], layout=[])
    if "Silent" in cells[5]:
        return dict(e, kind="silent")
    if "Token" in rest or "Discrete" in rest:
        return dict(e, kind="token")
    if m := re.search(r"SSS (?:Family )?Magic `(0x[0-9a-fA-F]+)`", rest):
        return dict(e, kind="report", layout=[dict(magic(m.group(1)), **SSS)])
    found: dict[str, tuple[int, int]] = {}
    for lo, hi, what in FIELD.findall(rest.split("+ Target")[0]):
        width = int(hi) - int(lo) + 1 if hi else WIDTH[what]
        if KEY[what] not in found and int(lo) + width <= 64:
            found[KEY[what]] = (int(lo), width)
    if "x" in found and "y" in found:
        layout = dict(mask="0x0", value="0x0")
        if m := re.search(r"Magic `(0x[0-9a-fA-F]+)`", rest):
            layout.update(magic(m.group(1)))
        spans = [((1 << w) - 1) << s for s, w in found.values()]
        if any(a & b for i, a in enumerate(spans) for b in spans[i + 1:]) or int(layout["mask"], 16) & sum(spans):
            return dict(e, kind="report", note=e["note"] + " (fields overlap: not encodable)")
        return dict(e, kind="report", layout=[dict(layout, **found)])
    if m := re.search(r"^[^|]*\| `(0x[0-9a-fA-F]+)`", rest):
        return dict(e, kind="keyed", layout=[magic(m.group(1))])
    return dict(e, kind="keyed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", type=Path)
    parser.add_argument("--version", required=True, help="the entries' version label, e.g. report-2026-10-10")
    parser.add_argument("--append", action="store_true", help="append the entries to tools/sonar/spec.toml")
    args = parser.parse_args()
    found = [entry(cells, args.version, str(args.report)) for cells in master_rows(args.report.read_text())]
    if args.append:
        formats.append(found)
        print(f"appended {len(found)} entries to {formats.SPEC}")
    else:
        print("\n".join(formats.entry_toml(e) for e in found))
    return 0


if __name__ == "__main__":
    sys.exit(main())

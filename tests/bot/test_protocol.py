"""Editable protocol scenarios for the bot; the runner passes the compiled main program."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BOT = Path(__file__).resolve().parents[2] / "bot"


def protocol_input(team: str, dragon_id: int, first_round: int) -> str:
    """Two consecutive turns in a fixed corridor whose only legal action is east."""
    lines = [f"ID {dragon_id}", f"TEAM {team}", "MAP 16 16", "UNIT_LIMIT 64"]
    north_walls = {(8, 8), (9, 8), (9, 9)}
    west_walls = {(8, 8)}
    for turn, (head_x, tail_x, tail_y, facing) in enumerate(((8, 8, 9, "N"), (9, 8, 8, "E"))):
        lines += [f"ROUND {first_round + turn}", f"DIR {facing}", "LENGTH 2", "UNIT_COUNT 4"]
        # Turn two arrives on protocol 3: a 64-bit value (an enemy's, since it fails our tag) and echoes.
        lines += ["NUM_MSGS 0"] if turn == 0 else ["NUM_MSGS 1", "18446744073709551615", "ECHOES 1 0 0 0 1"]
        lines += [f"{x} {y} 0 -1" for y in range(5, 12) for x in range(head_x - 3, head_x + 4)]
        lines += ["DRAGON_BODIES 2", f"{team} {dragon_id} {head_x} 8 {facing} 1",
                  f"{team} {dragon_id} {tail_x} {tail_y} {facing} 0"]
        lines += [" ".join("w" if (x, y) in north_walls else "." for x in range(head_x - 3, head_x + 4))
                  for y in range(5, 13)]
        lines += [" ".join("w" if (x, y) in west_walls else "." for x in range(head_x - 3, head_x + 5))
                  for y in range(5, 12)]
    return "\n".join([*lines, "ENDGAME", ""])


def ram_input(team: str, dragon_id: int, round_num: int) -> str:
    """One turn: our length-4 dragon heads east at (8,8); a length-6 enemy head waits two free tiles east."""
    enemy = "B" if team == "A" else "A"
    lines = [f"ID {dragon_id}", f"TEAM {team}", "MAP 16 16", "UNIT_LIMIT 64",
             f"ROUND {round_num}", "DIR E", "LENGTH 4", "UNIT_COUNT 4", "NUM_MSGS 0"]
    lines += [f"{x} {y} 0 -1" for y in range(5, 12) for x in range(5, 12)]
    ours = [(8, 8, "E", 1), (7, 8, "E", 0), (6, 8, "E", 0), (5, 8, "E", 0)]
    theirs = [(10, 8, "N", 1), (10, 9, "N", 0), (10, 10, "N", 0), (10, 11, "N", 0), (9, 11, "E", 0), (8, 11, "E", 0)]
    lines.append(f"DRAGON_BODIES {len(ours) + len(theirs)}")
    lines += [f"{team} {dragon_id} {x} {y} {facing} {head}" for x, y, facing, head in ours]
    lines += [f"{enemy} 99 {x} {y} {facing} {head}" for x, y, facing, head in theirs]
    lines += [" ".join("." for _ in range(7)) for _ in range(8)]
    lines += [" ".join("." for _ in range(8)) for _ in range(7)]
    return "\n".join([*lines, "ENDGAME", ""])


def check_ram(binary: Path) -> None:
    """A reachable longer enemy head is rammed with one two-step MOVE, for either team."""
    for team, dragon_id, round_num in (("A", 1, 100), ("B", 7, 450)):
        result = subprocess.run([str(binary)], input=ram_input(team, dragon_id, round_num),
                                text=True, capture_output=True, check=True, timeout=10)
        actions = [line for line in result.stdout.splitlines() if line.startswith(("MOVE ", "SPLIT "))]
        if actions != ["MOVE EE"]:
            raise ValueError(f"ram {team}: expected one MOVE EE, got {actions!r}")
        print(f"PASS protocol ram {team}: sprint MOVE EE", flush=True)


def check_protocol(binary: Path) -> None:
    """Exercise main, parsing, state updates and turn output, including a fresh late-game child."""
    for team, dragon_id, first_round in (("A", 1, 100), ("B", 7, 450)):
        result = subprocess.run([str(binary)], input=protocol_input(team, dragon_id, first_round),
                                text=True, capture_output=True, check=True, timeout=10)
        frames = result.stdout.split("ENDTURN\n")
        if len(frames) != 3 or frames[-1].strip():
            raise ValueError(f"protocol {team}: expected exactly two completed turns, got {result.stdout!r}")
        for frame in frames[:-1]:
            actions = [line for line in frame.splitlines() if line.startswith(("MOVE ", "SPLIT "))]
            if not actions or actions[-1] != "MOVE E":
                raise ValueError(f"protocol {team}: only east is legal, got {actions!r}")
            if "PROTOCOL 3" not in frame.splitlines():
                raise ValueError(f"protocol {team}: every turn must ask for protocol 3, got {frame!r}")
            if any(line.startswith("LOG") for line in frame.splitlines()):
                raise ValueError(f"protocol {team}: the debug log line is written only with debugIndicator on")
            for line in frame.splitlines():
                parts = line.split()
                if parts and parts[0] == "SONAR" and (len(parts) != 3 or parts[1] not in "NESW"
                                                      or not 0 <= int(parts[2]) < 2**64):
                    raise ValueError(f"protocol {team}: malformed sonar {line!r}")
        if not any(line.startswith("SONAR ") for line in result.stdout.splitlines()):
            raise ValueError(f"protocol {team}: a dragon must share what it sees over sonar")
        print(f"PASS protocol {team}: two legal protocol-3 turns with echoes and sonar, clean ENDGAME", flush=True)


def fields(line: str) -> dict[str, str]:
    words = line.split()[1:]
    return dict(zip(words[0::2], words[1::2]))


def check_debug_line(binary: Path) -> None:
    """A build with the debug line on (BOT_NOTES, as P.debugIndicator does) writes one LOG line a turn, with edge on
    every turn and vgain and rsteps on the ram, and plays exactly what the ordinary build plays."""
    compiler = shutil.which("clang++") or shutil.which("c++")
    with tempfile.TemporaryDirectory(prefix="bc-debug-line-") as directory:
        logging = Path(directory) / "logging"
        subprocess.run([compiler, "-std=c++20", "-O0", "-DBOT_NOTES=true", "-I", str(BOT), str(BOT / "main.cpp"),
                        "-o", str(logging)], check=True, timeout=120)
        for name, text in (("ram A", ram_input("A", 1, 100)), ("ram B", ram_input("B", 7, 450)),
                           ("protocol A", protocol_input("A", 1, 100)), ("protocol B", protocol_input("B", 7, 450))):
            plain, logged = (subprocess.run([str(bot)], input=text, text=True, capture_output=True, check=True,
                                            timeout=10).stdout for bot in (binary, logging))
            logs = [line for line in logged.splitlines() if line.startswith("LOG ")]
            if [line for line in logged.splitlines() if not line.startswith("LOG ")] != plain.splitlines():
                raise ValueError(f"debug line {name}: the logging build must play the same turns, got {logged!r}")
            if len(logs) != plain.count("ENDTURN") or not all("edge" in fields(line) for line in logs):
                raise ValueError(f"debug line {name}: one LOG line with edge a turn, got {logs!r}")
            if name.startswith("ram"):
                ram = fields(logs[0])
                if (ram.get("rule"), ram.get("vgain"), ram.get("rsteps")) != ("ram", "2", "2"):
                    raise ValueError(f"debug line {name}: the ram's line needs vgain 2 and rsteps 2, got {logs[0]!r}")
            elif any("vgain" in fields(line) for line in logs):
                raise ValueError(f"debug line {name}: only a ram logs vgain, got {logs!r}")
    print("PASS protocol debug line: same moves with the line on, edge every turn, vgain and rsteps on the ram",
          flush=True)


if __name__ == "__main__":
    check_protocol(Path(sys.argv[1]))
    check_ram(Path(sys.argv[1]))
    check_debug_line(Path(sys.argv[1]))

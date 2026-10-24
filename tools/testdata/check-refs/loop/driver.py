"""Top-level definitions copied from loop/driver.py (2026-09-26), bodies cut."""

ROTATION = ("swarm", "rammer", "breeder")


def refreshed_cleanly(session: dict) -> bool:
    return bool(session)


def refresh_referee() -> None:
    pass


def run_tuner(exp: Experiment, worktree: Path) -> None:
    pass


# A candidate that beats the champion then plays each frozen opponent in referee/gauntlet.toml on POOL_PAIRS
def discord_teams_alert() -> None:
    # teams.json as loop/discord_teams.refresh writes it, around a real leaderboard's member lists.
    folder = "referee/gauntlet.toml"
    return folder

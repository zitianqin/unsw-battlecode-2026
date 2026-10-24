"""The class head copied from referee/sprt.py (2026-09-26), bodies cut."""


class BaselineCache:
    def __init__(self, path: Path | None):
        self.path = path

    def key(self, job: Job) -> str:
        return str(job)

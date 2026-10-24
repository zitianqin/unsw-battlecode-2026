# The docstring of the real test of the tools' message reader (2026-09-26).
"""Checks the transcript reader on testdata/transcript-sample.jsonl: 20 entries of the orchestrator's real transcript
(bc-main-fresh, 2026-09-25), one or more of each kind, long strings cut to 240 characters and bulky fields dropped:
typed and mid-turn (queued) messages from sig, an interrupt, the assistant's text, thinking and tool calls, a tool
result, task notifications, a child's cross-session messages (as a turn and queued mid-turn), /compact and its summary.

    .venv/bin/python -m unittest tools.test_my_messages
"""

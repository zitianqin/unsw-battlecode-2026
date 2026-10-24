"""Prints, in order, what sig typed and what the orchestrator told him since a time, from a Claude Code session
transcript, for the REFLECT check (loop/REFLECT.md).

    .venv/bin/python tools/my_messages.py --since 2026-09-26T09:00 [--session PATH] [--chars 600] [--check-times |
        --check-evalline | --check-asks | --check-answers | --check-sources | --check-length]

The default session is the most recently modified transcript in this project's Claude folder that names itself the
orchestrator (a title or agent name starting "bc-main", CLAUDE.md). Each line is `local time sig|claude: text`: sig's
lines are his typed messages (queued ones sent mid-turn included) and "[interrupted]" when he stopped a turn; claude's
are the assistant's visible text. Tool calls and results, thinking, system reminders, task notifications, compaction
summaries and cross-session messages (a child's or a peer's, not sig's) are left out, as is a line that doesn't parse
(the session may still be writing it). A long message keeps its start and its end (where questions and hand-offs
usually sit). --since without a zone is local time. The file is streamed. Every mode's printed lines have what looks
like a password or API key replaced by "[redacted]" (redact).

--check-times prints instead the orchestrator's lines whose clock times look typed from memory (MISSES 26), one per
problem with the text around it, for REFLECT's item (b) (loop/reflect.py runs it beside the messages):

- a placeholder minute, "15:3x" or "16:xx": the minute was guessed, unless it sits in quotes (a message quoting the
  slip it reports, REFLECT 2011 F12); loop/health.py's typed_times runs the same detector (placeholders) on the
  records the orchestrator writes;
- a time at least LATE_MINUTES after the message's own minute (so still to come when it was written) that the words
  just before it place in the past: one of the last three words of its clause (text after the last . ; | ! or ?) is
  a PAST word ("since 15:40", "started at 14:55", "sig repeated it at 16:00", "(sig, ~16:00)") and none is a FUTURE
  word ("merged before 21:00", "was moved to 16:40", "has started, likely around 23:30"), no future auxiliary sits in
  the clause (FUTURE_AUX: "gets reviewed and merged in the 22:06 batch") and the time isn't a label ("the 22:06
  batch", LABEL). Later times with no past
  word are left alone: most are deadlines, reminders and finish estimates, and those are rarely marked ("- 18:30:
  check the pace", "16:37 advmaps refresh"): flagging every later time not marked future listed 116 in the 3 h to
  17:12 on 27 Sep.

- a time given as now ("Status (14:37)", "It's 12:12", "as of 14:52", NOW) that differs from its message's time by
  LATE_MINUTES or more, either way (REFLECT 2026-09-28 14:51 F6).

- a guess at the time of a message of sig's (sig_guesses; REFLECT 2026-09-29 20:42 F8): in a sentence about him, a
  time called "not logged", or an earlier one given as "about hh:mm". The fix it names is a date-only quote ("sig, 30
  Sep"), never a looked-up time (sig, 29 Sep).

- a capacity or finish estimate ("about 8 tests fit by 18:00", "finishes around 18:00", "the top 20 take until about
  05:00", ETA_CLAIM) in a reply that doesn't name its source (SOURCE_NAMED: evalline, results.jsonl, a measured rate)
  or opens with evalline's line, or whose turn ran no source (SOURCE_RUN: a Bash command reading evalline.py,
  results.jsonl, sprt-progress.json, remote-status.json or the utilisation records, or an evalline print read from a
  watcher's output) (REFLECT 2026-09-30 15:13 F21:
  four capacity figures in five minutes from 13:50:56, the first two before the 48-tests-in-24-h query ran).

A time right after a date ("2026-09-26 15:14", "26 Sep 15:21") counts on that date, so an earlier day's is never
late; one over 12 h ahead is taken as yesterday's, as around midnight.

--check-evalline prints the orchestrator's status replies that don't open with loop/evalline.py's line (CLAUDE.md
"Status lines"; REFLECT 2026-09-28 14:51 F8): a message naming an experiment with a pipeline state (PIPELINE: "has
started", "queued", "in flight") sent unasked (after an alert or a result) or after sig asked about the state, or
one opening "Status:" (STATUS_OPENING, REFLECT 2026-09-30 15:13 F19), or the first reply of a turn a notice started (a
lane's or agent's task notification or a child's cross-session message, notice; REFLECT 2026-10-01 15:21 F17: 44 such
replies on 1 Oct 09:18-15:21 named no pipeline word, "E0492's judge confirmed the discard", and passed). On 28 Sep 11:49-15:13 it listed 18 of 84
messages, among them the seven F8 found by hand but 12:05:44 and 12:42:51. It also prints a status reply opening with that line but leaving out the agents line the turn's latest evalline.py
print held (REFLECT 2026-09-29 20:42 F15): its own run, or its print read from another output, as watch.sh's; an
earlier turn's is never carried over (REFLECT 2026-09-30 09:12 F21: 03:49:59's agents line, finished agents and all,
was held against every status line to 06:17).

--check-asks prints each sentence handing work to sig (ASK: "want me to", "say if you want", "if you want, I'll",
"should I", "shall I", "can I ...?", "let me know", "if you have", "say yes and I'll", "want that?", "consider
<verb>ing", and "if you want X, say so" in a message recommending nothing, SAY_SO; not an option offered for his own
use, "works if you want it") or waiting on him (WAIT: "needs your go",
"needs your yes", "waits for your go", "waiting on your answer", "holding ... until you answer", "until you say go",
"on your go", "it's your call", "I'd like your yes"; not "nothing is waiting on your decision" or "without waiting for
your answer"), quotations left out ("starting it now" not "want me to?"),
unless it or an earlier sentence of its message names a permission sig hasn't granted (UNGRANTED: outward, public,
posting, money, his accounts, destructive, credentials, links) or it says what else blocks it (CANT: "I can't", "only
you"). A stated reason alone no longer passes (REFLECT 2026-09-28 17:52 F1: "That changes the gate, so it needs your
go" at 17:24, then 4 min waiting, when gates were already the orchestrator's): a sentence naming what the standing
grants cover (GRANTS, from the memory notes) says which grant. It also prints a recommendation ("I'd change", "my
plan") in a message that neither says "starting it now" nor names such a permission or blocker (F19: 16:04:47's "So
I'd change it once" had sig approve it). MISSES 19. The upkeep gives each check's output to the REFLECT worker
beside times.txt (loop/reflect.py).
It also prints a reply agreeing to a change of the loop's behaviour that sig asked for (AGREE: "Agreed", "Yes", "I
agree", "updating the memory") when it names no branch, commit, idea id or file that makes the change (CHANGE): sig's
message is a request (no "?") about the loop (GRANTS' first topic). REFLECT 2026-09-28 20:52 F1: 18:08:17's "Agreed
... Updating the memory to match" changed nothing, and sig asked twice more (MISSES 66). Over 27-28 Sep it listed 3
replies: 18:08:17, 23:59:29's "saving it to memory" and 03:10:35's "saved in memory".
And it prints each deferral (DEFER: "queued a fix", "goes on the fix list", "the 22:06 batch", "after usage resets",
"I'll ... later"; not a lane's deferral or close reported, LANE_NOTICE, REFLECT 2026-09-30 15:13 F13) in a message
naming no blocker but time (BLOCKER, UNGRANTED, CANT) whose turn writes no reminders.jsonl or owed.md line (WRITES; REFLECT 2026-09-29 20:42 F2, F5: said and never written down). A deferral
tied to a clock time or the reset (CLOCKED: "from now until 02:59", "held until after the reset", "I'll start it at
03:05") needs a reminders.jsonl line in its turn, whatever blocks it: owed.md never fires (21:10 F7). Outside the Stop
hook it also prints a manual step promised with no alert or build named to replace it (MANUAL: "by hand", "I'll
check it every time"; 03:11 F9). And it prints a standing rule the orchestrator sets itself ("From now on, ...", "I'll
always", "I won't ... again", "So it doesn't recur", PROMISE) when neither it nor the next sentence names a file,
commit, check or owed line enforcing it (ENFORCED; REFLECT 2026-09-30 09:12 F3, MISSES 66: three memory-only rules
that night, one broken again at 07:15).
And it prints a caution in place of a check (CAUTION: "worth watching", "keep an eye on", "a pattern to watch", "if
a third does"), unless its message names the check started (CHECK_STARTED: "starting it now", "a subagent is checking",
"the check is running", a reminders.jsonl line). REFLECT 2026-09-30 15:13 F4 (MISSES 9): 11:50:47 called E0411's
noValidAction deaths "worth watching" and let the SPRT go on; the check sig then asked for took 4 minutes.

--check-answers prints each reply to a message of sig's with a question in which no line's opening (its first
ANSWER_CHARS characters, after evalline's line when a question not about the state gets one) starts with an answer
(ANSWER: "Yes", "No", "Partly", "Not yet", "You're right") or shares a word with the question (3+ letters, compared on
their first 5, less STOPWORDS). REFLECT 2026-09-28 20:52 F5: 19:37:22's question got "Both notes are now on I758.", 18:29:00's "has it
started?" a merge report (MISSES 20). Over 27-28 Sep it listed 26 of 198 replies, most of them about other work;
"Checking <the question's subject>" passes, as the answer follows in that turn. Each question of a message is its own
(questions: each sentence with a "?", or opening like one, "Did we ...", "why ..."), so an answer to one doesn't
cover the others, and an opening "noted on I####" answers none unless it names a past result (E####), opens with an
answer word ("No: ... noted on I1122") or sig asked for that note (NOTE_ASK; REFLECT 2026-09-30 15:13
F8: 13:01:33's "are all the evals operaitng at full capacity? also our 5h usage reset" passed on "usage" alone, and
12:40:29's "Did we already try similar in our old experiments" got "The history is noted on I1122"). tools/orchestrator_stop_hook.py runs
it, and --check-asks, on the turn it ends (unanswered: any line of any reply of the turn may answer), so a miss is
caught when written, not at REFLECT (REFLECT 2026-09-29 17:32 F5, F12); there --check-asks gives only its ASK, WAIT,
deferral, standing-rule and caution lines.
A message of sig's holding an error trace (TRACE: a Python traceback, a Node stack, "MODULE_NOT_FOUND", "Failed with
non-blocking status code"; not a bare "XError: ..." line or exit code, which handoff notes hold too) is a question too,
pasted or not, and the reply has to quote its failing line (error_line: the trace's last "XError: ..." line, its first
LINE_CHARS characters) and name the command that produced it in backticks (REFLECT
2026-09-30 15:13 F1: 13:00:02's pasted "Stop hook error" ended in Node's `Cannot find module ...`, and the next reply
was a status line).

--check-length prints, while sig's terse rule stands in the quota-habits memory note (TERSE), each status reply and
each reply in a turn a notice started (a task notification or a child's message) over LENGTH_LINES lines, not
counting evalline's own lines pasted into it (counted_lines; REFLECT 2026-09-29 20:42 F6: sig, 20:42:00, "be conciser
in this main thread"; 20:42:36 replied to an expected lane exit). A status reply is --check-evalline's: an answer to a
question of sig's not about the state isn't one, whatever experiments and pipeline words it names (30 Sep 11:18:59's
answer to "did we get any useful information from analysing the top players", 13:50:56's to "do we have enough
time"), nor are the handoff notes he asked for (HANDOFF_ASK). tools/orchestrator_stop_hook.py blocks on it for the
turn it ends (REFLECT 2026-09-30 15:13 F20: 24 replies over the cap in 6 hours under the prompt hook's note alone).

check_briefs (the Stop hook's, no flag) lists each Agent call whose brief sends a study to opponents' games
(OPPONENT_GAMES: study-, scout- or ladder-replays, "team 306's games", "leaders' ranked replays", "their recent
ranked games"; not a brief for code work, one naming a branch, CODE_WORK) without a `Sources:` line saying ranked games
only, by submission id, loop/decoys.py's flags applied, and kept and dropped counts (SOURCES_PARTS). REFLECT
2026-09-30 15:13 F5 (MISSES 103, 71): 11:21:52's beacon study read Cutlery's "older ranked replays" with no decoy
filter until sig asked. A Stop hook runs after the call, so the fix it asks for is a message to the running agent;
a PreToolUse hook on Agent could refuse the call instead.

check_detached (the Stop hook's, no flag) lists each Bash call starting a `loop/driver.py --once` lane or
tools/orch/watch.sh detached (detached_start: a lone &, nohup, disown or stdout redirected in that command), which
both refuse to run. REFLECT 2026-10-01 15:21 F2: three such starts in 2 h; over the transcript to 1 Oct 15:21 it
lists 29 of the 1412 Bash calls naming either, every one of them a start the driver or watcher refuses.

--check-sources prints each reply answering a question of sig's about what the game, engine, site or server does
(a sentence of his own words, not a pasted quote of ours, with a "?" or "double check"/"pretty sure" and a SOURCE_TERMS
phrase: logs, other teams, public, docs, timeouts, the map pool, the website, online or server games) that cites none
of our own sources (CITED: a docs/unsw/ path or page name such as `timeouts.md`, discord/, messages.jsonl, a digest,
or "no hit in docs/unsw or Discord"). A reply answers the question when it shares a word with it and runs to
INTERIM_CHARS ("Checking the docs" notes pass); the question stays open across messages of sig's that ask nothing of
the kind, for QUESTION_MINUTES, until one reply cites a source or is flagged. REFLECT 2026-09-29 14:29 F1 (MISSES 14):
13:04:28 said debug lines reach public replays and 13:08:52 said "As far as the downloads show, no", both unchecked
against docs/unsw/, which says they do (13:23:03). Over 26 Sep 18:00 to 29 Sep 14:40 (2289 replies) it listed 7:
those two, 27 Sep 15:45:19 and 28 Sep 14:36:48 (replays and site Elo, answered without the docs), 29 Sep 00:58:02
(other teams' replays, answered from our own downloads) and two about our own downloads (27 Sep 19:56:18, 19:58:16).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

PROJECT = Path.home() / ".claude/projects/-Users-zitianqin-Dev-unsw-battlecode-2026"
ORCHESTRATOR = "bc-main"
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
# hh:mm, or a placeholder minute (hh:Nx, hh:xx), not inside a longer time, a zone offset (+10:00) or a number.
CLOCK = re.compile(r"(?<![\d:+\-.])([01]?\d|2[0-3]):([0-5][\dx]|xx)(?![\dx])", re.I)
DATE = re.compile(r"(?:(\d{4}-\d\d-\d\d)|(\d{1,2} [A-Z][a-z]{2})|([A-Z][a-z]{2} \d{1,2}),?)[T ]~?$")
QUOTES = "\"'`\u201c\u201d\u2018\u2019"  # straight, back and curly quotes around a quoted placeholder
LATE_MINUTES = 2
PAST = {"since", "started", "began", "said", "asked", "repeated", "fired", "merged", "pushed", "finished", "ended",
        "sent", "posted", "hit", "was", "were", "ran", "sig"}
FUTURE = {"before", "by", "until", "till", "to", "after", "due", "next", "finish", "resets", "reminder", "eta", "from",
          "likely", "probably", "expected"}
# A later time its clause puts in the future after all, past word or not: a future auxiliary anywhere in the clause, or
# a time naming a scheduled event ("the 22:06 batch"). 19:37:53 on 29 Sep: "gets reviewed and merged in the 22:06
# batch" read as past on "merged" (the review of orch/builder-c's hook replay).
FUTURE_AUX = re.compile(r"\b(?:will|won[’']t|shall|gets?|going to|to be|is due|scheduled)\b|[’']ll\b", re.I)
LABEL = re.compile(r"\b(?:the|its|their|each)\s+~?$", re.I)
# A guess at the time of a message of sig's (REFLECT 2026-09-29 20:42 F8: "came in at about 19:02; I didn't log the
# exact minute", "(minute not logged)"): the transcript logs his messages to the second and this file prints them, so a
# sentence about him (ABOUT_SIG) that calls a time unlogged, or gives an earlier one as "about hh:mm", guessed it.
ABOUT_SIG = re.compile(r"\b(?:sig|he|his|you|your)\b", re.I)
UNLOGGED = re.compile(r"\b(?:not logged|unlogged|didn[’']t log|did not log|wasn[’']t logged)\b", re.I)
ABOUT = re.compile(r"(?:\b(?:about|around|roughly|approximately)\s+|~)$", re.I)
# "its" is no "it's" (REFLECT 2026-09-30 09:12 F22: 08:02:14's "after its 07:58 firing" read as now).
NOW = re.compile(r"(?:\bstatus\s*\(|\bit(?:[’']s|\s+is)\s+|\bas of\s+|\bnow\s*\()~?$", re.I)
# The first words of evalline's status line (loop/evalline.py), which a status or alert reply opens with (CLAUDE.md
# "Status lines").
EVALLINE = re.compile(r"(?:\*\*)?(?:Evaluating:|Idle since\b)")
# A reply about the pipeline's state (REFLECT 2026-09-28 14:51 F8: "E0241 has started coding I425 ... All 6 places are
# in flight"): an experiment id with one of these, in a message not answering a question of sig's about something else.
PIPELINE = re.compile(r"\b(?:in flight|under test|being coded|evaluating|queued|has started|started coding|"
                      r"has claimed|ended with|ended at|now running|running now|is coding)\b", re.I)
# A reply that calls itself a status ("Status: ...", "Status (14:37): ..."), whatever it names (REFLECT 2026-09-30 15:13
# F19: from 13:13 a prose "Status:" replaced evalline's line, and 13:12:20's named no pipeline state).
STATUS_OPENING = re.compile(r"^\W*status\b\s*[:(]", re.I)
STATUS_ASK = re.compile(r"\b(?:status|eta|evaled|evaluat\w*|running|rn|progress|what'?s (?:being|going|up))\b", re.I)
# --check-asks' two patterns (MISSES 19; REFLECT 2026-09-28 14:51 F16, F17 and 17:52 F1, F19; the module docstring
# says what passes): ASK, handing work to sig ("want me to", "should I", "say yes and I'll"), and WAIT, waiting on him
# ("needs your go", "waiting on your answer", "on your go", "it's your call").
# REFLECT 2026-09-29 12:53 F7: "Say yes and I'll add it" (10:49:49), "Want that?" (11:35:32) and "consider restarting
# them" (11:18:17) passed ASK, as did 11:18:17's "it needs your yes" WAIT. REFLECT 2026-09-29 14:29 F18: WAIT missed
# "waits for your go" (27 Sep 23:19:24), "I'll start it on your go" (21:23:16), "Your call; I'll leave it as is
# otherwise" (16:35:53) and "not touching it until you say go".
# REFLECT 2026-09-29 17:32 review of orch/builder-c: "if you want" alone flagged an option offered for sig's own use
# ("the server version ... works if you want it", 18:16:34), so it counts only when it asks his go for our work
# ("If you want, I'll check ...", 17:38:43; "Say if you want either", 15:15:02), and "Nothing is waiting on your
# decision" (17:18:38) is no wait. REFLECT 2026-09-29 21:10 F4: "can I raise the limit to 0.9 ...?" (23:45:52) passed
# ASK, and "Raised the overnight load share to 0.9 without waiting for your answer" (00:00:56) was flagged as a WAIT.
ASK = re.compile(r"\b(?:want me to|(?:say|tell me) (?:if|whether) you want|if you (?:want|like|prefer)"
                 r"(?=[^.?!,]{0,60},? (?:I[’']ll|I can|I could|I will)\b)|should I|shall I|can I(?=[^?]*\?)|"
                 r"let me know|if you have|say yes and I[’']ll|want (?:that|this)(?: one)?(?=\?)|"
                 r"consider (?!(?:some|any|every|no)thing\b)\w+ing)\b", re.I)
WAIT = re.compile(r"(?<!n[’']t )(?<!not )(?<!nothing )(?<!nothing is )(?<!nothing[’']s )(?<!without )"
                  r"\b(?:(?:needs?|waits? (?:on|for)) your (?:go|answer|decision|call|approval|"
                  r"ok|yes)\w*|waiting (?:on|for) your \w+|"
                  r"(?:holding|hold|wait) [^.?!]{0,40}until you (?:answer|reply|say|decide)|"
                  r"(?:when|once|until) you say (?:go|so|yes)|on your (?:go|yes|ok)\b|"
                  r"(?:it[’']?s|is|that[’']?s) your call\b|(?<![\w ,])your call\b|I[’']?d like your (?:go|yes|ok)\w*)",
                  re.I)
# "If you want futility stops now, say so" (13:49:41 on 30 Sep, REFLECT 15:13 F14): an option handed to sig to choose,
# an ask only when its message recommends nothing (RECOMMEND); sig had to ask "well what do u recommend".
SAY_SO = re.compile(r"\bif you (?:want|would like|[’']d like|like|prefer)\b[^.?!]{0,80}?,? (?:just )?(?<!you'll )"
                    r"(?<!you’ll )(?<!you will )(?:say so|say the word|tell me|let me know|say (?:yes|go))\b", re.I)
RECOMMEND = re.compile(r"\b(?:I[’']?d (?:recommend|suggest|propose|change|add|make|build|fix|go with|switch|drop|"
                       r"move|raise|keep|use|start|queue|run)|I (?:recommend|suggest|propose)|my (?:plan|recommendation"
                       r"|proposal|pick)|the fix I[’']?d make)\b", re.I)
START = re.compile(r"\bstarting (?:it |them |that |this |those )?now\b|\bI[’']m (?:starting|doing) it\b|"
                   r"\bI[’']ve (?:started|queued|launched)\b", re.I)
# What sig has not granted standing (the memory notes gates-my-judgement and recommend-then-start: outward, money,
# destructive actions and his security rules; CLAUDE.md's force pushes, other people's branches and posted links; his
# own posts and the site's ratings and seeding, 29 Sep 04:29:21 and 07:38:53).
UNGRANTED = re.compile(r"\b(?:outward|public(?:ly)?|post(?:ing)? (?:it |this |that )?(?:in|to|on) (?:the )?"
                       r"(?:discord|server)|discord post|your (?:#[\w-]+ |discord )?posts?|rating reset|seeding|"
                       r"force[- ]push\w*|money|billing|spend\w* (?:credit|money)|"
                       r"your (?:azure |github |google |discord )?(?:account|login|machine|password)|credentials?|"
                       r"api key|remote control|destructive|delet\w+ (?:your|sig[’']s)|ranked request|"
                       r"(?:posted|each) (?:link|file)|gist|branch(?:es)? (?:I|the orchestrator) didn[’']t make)\b",
                       re.I)
CANT = re.compile(r"\b(?:I can[’']?t|I cannot|only you|blocked by|there[’']s no way)\b", re.I)
# --check-asks' deferrals (REFLECT 2026-09-29 20:42 F2, F5): work put off to a later step ("I've queued a fix", "goes on
# the fix list", "queued for the next batch", "the 22:06 batch", "I'll pick it up after the reset", "I'll do it
# later"), flagged when its turn neither names a blocker other than time (BLOCKER, UNGRANTED or CANT: then it can't
# start now) nor writes it down (WRITES: a reminders.jsonl or owed.md line, or health.py --remind) nor hands it to an
# agent (HANDS). A thing that happens later by itself ("safe to eat later", "a merge turn retries after the reset")
# isn't one: "later" and the reset count only after "I'll", "we'll" or a verb placing work there.
DEFER = re.compile(r"\b(?:queued (?:a|the|it as a|them as) fix\w*|(?:on|to|onto|into) (?:the |a |its |[\w-]+[’']s )?"
                   r"(?:next )?fix (?:list|round)|fix list|(?:for|in|into|to|with) the next batch|the \d{1,2}:\d\d "
                   r"batch|(?:I[’']ll|we[’']ll|I will|we will|goes|go|resumes?|picks? up|paused?)\b"
                   r"[^.?!|]{0,60}\b(?:after (?:the )?(?:usage )?(?:resets?|limit|cut)|later)\b|later (?:today|"
                   r"tonight))", re.I)
# A deferral tied to a clock time or the usage reset (REFLECT 2026-09-29 21:10 F7): "so from now until 02:59: I start no
# new builders or reviews" (00:03:14) went only into owed.md, which never fires. It passes only when its turn writes a
# reminders.jsonl line (REMINDS, or health.py --remind), whatever blocks it: the time is what the reminder is for.
# What the loop does by itself then (ITSELF: "the driver retries the paused coders on its own at 03:05", 03:01:47) is
# no deferral, nor is "queued" alone ("should stay queued and retry its merge turn after the reset", 22:09:29).
CLOCKED = re.compile(r"\b(?:from now (?:until|till)|(?:I[’']ll|we[’']ll|I will|we will|paus\w*|hold\w*|held|"
                     r"start no|defer\w*|picks? (?:it |them )?up)\b[^.?!|]{0,60}?\b"
                     r"(?:until|till|after|at|for)) (?:the )?(?:\d{1,2}:\d\d\b|(?:usage )?(?:limit )?resets?\b)", re.I)
# A lane's deferral or close reported (REFLECT 2026-09-30 15:13 F13): "That lane deferred: ... another lane from the 11:03
# batch took the slot first" (11:28:05) read as a deferral on "the 11:03 batch", and seven replies followed saying
# "Nothing is being put off". The driver's own event is no work put off; such notices belong in the log, not a reply.
LANE_NOTICE = re.compile(r"\blanes?\b[^.?!|]{0,80}\b(?:deferred|defers|closed|closing|closes|exited|exits)\b|"
                         r"^\W*(?:it|that lane) deferred\b", re.I)
ITSELF = re.compile(r"\bon (?:its|their) own\b|\bby (?:it|them)sel(?:f|ves)\b|\bautomatically\b", re.I)
REMINDS = re.compile(r"reminders\.jsonl|--remind\b")
# A manual step promised with nothing named to replace it (REFLECT 2026-09-30 03:11 F9, MISSES 30): "I'll check it every
# time I post a status line" (23:23:04) and "if one starts I'll stop it by hand" (02:04:31), each automated only after
# sig asked. A sentence naming the alert, build or hook that replaces it passes ("Until it's built, I'll do the 98%
# pause by hand", 02:06:34), as does a value set by hand (22:51:36's "one number set by hand"). Not blocking: REFLECT
# judges it.
MANUAL = re.compile(r"\b(?:I[’']ll|I will|we[’']ll|doing|do)\b[^.?!|]{0,60}?\bby hand\b|"
                    r"\bI[’']ll (?:check|read|look at|watch|verify|do)\b[^.?!|]{0,80}?\b(?:each|every) time\b", re.I)
REPLACED = re.compile(r"\b(?:alerts?|alerting|automat\w*|built|build\w*|orch/[\w.-]+|health (?:check|\.py)|hooks?|"
                      r"watcher|monitor)\b", re.I)
HANDS = ("Agent", "Task", "SendMessage")
BLOCKER = re.compile(r"\b(?:blocked|merge conflict|conflicts? with|load (?:gate|share|stop|is)|waits? (?:on|for) "
                     r"(?:orch/|E\d{4}|I\d{3,4}|the (?:merge|review|evaluation|result|SPRT))|needs? (?:orch/|E\d{4}|"
                     r"I\d{3,4})|until (?:orch/\S+|E\d{4}|I\d{3,4}) (?:merges|lands|ends|finishes))", re.I)
WRITES = re.compile(r"reminders\.jsonl|owed\.md|--remind\b")
READ_TOOLS = ("Read", "Grep", "Glob")  # a tool call naming those files with one of these only reads them
# --check-length (REFLECT 2026-09-29 20:42 F6): the memory note holding sig's terse rule, the headings that state it,
# and the most lines a status reply or a reply to a routine notice has while it stands, evalline's own lines not
# counted (sig, 29 Sep 22:10: "any reply is the evalline plus at most 2-3 short lines").
MEMORY = Path.home() / ".claude/projects/-Users-zitianqin-Dev-unsw-battlecode-2026/memory/quota-habits.md"
TERSE = re.compile(r"\*\*(?:Terse main thread|Silent unless needed)\*\*")
LENGTH_LINES = 3
# A message of sig's asking for notes to paste back after a compaction ("leave some notes in chat for ur post compact
# self", 27 Sep 02:08:36): the notes reply is as long as it needs to be.
HANDOFF_ASK = re.compile(r"(?=[\s\S]*\bnotes?\b)[\s\S]*\b(?:compact\w*|hand-?offs?)\b", re.I)
# One of evalline's own lines pasted into a reply (its first line, or its live, goal and agents lines, bold or bulleted
# as the replies write them: "- **Goal line:** ..."), or a --results-since line ("E0508 (I1148 ...) ended 13:20: ...",
# the digest the prompt hook asks for when sig is back): the rule asks for them, so they don't count against the cap.
EVALLINE_LINE = re.compile(r"^[\s*_>-]*(?:Evaluating:|Idle since\b|(?:live|goal(?: line)?|agents)\W*:|"
                           r"E\d{4} \(.*?\) ended \d{1,2}:\d\d\b)", re.I)
# A reply agreeing to what sig asked (--check-asks), and what names the change that carries it out: a branch, a
# commit (7+ hex with a digit and a letter), an idea id or a file.
AGREE = re.compile(r"^\W*(?:agreed|yes\b|yep\b|sure\b|will do\b|i agree\b|good (?:idea|call|point)\b|you[’']re "
                   r"right\b)|\b(?:updating|updated|saving|saved|noted)(?: it| that)? (?:in |to |into )?(?:the |my )?"
                   r"memory\b", re.I)
CHANGE = re.compile(r"\borch/[\w.-]+|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b|\bI\d{3,4}\b|"
                    r"[\w./-]+\.(?:py|md|sh|json|jsonl|toml|hpp|cpp)\b")
# A standing rule the orchestrator sets itself with nothing to enforce it (REFLECT 2026-09-30 09:12 F3, MISSES 66):
# "From now on, a targeted idea whose own measure moves the wrong way before hand-in goes to its post-mortem"
# (04:07:07), "So it doesn't recur: saved to memory" (03:50:51, broken again at 07:15) and "The lesson is saved to
# memory: copy data into the coder's clone" (06:36:40) each held only as memory. "From now on" counts opening its
# sentence or with "I" beside it, so code taking effect ("drivers started from now on run it": 28 of 45 uses on 26-30
# Sep) doesn't, nor a quote of sig's; a bare "saved to memory" doesn't either, since sig's grants belong there
# (CLAUDE.md). Over 26-30 Sep it listed 15 replies, the three above among them. It passes when
# it or the sentence after it names what enforces it (ENFORCED: a file, commit, branch or idea id, as CHANGE, or a
# check, hook, test, guard, alert or owed line). Blocking: tools/orchestrator_stop_hook.py asks for the mechanism.
PROMISE = re.compile(r"^[^\w\"“]*from now on\b|\bI\b[^.?!|,;]{0,40}?\bfrom now on\b|\bfrom now on,? I\b|"
                     r"\b(?:I|we)(?:[’']ll| will) (?:always|never)\b|\b(?:I|we) won[’']t\b[^.?!|]{0,60}?\bagain\b|"
                     r"\bnever again\b|\bso it (?:doesn[’']t|won[’']t|can[’']t) (?:recur|happen again)\b|"
                     r"\b(?:lesson|rule) (?:is |was )?(?:saved|noted) (?:to|in) memory\b", re.I)
ENFORCED = re.compile(CHANGE.pattern + r"|\b(?:checks?|checked by|hooks?|tests?|guards?|alerts?|refuses?|blocks?|"
                      r"owed(?:\.md)? line)\b", re.I)
# --check-asks' cautions (the module docstring): a warning left to watch, and what names the check started instead.
# "moments worth watching" is tools/watch.py's scan, not a caution.
CAUTION = re.compile(r"(?<!moments )\bworth (?:watching|monitoring|keeping an eye on)\b|\bkeep(?:ing)? an eye on\b|"
                     r"\b(?:a|one|something|the thing) (?:pattern |trend )?to watch\b|\bbears? watching\b|"
                     r"\b(?:I[’']ll|we[’']ll|let[’']s) (?:watch|monitor) (?:it|this|that|for|whether|how)\b|"
                     r"\bif (?:a third|another|it happens again|it recurs)\b[^.?!|]{0,40}\b(?:I[’']ll|we[’']ll)\b", re.I)
CHECK_STARTED = re.compile(START.pattern + r"|\b(?:check|measure\w*|investigation|study|subagent|agent|debug\w*)\b"
                           r"[^.?!|]{0,40}\b(?:is (?:running|checking|reading|on it)|has started|started)\b|"
                           r"\b(?:a subagent|an agent) is (?:checking|reading|measuring|looking)\b|reminders\.jsonl|"
                           r"--remind\b", re.I)
# --check-answers: an opening that answers ("Yes", "Not yet", "Partly"), how much of a reply counts as its opening, and
# the words too common to show a reply is about the question.
ANSWER = re.compile(r"^\W*(?:yes|yeah|yep|no|nah|nope|not\b|partly|mostly|probably|right\b|correct|both answers|"
                    r"short answers|answers? first|to answer|briefly|it[’']?s\b|it (?:is|was|does|will|can)\b|they\b|"
                    r"we (?:do|don[’']t|did|are|aren[’']t|can|can[’']t)\b|i (?:do|don[’']t|think|would|wouldn[’']t|"
                    r"agree)\b|then\b|kind of|sort of|good question|you[’']re right|my (?:recommendation|answer|advice|"
                    r"take|suggestion|pick)\b|short answer)", re.I)
ANSWER_CHARS = 150
# A sentence opening like a question though it has no "?" (questions), and a note filed on an idea (unanswering_opening).
# Only a verb before its subject ("Did we", "Is that"): "what was the result of the previous eval" (14:14:00) is a
# question too, but its answer ("E0396 ... was discarded") shares no word with it.
INTERROGATIVE = re.compile(r"^\W*(?:(?:also|and|but|so|wait|ok(?:ay)?|then)\W+)*(?:did|does|do|is|are|was|were|can|"
                           r"could|should|would|will|have|has|isn[’']t|aren[’']t|don[’']t|doesn[’']t|didn[’']t)\s+"
                           r"(?:we|you|u|it|they|i|the|this|that|there|our|any|all)\b", re.I)
NOTED = re.compile(r"\b(?:noted|recorded|written|logged|added|filed)\b[^.|]{0,20}\b(?:on|to|in|under) I\d{3,4}\b",
                   re.I)
# A question asking for exactly such a note ("can u add a note on I1122 that X?"), which "Added to I1122: X." answers.
NOTE_ASK = re.compile(r"\b(?:add|write|put|leave|note|record|log|file)\b[^.?!|]{0,40}\b(?:on|to|in|into|under) "
                      r"I\d{3,4}\b", re.I)
# An error trace in a message of sig's, its failing line, and a command named in backticks (the module docstring).
# Only a trace's strong forms: a bare "RuntimeError: host az-3 unreachable" or "exit code 1" also sits in the handoff
# notes he pastes back (the review of orch/batch-f).
TRACE = re.compile(r"Traceback \(most recent call last\)|^\s+at \S.*[(:]\d+|\bMODULE_NOT_FOUND\b|"
                   r"\bFailed with (?:non-)?blocking status code\b", re.M)
ERROR_LINE = re.compile(r"^\s*(?:[\w.]*[A-Z]\w*)?(?:Error|Exception)\b: \S.*$|^\s*(?:fatal|error): \S.*$", re.M)
QUOTE_CHARS = 30  # how much of the failing line a reply quotes, at least
LINE_CHARS = 500  # how much of the failing line is kept for matching: a pasted file can be one line of megabytes
STOPWORDS = frozenset(
    "that this with what have your from they them will would should could there their about just like also into been "
    "does dont doesnt cant then than when where which while were some only more most make made much many other "
    "because really right yeah okay think know need want still even well very being over after before again here "
    "those these ours".split()
    # three-letter words count too ("gpu" answered 03:00:04's "Any ideas on utilising it?" on 29 Sep), but these
    + "the and you are for can how why was not but our any has had get got its did use now yet all one two out who may "
      "see say new old too off way lot per via own his her him she let set put run ran top".split())
# What the standing grants cover, each with the memory note (or CLAUDE.md line) that grants it, for the flag's text.
GRANTS = (
    (re.compile(r"\b(?:gates?|rules?|thresholds?|bounds?|policy|retests?|re-?judg\w*|samples?|the loop|prompts?|"
                r"reviews?|backlog|ideas?|priorit\w+|tests?)\b", re.I),
     "gates-my-judgement: the loop, its gates and rules are the orchestrator's to change"),
    (re.compile(r"\b(?:merg\w+|branch\w*|push\w*)\b", re.I),
     "CLAUDE.md: it merges its own branches after a review and pushes main"),
    (re.compile(r"\b(?:upload\w*|publish\w*|activat\w+)\b", re.I), "uploads-site-limit-only"),
    (re.compile(r"\b(?:dev (?:battles?|games?)|scrims?|unranked|map sweeps?)\b", re.I), "dev-games-uncapped"),
    (re.compile(r"\bnew files?\b", re.I), "new-files-ok"),
    (re.compile(r"\bworldgen\b", re.I), "worldgen-ok"),
    (re.compile(r"\b(?:load (?:limit|share)|load-fraction)\b", re.I), "mac-load-0-4"),  # 21:10 F4: 23:53:44's "limit"
)


# True when the transcript's first title or agent-name entry starts with "bc-main" (children are "bc-E####-...").
def is_orchestrator(path: Path) -> bool:
    with path.open() as handle:
        for line in handle:
            if '"custom-title"' not in line and '"agent-name"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            name = entry.get("customTitle") or entry.get("agentName")
            if entry.get("type") in ("custom-title", "agent-name") and name:
                return name.startswith(ORCHESTRATOR)
    return False


def latest_session(folder: Path) -> Path:
    for path in sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        if is_orchestrator(path):
            return path
    raise SystemExit(f"no orchestrator transcript (titled {ORCHESTRATOR}*) in {folder}")


def text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content
                         if isinstance(block, dict) and block.get("type") == "text")
    return ""


# ("sig" | "claude", text) for an entry sig typed or the assistant showed him, else None.
def said(entry: dict) -> tuple[str, str] | None:
    kind = entry.get("type")
    if kind == "assistant":
        text = text_of(entry.get("message", {}).get("content"))
        return ("claude", text) if text.strip() else None
    if kind == "attachment":
        attachment = entry.get("attachment", {})
        if attachment.get("type") == "queued_command" and (attachment.get("origin") or {}).get("kind") == "human":
            return "sig", text_of(attachment.get("prompt"))
        return None
    if kind != "user" or entry.get("isCompactSummary") or entry.get("isMeta"):
        return None
    content = entry.get("message", {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    text = text_of(content)
    if (entry.get("origin") or {}).get("kind") == "human":
        return "sig", text
    if text.startswith("[Request interrupted by user"):
        return "sig", "[interrupted]"
    return None


# One line: system reminders dropped, line breaks as " | ".
def flatten(text: str) -> str:
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", " | ", REMINDER.sub("", text).strip()))


def shorten(text: str, chars: int) -> str:
    text = flatten(text)
    if len(text) <= chars:
        return text
    head = chars * 2 // 3
    return f"{text[:head].rstrip()} [...] {text[len(text) - (chars - head):].lstrip()}"


# (local time, entry) for each of the session's own timed transcript entries since `since`, in transcript order.
def records(path: Path, since: datetime) -> Iterator[tuple[datetime, dict]]:
    with path.open() as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:  # the session is still writing its last line
                continue
            stamp = entry.get("timestamp") if isinstance(entry, dict) else None
            if not isinstance(stamp, str) or entry.get("isSidechain"):
                continue
            at = datetime.fromisoformat(stamp).astimezone()
            if at >= since:
                yield at, entry


# (local time, "sig" | "claude", text) for each line sig typed or was shown since `since`, in transcript order.
def entries(path: Path, since: datetime) -> Iterator[tuple[datetime, str, str]]:
    for at, entry in records(path, since):
        if found := said(entry):
            yield at, *found


# The tool calls in a transcript entry, as (tool name, id, its input).
def tool_calls(entry: dict) -> Iterator[tuple[str, str, dict]]:
    content = (entry.get("message") or {}).get("content") if entry.get("type") == "assistant" else None
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") == "tool_use":
            yield str(part.get("name")), str(part.get("id")), part.get("input") if isinstance(part.get("input"),
                                                                                                dict) else {}


# The tool results in a transcript entry, as (the call's id, its text).
def tool_results(entry: dict) -> Iterator[tuple[str, str]]:
    content = (entry.get("message") or {}).get("content") if entry.get("type") == "user" else None
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") == "tool_result":
            value = part.get("content")
            yield str(part.get("tool_use_id")), value if isinstance(value, str) else text_of(value)


# What an entry says about the session's agents (loop/evalline.py's agents line): ("start", call id, its description)
# for an Agent call; ("launched", call id, agent id) for a background agent's launch note, which is no result; ("done",
# call id, "") for any other result; and ("done", call id or "", agent id) for a notice that an agent stopped, which
# names its call or only its agent (29 Sep 06:33:30's "Build host cache integrity fix").
AGENT_TOOLS = ("Agent", "Task")
NOTICE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)


def agent_events(entry: dict) -> Iterator[tuple[str, str, str]]:
    for name, call, given in tool_calls(entry):
        if name in AGENT_TOOLS:
            yield "start", call, str(given.get("description") or given.get("subagent_type") or "agent")
    for call, text in tool_results(entry):
        if text.startswith("Async agent launched"):
            if agent := re.search(r"agentId: ([\w-]+)", text):
                yield "launched", call, agent[1]
        else:
            yield "done", call, ""
    # A notice comes as a turn of its own, or queued while a turn runs (an attachment's queued_command).
    attachment = entry.get("attachment") if isinstance(entry.get("attachment"), dict) else {}
    text = text_of((entry.get("message") or {}).get("content")) if entry.get("type") == "user" else \
        text_of(attachment.get("prompt")) if attachment.get("type") == "queued_command" else ""
    for notice in NOTICE.findall(text) if "<task-notification>" in text else []:
        field = {key: value for key, value in re.findall(r"<([\w-]+)>([^<]*)</\1>", notice)}
        if field.get("status", "running") != "running":
            yield "done", field.get("tool-use-id", ""), field.get("task-id", "")


# The byte offset of the first whole line of `path`'s tail that reaches back before `since`: read back from its end
# CHUNK bytes at a time until a chunk's first timed line is older than `since` (or the file's start). The Stop hook and
# loop/evalline.py's agents line read an orchestrator's transcript (hundreds of MB) from there, not from its start.
CHUNK = 4_000_000


def tail_start(path: Path, since: datetime) -> int:
    with path.open("rb") as handle:
        start = handle.seek(0, 2)
        while start > 0:
            start = max(0, start - CHUNK)
            handle.seek(start)
            lines = handle.read(CHUNK).split(b"\n")[0 if start == 0 else 1:]
            if (found := first_time(lines)) is not None and found < since:
                break
        if start == 0:
            return 0
        handle.seek(start)
        return start + len(handle.readline())  # past a line cut by the chunk


# The session's latest reply in `path` (the orchestrator's text, not a subagent's), as (local time, text), from its last
# CHUNK bytes, or None: the Stop hook's pending_reply and the prompt hook's length note read it.
def last_reply(path: Path) -> tuple[datetime, str] | None:
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - CHUNK))
        tail = handle.read().decode("utf-8", "replace")
    for line in reversed(tail.splitlines()):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and not entry.get("isSidechain") and (found := said(entry)) \
                and found[0] != "sig" and isinstance(entry.get("timestamp"), str):  # a reply, not sig's line
            return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).astimezone(), found[1]
    return None


# The time of sig's latest line in `path` but `prompt` (the message being sent, which the transcript may hold already),
# read back from its end CHUNK bytes at a time, at most `chunks` of them, or None: the prompt hook's results digest
# reads how long he was away. Only lines naming "human" or an interrupt are parsed, as only those can be his.
def last_typed(path: Path, prompt: str = "", chunks: int = 25) -> datetime | None:
    skipped = False
    with path.open("rb") as handle:
        end = handle.seek(0, 2)
        for _ in range(chunks):
            if end <= 0:
                break
            start = max(0, end - CHUNK)
            handle.seek(start)
            lines = handle.read(end - start).split(b"\n")
            end = start + len(lines[0]) if start else 0  # a line the chunk cut is read whole with the next one
            for line in reversed(lines[1:] if start else lines):
                if b'"human"' not in line and b"Request interrupted" not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("isSidechain") or not isinstance(entry.get("timestamp"), str) \
                        or (said(entry) or ("",))[0] != "sig":
                    continue
                if not skipped and prompt.strip() and said(entry)[1].strip() == prompt.strip():
                    skipped = True  # the message being sent
                    continue
                return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).astimezone()
    return None


# The first time on a line of `lines` that has one, or None.
def first_time(lines: list[bytes]) -> datetime | None:
    for line in lines:
        try:
            stamp = json.loads(line).get("timestamp")
        except (ValueError, AttributeError):
            continue
        if isinstance(stamp, str):
            return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
    return None


def messages(path: Path, since: datetime, chars: int) -> Iterator[str]:
    for at, who, text in entries(path, since):
        if text := shorten(text, chars):
            yield f"{at:%Y-%m-%d %H:%M:%S} {who}: {text}"


# The date a time right after it names, when one does ("2026-09-26 15:14", "26 Sep 15:21"), else None.
def named_date(before: str, year: int) -> datetime | None:
    if not (found := DATE.search(before)):
        return None
    iso, day_month, month_day = found.groups()
    try:
        if iso:
            return datetime.fromisoformat(iso)
        return datetime.strptime(f"{day_month or month_day} {year}", "%d %b %Y" if day_month else "%b %d %Y")
    except ValueError:
        return None


# The placeholder minutes in `text` ("17:2x", "16:xx"), each as (where it starts, the time), leaving out one with a
# quote mark on both sides ("17:2x"-style, a quotation of the slip). Also used by loop/health.py (typed_times).
def placeholders(text: str) -> Iterator[tuple[int, str]]:
    for found in CLOCK.finditer(text):
        if "x" not in found[2].lower():
            continue
        start, end = found.start(), found.end()
        if 0 < start and end < len(text) and text[start - 1] in QUOTES and text[end] in QUOTES:
            continue
        yield start, found[0]


# What looks typed from memory in a message the orchestrator sent at `at` (the module docstring's rules), each as
# (where in `text` the time starts, "<the time>: <why>").
def time_problems(at: datetime, text: str) -> Iterator[tuple[int, str]]:
    sent = at.replace(second=0, microsecond=0, tzinfo=None)
    guessed = dict(placeholders(text))
    for found in CLOCK.finditer(text):
        hour, minute = found.groups()
        if "x" in minute.lower():
            if found.start() in guessed:
                yield found.start(), f"{found[0]}: a placeholder minute, guessed"
            continue
        before = text[:found.start()]
        day = named_date(before, sent.year) or sent
        stated = day.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if NOW.search(before):  # either way, across midnight the nearer day
            off = ((stated - sent).total_seconds() + 12 * 3600) % (24 * 3600) - 12 * 3600
            if abs(off) >= LATE_MINUTES * 60:
                yield found.start(), f"{found[0]}: given as now, but this message is from {at:%H:%M:%S}"
            continue
        if not LATE_MINUTES * 60 <= (stated - sent).total_seconds() <= 12 * 3600:
            continue
        clause = re.split(r"[.;|!?]", before)[-1]
        words = set(re.findall(r"[a-z]+", clause.lower())[-3:])
        if words & PAST and not words & FUTURE and not FUTURE_AUX.search(clause) and not LABEL.search(before):
            yield found.start(), f"{found[0]}: in the past by its wording, but after this message ({at:%H:%M:%S})"
    yield from sig_guesses(text, sent)


# The guesses at a message time of sig's in `text` (ABOUT_SIG, UNLOGGED, ABOUT), each as (where, what): in a sentence
# about him (or the one after it: "at about 19:02; I didn't log the exact minute"), a time called unlogged, or given
# as "about hh:mm" and (with `sent`, the message's minute) not after it. loop/health.py's typed_times runs it on the
# orchestrator's records too. Each says to quote sig by date only (sig, 29 Sep: "u do not need to look up my exact
# message times"; REFLECT 2026-09-29 21:10 F10: the check still said to look his time up, and "sig 30 Sep ~01:15-01:20"
# followed at 01:41:27).
def sig_guesses(text: str, sent: datetime | None = None) -> Iterator[tuple[int, str]]:
    before = False
    for sentence in re.finditer(r"[^.?!;|]+", text):
        about, before = before or bool(ABOUT_SIG.search(sentence[0])), bool(ABOUT_SIG.search(sentence[0]))
        if not about:
            continue
        for found in UNLOGGED.finditer(sentence[0]):
            yield (sentence.start() + found.start(), f"{found[0]!r}: quote sig by date only (\"sig, 30 Sep\"), with "
                   "no clock time")
        for found in CLOCK.finditer(sentence[0]):
            hour, minute = found.groups()
            if "x" in minute.lower() or not ABOUT.search(sentence[0][:found.start()]):
                continue
            if sent is None or sent.replace(hour=int(hour), minute=int(minute)) <= sent:
                yield (sentence.start() + found.start(), f"about {found[0]}: a guessed time for sig's message; quote "
                       "him by date only (\"sig, 30 Sep\"), with no clock time")


# --check-times' capacity and finish estimates (the module docstring): a count of tests by a time, or a finish or end
# "by", "around" or "until" a time (not "runs at 01:17", a schedule); what names a source for one; and the Bash commands
# that read one.
ETA_TIME = r"(?:about |around |roughly |~)?\d{1,2}:\d\d\b"
TESTS = r"\d+ (?:more )?(?:(?:tests?|evaluations?|experiments?|ideas?|SPRTs?|results?)\b|fit\b)"
ETA_CLAIM = re.compile(rf"\b{TESTS}[^.;|]{{0,40}}?\b(?:by|before|until) {ETA_TIME}|\b(?:by|to) {ETA_TIME},? "
                       rf"[^.;|]{{0,20}}?\b{TESTS}|\b(?:finish(?:es)?|done|ends?|completes?|lands?|takes?)\b"
                       rf"[^.;|]{{0,20}}?\b(?:by|around|near|until) {ETA_TIME}|\bdue by {ETA_TIME}", re.I)
SOURCE_NAMED = re.compile(r"\bevalline\b|results\.jsonl|sprt-progress|remote-status|utilisation|\bmeasured rate\b|"
                          r"\b(?:in|over) the last \d+ ?(?:h|hours?|min\w*)\b|\bat (?:its|the|today[’']s) (?:measured "
                          r"|recent )?rate\b", re.I)
SOURCE_RUN = re.compile(r"evalline\.py|results\.jsonl|sprt-progress|remote-status|utilisation|throughput\.py")


# The --check-times lines: one per problem in the orchestrator's messages since `since`, with the words around it. A
# turn (from sig's line) that ran a source (SOURCE_RUN) or read an evalline print (as check_evalline: a watcher's
# output file read with cat or Read) lets its replies' estimates name it.
def check_times(path: Path, since: datetime, chars: int = 80) -> Iterator[str]:
    ran = False
    for at, entry in records(path, since):
        ran = ran or any(name == "Bash" and SOURCE_RUN.search(str(given.get("command")))
                         for name, _, given in tool_calls(entry)) \
            or any(EVALLINE.match(re.sub(r"^\s*\d+[\t→]", "", line)) for _, output in tool_results(entry)
                   for line in output.splitlines())
        if not (found := said(entry)):
            continue
        who, text = found
        if who != "claude":
            ran = False
            continue
        text = flatten(text)
        for where, problem in time_problems(at, text):
            around = text[max(0, where - chars // 2):where + chars // 2]
            yield f"{at:%Y-%m-%d %H:%M:%S} claude: {problem} | ...{around}..."
        if ran and (EVALLINE.match(text) or SOURCE_NAMED.search(text)):
            continue
        for claim in ETA_CLAIM.finditer(text):
            why = "names no source" if ran else "comes from no source run this turn"
            yield (f'{at:%Y-%m-%d %H:%M:%S} claude: "{claim[0]}": a capacity or finish estimate that {why} (run '
                   f"evalline.py or a results.jsonl rate and name it) | {text[max(0, claim.start() - 40):claim.end() + 40]}")


# The --check-evalline lines: the orchestrator's status replies (a PIPELINE state with an experiment id, sent unasked
# or after sig asked about the state, or a turn's first reply to a notice) that don't open with evalline's line; those that open with it but have no
# evalline print this turn (`printed`: an earlier turn's line copied); and those that open with it but leave out the
# agents line the turn's latest evalline print held (REFLECT 2026-09-29 20:42 F15: no status line from 17:32 to 20:42
# had one), a print read with its agents lines cut by grep -v counting as holding one (`hidden`).
def check_evalline(path: Path, since: datetime, chars: int = 120) -> Iterator[str]:
    asked = None  # sig's latest line, None after one of ours
    runs, agents = set(), None  # evalline.py calls, and the agents line the turn's latest evalline print held
    hidden = set()  # calls reading a print with its agents lines cut out (grep -v "^agents")
    printed = False  # whether this turn has an evalline print (a status line is that turn's, CLAUDE.md)
    noticed = False  # whether this turn answers a notice and no reply of it has opened with evalline's line yet
    for at, entry in records(path, since):
        if entry.get("subtype") == "stop_hook_summary":
            agents, printed, noticed = None, False, False  # a new turn: its agents may have finished (09:12 F21)
        for name, call, given in tool_calls(entry):
            command = str(given.get("command")) if name == "Bash" else ""
            if re.search(r"loop/evalline\.py(?!\s+--)", command):
                runs.add(call)
            if re.search(r"grep -v[^|;]*agents", command):
                hidden.add(call)
        for call, output in tool_results(entry):
            # evalline.py's own run, or its print read from another output (watch.sh ends with one, read with cat or
            # Read). On 30 Sep 04:05-06:17 every status line's evalline came from the watcher's output, read with
            # `grep -v "^agents\|RE-ARM"`: the agents line was cut before the reply was written.
            lines = [re.sub(r"^\s*\d+[\t→]", "", line) for line in output.splitlines()]
            if call in runs or any(EVALLINE.match(line) for line in lines):
                printed = True
                # A line of running agents, or one saying the alerts watcher isn't running (evalline.watcher_text,
                # REFLECT 2026-09-30 21:15 F4), must reach the reply; "agents: none" with a running watcher needn't.
                agents = next((line for line in lines if line.startswith("agents: ")
                               and (not line.startswith("agents: none") or "watcher: not running" in line)), None)
                if agents is None and call in hidden:
                    agents = "agents: (cut from the evalline print by grep -v; read it whole)"
        if not (found := said(entry)):
            noticed = noticed or notice(entry)
            continue
        who, text = found
        if who == "sig":
            asked, agents, printed, noticed = text, None, False, False
            continue
        text, question = flatten(text), asked
        asked = None
        if (STATUS_OPENING.match(text) or noticed or (question is None or STATUS_ASK.search(question))
                and re.search(r"\bE\d{4}\b", text) and PIPELINE.search(text)) and not EVALLINE.match(text):
            noticed = False  # one per turn: the follow-up the Stop hook asks for carries the line
            yield f"{at:%Y-%m-%d %H:%M:%S} claude: a status reply not opening with evalline's line | {text[:chars]}..."
        noticed = noticed and not EVALLINE.match(text)
        if EVALLINE.match(text) and not printed:
            yield (f"{at:%Y-%m-%d %H:%M:%S} claude: a status line with no evalline print this turn (an earlier one's, "
                   f"copied) | {text[:chars]}...")
        elif EVALLINE.match(text) and agents and ("agents: " not in text or "watcher: not running" in agents
                                                   and "watcher: not running" not in text):
            yield (f"{at:%Y-%m-%d %H:%M:%S} claude: a status line without its agents line ({agents[:chars]}) | "
                   f"{text[:chars]}...")


# Whether a transcript entry not of sig's (said gives None) is a notice the orchestrator answers: a task notification
# (a lane's --once or an agent ending) or a child's cross-session message, not a tool result, a compaction summary or
# the Stop hook's own feedback (a blocked stop's follow-up answers the hook).
def notice(entry: dict) -> bool:
    if entry.get("type") != "user" or entry.get("isCompactSummary") or any(tool_results(entry)):
        return False
    text = text_of((entry.get("message") or {}).get("content")).strip()
    return bool(text) and not text.startswith("Stop hook feedback")


# A flattened message's sentences, a " | " (a line break) ending one too.
def sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.?!])\s+|\s*\|\s*", text)


# The --check-asks lines (the module docstring). Only an ungranted permission carries over from an earlier sentence:
# any stated reason did before, and looking back a sentence for one dropped 14:36:48's real ask on 28 Sep.
# `blocking` (tools/orchestrator_stop_hook.py) gives only the ASK, WAIT, deferral, PROMISE and CAUTION lines, leaving
# AGREE, MANUAL and RECOMMEND, which need judgement, to REFLECT.
def check_asks(path: Path, since: datetime, blocking: bool = False) -> Iterator[str]:
    asked = None  # sig's latest line, None after one of ours
    deferred, wrote = [], False  # this turn's deferrals (DEFER) and whether it wrote one down (WRITES)
    clocked, reminded = [], False  # its deferrals to a clock time (CLOCKED) and whether it wrote a reminder (REMINDS)
    for at, entry in records(path, since):
        calls = [(name, json.dumps(given)) for name, _, given in tool_calls(entry)]
        wrote = wrote or any(name in HANDS or name not in READ_TOOLS and WRITES.search(given) for name, given in calls)
        reminded = reminded or any(name not in READ_TOOLS and REMINDS.search(given) for name, given in calls)
        if not (said_now := said(entry)):
            continue
        who, text = said_now
        if who != "claude":
            yield from ([] if wrote else deferred) + ([] if reminded else clocked)  # the turn before sig's line ended
            asked, deferred, wrote, clocked, reminded = text, [], False, [], False
            continue
        request, asked = asked, None
        if not blocking and request and "?" not in request and GRANTS[0][0].search(request) and (found := AGREE.search(flatten(text))) \
                and not CHANGE.search(text):
            yield (f'{at:%Y-%m-%d %H:%M:%S} claude: "{found[0].strip()}" agrees to a loop change sig asked for but '
                   f"names no branch, commit, idea or file making it | {flatten(text)[:200]}")
        parts, excused = sentences(flatten(text)), False
        timed = [(sentence, found) for sentence in parts if (found := CLOCKED.search(sentence))
                 and not quoted(sentence, found) and not ITSELF.search(sentence)]
        clocked += [f'{at:%Y-%m-%d %H:%M:%S} claude: "{found[0]}" defers work to a clock time, but its turn writes no '
                    f"reminders.jsonl line (owed.md never fires) | {sentence[:200]}" for sentence, found in timed]
        if not any(BLOCKER.search(s) or UNGRANTED.search(s) or CANT.search(s) for s in parts):
            deferred += [f'{at:%Y-%m-%d %H:%M:%S} claude: "{found[0]}" defers work, but names no blocker and its turn '
                         f"writes no reminders.jsonl or owed.md line | {sentence[:200]}"
                         for sentence in parts if (found := DEFER.search(sentence)) and not quoted(sentence, found)
                         and sentence not in {s for s, _ in timed} and not LANE_NOTICE.search(sentence)]
        if not blocking:
            for sentence in parts:
                if (found := MANUAL.search(sentence)) and not quoted(sentence, found) and not REPLACED.search(sentence):
                    yield (f'{at:%Y-%m-%d %H:%M:%S} claude: "{found[0]}" promises a manual step, naming no alert or '
                           f"build that replaces it | {sentence[:200]}")
        if not CHECK_STARTED.search(text):
            for sentence in parts:
                if (found := CAUTION.search(sentence)) and not quoted(sentence, found):
                    yield (f'{at:%Y-%m-%d %H:%M:%S} claude: "{found[0]}" cautions without a check, naming none '
                           f"started | {sentence[:200]}")
        for i, sentence in enumerate(parts):
            if (found := PROMISE.search(sentence)) and not quoted(sentence, found) \
                    and not ENFORCED.search(" ".join(parts[i:i + 2])):
                yield (f'{at:%Y-%m-%d %H:%M:%S} claude: "{found[0]}" sets a standing rule, naming no file, commit, '
                       f"check or owed line that enforces it | {sentence[:200]}")
        advised = bool(RECOMMEND.search(text))
        for sentence in parts:
            excused = excused or bool(UNGRANTED.search(sentence))
            found = ASK.search(sentence) or WAIT.search(sentence) or (None if advised else SAY_SO.search(sentence))
            if not found or excused or CANT.search(sentence) or quoted(sentence, found):
                continue
            grants = [note for topic, note in GRANTS if topic.search(sentence)]
            why = f"the standing grants cover it ({grants[0]})" if grants else \
                "it names no permission sig hasn't granted"
            if found.re is SAY_SO:
                why = "its message recommends nothing"
            yield f'{at:%Y-%m-%d %H:%M:%S} claude: "{found[0]}" waits on sig, but {why} | {sentence[:200]}'
        if blocking or any(UNGRANTED.search(s) or CANT.search(s) for s in parts) or START.search(text):
            continue
        for sentence in parts:
            if found := RECOMMEND.search(sentence):
                yield (f'{at:%Y-%m-%d %H:%M:%S} claude: "{found[0]}" recommends without "starting it now" or a '
                       f"blocker | {sentence[:200]}")
    yield from ([] if wrote else deferred) + ([] if reminded else clocked)  # the last turn, so far


# Whether match `found` sits inside quotes in `sentence`: a quotation of the phrase ("starting it now" not "want me
# to?", 00:48:46 on 29 Sep, a handoff note on what sig wants), not the phrase said.
def quoted(sentence: str, found: re.Match) -> bool:
    return any(span.start() < found.start() < span.end()
               for span in re.finditer(r'"[^"]*"|“[^”]*”|`[^`]*`', sentence))


# The --check-length lines (REFLECT 2026-09-29 20:42 F6), while sig's terse rule stands (TERSE in MEMORY): each status
# reply (opening with evalline's line or "Status:", or check_evalline's test, its STATUS_ASK guard included) and each
# reply in a turn a notice started (a task notification or a child's message, not a line of sig's) that runs past
# LENGTH_LINES lines, evalline's own lines not counted (counted_lines), leaving out handoff notes sig asked for.
def check_length(path: Path, since: datetime, memory: Path = MEMORY) -> Iterator[str]:
    try:
        if not TERSE.search(memory.read_text()):
            return
    except OSError:
        return
    started_by = "sig"
    asked, handoff = None, False  # sig's latest line (None after one of ours), and whether it asks for handoff notes
    for at, entry in records(path, since):
        found = said(entry)
        if found is None:
            if notice(entry):
                started_by, handoff = "notice", False
            continue
        who, text = found
        if who == "sig":
            started_by, asked, handoff = "sig", text, bool(HANDOFF_ASK.search(own_words(text)))
            continue
        question, asked = asked, None
        lines = counted_lines(text)
        flat = flatten(text)
        status = EVALLINE.match(flat) or STATUS_OPENING.match(flat) or (question is None or STATUS_ASK.search(
            question)) and re.search(r"\bE\d{4}\b", flat) and PIPELINE.search(flat)
        if len(lines) > LENGTH_LINES and (status or started_by == "notice") and not handoff:
            kind = "a status reply" if status else "a reply to a notice"
            yield (f"{at:%Y-%m-%d %H:%M:%S} claude: {kind} {len(lines)} lines long, over {LENGTH_LINES} while the "
                   f"terse rule stands ({memory.name}) | {flat[:160]}")


# A reply's lines as --check-length counts them: non-blank, evalline's own lines left out (EVALLINE_LINE).
def counted_lines(text: str) -> list[str]:
    return [line for line in REMINDER.sub("", text).splitlines() if line.strip() and not EVALLINE_LINE.match(line)]


def words(text: str) -> set[str]:
    return {word[:5] for word in re.findall(r"[a-z][a-z0-9+]{2,}", re.sub(r"[’']s?\b", "", text.lower()))
            if word not in STOPWORDS}


# A reply's opening when it doesn't answer `question` by --check-answers' test (the module docstring), else None.
def unanswering_opening(text: str, question: str) -> str | None:
    reply = re.sub(r"[*`_]", "", flatten(text))
    if EVALLINE.match(reply):  # a status line first (CLAUDE.md): it is the answer to a question about the state
        if STATUS_ASK.search(question):
            return None
        reply = reply.partition(" | ")[2]
    opening = reply[:ANSWER_CHARS]
    if NOTED.search(opening) and not re.search(r"\bE\d{4}\b", reply) and not ANSWER.match(opening) \
            and not NOTE_ASK.search(question):
        return opening
    return None if ANSWER.match(opening) or words(opening) & words(question) else opening


# The questions in a message of sig's own words (own_words): each sentence with a "?" or opening like a question
# (INTERROGATIVE), so a message of three gets three. Each keeps the statements just before it, which it often asks about
# ("We have a gpu on zz ... Any ideas on utilising it?", "4 workers now read the games. - what does this mean"), but not
# an earlier question or what follows it. A tail of under two words (words) right after a question is part of it
# ("are we using the faster judge and engine now? btw?", "what are ur subagents doing rn? u have 10 i think?").
def questions(text: str) -> list[str]:
    asked, context = [], []
    for part in sentences(own_words(flatten(text))):
        if ("?" in part or INTERROGATIVE.match(part)) and asked and not context and len(words(part)) < 2:
            asked[-1] += " " + part
        elif "?" in part or INTERROGATIVE.match(part):
            asked.append(" ".join([*context, part]))
            context = []
        else:
            context.append(part)
    return asked


# The --check-answers lines (the module docstring): each question of sig's whose next reply has no line opening with
# its answer, and each error trace whose next reply doesn't quote it with its command.
def check_answers(path: Path, since: datetime) -> Iterator[str]:
    asked = None  # the questions in sig's latest line (questions), None after one of ours
    trace = None  # the failing line of an error trace in it
    for at, who, text in entries(path, since):
        if who == "sig":
            asked, trace = questions(text) or None, error_line(text)
            continue
        question, asked, failing, trace = asked, None, trace, None
        if failing is not None and not answers_trace(text, failing):
            yield (f"{at:%Y-%m-%d %H:%M:%S} claude: sig pasted an error trace, and this reply doesn't quote its "
                   f"failing line and name the command that produced it | failing: {failing[:120]} | reply: "
                   f"{flatten(text)[:120]}")
            continue
        lines = [line for line in REMINDER.sub("", text).splitlines() if line.strip()]
        for part in question or []:
            if all(unanswering_opening(line, part) is not None for line in lines):
                opening = unanswering_opening(text, part)
                yield (f"{at:%Y-%m-%d %H:%M:%S} claude: no line opens with an answer to sig's question | "
                       f"asked: {part[:120]} | reply: {opening}")


# The failing line of an error trace in `text` (TRACE; its last ERROR_LINE, else its last line naming an error), or
# None when it holds none.
def error_line(text: str) -> str | None:
    text = REMINDER.sub("", text)
    if not TRACE.search(text):
        return None
    found = ERROR_LINE.findall(text) or [line for line in text.splitlines() if re.search(r"error|fail", line, re.I)]
    return re.sub(r"\s+", " ", found[-1]).strip()[:LINE_CHARS] if found else None


# Whether `reply` quotes QUOTE_CHARS running characters of `line` (all of a shorter one) and names a command in
# backticks other than the quote.
def answers_trace(reply: str, line: str) -> bool:
    flat = re.sub(r"\s+", " ", REMINDER.sub("", reply))
    plain = re.sub(r"[`*]", "", flat)
    size = min(QUOTE_CHARS, len(line))
    quoted = any(line[i:i + size] in plain for i in range(len(line) - size + 1))
    commands = [span for span in re.findall(r"`([^`]+)`", flat) if span.strip() not in line]
    return quoted and bool(commands)


# --check-answers for the turn being ended (tools/orchestrator_stop_hook.py): each question of sig's (a "?" in his own
# words, own_words) since the orchestrator's last reply before `turn` that no line of a reply from `turn` on answers
# by its opening. Unlike check_answers, any line (or paragraph) of any reply of the turn may answer it, so a "Reading
# the logs." note before the answer passes, as does a second question's answer in the reply's second paragraph
# (15:07:05's "Portal maps in the eval maps: yes."); an answer sunk under status still doesn't (REFLECT 2026-09-29
# 17:32 F5: 17:11's "is the faster engine wrapper being used eveyrwhere now?" got "All 8 lanes are busy. ...").
# `since` only bounds the reading.
def unanswered(path: Path, since: datetime, turn: datetime) -> Iterator[str]:
    asked: list[tuple[datetime, str]] = []  # each question of sig's (questions) with its message's time
    traces: list[tuple[datetime, str]] = []  # error traces sig pasted, by their failing line (error_line)
    for at, who, text in entries(path, since):
        if who == "sig":
            asked += [(at, part) for part in questions(text)]
            if (failing := error_line(text)) is not None:
                traces.append((at, failing))
        elif at < turn:
            asked, traces = [], []  # replied to before this turn
        else:
            lines = [line for line in REMINDER.sub("", text).splitlines() if line.strip()]
            asked = [(when, q) for when, q in asked if all(unanswering_opening(line, q) is not None for line in lines)]
            traces = [(when, failing) for when, failing in traces if not answers_trace(text, failing)]
    for when, question in asked:
        yield f"{when:%Y-%m-%d %H:%M:%S} sig asked: {question[:160]} | no reply since opens with its answer"
    for when, failing in traces:
        yield (f"{when:%Y-%m-%d %H:%M:%S} sig pasted an error trace ending: {failing[:160]} | no reply since quotes "
               "that line and names the command that produced it")


# What sig wrote himself in a message: a pasted handoff is none of it, and a quote of ours he answers ("- It's still
# switched off ... - will running both engines use more?") only what follows its last " - ".
def own_words(text: str) -> str:
    if "<pasted_content" in text:
        return ""
    return text.rpartition(" - ")[2] if text.startswith("- ") else text


# --check-sources (the module docstring): a sentence of sig's own words asking (a "?" or CHECK_ASK) about what the
# game, engine, site or server does (SOURCE_TERMS), and what a reply citing our own sources names (CITED).
SOURCE_TERMS = re.compile(r"\b(?:logs?|logging)\b(?! in)|\b(?:other|enemy|opponent|rival)(?:[’']s)? teams?\b|"
                          r"\bpublic(?:ly)?\b|\bvisible\b|\bdocs?\b|\btimeouts?\b|\bmap pool\b|\bwebsite\b|"
                          r"\b(?:online|server|site|ranked|ladder) (?:games?|replays?|elo|ratings?|matches|battles|"
                          r"rules?|limits?)\b|\bgame rules?\b|\bagainst the rules\b|\ballowed\b|\bupload limits?\b",
                          re.I)
CHECK_ASK = re.compile(r"\?|\b(?:double[- ]?check|check (?:if|whether|that)|pretty sure|is it true|confirm)\b", re.I)
DOC_PAGES = sorted(path.name for path in (Path(__file__).resolve().parent.parent / "docs/unsw").glob("**/*.md")
                   if path.name != "README.md")
CITED = re.compile(r"\bdocs/unsw\b|\bdiscord/|\bmessages\.jsonl\b|\bdigests?(?:-[\w-]+)?\.md\b|"
                   r"\bno hits? in (?:the )?docs(?:/unsw)?\b|"
                   + "|".join(rf"(?<![\w/.-]){re.escape(name)}\b" for name in DOC_PAGES), re.I)
INTERIM_CHARS = 200  # a shorter reply is a "Checking the docs ..." note; the answer follows in the turn
QUESTION_MINUTES = 30  # how long a question stays open for a reply to answer it


# The --check-sources lines (the module docstring). Only replies from `flag_from` on are flagged: the stop hook reads
# from QUESTION_MINUTES before its turn, to find a question asked earlier, but checks only this turn's replies.
def check_sources(path: Path, since: datetime, flag_from: datetime | None = None) -> Iterator[str]:
    question, asked_at = None, since
    for at, who, text in entries(path, since):
        text = flatten(text)
        if who == "sig":
            if asked := [part for part in sentences(own_words(text))
                         if SOURCE_TERMS.search(part) and CHECK_ASK.search(part)]:
                question, asked_at = " ".join(asked), at
            continue
        if question is None or (at - asked_at).total_seconds() > QUESTION_MINUTES * 60:
            question = None
            continue
        if len(text) < INTERIM_CHARS or not words(text) & words(question):
            continue
        if not CITED.search(text) and (flag_from is None or at >= flag_from):
            yield (f"{at:%Y-%m-%d %H:%M:%S} claude: answers sig's question about the game or site citing no "
                   f"docs/unsw/ page or saved Discord message | asked: {question[:120]} | reply: {text[:150]}")
        question = None


# check_briefs (the module docstring): what makes a brief a study of opponents' games, and the parts its Sources line
# needs, each with the words that show it.
OPPONENT_GAMES = re.compile(r"\b(?:study|scout|ladder)-replays\b|\b(?:opponents?[’']?|leaders?[’']?|rivals?[’']?|"
                            r"other teams?[’']?|top(?:-\d+| \d+)? teams?[’']?|team (?!157\b)\d+(?:[’']s)?|their (?:\w+ )?"
                            r"(?:ranked|unranked|recent|older|real))(?: \w+){0,3} (?:replays?|games)\b", re.I)
CODE_WORK = re.compile(r"\borch/|\bbranch\b", re.I)  # a builder's or reviewer's brief (a study's names worktrees too)
SOURCES_PARTS = (("ranked games only", re.compile(r"\branked\b", re.I)),
                 ("by submission id", re.compile(r"\bsubmission", re.I)),
                 ("loop/decoys.py's flags applied", re.compile(r"decoys\.py")),
                 ("kept and dropped counts", re.compile(r"\bkept\b.*\bdropped\b|\bdropped\b.*\bkept\b", re.I)))


def check_briefs(path: Path, since: datetime) -> Iterator[str]:
    for at, entry in records(path, since):
        for name, _, given in tool_calls(entry):
            brief = str(given.get("prompt") or "")
            if name not in AGENT_TOOLS or not (found := OPPONENT_GAMES.search(brief)) or CODE_WORK.search(brief):
                continue
            line = next((line for line in brief.splitlines() if re.match(r"\W*Sources:", line)), "")
            if missing := [part for part, shown in SOURCES_PARTS if not shown.search(line)]:
                yield (f'{at:%Y-%m-%d %H:%M:%S} claude: the brief "{given.get("description", "")}" reads opponents\' '
                       f'games ("{found[0]}") with {"a Sources: line missing " if line else "no Sources: line: "}'
                       f"{', '.join(missing)}")


# check_detached (the Stop hook's, no flag): each Bash call that starts a supervised lane (`loop/driver.py --once`) or
# the alerts watcher (tools/orch/watch.sh) detached, which both refuse (driver.detached_start, watch.sh's refuse):
# with a lone `&`, `nohup` or `disown`, or stdout redirected (`>`, `>>`, `&>`; `2>&1` alone keeps it), in the same
# command (split at top-level `;`, `&&`, `||` and line breaks, so a subshell's own redirection counts). Quoted text and
# heredoc bodies are left out, so `pgrep -f "loop/driver.py --once"` is no start. REFLECT 2026-10-01 15:21 F2: three
# refused starts in 2 h (09:46 `bash tools/orch/watch.sh >/dev/null 2>&1 & disown`, 11:01 `uv run loop/driver.py
# --once ... > /dev/null 2>&1 < /dev/null`), each restarted by hand and no step changed (MISSES 66).
LANE_START = re.compile(r"(?:^|[;(|&{])\s*(?:(?:nohup|exec|time)\s+)*(?:(?:ba)?sh\s+|uv\s+run\s+|\S*python3?\s+)?"
                        r"(?:\S*loop/driver\.py\b[^;&|)]*\s--once\b|\S*\bwatch\.sh\b)")
DETACHED = re.compile(r"\bnohup\b|\bdisown\b|(?<![&>|])&(?![&>])|&>|(?<![\d&<>])>|\b1>")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1.*?\n.*?^\s*\2\s*$", re.S | re.M)
QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


# The top-level commands of a shell line: split at `;`, `&&`, `||` and line breaks outside parentheses.
def commands(command: str) -> list[str]:
    parts, depth, start, i = [], 0, 0, 0
    while i < len(command):
        char, pair = command[i], command[i:i + 2]
        depth += (char == "(") - (char == ")")
        if depth <= 0 and (pair in ("&&", "||") or char in ";\n"):
            parts.append(command[start:i])
            i += 2 if pair in ("&&", "||") else 1
            start = i
            continue
        i += 1
    return [*parts, command[start:]]


# What `command` starts detached that refuses to run detached, or "".
def detached_start(command: str) -> str:
    plain = QUOTED.sub("''", HEREDOC.sub("", command))
    for part in commands(plain):
        if (start := LANE_START.search(part)) and (how := DETACHED.search(part)):
            return f"{start[0].strip(' ;(|&{')} with `{how[0]}`"
    return ""


def check_detached(path: Path, since: datetime) -> Iterator[str]:
    for at, entry in records(path, since):
        for name, _, given in tool_calls(entry):
            if name == "Bash" and (what := detached_start(str(given.get("command") or ""))):
                yield f"{at:%Y-%m-%d %H:%M:%S} claude: started {what}, which refuses to run detached"


# Every printed line with what looks like a secret replaced by "[redacted]", since the REFLECT list copies this output
# into messages.txt, its report and reflect/*.md (1 Oct: a password sig pasted in chat reached all three): the word
# after "password is", "password:", "passwd" and the like (PASSWORD), and a token that looks like an API key (KEYLIKE:
# 32+ of letters, digits, _ - + =, switching between letters and digits 4+ times as random text does, where a path
# segment such as "-Users-zitianqin-Dev-unsw-battlecode-2026" doesn't), except a git SHA (40 hex) and a UUID.
PASSWORD = re.compile(r"(?i)(\bpass(?:word|phrase)s?\s*(?:is|was|=|:)\s*|\bpasswd\b\s*(?:is\s+|[=:]\s*)?)"
                      r"(\"[^\"]*\"|'[^']*'|`[^`]*`|[^\s\"'`]+)")
KEYLIKE = re.compile(r"(?<![\w+=-])[\w+=-]{32,}")
NOT_KEY = re.compile(r"[0-9a-f]{40}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def keylike(token: str) -> bool:
    return not NOT_KEY.fullmatch(token) and len(re.findall(r"[A-Za-z](?=\d)|\d(?=[A-Za-z])", token)) >= 4


def redact(text: str) -> str:
    text = PASSWORD.sub(lambda m: m.group(1) + "[redacted]", text)
    return KEYLIKE.sub(lambda m: "[redacted]" if keylike(m.group()) else m.group(), text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, help="ISO time; without a zone, local time")
    parser.add_argument("--session", type=Path, help="a transcript .jsonl (default: the latest orchestrator one)")
    parser.add_argument("--chars", type=int, default=600, help="longest message kept whole (default 600)")
    check = parser.add_mutually_exclusive_group()
    check.add_argument("--check-times", action="store_true",
                       help="print only the orchestrator's times that look typed from memory (MISSES 26), and its ETA "
                            "and capacity claims that name no source run that turn")
    check.add_argument("--check-evalline", action="store_true",
                       help="print only the orchestrator's status replies that don't open with evalline's line")
    check.add_argument("--check-asks", action="store_true",
                       help="print only the orchestrator's asks, waits and recommendations sig's grants cover, and "
                            "agreements naming no change")
    check.add_argument("--check-answers", action="store_true",
                       help="print only the orchestrator's replies that leave one of sig's questions (each checked on "
                            "its own) without a line opening with its answer, or an error trace he pasted unquoted")
    check.add_argument("--check-sources", action="store_true",
                       help="print only the orchestrator's answers about the game or site that cite no docs/unsw/ "
                            "or Discord source")
    check.add_argument("--check-length", action="store_true",
                       help="print only the orchestrator's status replies and replies to notices over LENGTH_LINES "
                            "lines while sig's terse rule stands")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since).astimezone()
    path = args.session or latest_session(PROJECT)
    print(f"# {path}, since {since:%Y-%m-%d %H:%M %Z}", file=sys.stderr)
    lines = (check_times(path, since) if args.check_times else check_evalline(path, since) if args.check_evalline
             else check_asks(path, since) if args.check_asks else check_answers(path, since) if args.check_answers
             else check_sources(path, since) if args.check_sources else check_length(path, since)
             if args.check_length else messages(path, since, args.chars))
    for line in lines:
        print(redact(line))


if __name__ == "__main__":
    main()

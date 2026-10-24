<!-- Mirrored from https://game.battlecode.au/docs/game-format on 2026-09-29. Do not edit by hand; re-run scripts/mirror_docs.py instead. -->

# Game format

By facing off with competitor teams through ranked and unranked games, you will get an ELO score that determines
your spot on the leaderboard. Your ELO score increases when you win games, and decreases when you lose games. [ELO System](elo.md) sets out the arithmetic and the tiers.

## Ranked and unranked

A **ranked** battle consists of five games and will cause team ELOs to update. Maps are chosen at
random by the competition server. Ranked battles come from two places: the ones you request, and the autoscrim draws
below, which are ranked as well. In an **unranked** battle, teams may choose to verse another team on particular
maps; one game is played for each map. Unranked battles do not update ELOs nor rankings. Your team keeps the same colour
across every game of a battle, and after each battle you will be able to see the replays.

## Requesting a battle ('scrim')

You can request a battle against a specific team on the [leaderboard](https://game.battlecode.au/leaderboard) or from a team's page
in [find a team](https://game.battlecode.au/teams). The battle outcome appears in [Battles](https://game.battlecode.au/battles), which the [My team](https://game.battlecode.au/battles?mine=1) box narrows to your own. Note that all battles are publicly viewable, so be wise
about concealing your strategy!

Both teams need an active submission. Unranked battles are then open to every team; ranked ones need the switch on
under Settings on both [Team](https://game.battlecode.au/team) pages, and must target a team rated no more than
50 below you, although anyone above you is fair game. That switch waits eight hours between flips, so
nobody can duck a rating by turning it off between requests. Your team may start 60 games an hour by hand, which is 12
ranked scrims. Battles against organising teams (marked DEV) have a separate allowance of another 60 games an hour.

## Battles you are drawn into ('autoscrim')

**Autoscrim battles are ranked.** They move your rating exactly as a ranked battle you asked for does, and
most of the games your team plays will be these, so your place on the ladder is mostly decided by them.

You do not have to ask for every battle. Every hour, on the UTC boundary, the ladder is sorted and each team is
drawn one opponent from the eight places above and the eight below it, so you meet teams near your own rating. The
draw favours opponents you have not played for a while, and over a day you see most of the ladder around you.

You are in the draw whenever you have an active submission; the ranked switch in your settings governs only the
battles teams request. Each draw is a ranked five-game series on five maps chosen at random, at most one per team
per hourly batch, and it does not spend your hourly allowance. You may receive none if no suitable opponent remains.

## Submitting a bot

Using either the CLI or the website, you may upload and submit code. Once submissions are made, they cannot be
deleted. Only one submission can be 'active' at a time. Your 'active' submission is the one that the game server
will use for any incoming / outgoing battles.

Your rating belongs to the team and carries over when you switch submissions, but a new submission moves it faster.
For its first 10 ranked battles, wins and losses count for more, so a better bot climbs quickly; after
that it settles and single results only nudge it. Only one new bot per team in 12 hours gets this, and uploading the same
code again doesn't. See [New bots](elo.md#new-bots).

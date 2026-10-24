<!-- Mirrored from https://game.battlecode.au/docs on 2026-09-23 by scripts/mirror_docs.py. -->

# UNSW Battlecode 2026 documentation (mirror)

Every page of https://game.battlecode.au/docs, converted to markdown by `scripts/mirror_docs.py`. Code samples include every language tab (Python, C, C++, and Raw protocol where the site has one). Animated board demos can't be captured as text; each is replaced by its caption.

## Intro

- [Overview](overview.md)
- [Quickstart](quickstart.md)
- [Submitting via Website](submitting.md)

## Game Rules

- [Structure](structure.md)
- [Game Map](map-info.md)
- [Pearls](pearls.md)
- [Kelp and Portals](kelp-and-portals.md)
- [Vision](vision.md)
- [Movement](movement.md)
- [Splitting](splitting.md)
- [Sonar](sonar.md)
- [Death](death.md)

## Competing

- [Game Format](game-format.md)
- [ELO System](elo.md)

## Advanced

- [CLI](cli.md)
- [Execution Order](execution-order.md)
- [Timeouts](timeouts.md)
- [Standard Library](libraries.md)
- [Helper Reference](helper.md)
- [IO Protocol](protocol.md)
- [Protocol Upgrade](protocol-upgrade.md)
- [Map Files](map-files.md)
- [API](api.md)

## Extras

- Helper library sources (what `unswbc init` ships): [Python](helper-source-python.md) · [C header](helper-source-c.md) · [C++](helper-source-cpp.md)

## Competition rules and schedule (site pages, not under /docs)

- [About](site/about.md): eligibility, prizes, timeline, FAQ (including the Grand Final bracket), from https://battlecode.au/
- [Terms & Conditions](site/terms.md): the rules (eligibility, accounts, teams, IP, fair play, prizes), from https://battlecode.au/terms
- [Tournaments](site/tournaments.md): Sprint / Qualifiers / Grand Final, from https://game.battlecode.au/tournaments

## Mirror notes

- **Animated board demos** are canvas animations on the site. Each is replaced here by its caption, marked
  *[Animated board demo on the live page]*.
- **Hidden text** (checked 2026-09-23). The site's search index includes two passages that sit inside HTML
  comments in the Movement page source and don't show on the page. Both are outdated, so they aren't
  mirrored as page text:
  - Sprinting: "Any move with more steps than the dragon can pay for is not executed at all, and is treated
    as an invalid action." The live rule (Death by Sprint) says the payable steps are taken and then the
    dragon dies.
  - Death by Sprint: "This can be safely avoided by using the helper interface's `ct.make_moves()` function,
    which safely returns without sending the move to the engine…" The current `helper.py` `make_moves`
    sends whatever it is given (see [helper-source-python.md](helper-source-python.md)).
- **Completeness.** The script warns if a page in the site's search index wasn't written, or if a tabbed
  code block had no matching JS snippet. This is a live-site snapshot, not a full Discord changelog.

# Open findings about the tests

M1-M4, M6, M7, M10 fixed: SPRT/tuner rotate champion → swarm → rammer, H1/promising results are screened (with
`folder`, `grower`), scouting works, `compare.longest_margin` reports endgame margins.

## M5. Mix maps dilute ladder-style gains (E0050 near 0.548 vs mix 0.495; E0053 0.58 vs 0.51)
- Still open: eval style 1/2 near for economy/exploration/endgame ideas, declared before games.

## M8. `grower` (few, longer dragons) is screened only: sig 2026-09-26, not in the rotation (both bots beat it ~97%).

## M9. Nothing local out-eats us early on small walled maps, and that is half our online losses (review 2026-09-25)
- Evidence: 5143 lost 21 of 45 online, 10 by elimination (Default Small 4, Devil 2, QoS, Trophy, Schooltime); r0-99
  pearls 13-96 vs 47-246. Every gauntlet entry shares our early game, so neither rotation nor screen sees it.
- Still open: report alive at r100 and pearls eaten r0-99 per screen opponent; for economy ideas, an eval mix weighted
  to small walled near-maps, declared before games.

## M11, M12, M14 fixed (cdeb2b4): study dated and kept on failure; `hunter` screened; evidence.md vs the screen.

## M15/M9 report half: done in online compare and evidence.md; the screen's second-longest and r100 need run.py
- Change: parse_game adds second, alive100, pearls100 to BotStats (after predict-games merges), round500 reports them.

## M13. Skip opponent games whose moves can't differ: measured ~10% of SPRT CPU (2026-09-26); being built, audited 1 in 20.

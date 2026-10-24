**One finding, one general rule** (loop/MISSES.md 4, 41 and 48; every prompt that writes ideas includes this same
text from `loop/GENERALISE.md`). It applies to each idea, follow-up or map variation you propose.

- **No hand-picked values** (sig, 2026-09-28, `meta.GENERALISE`): a threshold, weight or round you propose is an
  observed input or a fitted model with every relevant observed input, judged on held-out maps (each official map left
  out with its near_* copies, worldgen maps, maps new to the pool). A constant you can't avoid states what it depends on.
- **Name the general quantity first:** what your evidence measures, stated so it means something on any map or in any
  game, with its n and chance. For example "fast pearls eaten by r100, ours against the opponent's" or "rounds until
  our first dragon leaves its home region", not "Devil's centre" or "Schooltime's garden".
- **Then, when the remedy is one mechanism, propose it as one conditional rule for the whole class of maps or
  situations:** what the bot does, gated on what it can observe (pearl or spawn tiles seen, the map's size and mirror,
  known portals and doors, teammates' reports, the round, the opponent's numbers), so it acts wherever that quantity
  is behind and plays as today where it isn't.
- **Never key on which map this is** (loop/MISSES.md 102; sig, 2026-09-30): no map files in the bot, recognising a
  stored map or switches per map, since every tournament map is unseen and ladder rating doesn't matter;
  `meta.valid_proposals` refuses such an idea.
- **Every hazard has an upside** (loop/MISSES.md 71): before choosing a remedy, list them. Each idea's hypothesis
  carries an `Options:` line of three or more numbered candidate remedies for its finding from different framings,
  `(1) ... (2) ... (3) ...`: avoid it; turn the same situation into a gain for us or against them; combine existing
  behaviours; the opponent's mirror; when the hazard is missing information, getting it with another dragon (a
  scout) or sonar (REFLECT 2026-09-29 F15: sig's portal protocol, which no portal idea's options had). Each gives its rough reach from the measured numbers, and a waiting idea may
  stand as one ("I524 already covers the farm version"); then say which one this idea is and why. `meta.valid_proposals`
  refuses a new idea without it (not a `same change as I###` row naming a waiting idea: new evidence for it). The
  same holds for avoidance already in the champion (REFLECT 2026-09-28 20:52 F3:
  the unknown-portal penalty cost Trauma): an idea touching a penalty or refusal in bot/ weighs what it gives up
  too. A decision a model or rule chooses lists its action set, combinations of existing
  behaviours included (stay and breed with the child leaving; leave with a split), not only the options already
  coded: the every-legal-action rule below, one level up.
- **The per-map or per-case examples are that rule's evidence and its test scenarios** (and boundaries), written into
  its hypothesis, not separate ideas. The 27 Sep 02:35 audit found that in the opening we reach the rich ground after
  the opponent, and queued it as four map-shaped remedies, each an SPRT fixing one layout: a centre drift for Devil
  (I191), a centre race on small maps (I294), leaving a sealed start room on Trauma (I335) and portal scouts for
  Schooltime (I355). They were two mechanisms, heading for the centre when nothing in view can spawn (I191, I294) and
  crossing out of a home region whose exits are portals (I335, I355), each one conditional rule with its maps as
  scenarios.
- **Where the general version needs several mechanisms** (different code or information, or one a prerequisite of
  another), each is its own idea, measured alone first, since one experiment tests one attributable behaviour. Each
  says which general quantity it moves and names its siblings: waiting ideas by id, ones in your own output by title.
  A rule that chooses between those mechanisms by what the bot observes (I375 picks I355's scouts or I191's drift by
  the map's shape) is acceptable only as a stack: its own idea with `"after"` on the parts, built on their candidates
  once each is tested, never a bundle coded before them. stats.md's "Split findings" section lists waiting ideas that
  look like one finding split into several and don't name each other; reviews merge only true duplicates and
  otherwise note on each member how it differs.
- **A new case of a finding already queued** is `same change as I###` with the case as new evidence; when the case
  falls outside that idea's condition, say how the condition should widen to cover it.
- **A lever already tested** (backlog.md's "Tested ideas" when you are given it, else results.jsonl; sig 2026-09-28: "dont test the exact same thing twice")
  is named: its experiments, their results and conditions, and what differs now (a correction, a new condition, a
  changed champion). The exact change already tested is proposed only as a retest saying why its verdict may no
  longer hold. I689's middle-line opening race named none of E0114, E0159, E0175, E0205 or E0206.
- **State its trigger or gate as a rule over what the dragon observes, not a bare threshold** (loop/MISSES.md 56):
  a formula, ratio, fitted curve or probability over map size, distances, rounds left, counts, lengths or the
  opponent's behaviour, each constant with a meaning (`fold when rounds left < path to the king + its length`, not
  `from round 350`, `from about r250` or `from the middle game`, a phase word being a hand round). A bare threshold needs its stated reason why nothing observable does better, written as "bare
  threshold because ..." (`meta.bare_thresholds` refuses an idea whose rule has one without it). A new param named
  in place of the number is the same bare threshold (MISSES 96: I538's "dragons shorter than P.<searchMinLength>
  don't search" carried 8), and so is a param's value swapped for another (`meta.VALUE_SWAP`: I968's "mergeMinUnits
  8->4"): give it its formula (`P.<name> = <what the bot observes>`) or the reason; a decision that
  fires often may name a learned gate instead (I452, I461). Every formula's or learned gate's candidate inputs include
  rounds left with the dragon's length (sig, 2026-09-29 16:06: "when a snakes deciding what to do it should also know
  how many moves is left"): what it can still reach by round 500 changes what each choice is worth, so a gate that
  leaves them out says why. `loop/cutoffs.py` lists each one a candidate adds. An
  idea that only replaces an existing cutoff or tuned constant with such a rule, like for like, is `"kind":
  "formula"`: tested for no loss, since our generated maps are where the constant was fitted. A prerequisite whose own
  text says no move reads it yet (plumbing for later ideas) is `"kind": "noloss"` when its games can differ (timing,
  sonar, CPU) and `"kind": "identical"` when they can't: a +15 gain SPRT stops at H0 on a change built to have no
  effect (E0299, I492, 29 Sep: H0 at 378 pairs, est -3.7).
- **Name the choices its win depends on**, or the coder takes the narrowest reading and the test judges a weaker
  idea: its scope (which dragons, which situations), its trigger, what counts as a success or a loss, and what it
  leaves out on purpose, with the measured share of the targeted outcomes that leaves out. The scope names which units
  the rule moves and which pay its cost, compared with the units its success needs, with the expected share: an
  eligibility that is a self-belief (in E0282 about 5 dragons a round believed they were king) or a budget that
  shrinks with the unit's own size lands on the wrong units (MISSES 85). A target, order or tie-break comes from what
  the dragons observe, never "any fixed rule works" (on a wrapping board the centre is no nearer than any tile), and a
  cost the idea names gets a replay estimate, not "the SPRT prices that". A model of what an enemy,
  a teammate or our own dragon can do next covers every legal action unless the idea says otherwise; covering means
  the tiles and outcomes they reach (a reach fill, not every path), bounded to fit the CPU gate (`referee/cpu.py`),
  with any cap stated with the share of outcomes it leaves out. The legal actions: a step in each direction (the
  board wraps); a sprint of 2+ steps, one segment paid per extra step, so a dragon of length L can take up to L-1
  steps and each pearl eaten on the way pays for one more (an enemy whose tail is out of sight may be longer than it
  looks); portal crossings, mid-sprint too; a split, which leaves the parent's head in place and puts a child's head
  on its old tail, moving later that round; dying, which frees its tiles and drops pearls; and the move order,
  ascending id: every other dragon moves once before our next turn (plus any child split off in between), higher ids later this round and lower ids first
  next round, so when we move a lower id's tail has already advanced and a higher id's hasn't (docs/unsw/movement.md,
  execution-order.md, splitting.md). E0109's search let enemy heads only step, while 526 of 587 rams on our long
  dragons were 2-3-step sprints.

**A measure-first stop says how it rules out luck** (loop/MISSES.md 46). A stop line ends an idea without the SPRT,
so it is as strict about luck as the loop's own triggers:

- **A stop that compares counts** (candidate against champion, us against a leader, before against after) names its
  test and fires only when the count is on the stop side at p <= 0.1 (`chance.TRIGGER_P`): "stop only if the king
  ends shorter at sign p <= 0.1 over matched map-sides (`chance.sign_p`)", never "if the king ends shorter in most
  games" (`chance.rates_p` takes the stop side's rate first; `chance.paired_z` takes diffs as candidate minus
  champion). Failing to show a gain is not a stop; finding it is the SPRT's job. I316's "shorter in most games"
  ended it at 21 of 36 (p 0.20) while the same games had our alive count at r499 up 48.6 to 53.5 (p 0.003) (E0166).
- **A rate or per-game count against a line** ("under 1 a game", "under 30% of rounds") gives its n in independent
  units (games or maps: turns and rounds within one game move together) and fires only when the 95% interval lies
  wholly past the line (`chance.rate_ci` for a share of games; the mean plus or minus 1.96 standard errors over games
  for a per-game count, and with fewer than about 10 events in all, for a line under 1 a game (events at most
  games), `chance.rate_ci(events, games)` as well, since standard errors put none in 5 games at 0 to 0; none in n
  games bounds it at about 4/(n+4) a game). Size the measurement to reach its line: none in 24 games is an interval of 0
  to 14%, so "fewer than 1 game in 10" needs 35 games with none, or 53 with one. The coder plays up to about 100
  games unless the idea sizes it larger; a line still straddled then hasn't fired.
- **A quantity that depends on how an opponent plays** (how often they fold, grow a king, harvest drops, ram) is read
  on real opponents' online games chosen without regard to the result, never only on local games: every local
  rotation and screen opponent is a variant of our own bot and may not play that style (method note M44,
  2026-09-27). Such a sample is an index's saved games not kept by result (`online.sample_games`), or its API
  results counting every game (`online.all_games`) for a rate. A coder's `.online/` has none: its losses were
  picked because we lost (`"kept"`), the other losses were dropped and the unmarked rest are only wins and draws, so
  any rate read there is biased (MISSES 55). An idea naming online games (online, ladder, leaders, unselected:
  `driver.names_online`) gets the live version's whole result-blind sample in `.online-sample/` and every leader's
  current-version ranked games in `.leaders/` (M79: E0276 and E0285 stopped because the sample was readable only
  on the trusted side); otherwise a coder asks its parent for the orchestrator's measurement (with no parent, its
  stop reason names the measurement owed), and may watch `.online/` to see how a style looks. E0129, E0130 and E0182 stopped because no local
  opponent grew a king or 10+ dragons, and the lone-king report found starter keepers
  beating us in 30 of 63 round-limit losses on real Trauma, Autarky and Dilemma copies against 16 of 172 for the
  rotation (p 7e-10); the 2026-09-28 queue audit found 33 stops read only locally or on generated maps (I363, I405,
  I440 on opponent style). Say which games the stop was read on; if the local and online values differ, the online one
  decides (E0181's drop share was checked after: 0.64 locally and 0.640 online under one definition).
- **A change to how we contest pearls** (yielding one, racing an enemy head to one both see, targeting pearls
  beside enemy heads) declares the `crowder` opponent mix (its evaluation's `opponents`, a share of
  0.25-0.5), or `sprinter` where heads close by sprint: every rotation opponent is our own code and yields pearls as we do, so the race it
  changes barely plays there (M78: level races, we eat 46.7% against our own code, 41.0% online, ranks 1-2 50-54%;
  rule-counts.md's pearl race gives the local, online and leaders' tables).
- **A stop tests the rows the change acts on** (loop/MISSES.md 61): the decisions where the new rule would decide
  differently and what followed them, or how often it fires, counted as the decisions it alters (a shadow copy's
  changed moves, with `chance.rate_ci`), not as how often its situation occurs; never an average over rows it leaves
  alone, which says nothing about the ones it changes. I097's "stop if trips net more per round than
  the dragons' outside intake at sign p <= 0.1, in which case the gate would refuse almost nothing" fired on every
  trip (210 better, 155 worse, p 0.002, carried by 376 early short dragons), while the fold-window kings and folders
  it targets went 17 better and 27 worse (p 0.09) and 6 and 13 (p 0.08), and the formula changed 3.0 moves a game
  (1.3-4.7) (E0189). On its rows: "stop if the trips the formula refuses net at least the dragon's outside intake at
  sign p <= 0.1". A claim about how often it acts ("refuses almost nothing") is a firing-rate line, measured as one.
- **A stop fires on evidence against the idea, never on evidence missing**: "stop unless the lower bound is above X",
  "build only if X" and "commit unless" end an idea because the measurement was too small to show X (I417-I421,
  I426). Write "stop only if the upper bound is below X" (or the other side at p <= 0.1), and otherwise build.
- **A formula or fitted curve replacing a tuned constant is measured beyond our generator's layouts**
  (loop/MISSES.md 61): the constant was fitted on generated maps, so the two can read alike there and apart on unseen
  maps (sig, 2026-09-27: "it could eb close for local tests but ocmpeltely different for unseen maps that may even be
  in different style to our generator ones"), as in the formula go lines I458, I464-I473 and I497. Its stop reads its
  statistic on both, reported apart: our generator's maps, and off-generator maps, meaning the official, community
  and adversarial maps as they are (`maps/ladder`, `maps/sprint`, `maps/retired`, `maps/community`,
  `maps/adversarial`, `maps/extra`), fresh worldgen maps (`mapgen/gen.py --style worldgen`: another competitor's
  generator, none of our generator's choices; `meta.WORLDGEN_SHARE` of every SPRT is fresh ones of the same, read in
  its per-style worldgen line) or real opponents' games chosen blind to the result (`online.sample_games`,
  above: `.online-sample/` when the idea names online games, else ask the parent; a native lane without it plays
  the off-generator maps). The stop fires only when it holds on both. A rate stop
  (PROMPT.md's "under about 0.1 changed moves a game ... stop") too: count changed moves on our generator's maps
  (mapgen's near_* and fortress styles may add weight there) and, apart, on worldgen maps and renamed copies of the
  map files above, each copy differing only in its MAP_NAME line so it gets its own pearl seed, and stop only when
  the rate is low on both: a formula that agrees
  with its constant on generated maps may not elsewhere. The stop lines of I458, I464, I470 and I497 pooled all
  maps into one count; orchestrator notes correct them to count the two apart.
- **A go line** ("build it only if ...") is judged with its chance like a stop (the count and rate rules above), and
  one that doesn't fire still ends in building the idea: only a stop that fires ends it before the SPRT.

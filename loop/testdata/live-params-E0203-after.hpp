    // --- King beacon and final fold (fold.hpp) ------------------------------------------------------
    // The merge cascade only folds dragons that meet; long dragons far apart end the game separate, and only
    // the longest counts. A dragon at least as long as every teammate it knows of reports its head and length
    // over sonar (relayed like the belief map). From the fold's start (fold.hpp's foldStart) dragons within foldPathCap
    // walk to it and merge.
    bool kingBeacon = true;      // the king sends (and everyone relays) a king report
    int beaconRound = 405;       // ... from this round (earlier while fold.hpp's kingSeedOn)
    int kingMinLength = 4;       // ... when at least this long
    int kingReportAge = 12;      // a king report older than this many rounds is ignored
    int gossipKingValue = 1000;  // a fresh king report's gossip value: ahead of every belief report
    bool kingSuccession = true;  // a king that splits out passes the crown to its child: the child ignores its
                                 // parent's reports and reports itself as heir, and teammates drop the parent's
    bool finalFold = true;       // dragons walk to the reported king and merge into it
    int foldRound = 397;         // ... from this round, but not before beaconRound (fold.hpp's foldStart)
    int foldMinLength = 1;       // ... when at least this long (1: every dragon)
    // Fold near, breed far: on a map of at least localFoldArea tiles only a dragon within foldReach path steps of
    // the king's head folds; distant non-kings keep eating, farming and splitting when a feeder frees a slot,
    // until mergeRound starts the cascade. Smaller maps fold whoever has time.
    bool localFold = true;
    int foldReach = 12;
    int localFoldArea = 2400;    // ... W * H tiles
    bool kingHoldsSplits = true; // ... and from the fold's start the king stops ordinary splits (folders feed it)
    bool kingFoldSkipsFarms = true; // ... and farm entries (each ends in a split-out), on every map (fold.hpp's kingSkipsFarms)
    // Seeding the king (fold.hpp's kingSeedOn): on a map of at least kingSeedArea tiles the king reports itself before
    // beaconRound and makes no ordinary splits or farm trips while a split child is worth less to it than the
    // childLength it takes (a king-line child returned about 1 length by r499 in every 100-round band, 71 games):
    bool kingSeedOnValue = true;
    int kingSeedArea = 1152;     // ... W * H tiles; smaller maps end in eliminations, where every dragon counts
    int kingHoldLead = 2;        // ... with the team this many slots or fewer below where ordinary splits stop (another
                                 //     dragon's split refilled a freed slot there in a median 1 round) ...
    int kingSeedRound = 300;     // ... and from this round, whatever the team's size
    int foldMergeRadius = 4;     // a folding dragon merges when the king reaches our head within this many steps
    bool foldMergesOnlyKing = true;   // a folding dragon merges early and wide only into the king, not into any
                                      // longer teammate on the way (each extra merge drops half the mass again)
    bool foldIgnoresMinUnits = false; // a folding dragon merges even when the team is down to P.mergeMinUnits
    bool foldIgnoresEnemy = false;    // a folding dragon merges with an enemy head in view


    // --- King beacon and final fold (fold.hpp) ------------------------------------------------------
    // The merge cascade only folds dragons that meet; long dragons far apart end the game separate, and only
    // the longest counts. A dragon at least as long as every teammate it knows of reports its head and length
    // over sonar (relayed like the belief map). From foldRound dragons within foldPathCap walk to it and merge.
    bool kingBeacon = true;      // the king sends (and everyone relays) a king report
    int beaconRound = 405;       // ... from this round
    int kingMinLength = 4;       // ... when at least this long
    int kingReportAge = 12;      // a king report older than this many rounds is ignored
    int gossipKingValue = 1000;  // a fresh king report's gossip value: ahead of every belief report
    bool kingSuccession = true;  // a king that splits out passes the crown to its child: the child ignores its
                                 // parent's reports and reports itself as heir, and teammates drop the parent's
    bool finalFold = true;       // dragons walk to the reported king and merge into it
    int foldRound = 397;         // ... from this round
    int foldMinLength = 1;       // ... when at least this long (1: every dragon)
    // Fold near, breed far: on a map of at least localFoldArea tiles only a dragon within foldReach path steps of
    // the king's head folds; distant non-kings keep eating, farming and splitting when a feeder frees a slot,
    // until mergeRound starts the cascade. Smaller maps fold whoever has time.
    bool localFold = true;
    int foldReach = 12;
    int localFoldArea = 2400;    // ... W * H tiles
    bool kingHoldsSplits = true; // ... and from foldRound the king stops ordinary splits (folders feed it)
    int foldMergeRadius = 4;     // a folding dragon merges when the king reaches our head within this many steps
    bool foldMergesOnlyKing = true;   // a folding dragon merges early and wide only into the king, not into any
                                      // longer teammate on the way (each extra merge drops half the mass again)
    bool foldIgnoresMinUnits = false; // a folding dragon merges even when the team is down to P.mergeMinUnits
    bool foldIgnoresEnemy = false;    // a folding dragon merges with an enemy head in view


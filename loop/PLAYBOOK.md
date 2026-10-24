# Playbook

Short, durable lessons for lane agents. The meta session adds lines when the same mistake shows up twice,
and prunes lines that stop being true. Keep each line to one sentence.

## Game facts that are easy to get wrong

- Turns are sequential by dragon id: lower ids have already moved this round, higher ids move after you and can
  react to your move.
- A head-on collision kills both dragons, whatever their lengths. Moving into any body kills only the mover.
- The tail tile is still blocked on the turn the tail would move, so stepping onto your own tail kills you.
- A sprint of k steps costs k−1 tail segments, each pearl eaten on the way pays one back, and a step you can't
  pay for kills the dragon.
- An illegal split, a crash, or a turn over 100M CPU points kills the dragon.
- A split child is a fresh process with no memory, and it takes its first turn later in the same round.
- Tournament maps are unseen: never rely on knowing the map.

## Working lessons

- (Filled in by the meta session as experiments accumulate.)

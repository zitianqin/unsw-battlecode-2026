"""Prints battery/<team>.json compactly, one line per version.

    python3 tools/keyed/show.py TEAM [TEAM ...]
"""
import sys, json
from pathlib import Path

import common as C
for t in sys.argv[1:]:
    b = json.load(open(C.DATA / 'battery' / f'{t}.json'))
    print('TEAM', t, {k: b[k] for k in b if k != 'versions'})
    for v in b['versions']:
        if not v['sending_turns']:
            print('  ', v['label'], 'g', v['games'], 'won', v['won'], 'SILENT')
            continue
        print('  ', v['label'], 'g', v['games'], 'won', v['won'], 'turns', v['sending_turns'], 'rays', v['rays_per_turn'],
              'dist', v['distinct_per_turn'], 'rate', v['send_rate_by_round'], 'dir', v['direction_vs_facing'],
              'ss', v['same_state_pairs'], v['same_state_same_value'], 'pop', v['diff_state_mean_popcount'],
              'vals', v['values'], '2rounds', v['values_in_two_rounds'], 'shared', v['shared_same_round_share'],
              'bias', v['max_bit_bias'], v['bias_noise_4sd'], v['biased_bits'][:12], 'top12', v['top12'], v['top12_share'],
              'gf', v['gf2_bits_linear_after_round_key'], v['gf2_bits_linear_direct'], 'mix', v['mixer_best'],
              'chain', v['chain_prev_seen_twice'], v['chain_single_successor'], 'react', v['reaction'])

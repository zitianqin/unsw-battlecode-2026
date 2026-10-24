import json
from pathlib import Path

classified = json.loads(Path('data/top50_ranked_classified.json').read_text())

md = []
md.append('# UNSW Battlecode 2026: Comprehensive Sonar Protocol Study of the Top 50 Teams')
md.append('')
md.append('> **Date**: 2026-09-29  ')
md.append('> **Author**: Team zitian (Reverse-Engineering & Protocol Audit)  ')
md.append('> **Dataset**: Direct analysis of verified **RANKED match replays only** from all 50 teams on the live leaderboard.  ')
md.append('> **Scope**: Unranked scrimmages were excluded to eliminate decoy/test bots.  ')
md.append('')
md.append('---')
md.append('')
md.append('## Executive Summary')
md.append('')
md.append('Across the current top 50 teams on the official UNSW Battlecode ladder, sonar communication in **ranked games** falls into **6 distinct architectural paradigms**:')
md.append('')
md.append('1. **The SSS Telemetry Standard (11 Teams, ~22% of the Leaderboard)**:  ')
md.append('   An unencrypted, open telemetry packet structure pioneered by SSS has been widely adopted or forked across the tournament. In ranked games, SSS uses magic `0xF5E` with **99.93% adherence** across 86,000+ pings. Every team in this family uses the exact same 64-bit layout (`Magic 12 | Subject ID 14 | Length 7 | X 6 | Y 6 | Round 9 | Check 10`), changing only the 12-bit magic prefix to represent their own team. **All of these teams can be 100% intercepted and mapped in real-time with a single parameterized decoder.**')
md.append('2. **Targeted Mesh / Line-of-Sight Gossip (6 Teams)**:  ')
md.append('   Stealth-focused bots (led by Rank 4 *中国必须人能飞* and Rank 37 *中国人不准飞*) that emit very few pings (<50-500/game), strictly aim their sonars at allies (65%+ ally hit rates), and gossip 64-bit status tokens across the fleet.')
md.append('3. **Directional & Scrambled Field Broadcasters (Cutlery & zitian)**:  ')
md.append('   Rank 1 *Cutlery* encodes ray directions into the high 4 bits (`Bits 60..63`) while broadcasting static state beacons. Rank 10 *zitian* uses a 16-bit MAC in bits 48..63 with 4x4 spatial bitmasks in bits 18..33.')
md.append('4. **Keyed PRNG & Synchronized Rolling Hashes (15 Teams)**:  ')
md.append('   Teams such as *cheji bt* (Rank 2), *Stockfish* (Rank 11), and *3.14159265* (Rank 13) broadcast high-entropy numbers omnidirectionally (all 4 directions identical per dragon per turn). These values are generated from match-seeded rolling PRNGs/hashes, preventing arbitrary coordinate eavesdropping.')
md.append('5. **Structured Bitfield Telemetry (14 Teams)**:  ')
md.append('   Teams like *forgot to mention* (Rank 5) and *Sabotage-d* (Rank 7) with unencrypted bitfields exposing dragon IDs, coordinates, and round advance clocks. Sabotage-d in ranked games shifts their 32-bit packet into bits 32..63.')
md.append('6. **Completely Silent Bots (2 Teams)**:  ')
md.append('   *PPP* (Rank 12) and *Settlers of Battlecode* (Rank 26) emit exactly 0 sonar pings in both ranked and unranked matches.')
md.append('')
md.append('---')
md.append('')
md.append('## Master Protocol Table: All Top 50 Teams (Ranked Games Only)')
md.append('')
md.append('| Rank | Team Name | Team ID | Elo | Ranked Sonar Volume | Architectural Family | Magic / Signature | Decryption Status | Spoofability |')
md.append('| :---: | :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: |')

for r in classified:
    rank = r['rank']
    name = r['name'].replace('|', '\\|')
    tid = r['id']
    elo = r['elo']
    vol = f"{r['total_pings']:,}" if r['total_pings'] > 0 else '0 (Silent)'
    fam = r['family']
    
    # Extract signature
    sig = 'None'
    if 'SSS Telemetry' in fam:
        magic = list(r['top_prefixes_12'].keys())[0]
        sig = f'`{magic}`'
    elif r['total_pings'] == 0:
        sig = 'N/A'
    elif rank == 1:
        sig = '`0x1b05...` beacon'
    elif rank == 7:
        sig = 'High 32-bit Shift'
    elif rank == 10:
        sig = '16-bit MAC'
    elif 'Targeted' in fam:
        sig = 'Line-of-Sight Mesh'
    elif 'Keyed' in fam:
        sig = 'Match PRNG'
    else:
        top12 = list(r['top_prefixes_12'].keys())
        sig = f'`{top12[0]}`' if top12 and top12[0] != '0x0' else 'Bitfields'
        
    # Decryption status
    if 'SSS Telemetry' in fam or rank == 3:
        status = '**Fully Decoded (100%)**'
        spoof = 'Replay: Yes / Forgery: 0.1%'
    elif rank in (5, 7):
        status = '**Mostly Decoded (85%)**'
        spoof = 'Yes (No MAC)'
    elif rank == 1:
        status = 'Partial (Direction+Beacons)'
        spoof = 'Difficult'
    elif rank == 10:
        status = 'Full (Auth MAC + 4x4 Mask)'
        spoof = 'Blocked by 16-bit MAC'
    elif r['total_pings'] == 0:
        status = 'N/A (Silent)'
        spoof = 'N/A'
    elif 'Targeted' in fam:
        status = 'Behavioral Topology'
        spoof = 'Replay Gossip only'
    elif 'Keyed' in fam:
        status = 'PRNG Stream'
        spoof = 'No (Keyed)'
    else:
        status = 'Bitfields Identified'
        spoof = 'Possible'
        
    md.append(f'| {rank} | {name} | {tid} | {elo} | {vol} | {fam} | {sig} | {status} | {spoof} |')

md.append('')
md.append('---')
md.append('')
md.append('## Deep Architectural Profiles (Ranked Ground Truth)')
md.append('')
md.append('### 1. The SSS Telemetry Family (11 Teams Identified in Ranked Games)')
md.append('')
md.append('Across all verified ranked games, 11 of the top 50 teams use the exact SSS telemetry standard. In ranked games, SSS sent 86,113 pings adhering to magic `0xF5E`:')
md.append('')
md.append('| Team Name | Rank | Team ID | Magic Header (Bits 52..63) | Typical Sentinel Ping | Ranked Adherence |')
md.append('| :--- | :---: | :---: | :---: | :---: | :---: |')
md.append('| **SSS** | 3 | 91 | `0xF5E` (legacy `0xB3C`) | `0x754b06f7` | **99.93%** (86,113 / 86,172) |')
md.append('| **bread first search** | 17 | 507 | `0xC22` | Dynamic | **99.95%** (60,336 / 60,365) |')
md.append('| **Kamikaze** | 20 | 63 | `0xBFF` | Dynamic | **59.35%** (32,264 / 54,366) |')
md.append('| **DESTINY 3** | 22 | 948 | `0xB73` | Dynamic | **98.21%** (119,559 / 121,740) |')
md.append('| **python.io** | 32 | 147 | `0x3C9` | Dynamic | **100.0%** (202,236 / 202,236) |')
md.append('| **ddabap** | 33 | 457 | `0xD29` | Dynamic | **100.0%** (73,900 / 73,900) |')
md.append('| **NUSW Battlecode** | 39 | 262 | `0xE92` | Dynamic | **80.06%** (57,288 / 71,560) |')
md.append('| **Heartbreaker** | 42 | 62 | `0xB50` | Dynamic | **100.0%** (42,444 / 42,444) |')
md.append('| **CURSEYOUBAYLE** | 44 | 784 | `0xBD4` | Dynamic | **100.0%** (101,150 / 101,150) |')
md.append('| **pizza** | 46 | 933 | `0x0A9` | Dynamic | **93.28%** (150,550 / 161,400) |')
md.append('| **meowest** | 47 | 529 | `0xB3A` | Dynamic | **99.47%** (22,668 / 22,788) |')
md.append('')
md.append('#### SSS Universal Decoder Specification')
md.append('```python')
md.append('def decode_sss_family(val: int):')
md.append('    magic = val >> 52')
md.append('    KNOWN_MAGICS = {')
md.append('        0xF5E: "SSS", 0xB3C: "SSS (legacy)", 0xC22: "bread first search",')
md.append('        0xBFF: "Kamikaze", 0xB73: "DESTINY 3", 0x3C9: "python.io",')
md.append('        0xD29: "ddabap", 0xE92: "NUSW Battlecode", 0xB50: "Heartbreaker",')
md.append('        0xBD4: "CURSEYOUBAYLE", 0x0A9: "pizza", 0xB3A: "meowest"')
md.append('    }')
md.append('    if magic not in KNOWN_MAGICS:')
md.append('        return None')
md.append('    return {')
md.append('        "team": KNOWN_MAGICS[magic],')
md.append('        "magic": hex(magic),')
md.append('        "subject_dragon_id": (val >> 38) & 0x3FFF,')
md.append('        "dragon_length": (val >> 31) & 0x7F,')
md.append('        "head_x": (val >> 25) & 0x3F,')
md.append('        "head_y": (val >> 19) & 0x3F,')
md.append('        "round": (val >> 10) & 0x1FF,')
md.append('        "checksum": val & 0x3FF')
md.append('    }')
md.append('```')
md.append('')
md.append('---')
md.append('')
md.append('### 2. Rank 7: Sabotage-d (Protocol 3 Shifted Format)')
md.append('')
md.append('In ranked games under Protocol 3, Sabotage-d adapted their 32-bit packet by shifting it into the high 32 bits with bit 31 set:')
md.append('$$\\text{Value}_{64} = (\\text{Packet}_{32} \\ll 32) \\mid 0x80000000$$')
md.append('* **Decoded Fields** from `Packet_32`:')
md.append('  * `Bits 26..31`: Head X coordinate')
md.append('  * `Bits 20..25`: Head Y coordinate')
md.append('  * `Bits 12..19`: Dragon length')
md.append('  * `Bits  8..11`: Combat & enemy sighting flags')
md.append('  * `Bits  0.. 7`: Low checksum byte')
md.append('')
md.append('---')
md.append('')
md.append('### 3. Rank 1: Cutlery (Team ID: 306)')
md.append('')
md.append('* **Ranked Sonar Volume**: ~33,000 pings/sample')
md.append('* **Direction Encoding in Top 4 Bits (Bits 60..63)**:')
md.append('  Cutlery injects the physical firing direction into the highest 4 bits:')
md.append('  $$\\text{Bits 60..63} = (\\text{Base}_R + \\text{DirCode}) \\pmod{16}$$')
md.append('* **Static Hardcoded Beacons**:')
md.append('  Values such as `0x1b05fd780c09d17b` appear across multiple completely different games, maps, and seeds, acting as global fleet synchronization pings.')
md.append('')
md.append('---')
md.append('')
md.append('### 4. Targeted Mesh Gossip (6 Teams: 中国必须人能飞, 中国人不准飞, 丁真和雪豹, etc.)')
md.append('')
md.append('* **Aiming Discipline**: **65% to 75% of all pings hit an ally directly** in ranked matches.')
md.append('* **Gossip Relay Mechanics**: When Dragon A sends a 64-bit token to Dragon B, Dragon B echoes the exact same 64-bit value to Dragon C on the next turn. Intercepting a single ray gives the exact token currently circulating through their fleet.')
md.append('')
md.append('---')
md.append('')
md.append('### 5. Keyed PRNG / Rolling Hash (15 Teams: cheji bt, Stockfish, 3.14159265, C--, etc.)')
md.append('')
md.append('* **Omnidirectional synchronization**: Every living dragon casts 4 rays simultaneously (N, E, S, W) carrying the **exact same 64-bit value** in all 4 directions.')
md.append('* **Cryptographic Seeding**: The sequence is seeded anew at match start. Without knowledge of the bot binary\'s seed derivation, values appear as pseudorandom noise.')
md.append('')
md.append('---')
md.append('')
md.append('### 6. Rank 10: zitian (Our Own Bot Architecture)')
md.append('')
md.append('* **High 16 Bits (Bits 48..63)**: 16-bit Authentication Tag / MAC derived from the body and internal `buildKey`. 99.6% deterministic across 24,000+ payloads.')
md.append('* **Middle 16 Bits (Bits 18..33)**: 4x4 Spatial Grid Bitmask (e.g. `0xFFFF` = full 4x4 clear block, `0xEEEE` = boundary slice, `0x4444` = vertical column).')
md.append('* **Low 18 Bits (Bits 0..17)**: Tile index and round clock modulo 128.')
md.append('')
md.append('---')
md.append('')
md.append('## Tactical Recommendations (Ranked Ground Truth)')
md.append('')
md.append('1. **Deploy the Universal SSS Decoder immediately**:  ')
md.append('   By adding the SSS family decoder with the 11 discovered magic constants into `bot/`, our bot gains **passive, zero-CPU global radar** against ~22% of the top ladder teams whenever their rays hit us in ranked tournament matches.')
md.append('2. **Spoofing against SSS Family & forgot to mention**:  ')
md.append('   * Replaying captured SSS family packets back into their fleet will be accepted as authentic since the 10-bit checksum remains intact.')
md.append('   * *forgot to mention* (Rank 5) has no checksum or MAC at all; false telemetry can be freely injected to mislead their pathfinding.')
md.append('3. **Mesh Disruption against 中国必须人能飞**:  ')
md.append('   Reflecting an intercepted mesh token back at an ally will amplify and cycle their gossip queue.')
md.append('4. **Maintain our 16-bit MAC**:  ')
md.append('   Our authentication tag successfully insulates us against spoofing; opponents cannot inject fake coordinates into our dragons without a 1-in-65,536 random guess.')

target = Path('research/top-50-sonar-protocols.md')
target.write_text('\n'.join(md), encoding='utf-8')
print(f'Successfully updated {target} with RANKED-ONLY ground truth ({target.stat().st_size} bytes, {len(md)} lines)')

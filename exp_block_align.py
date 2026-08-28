#!/usr/bin/env python3
"""场景内块对齐（三步第一步）：用「两边 normalize 唯一」的锚点投票，建立 Remake块↔EVO块 候选对应。

锚点 = 全部台词中，normalize 在 EVO 唯一 且 在 Remake 唯一的台词。
块候选：Remake 块的锚点 -> EVO 块（投票），判定 1:1 / 1:N / 0锚点。
"""
import json, sys
from collections import defaultdict, Counter
sys.path.insert(0, '/var/minis/workspace/TrailsInTheSkyRemakeScriptAligner')
from synonyms import normalize
W = '/var/minis/workspace'

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))

# ---------- norm -> 出现位置 ----------
# EVO: norm -> [(scene, func, block)]，Remake 同理
evo_pos = defaultdict(list)
remake_pos = defaultdict(list)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text'])
                if n:
                    evo_pos[n].append((sc, fn, lab))
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t)
                if n:
                    remake_pos[n].append((sc, fn, lab))

# ---------- 锚点：两边都唯一 ----------
anchor_norms = set()
for n in evo_pos:
    if len(evo_pos[n]) == 1 and len(remake_pos.get(n, [])) == 1:
        anchor_norms.add(n)
print(f'两边唯一锚点 norms: {len(anchor_norms)}')

# ---------- 块候选：Remake 块 -> EVO 块投票 ----------
rem_block_anchors = defaultdict(list)  # (rscene,rfunc,rblk) -> [(evo块)]
for n in anchor_norms:
    rpos = remake_pos[n][0]
    epos = evo_pos[n][0]
    rem_block_anchors[rpos].append(epos)

# 统计
n_1to1 = n_1toN = n_0anchor = 0
detail_1toN = []
for rpos, eposes in rem_block_anchors.items():
    uniq = set(eposes)
    if len(uniq) == 1:
        n_1to1 += 1
    else:
        n_1toN += 1
        if len(detail_1toN) < 10:
            detail_1toN.append((rpos, Counter(eposes).most_common(3)))

# Remake 总块数
n_rem_blocks = sum(len(f['blocks']) for sc in remake.values() for f in sc.values())
n_rem_blocks_with_talk = sum(1 for sc in remake.values() for f in sc.values() for blk in f['blocks'].values() if blk)

print(f'Remake 总块: {n_rem_blocks}, 有台词块: {n_rem_blocks_with_talk}')
print(f'有锚点的 Remake 块: {len(rem_block_anchors)}')
print(f'  1:1 对齐: {n_1to1} = {n_1to1/max(len(rem_block_anchors),1)*100:.1f}%')
print(f'  1:N 候选: {n_1toN} = {n_1toN/max(len(rem_block_anchors),1)*100:.1f}%')
print(f'  无锚点的有台词块: {n_rem_blocks_with_talk - len(rem_block_anchors)}')

print()
print('1:N 样本（前10）:')
for rpos, top in detail_1toN:
    print(f'  {rpos[0]}/{rpos[1]}/{rpos[2]}: {top}')

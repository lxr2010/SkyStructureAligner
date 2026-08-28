#!/usr/bin/env python3
"""无锚点块串的两端锚点覆盖统计。

对每个「有台词但无锚点」的 Remake 块，沿边(BFS)找最近的前驱/后继锚点块：
  两端都有 = 可夹逼 | 仅一端 = 半夹逼 | 两端都无 = 悬空
"""
import json, sys
from collections import defaultdict, deque
sys.path.insert(0, '/var/minis/workspace/TrailsInTheSkyRemakeScriptAligner')
from synonyms import normalize
W = '/var/minis/workspace'

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))

evo_pos = defaultdict(list)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text'])
                if n: evo_pos[n].append((sc, fn, lab))
remake_pos = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t)
                if n: remake_pos[n].append((sc, fn, lab))
anchor_norms = {n for n in evo_pos if len(evo_pos[n])==1 and len(remake_pos.get(n,[]))==1}

def block_has_anchor(blk):
    return any(normalize(t) in anchor_norms for t in blk)

# 对每个函数，构建邻接表 + 分类
stats = {'可夹逼': 0, '半夹逼': 0, '悬空': 0}
n_noanchor = 0
depth_hist = defaultdict(int)

for sc, funcs in remake.items():
    for fn, f in funcs.items():
        out_adj = defaultdict(list)  # 块 -> [(目标块, 边类型)]
        in_adj = defaultdict(list)   # 块 -> [(源块, 边类型)]
        for e in f['edges']:
            out_adj[e['f']].append((e['t'], e['type']))
            in_adj[e['t']].append((e['f'], e['type']))
        for lab, blk in f['blocks'].items():
            if not blk or block_has_anchor(blk):
                continue
            n_noanchor += 1
            # BFS 找后继锚点块
            def find_anchor(start, adj):
                q = deque([(start, 0)]); seen = {start}
                while q:
                    cur, d = q.popleft()
                    if d > 50: return None
                    for nxt, _ in adj.get(cur, []):
                        if nxt in seen: continue
                        seen.add(nxt)
                        if nxt in f['blocks'] and f['blocks'][nxt] and block_has_anchor(f['blocks'][nxt]):
                            return (nxt, d + 1)
                        q.append((nxt, d + 1))
                return None
            succ = find_anchor(lab, out_adj)
            pred = find_anchor(lab, in_adj)
            if succ and pred:
                stats['可夹逼'] += 1
                depth_hist[max(succ[1], pred[1])] += 1
            elif succ or pred:
                stats['半夹逼'] += 1
            else:
                stats['悬空'] += 1

print(f'无锚点块总数: {n_noanchor}')
print(f'  两端都有锚点(可夹逼): {stats["可夹逼"]} = {stats["可夹逼"]/max(n_noanchor,1)*100:.1f}%')
print(f'  仅一端(半夹逼):       {stats["半夹逼"]} = {stats["半夹逼"]/max(n_noanchor,1)*100:.1f}%')
print(f'  两端都无(悬空):       {stats["悬空"]} = {stats["悬空"]/max(n_noanchor,1)*100:.1f}%')
print()
print('可夹逼块的传播深度分布(最大前后跳数):')
for d in sorted(depth_hist):
    print(f'  {d}跳: {depth_hist[d]}')

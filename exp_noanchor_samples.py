#!/usr/bin/env python3
"""抽样分析无锚点块：与相邻块的控制流关系 + 块内台词类型。

无锚点块 = 有台词但无「两边唯一」锚点的 Remake 块。
分析: 出边/入边类型、相邻块锚点数、块内台词(短句=EVO多匹配/改写=EVO无匹配)。
"""
import json, sys
from collections import defaultdict, Counter
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

# 收集无锚点块，含边信息
def block_edges(remake, sc, fn, lab):
    """返回该块的出边（f==lab）"""
    return [e for e in remake[sc][fn]['edges'] if e['f'] == lab]

def incoming_edges(remake, sc, fn, lab):
    return [e for e in remake[sc][fn]['edges'] if e['t'] == lab]

samples = []
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        edges = f['edges']
        # 建立 块名 -> 出边
        for lab, blk in f['blocks'].items():
            if not blk: continue
            norms = [normalize(t) for t in blk]
            n_anchor = sum(1 for n in norms if n in anchor_norms)
            if n_anchor > 0: continue  # 只取无锚点块
            out_e = [e for e in edges if e['f'] == lab]
            in_e = [e for e in edges if e['t'] == lab]
            # 相邻块锚点：出边目标块 + 入边源块 的锚点数
            def blk_anchor(l):
                if l not in f['blocks']: return None
                return sum(1 for t in f['blocks'][l] if normalize(t) in anchor_norms)
            samples.append({
                'scene': sc, 'func': fn, 'lab': lab, 'n': len(blk),
                'texts': blk[:4],
                'out_edges': [(e['t'], e['type']) for e in out_e],
                'in_edges': [(e['f'], e['type']) for e in in_e],
                'neighbor_anchor': {e['t']: blk_anchor(e['t']) for e in out_e} |
                                   {e['f']: blk_anchor(e['f']) for e in in_e},
                'types': Counter('短句' if len(evo_pos.get(normalize(t), []))>1 else
                                 '改写' if len(evo_pos.get(normalize(t), []))==0 else '唯一'
                                 for t in blk)
            })

print(f'无锚点块总数: {len(samples)}')
print()
# 汇总类型分布
all_types = Counter()
for s in samples: all_types.update(s['types'])
print('无锚点块内台词类型分布:', dict(all_types))
print()
# 打印前 8 个样本
for s in samples[:8]:
    print(f"=== {s['scene']}/{s['func']}/{s['lab']} ({s['n']}句) ===")
    print(f"  类型: {dict(s['types'])}")
    for t in s['texts']:
        print(f"    {t[:30]}")
    print(f"  出边: {s['out_edges']}")
    print(f"  入边: {s['in_edges']}")
    print(f"  相邻块锚点数: {s['neighbor_anchor']}")
    print()

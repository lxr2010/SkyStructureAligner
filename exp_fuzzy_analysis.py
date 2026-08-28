#!/usr/bin/env python3
"""模糊检索救悬空块：抽改写台词样本，rapidfuzz 找 EVO top-k，对比 gt 正确文本。
"""
import json, csv, sys, re
from collections import defaultdict, deque
sys.path.insert(0, '/var/minis/workspace/TrailsInTheSkyRemakeScriptAligner')
from synonyms import normalize
from rapidfuzz import fuzz
W = '/var/minis/workspace'

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))

# EVO 台词列表
evo_texts = []  # (norm, text, voice_id)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t['text']:
                    evo_texts.append((normalize(t['text']), t['text'], t.get('voice_id')))

# gt: Remake norm -> EVO voice_id
sd = json.load(open(f'{W}/script_data_fc.json'))
sid_to_vid = {x['script_id']: x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id'] for x in sd}
vid_to_text = {}
for x in sd:
    vid = x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id']
    vid_to_text[vid] = x['text']
rows = list(csv.DictReader(open(f'{W}/match_result.csv', encoding='utf-8-sig')))
gt_norm_to_vid = defaultdict(set)
for r in rows:
    if r['MatchType']=='matched' and r['OldScriptId']:
        sid = int(r['OldScriptId'])
        vid = sid_to_vid.get(sid)
        if vid: gt_norm_to_vid[normalize(r['RemakeVoiceText'])].add(vid)

# 锚点集
evo_pos = defaultdict(list)
for n, txt, vid in evo_texts:
    evo_pos[n].append(vid)
remake_pos = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t)
                if n: remake_pos[n].append((sc,fn,lab))
anchor_norms = {n for n in evo_pos if len(evo_pos[n])==1 and len(remake_pos.get(n,[]))==1}
def has_anchor(blk): return any(normalize(t) in anchor_norms for t in blk)

# 抽悬空块的「改写」台词（EVO 无精确匹配），带 gt 的
samples = []
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        out_adj = defaultdict(list); in_adj = defaultdict(list)
        for e in f['edges']:
            out_adj[e['f']].append(e['t']); in_adj[e['t']].append(e['f'])
        for lab, blk in f['blocks'].items():
            if not blk or has_anchor(blk): continue
            # 悬空判定
            def find_anchor(start, adj):
                q = deque([(start,0)]); seen={start}
                while q:
                    cur,d=q.popleft()
                    if d>50: return None
                    for nxt in adj.get(cur,[]):
                        if nxt in seen: continue
                        seen.add(nxt)
                        if nxt in f['blocks'] and f['blocks'][nxt] and has_anchor(f['blocks'][nxt]): return True
                        q.append((nxt,d+1))
                return None
            if find_anchor(lab,out_adj) or find_anchor(lab,in_adj): continue
            for t in blk:
                n = normalize(t)
                if len(evo_pos.get(n,[])) == 0 and gt_norm_to_vid.get(n):  # 改写 + 有 gt
                    samples.append((sc, fn, lab, t, n))

print(f'悬空块的改写台词(有gt)样本数: {len(samples)}')
print()

# 手动分析前 8 个：fuzzy 找 EVO top-3
for sc, fn, lab, t, n in samples[:8]:
    gt_vids = gt_norm_to_vid[n]
    gt_texts = [vid_to_text.get(v, '?') for v in gt_vids]
    # fuzzy top-3
    scored = [(fuzz.ratio(n, en), et) for en, et, _ in evo_texts]
    scored.sort(reverse=True, key=lambda x: x[0])
    top3 = scored[:3]
    print(f'=== {sc}/{fn}/{lab} ===')
    print(f'  Remake: {t[:40]!r}')
    print(f'  gt 正确EVO: {[g[:40] for g in gt_texts]}')
    for score, et in top3:
        mark = ' ★' if et in gt_texts else ''
        print(f'    fuzzy {score:5.1f}: {et[:40]!r}{mark}')
    print()

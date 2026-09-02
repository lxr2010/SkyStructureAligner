#!/usr/bin/env python3
"""段内精化 v2：块级对齐后，段内用「说话人 + seq顺序 + rapidfuzz编辑距离」定位（允许小模糊）。

对比旧版「角色码+文本精确匹配」（改写导致候选空）。
"""
import json, csv, sys
import os
from collections import defaultdict, Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'TrailsInTheSkyRemakeScriptAligner'))
from synonyms import normalize
from rapidfuzz import fuzz
W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))
sm = json.load(open(f'{W}/speaker_map_fc.json'))
add = json.load(open(f'{W}/additional_voice_fc.json'))
def rem_char(spk): return sm.get(str(spk)) if spk is not None else None

evo_pos = defaultdict(list)
evo_key = defaultdict(list)
vid_info = {}  # vid -> (char, region, seq, norm)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text']); vid = t.get('voice_id')
                if n: evo_pos[n].append((sc, fn, lab))
                if n and vid:
                    evo_key[(vid[:3], n)].append(vid)
                    vid_info[vid] = (vid[:3], int(vid[3:6]), int(vid[6:10]), n)
evo_block_vids = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            evo_block_vids[(sc, fn, lab)] = [t['voice_id'] for t in blk if t.get('voice_id')]
remake_pos = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text'])
                if n: remake_pos[n].append((sc, fn, lab))
anchor_norms = {n for n in evo_pos if len(evo_pos[n])==1 and len(remake_pos.get(n,[]))==1}

def split_block(toks):
    segs = []; cur = []; prev = None
    for norm, char, ablk in toks:
        if ablk is not None:
            if prev is not None and ablk != prev and cur:
                segs.append(cur); cur = []
            prev = ablk
        cur.append((norm, char, ablk))
    if cur: segs.append(cur)
    return segs

# 锚点：(角色码, norm) 唯一
remake_key = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text']); char = rem_char(t['speaker'])
                if n and char: remake_key[(char, n)].append((sc, fn, lab))
anchor_keys = {k for k in evo_key if len(evo_key[k])==1 and len(remake_key.get(k,[]))==1}
vid_to_evoblk = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('voice_id'): vid_to_evoblk[t['voice_id']] = (sc, fn, lab)

block_segs = {}
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            toks = [(normalize(t['text']), rem_char(t['speaker']), vid_to_evoblk.get(evo_key[(rem_char(t['speaker']), normalize(t['text']))][0]) if (rem_char(t['speaker']), normalize(t['text'])) in anchor_keys else None) for t in blk]
            segs = split_block(toks)
            info = []
            for seg in segs:
                a_blks = [ablk for _, _, ablk in seg if ablk is not None]
                info.append((seg, max(set(a_blks), key=a_blks.count) if a_blks else None))
            block_segs[(sc, fn, lab)] = info

def entry_blk(rblk):
    for seg, eb in block_segs.get(rblk, []):
        if eb is not None: return eb
    return None
def exit_blk(rblk):
    for seg, eb in reversed(block_segs.get(rblk, [])):
        if eb is not None: return eb
    return None

MIRROR = {'next':'next','jump':'jump','cond_false':'cond_true','cond_true':'cond_false'}
evo_out = defaultdict(list)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for e in f['edges']:
            evo_out[(sc, fn, e['f'])].append((e['t'], e['type']))
def out_edges(sc, fn, lab):
    return [(e['t'], e['type']) for e in remake[sc][fn]['edges'] if e['f']==lab]

aligned = {k for k in block_segs if entry_blk(k) is not None}
propagated = set()
changed = True
while changed:
    changed = False
    for rblk in list(aligned):
        rscene, rfn, rlab = rblk
        eexit = exit_blk(rblk)
        if eexit is None: continue
        for rt, rtype in out_edges(rscene, rfn, rlab):
            rnxt = (rscene, rfn, rt)
            if rnxt in aligned or rnxt in propagated: continue
            if rnxt not in block_segs: continue
            mt = MIRROR[rtype]
            escene, efn, elab = eexit
            cand = [t for t, ty in evo_out[eexit] if ty == mt]
            valid = [t for t in cand if t in evo[escene][efn]['blocks']]
            if len(valid) == 1:
                blk_data = remake[rscene][rfn]['blocks'].get(rt, [])
                block_segs[rnxt] = [([(normalize(t['text']), rem_char(t['speaker']), None) for t in blk_data], (escene, efn, valid[0]))]
                propagated.add(rnxt); aligned.add(rnxt); changed = True

# gt
sd = json.load(open(f'{W}/script_data_fc.json'))
sid_to_vid = {x['script_id']: x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id'] for x in sd}
rows = list(csv.DictReader(open(f'{W}/match_result.csv', encoding='utf-8-sig')))
gt_norm_to_vid = defaultdict(set)
for r in rows:
    if r['MatchType']=='matched' and r['OldScriptId']:
        vid = sid_to_vid.get(int(r['OldScriptId']))
        if vid: gt_norm_to_vid[normalize(r['RemakeVoiceText'])].add(vid)

# 段内精化 v2：段 EVO 块内，按 seq 排序，说话人匹配，编辑距离选最像
stat = {'锚点唯一':[0,0], '段内精确唯一':[0,0], '段内模糊救回':[0,0]}
for rblk, segs in block_segs.items():
    sc, fn, lab = rblk
    if lab not in remake[sc][fn]['blocks']: continue
    blk = remake[sc][fn]['blocks'][lab]
    is_tk = fn.startswith('TK_')
    for seg, evo_blk in segs:
        if evo_blk is None: continue
        block_vids = evo_block_vids.get(evo_blk, [])
        # 段 EVO 块内 voice_id 按 seq 排序，按角色码分组
        by_char = defaultdict(list)
        for v in block_vids:
            by_char[vid_info[v][0]].append(v)
        for c in by_char:
            by_char[c].sort(key=lambda v: vid_info[v][2])  # seq
        for norm, char, ablk in seg:
            gt = gt_norm_to_vid.get(norm, set())
            if not gt: continue
            if ablk is not None:
                stat['锚点唯一'][0] += 1
                if ablk in gt: stat['锚点唯一'][1] += 1
                continue
            # 精确匹配（旧版）
            cand = evo_key.get((char, norm), [])
            in_blk = [v for v in cand if v in block_vids]
            if len(in_blk) == 1:
                stat['段内精确唯一'][0] += 1
                if in_blk[0] in gt: stat['段内精确唯一'][1] += 1
                continue
            # 模糊：段内说话人匹配的 voice_id，编辑距离选最像
            text = norm  # 近似用 norm 做编辑距离（缺原文，这里用 norm 足够演示）
            cands = by_char.get(char, [])
            if cands:
                best = max(cands, key=lambda v: fuzz.ratio(norm, vid_info[v][3]))
                if fuzz.ratio(norm, vid_info[best][3]) >= 60:
                    stat['段内模糊救回'][0] += 1
                    if best in gt: stat['段内模糊救回'][1] += 1

print('=== 段内精化 v2（编辑距离允许小模糊）===')
for k in ['锚点唯一','段内精确唯一','段内模糊救回']:
    a, b = stat[k]
    print(f'  {k}: {b}/{a} = {b/max(a,1)*100:.1f}%')

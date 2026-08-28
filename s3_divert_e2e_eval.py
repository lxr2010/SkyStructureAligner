#!/usr/bin/env python3
"""分流端到端：TK_xxx 走角色码+文本，非 TK_xxx 走图匹配(块对齐+传播+段内夹逼)。

验证：句级唯一确定率(候选唯一且=gt) + 集合命中率(候选含gt)。
"""
import json, csv, sys
from collections import defaultdict, Counter
sys.path.insert(0, '/var/minis/workspace/TrailsInTheSkyRemakeScriptAligner')
from synonyms import normalize
W = '/var/minis/workspace'

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))
sm = json.load(open(f'{W}/speaker_map_fc.json'))
def rem_char(spk): return sm.get(str(spk)) if spk is not None else None

# ---------- 索引 ----------
evo_pos = defaultdict(list)          # norm -> [(sc,fn,lab)]
evo_key = defaultdict(list)          # (char,norm) -> [voice_id]
vid_to_evoblk = {}
vid_text = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text']); vid = t.get('voice_id')
                if n: evo_pos[n].append((sc, fn, lab))
                if n and vid: evo_key[(vid[:3], n)].append(vid)
                if vid: vid_to_evoblk[vid] = (sc, fn, lab); vid_text[vid] = t['text']
evo_block_vids = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            evo_block_vids[(sc, fn, lab)] = {t['voice_id'] for t in blk if t.get('voice_id')}
remake_pos = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text'])
                if n: remake_pos[n].append((sc, fn, lab))
anchor_norms = {n for n in evo_pos if len(evo_pos[n])==1 and len(remake_pos.get(n,[]))==1}

# gt
sd = json.load(open(f'{W}/script_data_fc.json'))
sid_to_vid = {x['script_id']: x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id'] for x in sd}
rows = list(csv.DictReader(open(f'{W}/match_result.csv', encoding='utf-8-sig')))
gt_norm_to_vid = defaultdict(set)
for r in rows:
    if r['MatchType']=='matched' and r['OldScriptId']:
        vid = sid_to_vid.get(int(r['OldScriptId']))
        if vid: gt_norm_to_vid[normalize(r['RemakeVoiceText'])].add(vid)

# ---------- 块级对齐（非 TK_xxx 用） ----------
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

block_segs = {}
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            toks = [(normalize(t['text']), rem_char(t['speaker']), evo_pos[normalize(t['text'])][0] if normalize(t['text']) in anchor_norms else None) for t in blk]
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

# 传播（非 TK_xxx）
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
            if len(valid) != 1 and rtype == 'cond_false':
                from collections import Counter as _C
                def evo_block_char(blk):
                    c = _C(t['voice_id'][:3] for t in blk if t.get('voice_id'))
                    return c.most_common(1)[0][0] if c else None
                def rem_block_char(blk):
                    c = _C(sm.get(str(t['speaker'])) for t in blk if t.get('speaker') is not None)
                    return c.most_common(1)[0][0] if c else None
                r_char = rem_block_char(remake[rscene][rfn]['blocks'].get(rt, []))
                sw_cand = [t for t, ty in evo_out[eexit] if ty == 'switch' and t in evo[escene][efn]['blocks']]
                if r_char:
                    valid = [t for t in sw_cand if evo_block_char(evo[escene][efn]['blocks'][t]) == r_char]
            if len(valid) == 1:
                blk_data = remake[rscene][rfn]['blocks'].get(rt, [])
                block_segs[rnxt] = [([(normalize(t['text']), rem_char(t['speaker']), None) for t in blk_data], (escene, efn, valid[0]))]
                propagated.add(rnxt); aligned.add(rnxt); changed = True

# ---------- 分流验证 ----------
stat = {'锚点唯一':[0,0], 'TK唯一':[0,0], 'TK集合':[0,0], '非TK段内唯一':[0,0], '非TK段内多':[0,0], '非TK无候选':[0,0]}
for rblk, segs in block_segs.items():
    sc, fn, lab = rblk
    if lab not in remake[sc][fn]['blocks']: continue
    blk = remake[sc][fn]['blocks'][lab]
    is_tk = fn.startswith('TK_')
    for seg, evo_blk in segs:
        block_vids = evo_block_vids.get(evo_blk, set()) if evo_blk is not None else set()
        for norm, char, ablk in seg:
            gt = gt_norm_to_vid.get(norm, set())
            if not gt: continue
            if ablk is not None:  # 锚点
                stat['锚点唯一'][0] += 1
                if vid_to_evoblk.get(evo_key[(char, norm)][0] if evo_key.get((char, norm)) else None) in {vid_to_evoblk.get(g) for g in gt} or any(v in gt for v in evo_key.get((char, norm), [])):
                    stat['锚点唯一'][1] += 1
                continue
            if is_tk:
                cand = evo_key.get((char, norm), [])
                if len(cand) == 1:
                    stat['TK唯一'][0] += 1
                    if cand[0] in gt: stat['TK唯一'][1] += 1
                elif len(cand) > 1:
                    stat['TK集合'][0] += 1
                    if any(v in gt for v in cand): stat['TK集合'][1] += 1
            else:
                cand = [v for v in evo_key.get((char, norm), []) if v in block_vids]
                if len(cand) == 1:
                    stat['非TK段内唯一'][0] += 1
                    if vid_to_evoblk.get(cand[0]) in {vid_to_evoblk.get(g) for g in gt}: stat['非TK段内唯一'][1] += 1
                elif len(cand) > 1:
                    stat['非TK段内多'][0] += 1
                else:
                    stat['非TK无候选'][0] += 1

print('=== 分流端到端（句级）===')
for k in ['锚点唯一','TK唯一','TK集合','非TK段内唯一','非TK段内多','非TK无候选']:
    a, b = stat[k]
    pct = f'{b/max(a,1)*100:.1f}%' if a else '-'
    print(f'  {k}: {b}/{a} = {pct}')
# 唯一确定率（锚点唯一 + TK唯一 + 非TK段内唯一）
uniq_tot = stat['锚点唯一'][0] + stat['TK唯一'][0] + stat['非TK段内唯一'][0]
uniq_hit = stat['锚点唯一'][1] + stat['TK唯一'][1] + stat['非TK段内唯一'][1]
print(f'\n唯一确定率: {uniq_hit}/{uniq_tot} = {uniq_hit/max(uniq_tot,1)*100:.1f}%')
# 集合命中率（唯一 + 集合）
set_tot = uniq_tot + stat['TK集合'][0]
set_hit = uniq_hit + stat['TK集合'][1]
print(f'集合命中率(唯一+集合): {set_hit}/{set_tot} = {set_hit/max(set_tot,1)*100:.1f}%')

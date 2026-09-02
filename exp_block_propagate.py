#!/usr/bin/env python3
"""结构匹配 v2：锚点块对齐(1:1) + 1:N 按锚点 EVO 块变化点拆分 + 沿边传播。

1. 1:1 块：锚点投票唯一 -> 直接对齐
2. 1:N 块：按锚点 EVO 块变化点拆成多段，每段对齐一个 EVO 块
3. 沿边传播：块级别，从已对齐块的「出口 EVO 块」沿边(语义镜像)对齐下一块
4. gt 验证
"""
import json, csv, sys
import os
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'TrailsInTheSkyRemakeScriptAligner'))
from synonyms import normalize
W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))

# ---------- norm 索引 ----------
evo_pos = defaultdict(list)
vid_to_evoblk = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text'])
                if n: evo_pos[n].append((sc, fn, lab))
                if t.get('voice_id'): vid_to_evoblk[t['voice_id']] = (sc, fn, lab)
# (角色码, normalize) -> [voice_id]
evo_key = defaultdict(list)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text']); vid = t.get('voice_id')
                if n and vid: evo_key[(vid[:3], n)].append(vid)
sm = json.load(open(f'{W}/speaker_map_fc.json'))
def rem_char(spk): return sm.get(str(spk)) if spk is not None else None
remake_pos = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text'])
                if n: remake_pos[n].append((sc, fn, lab))
anchor_norms = {n for n in evo_pos if len(evo_pos[n])==1 and len(remake_pos.get(n,[]))==1}

def split_block(toks):
    """按锚点 EVO 块变化点切分，返回段列表（每段是 token 列表）"""
    segs = []; cur = []; prev = None
    for norm, char, ablk in toks:
        if ablk is not None:
            if prev is not None and ablk != prev and cur:
                segs.append(cur); cur = []
            prev = ablk
        cur.append((norm, char, ablk))
    if cur: segs.append(cur)
    return segs

# ---------- 块对齐 + 1:N 拆分 ----------
# block_segs: (scene,fn,lab) -> [(tokens, evo_blk or None)]
block_segs = {}
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            toks = [(normalize(t['text']), rem_char(t['speaker']), evo_pos[normalize(t['text'])][0] if normalize(t['text']) in anchor_norms else None) for t in blk]
            segs = split_block(toks)
            info = []
            for seg in segs:
                a_blks = [ablk for _, _, ablk in seg if ablk is not None]
                if a_blks:
                    evo_blk = max(set(a_blks), key=a_blks.count)
                else:
                    evo_blk = None
                info.append((seg, evo_blk))
            block_segs[(sc, fn, lab)] = info

# 块入口/出口 EVO 块
def entry_blk(rblk):
    segs = block_segs.get(rblk, [])
    for seg, eb in segs:
        if eb is not None: return eb
    return None
def exit_blk(rblk):
    segs = block_segs.get(rblk, [])
    for seg, eb in reversed(segs):
        if eb is not None: return eb
    return None

print(f'块对齐: {sum(1 for v in block_segs.values() if entry_blk(k) if False) if False else len(block_segs)} 块(含拆分)')

# ---------- 沿边传播（块级别） ----------
sm = json.load(open(f'{W}/speaker_map_fc.json'))
MIRROR = {'next':'next','jump':'jump','cond_false':'cond_true','cond_true':'cond_false'}
# 块的主要角色码/说话人（用于 switch 多 case 的说话人过滤）
from collections import Counter as _C
def evo_block_char(blk):
    c = _C(t['voice_id'][:3] for t in blk if t.get('voice_id'))
    return c.most_common(1)[0][0] if c else None
def rem_block_char(blk):
    c = _C(sm.get(str(t['speaker'])) for t in blk if t.get('speaker') is not None)
    return c.most_common(1)[0][0] if c else None
# 块 -> 出边
def out_edges(sc, fn, lab):
    return [(e['t'], e['type']) for e in remake[sc][fn]['edges'] if e['f']==lab]
# EVO 出边索引（全局 label -> (scene,fn) 归属）
evo_out = defaultdict(list)  # (scene,fn,lab) -> [(target_label, type)]
lab_to_loc = {}  # label -> set((scene,fn))
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for e in f['edges']:
            evo_out[(sc, fn, e['f'])].append((e['t'], e['type']))
        for lab in f['blocks']:
            lab_to_loc.setdefault(lab, set()).add((sc, fn))

# 已对齐块：entry_blk 非 None 的块
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
            # 选项分支(cond_false)若常规候选不唯一，尝试 switch 边 + 说话人过滤
            if len(valid) != 1 and rtype == 'cond_false':
                rblk_data = remake[rscene][rfn]['blocks'].get(rt)
                r_char = rem_block_char(rblk_data) if rblk_data else None
                sw_cand = [t for t, ty in evo_out[eexit] if ty == 'switch' and t in evo[escene][efn]['blocks']]
                if r_char:
                    valid = [t for t in sw_cand if evo_block_char(evo[escene][efn]['blocks'][t]) == r_char]
            if len(valid) == 1:
                blk_data = remake[rscene][rfn]['blocks'].get(rt, [])
                blk_toks = [(normalize(t['text']), rem_char(t['speaker']), None) for t in blk_data]
                block_segs[rnxt] = [(blk_toks, (escene, efn, valid[0]))]
                propagated.add(rnxt)
                aligned.add(rnxt)
                changed = True

print(f'沿边传播: +{len(propagated)} 块')

# ---------- gt 定义（段内夹逼 + gt 验证共用） ----------
sd = json.load(open(f'{W}/script_data_fc.json'))
sid_to_vid = {x['script_id']: x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id'] for x in sd}
rows = list(csv.DictReader(open(f'{W}/match_result.csv', encoding='utf-8-sig')))
gt_norm_to_evoblk = defaultdict(set)
for r in rows:
    if r['MatchType']=='matched' and r['OldScriptId']:
        vid = sid_to_vid.get(int(r['OldScriptId']))
        if vid and vid in vid_to_evoblk: gt_norm_to_evoblk[normalize(r['RemakeVoiceText'])].add(vid_to_evoblk[vid])

# ---------- 段内夹逼（句级精化）：候选限制在段对应 EVO 块 ----------
vid_text = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('voice_id'): vid_text[t['voice_id']] = normalize(t['text'])
evo_block_vids = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            evo_block_vids[(sc, fn, lab)] = {t['voice_id'] for t in blk if t.get('voice_id')}

bracket_stat = {'锚点唯一':[0,0], '段内唯一':[0,0], '段内多候选':[0,0]}
from collections import Counter as _C2
cat = _C2()
for rblk, segs in block_segs.items():
    sc, fn, lab = rblk
    if lab not in remake[sc][fn]['blocks']: continue
    blk = remake[sc][fn]['blocks'][lab]
    for seg, evo_blk in segs:
        block_vids = evo_block_vids.get(evo_blk, set()) if evo_blk is not None else set()
        for norm, char, ablk in seg:
            gt = gt_norm_to_evoblk.get(norm, set())
            if not gt: continue
            if ablk is not None:
                bracket_stat['锚点唯一'][0] += 1
                if ablk in gt: bracket_stat['锚点唯一'][1] += 1
                continue
            cand = [v for v in evo_key.get((char, norm), []) if v in block_vids]
            if len(cand) == 1:
                bracket_stat['段内唯一'][0] += 1
                if vid_to_evoblk.get(cand[0]) in gt: bracket_stat['段内唯一'][1] += 1
            elif len(cand) > 1:
                bracket_stat['段内多候选'][0] += 1
            else:
                # 候选空，分类归因
                if evo_blk is None:
                    cat['A.段无EVO块(块级对齐失败)'] += 1
                elif len(evo_key.get((char, norm), [])) == 0:
                    cat['B.该角色该文本无语音'] += 1
                else:
                    cat['C.有语音但不在段EVO块内'] += 1

print()
print('=== 段内夹逼（候选限制在段 EVO 块）===')
for k in ['锚点唯一','段内唯一','段内多候选']:
    a, b = bracket_stat[k]
    print(f'  {k}: {b}/{a} = {b/max(a,1)*100:.1f}%')
print('=== 候选空归因 ===')
for k, v in cat.most_common():
    print(f'  {k}: {v}')

# ---------- 抽样：A类(块级对齐失败) + 段内唯一但错 ----------
samples_A = []
samples_wrong = []
for rblk, segs in block_segs.items():
    sc, fn, lab = rblk
    if lab not in remake[sc][fn]['blocks']: continue
    blk = remake[sc][fn]['blocks'][lab]
    for seg, evo_blk in segs:
        block_vids = evo_block_vids.get(evo_blk, set()) if evo_blk is not None else set()
        for norm, char, ablk in seg:
            gt = gt_norm_to_evoblk.get(norm, set())
            if not gt or ablk is not None: continue
            cand = [v for v in evo_key.get((char, norm), []) if v in block_vids]
            if len(cand) == 1 and vid_to_evoblk.get(cand[0]) not in gt and len(samples_wrong) < 6:
                samples_wrong.append((sc, fn, lab, norm, char, cand[0], gt, evo_blk))
            elif evo_blk is None and len(samples_A) < 6:
                samples_A.append((sc, fn, lab, norm, char, gt))

print()
print('=== 抽样：段内唯一但错（候选1个≠gt）===')
for sc, fn, lab, norm, char, cand, gt, evo_blk in samples_wrong:
    print(f'  {sc}/{fn}/{lab}: norm={norm[:20]!r} char={char}')
    print(f'    候选={cand}({vid_text.get(cand, cand)[:20]!r})  段EVO块={evo_blk}')
    print(f'    gt={[vid_text.get(g, g)[:20] for g in gt]}')

print()
print('=== 抽样：A类 块级对齐失败（段evo_blk=None）===')
for sc, fn, lab, norm, char, gt in samples_A:
    # 该块的前驱/后继
    pred = [(e['f'], e['type']) for e in remake[sc][fn]['edges'] if e['t'] == lab]
    succ = [(e['t'], e['type']) for e in remake[sc][fn]['edges'] if e['f'] == lab]
    print(f'  {sc}/{fn}/{lab}: norm={norm[:20]!r}')
    print(f'    前驱={pred} 后继={succ}')

# ---------- gt 验证 ----------
n_ok = n_tot = 0
for rblk, segs in block_segs.items():
    sc, fn, lab = rblk
    if lab not in remake[sc][fn]['blocks']: continue
    blk = remake[sc][fn]['blocks'][lab]
    # 每句台词的段对应（按 seg 内的 token 顺序，重建 段 -> 台词 的 EVO 块）
    # 简化：整块按第一段的 EVO 块验证（对 1:1 块正确，1:N 块需段级）
    for t in blk:
        gt_blks = gt_norm_to_evoblk.get(normalize(t['text']), set())
        if not gt_blks: continue
        n_tot += 1
        # 找该台词属于哪个段
        for seg, eb in segs:
            seg_norms = {x for x, _, _ in seg}
            if normalize(t['text']) in seg_norms:
                if eb is not None and eb in gt_blks:
                    n_ok += 1
                break
print(f'gt 验证（段级，对齐正确）: {n_ok}/{n_tot} = {n_ok/max(n_tot,1)*100:.1f}%')

rem_with_talk = sum(1 for sc in remake.values() for f in sc.values() for b in f['blocks'].values() if b)
print(f'有台词块: {rem_with_talk}, 已对齐(含拆分/传播): {len(aligned)} = {len(aligned)/rem_with_talk*100:.1f}%')

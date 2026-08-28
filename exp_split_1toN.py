#!/usr/bin/env python3
"""处理 1:N 块：按「锚点的 EVO 块归属变化点」拆分 Remake 块，并用 gt 验证。

拆分：块内锚点(两边唯一)按顺序，锚点 EVO 块变化处切分 -> 子块各自 1:1。
验证：gt = match_result(Remake台词->OldScriptId->voice_id->EVO块)，子块内台词 gt 块是否一致。
"""
import json, csv, sys
from collections import defaultdict, Counter
sys.path.insert(0, '/var/minis/workspace/TrailsInTheSkyRemakeScriptAligner')
from synonyms import normalize
W = '/var/minis/workspace'

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))

# ---------- norm -> 位置 ----------
evo_pos = defaultdict(list)   # norm -> [(scene,func,block)]
vid_to_evoblk = {}            # voice_id -> (scene,func,block)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t['text'])
                if n: evo_pos[n].append((sc, fn, lab))
                if t.get('voice_id'): vid_to_evoblk[t['voice_id']] = (sc, fn, lab)
remake_pos = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = normalize(t)
                if n: remake_pos[n].append((sc, fn, lab))

# 锚点：两边唯一
anchor_norms = {n for n in evo_pos if len(evo_pos[n])==1 and len(remake_pos.get(n,[]))==1}

# ---------- gt: Remake norm -> EVO 块 ----------
sd = json.load(open(f'{W}/script_data_fc.json'))
sid_to_vid = {x['script_id']: x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id'] for x in sd}
rows = list(csv.DictReader(open(f'{W}/match_result.csv', encoding='utf-8-sig')))
gt_norm_to_evoblk = defaultdict(set)   # Remake norm -> {EVO块}
for r in rows:
    if r['MatchType']=='matched' and r['OldScriptId']:
        sid = int(r['OldScriptId'])
        vid = sid_to_vid.get(sid)
        if vid and vid in vid_to_evoblk:
            gt_norm_to_evoblk[normalize(r['RemakeVoiceText'])].add(vid_to_evoblk[vid])

# ---------- 拆分 1:N 块 ----------
def split_block(blk_tokens):
    """blk_tokens: [(norm, anchor_evo_block or None)]，按锚点 EVO 块变化切分。
    返回子块列表，每个子块是 token 列表。"""
    segs = []; cur = []; prev_blk = None
    for norm, ablk in blk_tokens:
        if ablk is not None:
            if prev_blk is not None and ablk != prev_blk and cur:
                segs.append(cur); cur = []
            prev_blk = ablk
        cur.append((norm, ablk))
    if cur: segs.append(cur)
    return segs

n_1toN = 0; n_split_ok = 0; n_still_1toN = 0
n_tokens_gt_ok = 0; n_tokens_gt_total = 0
samples = []
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            toks = []
            for t in blk:
                n = normalize(t)
                ablk = evo_pos[n][0] if n in anchor_norms else None
                toks.append((n, ablk))
            # 该块的锚点 EVO 块集合
            a_blks = {ablk for _, ablk in toks if ablk is not None}
            if len(a_blks) <= 1:
                continue
            n_1toN += 1
            segs = split_block(toks)
            # 拆分后每个子块的锚点 EVO 块
            seg_blks = []
            for seg in segs:
                sb = {ablk for _, ablk in seg if ablk is not None}
                seg_blks.append(sb)
            if all(len(sb) <= 1 for sb in seg_blks) and len(segs) > 1:
                n_split_ok += 1
            else:
                n_still_1toN += 1
                if len(samples) < 8:
                    samples.append((sc, fn, lab, len(toks), [len(sb) for sb in seg_blks]))
            # gt 验证：每个子块内台词 gt EVO 块是否一致
            for seg in segs:
                gt_blks = set()
                for norm, _ in seg:
                    gt_blks |= gt_norm_to_evoblk.get(norm, set())
                n_tokens_gt_total += 1
                if len(gt_blks) <= 1:
                    n_tokens_gt_ok += 1

print(f'1:N 块总数: {n_1toN}')
print(f'  拆分后全部 1:1: {n_split_ok} = {n_split_ok/max(n_1toN,1)*100:.1f}%')
print(f'  仍有多块子块: {n_still_1toN} = {n_still_1toN/max(n_1toN,1)*100:.1f}%')
print()
print(f'gt 验证：拆出的子块内台词 gt 块一致 {n_tokens_gt_ok}/{n_tokens_gt_total} = {n_tokens_gt_ok/max(n_tokens_gt_total,1)*100:.1f}%')
print()
print('仍 1:N 的样本:')
for s in samples:
    print(f'  {s[0]}/{s[1]}/{s[2]}: {s[3]}句, 子块锚点块数={s[4]}')

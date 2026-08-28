#!/usr/bin/env python3
"""生成最终匹配结果：Remake台词 -> voice_id（含 additional_voice 脚本外语音）。

匹配：锚点(角色码+文本唯一) / TK(角色码+文本) / 非TK(段内夹逼, 空则additional补)。
输出 my_match_result.csv：RemakeVoiceText, Speaker, SpeakerChar, MyVoiceId, Candidates, MatchType, Source
"""
import json, csv, sys, re
from collections import defaultdict, Counter
from rapidfuzz import fuzz
sys.path.insert(0, '/var/minis/workspace/TrailsInTheSkyRemakeScriptAligner')
from synonyms import normalize
W = '/var/minis/workspace'

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))
sm = json.load(open(f'{W}/speaker_map_fc.json'))
add = json.load(open(f'{W}/additional_voice_fc.json'))
def rem_char(spk): return sm.get(str(spk)) if spk is not None else None

def clean_final(text):
    """normalize + 仅浊点归一化（う゛/ヴ → う），不做标点/emoji 过度归一化"""
    if not text: return ''
    t = normalize(text)
    t = t.replace('\u3046\u3099', 'う').replace('ヴ', 'う')
    return t

# ---------- 索引（script_data + additional_voice） ----------
evo_pos = defaultdict(list)
evo_key = defaultdict(list)      # (char,norm) -> [vid]  script_data
add_key = defaultdict(list)      # (char,norm) -> [vid]  additional_voice
add_vids = set()
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = clean_final(t['text']); vid = t.get('voice_id')
                if n: evo_pos[n].append((sc, fn, lab))
                if n and vid: evo_key[(vid[:3], n)].append(vid)
for x in add:
    vid = x['voice_id'][:-1]; n = clean_final(x['text'])
    if n and vid:
        add_key[(vid[:3], n)].append(vid); add_vids.add(vid)
evo_block_vids = {}
vid_text = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            evo_block_vids[(sc, fn, lab)] = {t['voice_id'] for t in blk if t.get('voice_id')}
            for t in blk:
                if t.get('voice_id'): vid_text[t['voice_id']] = clean_final(t['text'])
remake_pos = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = clean_final(t['text'])
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

# norm -> 所有 voice_id（用于锚点：文本唯一的 voice_id，不管说话人）
norm_all_vids = defaultdict(list)
vid_to_evoblk = {}
for (c, nn), vids in evo_key.items():
    norm_all_vids[nn].extend(vids)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('voice_id'): vid_to_evoblk[t['voice_id']] = (sc, fn, lab)

# 锚点：(角色码, norm) 两边唯一（考虑说话人）
remake_key = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                n = clean_final(t['text']); char = rem_char(t['speaker'])
                if n and char: remake_key[(char, n)].append((sc, fn, lab))
anchor_keys = {k for k in evo_key if len(evo_key[k])==1 and len(remake_key.get(k,[]))==1}

block_segs = {}
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            toks = [(clean_final(t['text']), rem_char(t['speaker']), vid_to_evoblk.get(evo_key[(rem_char(t['speaker']), clean_final(t['text']))][0]) if (rem_char(t['speaker']), clean_final(t['text'])) in anchor_keys else None) for t in blk]
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
            if len(valid) != 1:
                def evo_block_char(blk):
                    c = Counter(t['voice_id'][:3] for t in blk if t.get('voice_id'))
                    return c.most_common(1)[0][0] if c else None
                def rem_block_char(blk):
                    c = Counter(sm.get(str(t['speaker'])) for t in blk if t.get('speaker') is not None)
                    return c.most_common(1)[0][0] if c else None
                r_char = rem_block_char(remake[rscene][rfn]['blocks'].get(rt, []))
                if r_char:
                    all_cand = list(valid)
                    if rtype == 'cond_false':
                        all_cand += [t for t, ty in evo_out[eexit] if ty == 'switch' and t in evo[escene][efn]['blocks']]
                    valid = [t for t in all_cand if evo_block_char(evo[escene][efn]['blocks'][t]) == r_char]
            if len(valid) == 1:
                blk_data = remake[rscene][rfn]['blocks'].get(rt, [])
                block_segs[rnxt] = [([(clean_final(t['text']), rem_char(t['speaker']), None) for t in blk_data], (escene, efn, valid[0]))]
                propagated.add(rnxt); aligned.add(rnxt); changed = True

def refine_segment(seg, evo_blk):
    """段内纯编辑距离（阈值 70），返回 {norm: best_vid}"""
    block_vids = evo_block_vids.get(evo_blk, set())
    by_char = defaultdict(list)
    for v in block_vids:
        by_char[v[:3]].append(v)
    result = {}
    for c, vids in by_char.items():
        seg_texts = [norm for norm, char, ablk in seg if char == c and ablk is None]
        for norm in seg_texts:
            best = max(vids, key=lambda v: fuzz.ratio(norm, vid_text.get(v, '')))
            if fuzz.ratio(norm, vid_text.get(v, '')) >= 70:
                result[norm] = best
    return result

# ---------- 函数级对齐：函数内长句锚点投票到 EVO 函数 ----------
func_align = {}
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        if fn.startswith('TK_'): continue
        anchors_evofn = Counter()
        for lab, blk in f['blocks'].items():
            for t in blk:
                k = (rem_char(t.get('speaker')), clean_final(t['text']))
                if k in anchor_keys:
                    vid = evo_key[k][0]
                    eblk = vid_to_evoblk.get(vid)
                    if eblk: anchors_evofn[(eblk[0], eblk[1])] += 1
        if len(anchors_evofn) == 1:
            func_align[(sc, fn)] = list(anchors_evofn.keys())[0]

def func_refine(norm, char, sc, fn):
    """函数级短句精化：函数级对齐的 EVO 函数内，编辑距离选最像（阈值80）"""
    efn = func_align.get((sc, fn))
    if not efn: return None
    f = evo.get(efn[0], {}).get(efn[1])
    if not f: return None
    vids = [t['voice_id'] for lab, blk in f['blocks'].items() for t in blk if t.get('voice_id') and t['voice_id'][:3] == char]
    if not vids: return None
    best = max(vids, key=lambda v: fuzz.ratio(norm, vid_text.get(v, '')))
    if fuzz.ratio(norm, vid_text.get(best, '')) >= 80:
        return best
    return None

# ---------- 生成最终匹配结果 ----------
out = []
for rblk, segs in block_segs.items():
    sc, fn, lab = rblk
    if lab not in remake[sc][fn]['blocks']: continue
    is_tk = fn.startswith('TK_')
    blk = remake[sc][fn]['blocks'][lab]
    for seg, evo_blk in segs:
        block_vids = evo_block_vids.get(evo_blk, set()) if evo_blk is not None else set()
        refine = refine_segment(seg, evo_blk) if evo_blk is not None else {}
        for norm, char, ablk in seg:
            # 找原始文本 + speaker
            text = spk = None
            for t in blk:
                if clean_final(t['text']) == norm:
                    text = t['text']; spk = t['speaker']; break
            if text is None: continue
            # 候选（script_data + additional_voice）
            cand = list(evo_key.get((char, norm), []))
            cand_add = list(add_key.get((char, norm), []))
            if ablk is not None:
                # 锚点：角色码+文本唯一的 voice_id
                all_cand = evo_key.get((char, norm), [])
            elif is_tk:
                all_cand = cand + cand_add
            else:
                in_blk = [v for v in cand if v in block_vids]
                if in_blk:
                    all_cand = in_blk
                else:
                    bv = refine.get(norm) or func_refine(norm, char, sc, fn)
                    all_cand = [bv] if bv else cand_add  # 段内模糊 -> 函数级精化 -> additional 补
            # 去重保序
            uniq = []
            for v in all_cand:
                if v not in uniq: uniq.append(v)
            # 来源
            src = []
            for v in uniq:
                src.append('additional' if v in add_vids else 'script')
            if len(uniq) == 0:
                ty = '无候选'
            elif len(uniq) == 1:
                ty = '唯一'
            else:
                ty = '多候选'
            # 说话人对应标记
            spk_match = ''
            if uniq and char:
                spk_match = '对应' if all(v[:3] == char for v in uniq) else '说话人不对应'
            out.append((text, spk, char, uniq[0] if uniq else '', '|'.join(uniq), ty, '|'.join(src), spk_match))

with open(f'{W}/my_match_result.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['RemakeVoiceText', 'Speaker', 'SpeakerChar', 'MyVoiceId', 'Candidates', 'MatchType', 'Source', 'SpeakerMatch'])
    for row in out:
        w.writerow(row)

from collections import Counter
ty = Counter(r[5] for r in out)
print(f'总台词: {len(out)}')
print(f'匹配类型: {dict(ty)}')
spk = Counter(r[7] for r in out if r[7])
print(f'说话人标记: {dict(spk)}')
uniq = sum(1 for r in out if r[5] == '唯一')
print(f'唯一确定: {uniq} = {uniq/max(len(out),1)*100:.1f}%')

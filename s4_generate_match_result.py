#!/usr/bin/env python3
"""生成最终匹配结果：Remake台词 -> voice_id（含 additional_voice 脚本外语音）。

匹配：锚点(角色码+文本唯一) / TK(角色码+文本) / 非TK(段内夹逼, 空则additional补)。
输出 my_match_result[_sc].csv：RemakeVoiceText, Speaker, SpeakerChar, MyVoiceId, Candidates, MatchType, Source

用法: python s4_generate_match_result.py [fc|sc]，默认 fc。
"""
import json, csv, sys, re
import os
from collections import defaultdict, Counter
from rapidfuzz import fuzz
from synonyms import normalize
from paths import W, resolve, require

GAME = (sys.argv[1].lower() if len(sys.argv) > 1 else 'fc')
assert GAME in ('fc', 'sc'), f'未知游戏代号: {GAME}'
SUF = '' if GAME == 'fc' else f'_{GAME}'

evo = json.load(open(require(f'evo_structure{SUF}.json')))
remake = json.load(open(require(f'remake_structure{SUF}.json')))
sm = json.load(open(require(f'speaker_map_{GAME}.json')))
add = json.load(open(require(f'additional_voice_{GAME}.json')))
_sms_p = os.path.join(W, f'speaker_map_scene_{GAME}.json')
sm_scene = json.load(open(_sms_p, encoding='utf-8')) if os.path.exists(_sms_p) else {}
def rem_char(spk, scene=None):
    """说话人码 -> EVO角色码；优先场景条件映射（如 21000 女性广播 依场景为 088/386）"""
    if spk is None: return None
    s = str(spk)
    if scene is not None:
        sc_map = sm_scene.get(scene)
        if sc_map and s in sc_map: return sc_map[s]
    return sm.get(s)

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

def get_cands(char, norm):
    """候选查找: 主键(精确归一化) -> 二级键(去首尾省略号)。返回 (cands, 是否走了放松键)"""
    c = evo_key.get((char, norm), [])
    if c: return c, False
    n2 = _strip_edge(norm)
    if n2 and n2 != norm:
        return evo_key2.get((char, n2), []), True
    return [], False
evo_block_vids = {}
vid_text = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            evo_block_vids[(sc, fn, lab)] = {t['voice_id'] for t in blk if t.get('voice_id')}
            for t in blk:
                if t.get('voice_id'): vid_text[t['voice_id']] = clean_final(t['text'])

# script_data 兜底：py反编译/msg解析遗漏的语音（无块结构，仅入全局文本索引）
_known_vids = set(vid_text) | add_vids
_sd_p = resolve(f'script_data_{GAME}.json')
for x in (json.load(open(_sd_p, encoding='utf-8')) if _sd_p else []):
    v = (x.get('voice_id') or '').rstrip('V')
    n = clean_final(x.get('text') or '')
    if v and n and v not in _known_vids:
        vid_text[v] = clean_final(x['text'])
        evo_key[(v[:3], n)].append(v)
        _known_vids.add(v)

# 标点边缘放松二级索引：去首尾省略号/句读后的键，无条件收录（normalize 会把……转成'......'，须含ASCII句点）
_strip_edge = lambda n: n.strip('……….。、,，・~～―ー ')
evo_key2 = defaultdict(list)
for (c_, n_), vs in evo_key.items():
    n2 = _strip_edge(n_)
    if n2:
        evo_key2[(c_, n2)].extend(vs)

# 全局模糊索引：角色 -> [(norm, vid)]（TK/QS直配无模糊路径的变体兜底，如 'な、なに…' vs 'なに…'）
char_norms = defaultdict(list)
for (c_, n_), vs in evo_key.items():
    for v in vs:
        char_norms[c_].append((n_, v))
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
    for tok in toks:  # tok = (norm, char, ablk, 原始text, speaker)
        if tok[2] is not None:
            if prev is not None and tok[2] != prev and cur:
                segs.append(cur); cur = []
            prev = tok[2]
        cur.append(tok)
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
                n = clean_final(t['text']); char = rem_char(t['speaker'], sc)
                if n and char: remake_key[(char, n)].append((sc, fn, lab))
anchor_keys = {k for k in evo_key if len(evo_key[k])==1 and len(remake_key.get(k,[]))==1}

block_segs = {}
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            toks = [(clean_final(t['text']), rem_char(t['speaker'], sc), vid_to_evoblk.get(evo_key[(rem_char(t['speaker'], sc), clean_final(t['text']))][0]) if (rem_char(t['speaker'], sc), clean_final(t['text'])) in anchor_keys else None, t['text'], t['speaker']) for t in blk]
            segs = split_block(toks)
            info = []
            for seg in segs:
                a_blks = [tok[2] for tok in seg if tok[2] is not None]
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
                def rem_block_char(blk, scene):
                    c = Counter(rem_char(t['speaker'], scene) for t in blk if t.get('speaker') is not None)
                    return c.most_common(1)[0][0] if c else None
                r_char = rem_block_char(remake[rscene][rfn]['blocks'].get(rt, []), rscene)
                if r_char:
                    all_cand = list(valid)
                    if rtype == 'cond_false':
                        all_cand += [t for t, ty in evo_out[eexit] if ty == 'switch' and t in evo[escene][efn]['blocks']]
                    valid = [t for t in all_cand if evo_block_char(evo[escene][efn]['blocks'][t]) == r_char]
            if len(valid) == 1:
                blk_data = remake[rscene][rfn]['blocks'].get(rt, [])
                block_segs[rnxt] = [([(clean_final(t['text']), rem_char(t['speaker'], rscene), None, t['text'], t['speaker']) for t in blk_data], (escene, efn, valid[0]))]
                propagated.add(rnxt); aligned.add(rnxt); changed = True

def refine_segment(seg, evo_blk):
    """段内纯编辑距离（阈值 70），返回 {norm: best_vid}"""
    block_vids = evo_block_vids.get(evo_blk, set())
    by_char = defaultdict(list)
    for v in block_vids:
        by_char[v[:3]].append(v)
    result = {}
    for c, vids in by_char.items():
        seg_texts = [tok[0] for tok in seg if tok[1] == c and tok[2] is None]
        for norm in seg_texts:
            best = max(vids, key=lambda v: fuzz.ratio(norm, vid_text.get(v, '')))
            if fuzz.ratio(norm, vid_text.get(v, '')) >= 70:
                result[norm] = best
    return result

# ---------- 函数级对齐：函数内长句锚点投票到 EVO 函数 ----------
func_align = {}
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        if fn.startswith(('TK_', 'QS')): continue
        anchors_evofn = Counter()
        for lab, blk in f['blocks'].items():
            for t in blk:
                k = (rem_char(t.get('speaker'), sc), clean_final(t['text']))
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
# 结构救援索引：EVO块内(任意角色)文本 -> vid；additional 任意角色文本 -> vid
evo_block_lines = {}
for sc_, funcs_ in evo.items():
    for fn_, f_ in funcs_.items():
        for lab_, blk_ in f_['blocks'].items():
            evo_block_lines[(sc_, fn_, lab_)] = blk_
add_any = defaultdict(list)
for x in add:
    n = clean_final(x['text'])
    if n: add_any[n].append(x['voice_id'][:-1])
# norm -> {EVO角色码}（用于「同文本异角色」检测）
norm_chars = defaultdict(set)
for (c, n) in evo_key:
    norm_chars[n].add(c)

rescue_stat = Counter()
def rescue(norm, char, evo_blk):
    """无候选时的结构救援：块内文本唯一(角色放松) -> 块内模糊(>=80) -> 全局唯一(段外)"""
    if evo_blk is not None:
        blk = evo_block_lines.get(evo_blk, [])
        exact = [t['voice_id'] for t in blk if t.get('voice_id') and clean_final(t['text']) == norm]
        if len(exact) == 1:
            rescue_stat['块内文本唯一·角色放松'] += 1
            return exact[0], 'script|块内角色放松'
        best, bv = 0, None
        for t in blk:
            if not t.get('voice_id'): continue
            r = fuzz.ratio(norm, vid_text.get(t['voice_id'], ''))
            if r > best: best, bv = r, t['voice_id']
        if bv is not None and best >= 80:
            rescue_stat['块内模糊>=80'] += 1
            return bv, 'script|块内模糊'
    # 全局唯一(角色码+文本在全EVO仅一处)：QS测验等块对齐失败区域的强证据（含标点放松二级键；按去重后的vid集合判唯一）
    if char:
        gc = sorted({v for v in get_cands(char, norm)[0] if v not in add_vids})
        if len(gc) == 1:
            rescue_stat['全局唯一·段外'] += 1
            return gc[0], 'script|全局唯一段外'
    return None, None

out = []
for rblk, segs in block_segs.items():
    sc, fn, lab = rblk
    if lab not in remake[sc][fn]['blocks']: continue
    # TK_/QS: 结构与 Remake 重构差异大(QS测验分支重排)，走全局文本直配而非图匹配
    is_tk = fn.startswith(('TK_', 'QS'))
    for seg, evo_blk in segs:
        block_vids = evo_block_vids.get(evo_blk, set()) if evo_blk is not None else set()
        refine = refine_segment(seg, evo_blk) if evo_blk is not None else {}
        for norm, char, ablk, text, spk in seg:
            # 候选（script_data + additional_voice；标点边缘放松二级键兜底）
            cand, relaxed = get_cands(char, norm)
            cand = list(cand)
            cand_add = list(add_key.get((char, norm), []))
            if ablk is not None:
                # 锚点：角色码+文本唯一的 voice_id
                all_cand = cand
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
            if not uniq and norm:
                # 结构救援（无候选 -> 块内/场景上下文 -> 全局唯一，放松说话人或段约束）
                rv, rsrc = rescue(norm, char, evo_blk)
                if rv is not None and rv not in uniq:
                    uniq.append(rv)
            else:
                rsrc = None
            # 来源
            src = []
            for v in uniq:
                src.append('additional' if v in add_vids else 'script')
            if relaxed and uniq:
                src[-1] += '|标点放松'
            if rsrc is not None:
                src[-1] = src[-1] + '|' + rsrc.split('|')[1]
                rsrc = None
            if len(uniq) == 0:
                ty = '无候选'
            elif len(uniq) == 1:
                ty = '唯一'
            else:
                ty = '多候选'
            # 说话人对应标记（供人工校对）
            # ''=正常  对应=匹配且角色一致  说话人不对应=匹配但语音角色码不同
            # 同文本异角色=未匹配但该文本在 EVO 存在于其他角色(疑似说话人映射错/改派)
            # 说话人未映射=说话人码无映射但仍有匹配(救援行常见)
            spk_match = ''
            if uniq and char:
                spk_match = '对应' if all(v[:3] == char for v in uniq) else f'说话人不对应(语音角色{",".join(sorted({v[:3] for v in uniq}))})'
            elif uniq and not char:
                spk_match = f'说话人未映射(语音角色{",".join(sorted({v[:3] for v in uniq}))})'
            elif not uniq and norm:
                others = sorted(norm_chars.get(norm, set()) - ({char} if char else set()))
                if others:
                    spk_match = f'同文本异角色({",".join(others[:4])})'
            eblk = vid_to_evoblk.get(uniq[0]) if uniq else None
            out.append((sc, fn, lab, text, spk, char, uniq[0] if uniq else '', '|'.join(uniq), ty, '|'.join(src), spk_match,
                        eblk[0] if eblk else '', eblk[1] if eblk else '', eblk[2] if eblk else ''))

# ---------- multi 夹逼：用邻行已定 vid 的末四位连续性消歧 ----------
# 同时覆盖: 无候选但全局存在段外差分候选(2-4条)的行（QS测验等块对齐失败区域）
bracket_stat = Counter()
seq_by_fn = defaultdict(list)
for i, r in enumerate(out):
    seq_by_fn[(r[0], r[1])].append(i)
for (sc, fn), seq in seq_by_fn.items():
    for pos, i in enumerate(seq):
        ty_ = out[i][8]
        if ty_ == '多候选':
            cands = [c for c in out[i][7].split('|') if c]
        elif ty_ == '无候选' and out[i][5]:
            # 泛用句候选可达8+，场景/最近邻规则本身即安全闸，上限放到12
            cands = sorted({v for v in get_cands(out[i][5], clean_final(out[i][3]))[0] if v not in add_vids and len(v) == 10})
            if not (2 <= len(cands) <= 12): continue
        else:
            continue
        prev_v = next((out[j][6] for j in reversed(seq[:pos]) if out[j][8] == '唯一' and out[j][6]), None)
        next_v = next((out[j][6] for j in seq[pos+1:] if out[j][8] == '唯一' and out[j][6]), None)
        pick = None; how = None
        if prev_v and next_v and prev_v[:6] == next_v[:6]:
            lo, hi = int(prev_v[6:]), int(next_v[6:])
            between = [c for c in cands if c[:6] == prev_v[:6] and lo < int(c[6:]) < hi]
            if len(between) == 1:
                pick, how = between[0], '夹逼'
        if pick is None and (prev_v or next_v):
            # 场景=vid[3:6]（不含角色）——对话块邻行角色交替，用[:6]会把不同角色同场景的候选全部漏掉
            scenes = {v[3:6] for v in (prev_v, next_v) if v}
            in_scene = [c for c in cands if c[3:6] in scenes]
            if len(in_scene) == 1:
                pick, how = in_scene[0], '场景唯一'
            elif len(in_scene) >= 2 and (ty_ == '无候选' or not out[i][1].startswith(('TK_', 'QS'))):
                # 差分对均在区间内：取与邻行已定 vid 最近的（最近邻）
                # 仅限: 无候选行(段外差分,验证97%) 与 非TK/QS 多候选(验证100%)；
                # TK/QS 多候选经验证仅83%——差分take紧邻无法区分，保留多候选待人工
                anchor = prev_v or next_v
                in_scene.sort(key=lambda c: abs(int(c[6:]) - int(anchor[6:])))
                pick, how = in_scene[0], '最近邻'
        if pick is not None:
            src_l = out[i][9].split('|') if out[i][9] else []
            src_new = '|'.join(src_l + [how + ('·段外' if ty_ == '无候选' else '')])
            eblk = vid_to_evoblk.get(pick)
            out[i] = (*out[i][:6], pick, out[i][7], '唯一', src_new, out[i][10],
                      eblk[0] if eblk else '', eblk[1] if eblk else '', eblk[2] if eblk else '')
            bracket_stat[how + ('·段外' if ty_ == '无候选' else '')] += 1

# ---------- 块内连续段救援：整块无候选的行，全局候选按台词顺序串成严格递增 vid 链 ----------
# 针对 MayaEvent/整块对齐失败的事件：EVO 侧为同一角色的连续录音段（如 001019:403-411）
# 候选不限角色（说话人常未映射），但要求全链同一角色前缀、严格递增、间隔<=12、覆盖块内全部待解行
run_stat = Counter()
seq_used = defaultdict(set)   # 台词文本序列 -> 各副本已用 vids（复制块排除已用后链到另一遍平行录音）
norm_all_vids = defaultdict(list)
for (c_, n_), vs in evo_key.items():
    norm_all_vids[n_].extend(v for v in vs if len(v) == 10)   # 6位系统音无场景行号, 不参与链
blk_rows = defaultdict(list)
for i, r in enumerate(out):
    blk_rows[(r[0], r[1], r[2])].append(i)
for (sc, fn, lab), idxs in blk_rows.items():
    pend = []   # [(i, cands, char)] char=该行映射的EVO角色码(可为None)
    for i in idxs:
        r = out[i]
        if r[8] != '无候选': continue
        cands = sorted({v for v in norm_all_vids.get(clean_final(r[3]), []) if v not in add_vids})
        if cands: pend.append((i, cands, r[5] or None))
    fuzzy_chain = False
    if len(pend) < 3:
        # 精确候选不足的块（变体文本）：同角色 top 模糊候选(≥85)建链，走同一套场景/尾号/唯一性闸
        pend = []
        for i in idxs:
            r = out[i]
            if r[8] != '无候选' or not r[5]: continue
            n_i = clean_final(r[3])
            if not n_i: continue
            scored = sorted(char_norms.get(r[5], ()), key=lambda x: -fuzz.ratio(n_i, x[0]))[:12]
            cands = sorted({v for n_, v in scored if fuzz.ratio(n_i, n_) >= 85 and v not in add_vids and len(v) == 10})
            if cands: pend.append((i, cands, r[5] or None))
        if len(pend) < 2: continue
        fuzzy_chain = True
    seq_key = tuple(out[i][3] for i, _, _ in pend)
    used = seq_used.get(seq_key, set())

    def pick_fit(cands, scene3, lo, hi, char, forward):
        """窗口内候选: 优先角色匹配, 再按行号取最贴近前一行的"""
        fits = [c for c in cands if c[3:6] == scene3 and lo < int(c[6:]) < hi]
        if not fits: return None
        if char:
            pref = [c for c in fits if c[:3] == char]
            if pref: fits = pref
        return (min if forward else max)(fits, key=lambda c: int(c[6:]))

    def try_chain(pl):
        """对给定 pend 串链：锚点(候选最少行,说话人优先排序) + 双向延伸 + 完整链唯一闸"""
        a_ = min(range(len(pl)), key=lambda k: len(pl[k][1]))
        if len(pl[a_][1]) > 8 or not pl[a_][1]: return None
        anchor_char = pl[a_][2]
        anchors = sorted(pl[a_][1], key=lambda c: c[:3] != anchor_char if anchor_char else False)
        complete_ = []
        for anchor in anchors:
            scene3 = anchor[3:6]
            aline = int(anchor[6:])
            assign = {a_: anchor}
            ok = True
            prev_line = aline; widened = False
            for k in range(a_ + 1, len(pl)):
                i_, cands_, char_ = pl[k]
                nxt = pick_fit(cands_, scene3, prev_line, prev_line + (61 if not widened else 26), char_, True)
                if nxt is None and not widened:
                    widened = True
                    nxt = pick_fit(cands_, scene3, prev_line, prev_line + 61, char_, True)
                if nxt is None: ok = False; break
                assign[k] = nxt; prev_line = int(nxt[6:])
            if ok:
                hi_line = aline; widened_b = False
                for k in range(a_ - 1, -1, -1):
                    i_, cands_, char_ = pl[k]
                    nxt = pick_fit(cands_, scene3, hi_line - (61 if not widened_b else 26), hi_line, char_, False)
                    if nxt is None and not widened_b:
                        widened_b = True
                        nxt = pick_fit(cands_, scene3, hi_line - 61, hi_line, char_, False)
                    if nxt is None: ok = False; break
                    assign[k] = nxt; hi_line = int(nxt[6:])
            if ok and len(assign) == len(pl):
                complete_.append([(pl[k][0], assign[k]) for k in sorted(assign)])
        return complete_[0] if len(complete_) == 1 else None

    # 副本块策略：先排除前副已用 vids 链另一遍平行录音(·复制)；链不成则复用同一录音段(·复用)
    best = None; src_tag = 'script|块内连续段' + ('·模糊链' if fuzzy_chain else '')
    if used:
        pend_ex = [(i, [c for c in cands if c not in used], ch) for i, cands, ch in pend]
        if all(c for _, c, _ in pend_ex):
            best = try_chain(pend_ex)
            if best: src_tag += '·复制'
    if best is None:
        best = try_chain(pend)
        if best and used: src_tag += '·复用'

    if best and len(best) >= 3:
        seq_used[seq_key].update(v for _, v in best)
        chained_pos = {i: v for i, v in best}
        for i, v in best:
            eblk = vid_to_evoblk.get(v)
            # 链内角色校验：映射说话人与所选语音角色不符的行打标（Remake改派 or 错配，交人工）
            char_i = out[i][5]
            sm_ = out[i][10]
            if char_i and v[:3] != char_i:
                sm_ = f'链内角色不符(语音角色{v[:3]})'
                run_stat['链内角色不符'] += 1
            out[i] = (*out[i][:6], v, out[i][7], '唯一', src_tag, sm_,
                      eblk[0] if eblk else '', eblk[1] if eblk else '', eblk[2] if eblk else '')
        run_stat[src_tag.split('|')[1]] += len(best)
        # 改写行的位置约束模糊：块内无精确候选的行，在两侧已定 vid 夹出的同场景窗口内模糊匹配
        order = [i for i in idxs]
        for pos, i in enumerate(order):
            if out[i][8] != '无候选' or i in chained_pos: continue
            if not clean_final(out[i][3]): continue
            prev_c = next((chained_pos[j] for j in reversed(order[:pos]) if j in chained_pos), None)
            next_c = next((chained_pos[j] for j in order[pos+1:] if j in chained_pos), None)
            scene3 = (prev_c or next_c or [None]*10)[3:6] if (prev_c or next_c) else None
            if not scene3: continue
            lo = int(prev_c[6:]) if prev_c else int(next_c[6:]) - 26
            hi = int(next_c[6:]) if next_c else int(prev_c[6:]) + 26
            if prev_c and next_c and hi - lo > 80: continue   # 窗口过大风险高
            win = [(int(v[6:]), v) for v in vid_text
                   if v[3:6] == scene3 and lo < int(v[6:]) < hi and v not in add_vids]
            if not win: continue
            norm_i = clean_final(out[i][3])
            scored = sorted(((fuzz.ratio(norm_i, vid_text[v]), abs(ln - (lo+hi)//2), v) for ln, v in win),
                            key=lambda x: (-x[0], x[1]))
            best_r, _, bv = scored[0]
            second_r = scored[1][0] if len(scored) > 1 else -1
            if best_r < 80: continue
            if best_r == second_r and vid_text[bv] != vid_text[scored[1][2]]:
                continue   # 两个不同文本同分，歧义放弃
            char_i = out[i][5]
            sm_ = out[i][10]
            if char_i and bv[:3] != char_i:
                sm_ = f'链内角色不符(语音角色{bv[:3]})'
                run_stat['链内角色不符'] += 1
            eblk = vid_to_evoblk.get(bv)
            out[i] = (*out[i][:6], bv, out[i][7], '唯一', 'script|块内连续段·模糊', sm_,
                      eblk[0] if eblk else '', eblk[1] if eblk else '', eblk[2] if eblk else '')
            run_stat['块内连续段·模糊'] += 1

OUT = os.path.join(W, f'my_match_result{SUF}.csv')
with open(OUT, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['Scene', 'Function', 'Block', 'RemakeVoiceText', 'Speaker', 'SpeakerChar', 'MyVoiceId', 'Candidates', 'MatchType', 'Source', 'SpeakerMatch', 'EvoScene', 'EvoFunction', 'EvoBlock'])
    for row in out:
        w.writerow(row)

from collections import Counter
ty = Counter(r[8] for r in out)
print(f'[{GAME}] 总台词: {len(out)} -> {os.path.basename(OUT)}')
print(f'匹配类型: {dict(ty)}')
spk = Counter(r[10].split('(')[0] for r in out if r[10])
print(f'说话人标记: {dict(spk)}')
uniq = sum(1 for r in out if r[8] == "唯一")
print(f'唯一确定: {uniq} = {uniq/max(len(out),1)*100:.1f}%')
if rescue_stat: print(f'结构救援: {dict(rescue_stat)}')
if bracket_stat: print(f'multi夹逼: {dict(bracket_stat)}')
if run_stat: print(f'块内连续段: {dict(run_stat)}')

#!/usr/bin/env python3
"""应用自动校对裁定到 match_result_detailed.csv，并产出校对信息汇总。

应用规则:
  OK          -> MatchType 不变，Annotation 追加「自动校对:OK」
  WRONG       -> MyVoiceId/Old* 换成 correct_vid（multi 行同步清 Candidates），Annotation 追加校对依据
  FOUND       -> 未匹配行补上匹配（MatchType=matched，Old* 填全），Annotation 记录寻配证据
  CANDIDATES  -> Candidates 列追加候选（若无），MatchType 保持 multi，Annotation 记录
  SUSPECT     -> 只追加标注，不动匹配
  NO_VOICE / UNRESOLVED -> 不动匹配，Annotation 追加（确认 EVO 无此语音，后续可不再排查）

同时输出:
  apply_summary.json      应用统计 + 全部非 OK 裁定的详情（供人工复核）
  *_corrected.csv         修正后的详表（原表不动）
"""
import csv, json, os, sys
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = (sys.argv[1].lower() if len(sys.argv) > 1 else 'sc')
SUF = '' if GAME == 'fc' else f'_{GAME}'
os.environ.setdefault('SKYSA_HOME', os.path.join(HERE, 'data'))
sys.path.insert(0, HERE)
from paths import resolve

det_p = resolve(f'match_result{SUF}_detailed.csv')
if not det_p:
    det_p = resolve('match_result_sc_detailed.csv')
det = list(csv.DictReader(open(det_p, encoding='utf-8')))
# Candidates 在 my_match_result (s4 输出) 中，按 RemakeVoiceID 关联
_mine_p = resolve(f'my_match_result{SUF}.csv')
mine_by_id = {}   # RemakeVoiceID -> s4行
if _mine_p:
    import csv as _csv
    for m in _csv.DictReader(open(_mine_p, encoding='utf-8')):
        mine_by_id.setdefault(m.get('RemakeVoiceId') or m.get('RemakeVoiceID'), m)
vp = os.path.join(HERE, 'review_agent', 'review_pack', 'verdicts.jsonl')
verdicts = [json.loads(l) for l in open(vp, encoding='utf-8') if l.strip()]
evo = json.load(open(resolve(f'evo_structure{SUF}.json'), encoding='utf-8'))

# vid -> (scene, func, block, text)   evo_structure + additional 两池
vid_info = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('voice_id'):
                    vid_info[t['voice_id']] = (sc, fn, lab, t['text'])
add_text = {}
for x in json.load(open(resolve(f'additional_voice_{GAME}.json'), encoding='utf-8')):
    _v = x['voice_id']
    _v = _v[2:] if _v.startswith('ch') else _v
    _v = _v[:-1] if _v.endswith('V') else _v
    if _v:
        add_text[_v] = x['text']
        vid_info.setdefault(_v, ('additional', '', '', x['text']))

# RemakeVoiceID -> 行索引（详表主键唯一）
row_by_id = {r['RemakeVoiceID']: r for r in det}

applied = Counter()
by_id = defaultdict(list)
for v in verdicts:
    by_id[v['RemakeVoiceID']].append(v)

summary = {'applied': Counter(), 'skipped': [], 'wrong': [], 'found': [], 'candidates': [], 'suspect': []}

for vid, vs in by_id.items():
    r = row_by_id.get(vid)
    if r is None:
        summary['skipped'].append({'RemakeVoiceID': vid, 'reason': '主表中无此ID'})
        continue
    # 同ID多条裁定: 有 WRONG 优先，其次 FOUND，再次取最后一条
    priority = {'WRONG': 0, 'FOUND': 1, 'SUSPECT': 2, 'CANDIDATES': 3, 'NO_VOICE': 3.5, 'UNRESOLVED': 4, 'OK': 5}
    v = sorted(vs, key=lambda x: priority.get(x['verdict'], 9))[0]
    tag = f"自动校对[{v['verdict']}]"
    anno_add = f"{tag} {v.get('reason', '')[:120]}"

    if v['verdict'] == 'OK':
        applied['OK'] += 1
        r['Annotation'] = (r['Annotation'] + '; ' + anno_add).strip('; ')
    elif v['verdict'] == 'WRONG':
        cv = v.get('correct_vid', '')
        info = vid_info.get(cv)
        if not info:
            summary['skipped'].append({'RemakeVoiceID': vid, 'reason': f'correct_vid {cv} 不在EVO结构'})
            continue
        r['OldVoiceFilename'] = 'ch' + cv + 'V'
        r['OldVoiceText'] = info[3]
        r['EvoScene'], r['EvoFunction'], r['EvoBlock'] = info[0], info[1], info[2]
        # Candidates 属 s4/my_match_result 体系; 详表只更正匹配结果
        mine_by_id.pop(vid, None)
        r['MatchType'] = 'matched'
        r['Annotation'] = (r['Annotation'] + '; ' + anno_add).strip('; ')
        applied['WRONG'] += 1
        summary['wrong'].append({'RemakeVoiceID': vid, 'old': '', 'new': cv,
                                 'scene': r['RemakeScenaScriptFilename'], 'func': r['RemakeFunction'],
                                 'text': r['RemakeVoiceText'], 'reason': v.get('reason', '')})
        if summary['wrong'][-1]['old'] == '':
            summary['wrong'][-1]['old'] = '见Annotation'
    elif v['verdict'] == 'FOUND':
        cv = v.get('correct_vid', '')
        info = vid_info.get(cv)
        if not info:
            summary['skipped'].append({'RemakeVoiceID': vid, 'reason': f'correct_vid {cv} 不在EVO结构'})
            continue
        ent = None
        sd_p = resolve(f'script_data_{GAME}.json')
        if sd_p:
            for x in json.load(open(sd_p, encoding='utf-8')):
                if (x.get('voice_id') or '').rstrip('V') == cv:
                    ent = x
                    if x.get('script_id', -1) >= 0:
                        break
        if ent and ent.get('script_id', -1) >= 0:
            r['MatchType'] = 'matched'
            r['OldScriptId'] = str(ent['script_id'])
            r['OldCharacterId'] = ent.get('character_id', '')
        else:
            r['MatchType'] = 'voiceonly'
        r['OldVoiceFilename'] = 'ch' + cv + 'V'
        r['OldVoiceText'] = info[3]
        r['EvoScene'], r['EvoFunction'], r['EvoBlock'] = info[0], info[1], info[2]
        # additional 的 vid -> voiceonly(不在evo结构)
        r['Annotation'] = (r['Annotation'] + '; ' + anno_add).strip('; ')
        applied['FOUND'] += 1
        summary['found'].append({'RemakeVoiceID': vid, 'vid': cv,
                                 'scene': r['RemakeScenaScriptFilename'], 'func': r['RemakeFunction'],
                                 'text': r['RemakeVoiceText'], 'reason': v.get('reason', '')})
    elif v['verdict'] == 'CANDIDATES':
        cands = [c.get('vid') for c in v.get('candidates', []) if c.get('vid')]
        # 候选清单记录在 summary（详表无 Candidates 列，由 s4/my_match_result 维护）
        r['Annotation'] = (r['Annotation'] + '; ' + anno_add).strip('; ')
        applied['CANDIDATES'] += 1
        summary['candidates'].append({'RemakeVoiceID': vid, 'candidates': cands, 'note': '候选见summary, 详表无此列'})
    elif v['verdict'] == 'SUSPECT':
        r['Annotation'] = (r['Annotation'] + '; ' + anno_add).strip('; ')
        applied['SUSPECT'] += 1
        summary['suspect'].append({'RemakeVoiceID': vid, 'text': r['RemakeVoiceText'], 'reason': v.get('reason', '')})
    else:   # NO_VOICE / UNRESOLVED
        applied[v['verdict']] += 1
        r['Annotation'] = (r['Annotation'] + '; ' + anno_add).strip('; ')

out_p = os.path.join(HERE, 'data', f'match_result{SUF}_detailed_corrected.csv')
with open(out_p, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(det[0].keys()))
    w.writeheader(); w.writerows(det)

# 汇总
summary['applied'] = dict(applied)
summary['MatchType_修正后'] = dict(Counter(r['MatchType'] for r in det))
sp = os.path.join(HERE, 'data', f'apply_summary{SUF}.json')
json.dump(summary, open(sp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

print(f'应用裁定: {dict(summary["applied"])} (共 {sum(summary["applied"].values())}/{len(verdicts)} 条)')
print(f'修正后 MatchType: {summary["MatchType_修正后"]}')
print(f'WRONG 修正: {len(summary["wrong"])}  FOUND 补配: {len(summary["found"])}  CANDIDATES 更新: {len(summary["candidates"])}')
print(f'跳过: {len(summary["skipped"])}')
print(f'输出: {out_p}')
print(f'汇总: {sp}')

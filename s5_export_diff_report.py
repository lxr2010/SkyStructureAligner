#!/usr/bin/env python3
"""导出所有「错配 + 比gt少匹配」台词的完整结构/控制流/位置/类型，供检查。"""
import json, csv, sys
import os
from collections import defaultdict, Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'TrailsInTheSkyRemakeScriptAligner'))
from synonyms import normalize
W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

evo = json.load(open(f'{W}/evo_structure.json'))
remake = json.load(open(f'{W}/remake_structure.json'))
sm = json.load(open(f'{W}/speaker_map_fc.json'))
def rem_char(spk): return sm.get(str(spk)) if spk is not None else None

# gt
sd = json.load(open(f'{W}/script_data_fc.json'))
sid_to_vid = {x['script_id']: x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id'] for x in sd}
vid_to_text = {x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id']: x['text'] for x in sd}
vid_to_scene = {x['voice_id'][:-1] if x['voice_id'].endswith('V') else x['voice_id']: x['source_file'].replace('.txt','') for x in sd}
rows = list(csv.DictReader(open(f'{W}/match_result.csv', encoding='utf-8-sig')))
gt_norm_to_vid = defaultdict(set)
for r in rows:
    if r['MatchType'] == 'matched' and r['OldScriptId']:
        vid = sid_to_vid.get(int(r['OldScriptId']))
        if vid: gt_norm_to_vid[normalize(r['RemakeVoiceText'])].add(vid)

# vid -> EVO 块
vid_to_evoblk = {}
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('voice_id'): vid_to_evoblk[t['voice_id']] = (sc, fn, lab)

# Remake 台词 -> 块归属 + speaker
norm_to_rblk = defaultdict(list)
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                norm_to_rblk[normalize(t['text'])].append((sc, fn, lab, t['speaker']))

# 边索引（前驱/后继）
def edges_of(struct, sc, fn, lab):
    preds = []; succs = []
    f = struct[sc][fn]
    for e in f['edges']:
        if e['t'] == lab: preds.append((e['f'], e['type']))
        if e['f'] == lab: succs.append((e['t'], e['type']))
    return preds, succs

my_rows = list(csv.DictReader(open(f'{W}/my_match_result.csv', encoding='utf-8-sig')))
# 按 norm 聚合我的结果
my_by_norm = {}
for r in my_rows:
    my_by_norm[normalize(r['RemakeVoiceText'])] = r

out = []
def add(row):
    out.append(row)

def make_row(n, r, gt, kind, desc):
    row = {'类型': kind}
    row['RemakeText'] = r['RemakeVoiceText']
    row['RemakeSpeaker'] = r['Speaker']
    row['RemakeChar'] = r['SpeakerChar']
    rb = norm_to_rblk.get(n, [])
    if rb:
        sc, fn, lab, spk = rb[0]
        row['RemakeScene'] = sc
        row['RemakeFunc'] = fn
        row['RemakeBlock'] = lab
        row['RemakeFuncType'] = 'TK' if fn.startswith('TK_') else '非TK'
        preds, succs = edges_of(remake, sc, fn, lab)
        row['Remake前驱'] = ';'.join(f'{f}:{t}' for f, t in preds)
        row['Remake后继'] = ';'.join(f'{t}:{ty}' for t, ty in succs)
    row['MatchType'] = r['MatchType']
    row['MyVoiceId'] = r['MyVoiceId']
    row['MyCandidates'] = r['Candidates']
    gt_vids = sorted(gt)
    row['GtVoiceId'] = '|'.join(gt_vids)
    row['GtText'] = '|'.join(vid_to_text.get(v, '') for v in gt_vids)
    for v in gt_vids:
        eblk = vid_to_evoblk.get(v)
        if eblk:
            esc, efn, elab = eblk
            row['GtScene'] = vid_to_scene.get(v, esc)
            row['GtFunc'] = efn
            row['GtBlock'] = elab
            preds, succs = edges_of(evo, esc, efn, elab)
            row['Gt前驱'] = ';'.join(f'{f}:{t}' for f, t in preds)
            row['Gt后继'] = ';'.join(f'{t}:{ty}' for t, ty in succs)
            break
    return row

# 1) 错配：我唯一但 ≠ gt
n_mismatch = 0
for r in my_rows:
    n = normalize(r['RemakeVoiceText'])
    if r['MatchType'] != '唯一' or not r['MyVoiceId']: continue
    gt = gt_norm_to_vid.get(n, set())
    if not gt or r['MyVoiceId'] in gt: continue
    n_mismatch += 1
    add(make_row(n, r, gt, '错配', '我唯一但≠gt'))

# 2) 比gt少匹配：gt有但我无候选/多候选
n_missing = 0
for n, gt in gt_norm_to_vid.items():
    r = my_by_norm.get(n)
    if not r: continue
    if r['MatchType'] == '唯一' and r['MyVoiceId'] in gt: continue  # 已一致
    if r['MatchType'] == '唯一' and r['MyVoiceId'] not in gt: continue  # 错配已统计
    # 少匹配：gt有，但我无候选或多候选不含gt
    if r['MatchType'] == '无候选' or (r['MatchType'] == '多候选' and not (set(r['MyVoiceId'].split('|')) & gt)):
        n_missing += 1
        add(make_row(n, r, gt, '少匹配', 'gt有但我无'))

# 写 CSV
fields = ['类型','RemakeText','RemakeSpeaker','RemakeChar','RemakeScene','RemakeFunc','RemakeBlock','RemakeFuncType',
          'Remake前驱','Remake后继','MatchType','MyVoiceId','MyCandidates',
          'GtVoiceId','GtText','GtScene','GtFunc','GtBlock','Gt前驱','Gt后继']
with open(f'{W}/diff_export.csv', 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(fields)
    for row in out:
        w.writerow([row.get(k, '') for k in fields])

print(f'错配 {n_mismatch}, 少匹配 {n_missing}, 共 {len(out)} 条')
print(f'已导出 diff_export.csv')

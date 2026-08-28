#!/usr/bin/env python3
"""临时：导出「对手(最新版Aligner)匹配、我们无候选」的行，供人工裁定。

一行一句台词：我方信息 + 对方匹配(vid/角色/EVO台词/EVO结构/相似度) + Verdict。
用法: uv run python tmp_gap_review_sc.py
输出: W/gap_review_sc.csv
"""
import csv, json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'TrailsInTheSkyRemakeScriptAligner'))
from synonyms import normalize
from rapidfuzz import fuzz

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVO_TYPE = {'T': '主线剧情场景', 'C': '城镇/街区场景', 'E': '事件/演出场景',
            'R': '街道/野外场景', 'A': '系统/特殊场景', 'S': '系统'}

def cf(t):
    return normalize(t).replace('\u3046\u3099', 'う').replace('ヴ', 'う') if t else ''

ours = list(csv.DictReader(open(os.path.join(W, 'match_result_sc_detailed.csv'), encoding='utf-8')))
theirs = list(csv.DictReader(open(os.path.join(W, 'TrailsInTheSkyRemakeScriptAligner-latest', 'match_result_sc.csv'), encoding='utf-8-sig')))
def key(r): return (r['RemakeScenaScriptFilename'], r['RemakeScenaScriptLineno'])
def vid(r):
    f = r.get('OldVoiceFilename', '')
    return f[2:-1] if f.startswith('ch') else f
t = {key(r): r for r in theirs}

vid_text, vid_to_evoblk = {}, {}
for sc, funcs in json.load(open(os.path.join(W, 'evo_structure_sc.json'), encoding='utf-8')).items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for x in blk:
                if x.get('voice_id'):
                    vid_text[x['voice_id']] = x['text']
                    vid_to_evoblk[x['voice_id']] = (sc, fn, lab)
for x in json.load(open(os.path.join(W, 'additional_voice_sc.json'), encoding='utf-8')):
    vid_text.setdefault(x['voice_id'][:-1], x['text'])

COLS = ['RemakeVoiceID', 'RemakeScenaScriptFilename', 'RemakeScenaScriptLineno', 'RemakeFunction', 'RemakeBlock',
        'BlockAlert', 'RemakeVoiceText', 'RemakeVoiceTranslation', 'OurMatchType', 'SpeakerCheck',
        'TheirVoiceId', 'TheirVoiceChar', 'TheirVoiceText', 'TheirMatchType',
        'TheirEvoScene', 'TheirEvoFunction', 'TheirEvoBlock', 'TheirEvoNote',
        'Similarity', 'Verdict']
out = []
for r in ours:
    if r['OldVoiceFilename']:
        continue
    tr = t.get(key(r))
    tv = vid(tr) if tr else ''
    if not tv:
        continue
    b = vid_to_evoblk.get(tv)
    vt = vid_text.get(tv, '')
    row = dict.fromkeys(COLS, '')
    for k in ('RemakeVoiceID', 'RemakeScenaScriptFilename', 'RemakeScenaScriptLineno',
              'RemakeFunction', 'RemakeVoiceText', 'RemakeVoiceTranslation', 'SpeakerCheck'):
        row[k] = r[k]
    row['RemakeBlock'] = r.get('RemakeBlock', '')
    row['OurMatchType'] = r['MatchType']
    row['TheirVoiceId'] = tv
    row['TheirVoiceChar'] = tv[:3]
    row['TheirVoiceText'] = vt.replace('\n', '\\n')
    row['TheirMatchType'] = tr['MatchType']
    if b:
        row['TheirEvoScene'], row['TheirEvoFunction'], row['TheirEvoBlock'] = b
        letter = b[0][0] if b[0][0].isalpha() else '?'
        row['TheirEvoNote'] = f'{letter}={EVO_TYPE.get(letter, "其他")}'
    row['Similarity'] = round(fuzz.ratio(cf(r['RemakeVoiceText']), cf(vt)), 1)
    out.append(row)

# ---- 块级告警: 同一 Remake 块内相似度>=88 的 gap 行 >=3 即标记待查 ----
from collections import defaultdict
blk_hi = defaultdict(int)
for r in out:
    if r['Similarity'] >= 88:
        blk_hi[(r['RemakeScenaScriptFilename'], r['RemakeFunction'], r['RemakeBlock'])] += 1
for r in out:
    n = blk_hi.get((r['RemakeScenaScriptFilename'], r['RemakeFunction'], r['RemakeBlock']), 0)
    if n >= 3:
        r['BlockAlert'] = f'同块高相似x{n}'

OUT = os.path.join(W, 'gap_review_sc.csv')
with open(OUT, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader(); w.writerows(out)

# ---- 块级汇总表: 告警块一览(对方vid是否连续/同场景——判断是否可按连续段回收) ----
BLK_COLS = ['RemakeScenaScriptFilename', 'RemakeFunction', 'RemakeBlock', 'GapRows', 'HiSimRows',
            'AvgSim', 'TheirVidRange', 'TheirScenes', 'SpeakerChecks', 'SampleText']
blk_rows = defaultdict(list)
for r in out:
    blk_rows[(r['RemakeScenaScriptFilename'], r['RemakeFunction'], r['RemakeBlock'])].append(r)
alerts = []
for k, rs in blk_rows.items():
    hi = [r for r in rs if r['Similarity'] >= 88]
    if len(hi) < 3: continue
    vids = sorted(r['TheirVoiceId'] for r in hi)
    tail = [int(v[6:]) for v in vids if len(v) == 10]
    rng = f'{min(tail)}-{max(tail)}' if tail else ''
    contiguous = tail and max(tail) - min(tail) <= len(tail) * 2   # 末四位近似连续
    alerts.append({
        'RemakeScenaScriptFilename': k[0], 'RemakeFunction': k[1], 'RemakeBlock': k[2],
        'GapRows': len(rs), 'HiSimRows': len(hi),
        'AvgSim': round(sum(r['Similarity'] for r in hi) / len(hi), 1),
        'TheirVidRange': (vids[0][:6] + ':' + rng) + ('(连续·可整块回收)' if contiguous else ''),
        'TheirScenes': ','.join(sorted({r['TheirEvoScene'] for r in hi if r['TheirEvoScene']})),
        'SpeakerChecks': ','.join(sorted({r['SpeakerCheck'].split('(')[0] for r in hi if r['SpeakerCheck']})) or '无标记',
        'SampleText': hi[0]['RemakeVoiceText'][:20],
    })
alerts.sort(key=lambda a: (-a['HiSimRows'], -a['AvgSim']))
BLKOUT = os.path.join(W, 'gap_block_review_sc.csv')
with open(BLKOUT, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=BLK_COLS)
    w.writeheader(); w.writerows(alerts)

from collections import Counter
sim = Counter()
for r in out:
    s = r['Similarity']
    sim['=100(同文)' if s == 100 else '>=80(近义)' if s >= 80 else '>=50' if s >= 50 else '<50(远/LLM)'] += 1
print(f'仅对手匹配(我方无候选): {len(out)} 行 -> {os.path.basename(OUT)}')
print(f'我方文本 vs 对方语音文本 相似度分布: {dict(sim)}')
print(f'对方MatchType: {dict(Counter(r["TheirMatchType"] for r in out))}')
n_alert_rows = sum(1 for r in out if r['BlockAlert'])
print(f'块级告警: {len(alerts)} 个块 / {n_alert_rows} 行(同块>=3行且sim>=88) -> {os.path.basename(BLKOUT)}')

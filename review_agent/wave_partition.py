#!/usr/bin/env python3
"""波次分区器(主智能体编排用)：把待办块按优先级切成 K 份互不重叠的子智能体任务。

用法:
  uv run python wave_partition.py --agents 4 --wave 1 [--target 120] [--cap 165]
输出 JSON: {"pending_blocks":..,"pending_lines":..,"assignments":[{"agent":"W1A1","lines":..,"blocks":[[scene,func,lines],..]},..]}

已完成判定: review_pack/verdicts.jsonl 里出现过该行 RemakeVoiceID 即视为已裁定(去重)。
块排序与 rt.py todo 一致: flags>0 优先, 部分匹配优先, flags 多者优先。
"""
import csv, json, os, sys, argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import resolve

ap = argparse.ArgumentParser()
ap.add_argument('--agents', type=int, default=4)
ap.add_argument('--wave', default='1')
ap.add_argument('--target', type=int, default=100, help='单智能体目标行数')
ap.add_argument('--cap', type=int, default=125, help='单智能体行数上限(超限大块单独成任务)')
args = ap.parse_args()

det = list(csv.DictReader(open(resolve('match_result_sc_detailed.csv'), encoding='utf-8')))
vp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'review_pack', 'verdicts.jsonl')
done = set()
if os.path.exists(vp):
    for line in open(vp, encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        try:
            done.add(str(json.loads(line)['RemakeVoiceID']))
        except Exception:
            pass

blocks = defaultdict(list)
for r in det:
    blocks[(r['RemakeScenaScriptFilename'], r['RemakeFunction'])].append(r)

pending = []
for (s, f), rows in blocks.items():
    miss = sum(1 for r in rows if str(r['RemakeVoiceID']) not in done)
    if not miss:
        continue
    flags = sum(1 for r in rows if r['VoiceReuseAlert'] or r['SpeakerCheck'])
    matched = sum(1 for r in rows if r['OldVoiceFilename'])
    pending.append({'scene': s, 'func': f, 'lines': len(rows), 'matched': matched, 'flags': flags})

pending.sort(key=lambda b: (b['flags'] == 0, b['matched'] == 0, -b['flags']))

bins, cur, cur_lines = [], [], 0
for b in pending:
    if b['lines'] > args.cap:
        if cur:
            bins.append(cur)
            cur, cur_lines = [], 0
        bins.append([b])
        continue
    if cur and cur_lines + b['lines'] > args.cap:
        bins.append(cur)
        cur, cur_lines = [], 0
    cur.append(b)
    cur_lines += b['lines']
    if cur_lines >= args.target:
        bins.append(cur)
        cur, cur_lines = [], 0
if cur:
    bins.append(cur)
bins = bins[:args.agents]

assignments = [{'agent': f'W{args.wave}A{i}',
                'lines': sum(b['lines'] for b in bin_),
                'blocks': [[b['scene'], b['func'], b['lines']] for b in bin_]}
               for i, bin_ in enumerate(bins, 1)]
print(json.dumps({'pending_blocks': len(pending),
                  'pending_lines': sum(b['lines'] for b in pending),
                  'assignments': assignments}, ensure_ascii=False, indent=1))

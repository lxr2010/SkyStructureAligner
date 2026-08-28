#!/usr/bin/env python3
"""找 EVO 场景 <-> Remake 场景 对应关系：锚点台词(唯一匹配)投票。

Remake 台词来自 remake_structure.json，EVO 台词来自 script_data(text+source_file)。
用「在 EVO 里唯一的 normalize 台词」作锚点投票，避免短句/语气词误投。
gt 来自 match_result.csv (RemakeScenaScriptFilename -> OldScriptId -> source_file) 验证。
"""
import json, csv, sys
from collections import defaultdict, Counter
sys.path.insert(0, '/var/minis/workspace/TrailsInTheSkyRemakeScriptAligner')
from synonyms import normalize
W = '/var/minis/workspace'

# ---------- EVO 台词索引 ----------
sd = json.load(open(f'{W}/script_data_fc.json'))
evo_scene_lines = defaultdict(list)   # scene -> [norm]
norm_to_scenes = defaultdict(set)     # norm -> {scene}
for x in sd:
    scene = x['source_file'].replace('.txt', '')
    n = normalize(x['text'])
    if n:
        evo_scene_lines[scene].append(n)
        norm_to_scenes[n].add(scene)

# ---------- Remake 台词（按场景）----------
remake = json.load(open(f'{W}/remake_structure.json'))
remake_scene_lines = defaultdict(list)
for scene, funcs in remake.items():
    for fn, f in funcs.items():
        for blk, lines in f['blocks'].items():
            remake_scene_lines[scene].extend(lines)

# ---------- gt ----------
rows = list(csv.DictReader(open(f'{W}/match_result.csv', encoding='utf-8-sig')))
sid_to_scene = {x['script_id']: x['source_file'].replace('.txt', '') for x in sd}
gt_r2e = defaultdict(set)
for r in rows:
    if r['MatchType'] == 'matched' and r['OldScriptId']:
        sid = int(r['OldScriptId'])
        if sid in sid_to_scene:
            gt_r2e[r['RemakeScenaScriptFilename']].add(sid_to_scene[sid])

# ---------- 锚点投票 ----------
print(f'Remake 场景数: {len(remake_scene_lines)}, gt 覆盖: {len(gt_r2e)}')
print(f'{"Remake场景":18s} {"台词":>5s} {"锚点":>5s} {"GT场景数":>6s} {"TOP1命中":>7s} {"召回":>6s}  GT场景')
print('-' * 90)

n_gt = 0; top1_hit = 0; recall_any = 0
detail = []
for rscene in sorted(remake_scene_lines):
    lines = remake_scene_lines[rscene]
    gt_scenes = gt_r2e.get(rscene, set())
    if not gt_scenes:
        continue
    n_gt += 1
    # 锚点投票：normalize 在 EVO 里唯一的台词
    votes = Counter()
    anchors = 0
    for n in lines:
        scenes = norm_to_scenes.get(n, set())
        if len(scenes) == 1:
            anchors += 1
            votes[list(scenes)[0]] += 1
    ranked = [sc for sc, _ in votes.most_common()]
    top1 = ranked[0] if ranked else None
    hit = 1 if top1 in gt_scenes else 0
    recall = len(set(ranked[:len(gt_scenes)]) & gt_scenes) / len(gt_scenes) if gt_scenes else 0
    top1_hit += hit; recall_any += (1 if recall > 0 else 0)
    detail.append((rscene, len(lines), anchors, len(gt_scenes), hit, recall, sorted(gt_scenes), ranked[:4]))
    print(f'{rscene:18s} {len(lines):5d} {anchors:5d} {len(gt_scenes):6d} {hit:7d} {recall*100:5.0f}%  {sorted(gt_scenes)[:5]}')

print('-' * 90)
print(f'有 gt 的 Remake 场景: {n_gt}')
print(f'TOP1 命中率: {top1_hit}/{n_gt} = {top1_hit/n_gt*100:.1f}%')
print(f'至少召回 1 个 GT 场景: {recall_any}/{n_gt} = {recall_any/n_gt*100:.1f}%')
json.dump(detail, open(f'{W}/scene_correspondence.json', 'w'), ensure_ascii=False, indent=1)

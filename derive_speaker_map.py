#!/usr/bin/env python3
"""从结构数据推导 说话人码 -> EVO角色码 映射（speaker_map_{game}.json），供 s4 等复用。

原理（锚点投票）: 取「EVO 中文本唯一角色」的台词，对 Remake 每个说话人码投票；
强共识(>=3票 且 >=80%) 采纳。结果与现成 speaker_map 合并（推导优先，保留旧映射其余项）。

用法: python derive_speaker_map.py [sc]     （默认 sc；fc 需有 remake_structure.json）
依赖: s1/s2 先生成 remake_structure[_sc].json 与 evo_structure[_sc].json
"""
import json, sys, os, re
from collections import defaultdict, Counter
from synonyms import normalize

from paths import W, resolve, require
GAME = (sys.argv[1].lower() if len(sys.argv) > 1 else 'sc')
SUF = '' if GAME == 'fc' else f'_{GAME}'

def resolve(name):
    for d in (W, os.path.join(W, 'TrailsInTheSkyRemakeScriptAligner'), os.path.join(W, 'ed6-scripts')):
        p = os.path.join(d, name)
        if os.path.exists(p): return p
    return None

remake_p = require(f'remake_structure{SUF}.json')
evo_p = require(f'evo_structure{SUF}.json')
if not remake_p or not evo_p:
    raise SystemExit(f'缺少结构文件，请先运行 s1/s2 ({GAME})')
remake = json.load(open(remake_p, encoding='utf-8'))
evo = json.load(open(evo_p, encoding='utf-8'))

tag = lambda s: re.sub(r'<[^>]*>', '', s or '')

# EVO: norm -> {char: n}（只保留文本在 EVO 中只对应一个角色的）
en = defaultdict(Counter)
for sc, funcs in evo.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('voice_id') and normalize(tag(t.get('text') or '')):
                    en[normalize(tag(t.get('text') or ''))][t['voice_id'][:3]] += 1

votes = defaultdict(Counter)
scene_votes = defaultdict(Counter)   # (scene, spk) -> Counter(char)  场景条件映射
for sc, funcs in remake.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('speaker') is None:
                    continue   # skind=var/unknown: 说话人静态不定, 不参与锚点投票(s1已标注)
                n = normalize(tag(t.get('text') or ''))
                if n and n in en and len(en[n]) == 1:
                    char = next(iter(en[n]))
                    votes[t['speaker']][char] += 1
                    scene_votes[(sc, t['speaker'])][char] += 1

derived, weak = {}, []
for spk, c in votes.items():
    char, n = c.most_common(1)[0]
    if n >= 3 and n / sum(c.values()) >= 0.8:
        derived[str(spk)] = char
    else:
        weak.append((spk, c.most_common(3), sum(c.values())))

# 场景条件映射：仅对全局未采纳(弱共识)的说话人，取场景内强共识
# 65535=无说话人哨兵(旁白)，排除——同场景旁白可能对应多个角色，强绑会误伤
scene_map = {}
n_scene = 0
for (sc, spk), c in scene_votes.items():
    if spk is None or str(spk) in derived or int(spk) == 65535:
        continue
    char, n = c.most_common(1)[0]
    if n >= 3 and n / sum(c.values()) >= 0.8:
        scene_map.setdefault(sc, {})[str(spk)] = char
        n_scene += 1

# 与现成/旧映射合并：仓库现成打底 -> 旧自建保留 -> 推导覆盖
repo_p = resolve(f'speaker_map_{GAME}.json')
merged = {}
if repo_p and os.path.dirname(repo_p).endswith('TrailsInTheSkyRemakeScriptAligner'):
    merged.update(json.load(open(repo_p, encoding='utf-8')))
    base_n = len(merged)
else:
    base_n = 0
old_p = os.path.join(W, f'speaker_map_{GAME}.json')
if os.path.exists(old_p):
    merged.update(json.load(open(old_p, encoding='utf-8')))
old_n = len(merged)
conflict = {k: (merged.get(k), v) for k, v in derived.items() if k in merged and merged[k] != v}
merged.update(derived)

out_p = os.path.join(W, f'speaker_map_{GAME}.json')
json.dump(merged, open(out_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
scene_p = os.path.join(W, f'speaker_map_scene_{GAME}.json')
json.dump(scene_map, open(scene_p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

print(f'推导成功: {len(derived)} 项（强共识），弱共识待定: {len(weak)}')
print(f'场景条件映射: {n_scene} 项（{len(scene_map)} 个场景）-> {os.path.basename(scene_p)}')
for sc, m in list(scene_map.items())[:6]:
    print(f'  {sc}: {m}')
for spk, top, tot in sorted(weak, key=lambda x: -x[2])[:8]:
    print(f'  弱: {spk} -> {top} (总{tot})')
if conflict:
    print(f'警告: 与现成映射冲突 {len(conflict)} 项（已按推导覆盖）:')
    for k, (o, n) in list(conflict.items())[:5]:
        print(f'  {k}: 旧={o} 新={n}')
else:
    print('与现成映射无冲突')
print(f'合并: 仓库打底{base_n} -> 含旧映射{old_n} -> 最终 {len(merged)} 项 -> {out_p}')

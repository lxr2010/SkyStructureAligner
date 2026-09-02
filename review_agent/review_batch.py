#!/usr/bin/env python3
"""校对Agent工作包生成器：把一个 Remake 块所需的全部上下文切成一个小 JSON 包。

用法:
  uv run python review_batch.py sc <场景名> [函数名]        # 输出 review_pack/<场景>_<函数>.json
  uv run python review_batch.py sc --list                   # 列出可领取的块(按待办优先级)
  uv run python review_batch.py sc --next                   # 输出下一个最值得校对的块名

包内容: Remake块行(含匹配/翻译/审查标记) + 邻块上下文 + EVO侧结构邻域 + 源文件行号定位 + 查证坐标。
"""
import csv, json, os, sys, re
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import W, resolve

GAME = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else 'sc'
SUF = '' if GAME == 'fc' else f'_{GAME}'

_det_p = resolve(f'match_result_{GAME}_detailed.csv') or resolve('match_result_sc_detailed.csv')
det = list(csv.DictReader(open(_det_p, encoding='utf-8')))
evo = json.load(open(resolve(f'evo_structure{SUF}.json'), encoding='utf-8'))
remake_st = json.load(open(resolve(f'remake_structure{SUF}.json'), encoding='utf-8')) if resolve(f'remake_structure{SUF}.json') else {}
# (scene, text出现序) -> {speaker, rid}：与主表行序一致
remake_lines = {}
for sc, funcs in remake_st.items():
    seq = []
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                seq.append((t['text'], t.get('speaker'), t.get('rid')))
    remake_lines[sc] = seq

# Remake py 源文件目录探测
PY_DIRS = [os.path.join(W, 'remake2nd_demo', 'py'), os.path.join(W, 'remake_jp')]
PY_DIR = next((d for d in PY_DIRS if os.path.isdir(d)), None)

def evo_vid_info(vid):
    for sc, funcs in evo.items():
        for fn, f in funcs.items():
            for lab, blk in f['blocks'].items():
                for t in blk:
                    if t.get('voice_id') == vid:
                        return {'vid': vid, 'scene': sc, 'func': fn, 'block': lab,
                                'text': t['text'], 'talk_num': t['talk_num'], 'speaker': t['speaker']}
    return {'vid': vid, 'text': '(不在evo结构中)', 'scene': '', 'func': '', 'block': ''}

def build_pack(scene, func=None):
    rows = [r for r in det if r['RemakeScenaScriptFilename'] == scene and (func is None or r['RemakeFunction'] == func)]
    if not rows:
        return None
    funcs = defaultdict(list)
    for r in rows: funcs[r['RemakeFunction']].append(r)
    pack = {'scene': scene, 'functions': {}}
    rseq = remake_lines.get(scene, [])
    rptr = 0
    for fn, rs in funcs.items():
        for r in rs:
            # 按文本顺序对齐补说话人码与rid（主表无此二列）
            while rptr < len(rseq) and rseq[rptr][0] != r['RemakeVoiceText']:
                rptr += 1
            if rptr < len(rseq):
                r['RemakeSpeakerCode'] = rseq[rptr][1]
                r['RemakeRid'] = rseq[rptr][2]
                rptr += 1
    for fn, rs in funcs.items():
        matched = [r for r in rs if r['OldVoiceFilename']]
        evo_nei = []
        for r in matched:
            info = evo_vid_info(r['OldVoiceFilename'][2:-1] if r['OldVoiceFilename'].startswith('ch') else r['OldVoiceFilename'])
            info['remake_lineno'] = r['RemakeScenaScriptLineno']
            info['remake_text'] = r['RemakeVoiceText']
            evo_nei.append(info)
        pack['functions'][fn] = {
            'rows': rs, 'matched_count': len(matched), 'total': len(rs),
            'evo_neighborhood': evo_nei,
            'py_file': os.path.join(PY_DIR, scene + '.py') if PY_DIR else '',
        }
    return pack

if '--list' in sys.argv:
    from collections import Counter
    todo = defaultdict(lambda: [0, 0, 0])  # scene/func -> [total, matched, alerts]
    for r in det:
        k = (r['RemakeScenaScriptFilename'], r['RemakeFunction'])
        todo[k][0] += 1
        if r['OldVoiceFilename']: todo[k][1] += 1
        if r['VoiceReuseAlert'] or r['SpeakerCheck']: todo[k][2] += 1
    ranked = sorted(todo.items(), key=lambda x: (x[1][2] == 0, x[1][1] == 0, -x[1][2]))
    for (s, f), (t, m, a) in ranked[:40]:
        print(f'{s}\t{f}\t{t}行\t匹配{m}\t待办标记{a}')
    sys.exit(0)

if '--next' in sys.argv:
    from collections import defaultdict as dd
    todo = dd(lambda: [0, 0, 0])
    for r in det:
        k = (r['RemakeScenaScriptFilename'], r['RemakeFunction'])
        todo[k][0] += 1
        if r['OldVoiceFilename']: todo[k][1] += 1
        if r['VoiceReuseAlert'] or r['SpeakerCheck']: todo[k][2] += 1
    (s, f), _ = sorted(todo.items(), key=lambda x: (x[1][2] == 0, x[1][1] == 0, -x[1][2]))[0]
    print(f'{s} {f}')
    sys.exit(0)

_pos = [a for a in sys.argv[2:] if not a.startswith('-')]
if len(_pos) < 2:
    sys.exit('用法: review_batch.py <game> <场景名> <函数名>   (或 --list / --next)')
scene, func = _pos[0], _pos[1]
pack = build_pack(scene, func)
if pack is None:
    sys.exit(f'未找到: {scene}/{func}')
_here = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(_here, 'review_pack'), exist_ok=True)
out = os.path.join(_here, 'review_pack', f'{scene}_{func}.json')
json.dump(pack, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'工作包: {out}')
print(f'函数数: {len(pack["functions"])}, 行数: {sum(len(f["rows"]) for f in pack["functions"].values())}')

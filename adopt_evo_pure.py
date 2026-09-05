#!/usr/bin/env python3
"""evo_structure_pure_sc.json 直接换装为管线主数据（v2：控制流已完整，无需合并）。

  - 补 bank 字段（vid 前3位）
  - speaker 0x0 → 0xFF(system)（与旧结构 0xFF 系统文本语义对齐）
  - script_data_sc.json 由 pure 合成（script_id=msg_id），不再依赖 SoraVoice
  - 重建 evo_talk_bank_index_sc.json（talk_num=指令偏移体系）
  - 清 rt.py pickle 缓存
"""
import json, os, shutil, glob

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, 'data')

def load(p): return json.load(open(p, encoding='utf-8'))
def save(p, o):
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(o, f, ensure_ascii=False)

pure = load(os.path.join(D, 'evo_structure_pure_sc.json'))

# 备份（幂等）
for name in ('evo_structure_sc.json', 'script_data_sc.json', 'evo_talk_bank_index_sc.json'):
    src = os.path.join(D, name)
    dst = os.path.join(D, name.replace('_sc.json', '_sora_sc.json'))
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copy2(src, dst)
        print('备份', os.path.basename(dst))

n_sys = 0
sd = []
tbi = {}
for sc, funcs in pure.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                t['bank'] = t['voice_id'][:3] if t.get('voice_id') else None
                if t.get('speaker') == '0x0':
                    t['speaker'] = '0xFF'
                    t['speaker_kind'] = 'system'
                    n_sys += 1
                if t.get('voice_id'):
                    if t.get('msg_id') is not None:
                        sd.append({'script_id': int(t['msg_id']),
                                   'voice_id': t['voice_id'] + 'V',
                                   'text': t['text'],
                                   'character_id': t['speaker'],
                                   'source_file': sc + '.txt'})
                    tbi[f'{sc}/{fn}/{t.get("talk_num")}'] = t['voice_id'][:3]

save(os.path.join(D, 'evo_structure_sc.json'), pure)
save(os.path.join(D, 'script_data_sc.json'), sd)
save(os.path.join(D, 'evo_talk_bank_index_sc.json'), tbi)

n_talk = sum(len(b) for fs in pure.values() for f in fs.values() for b in f['blocks'].values())
n_vid = sum(1 for fs in pure.values() for f in fs.values() for b in f['blocks'].values() for t in b if t.get('voice_id'))
n_edge = sum(len(f['edges']) for fs in pure.values() for f in fs.values())
print(f'evo_structure_sc.json <- pure: 场景{len(pure)} 台词{n_talk} 带vid{n_vid} 边{n_edge} 0x0→0xFF:{n_sys}')
print(f'script_data_sc.json(合成): {len(sd)}   evo_talk_bank_index: {len(tbi)}')

for p in glob.glob(os.path.join(HERE, 'review_agent', 'review_pack', '.rt_cache_*.pkl*')):
    os.remove(p)
    print('清缓存', os.path.basename(p))
print('完成')

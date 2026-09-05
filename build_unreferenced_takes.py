#!/usr/bin/env python3
"""生成权威「未引用录音登记表」，替换 additional_voice_sc.json。

  未引用集 = AT9 全集 − pure 引用集（权威口径）
  文本来源优先级: additional(SoraVoice补录转写) > sora结构行文本(SoraVoice行绑定) > 无(侧表待听辨)
  同时清掉原表与 pure 引用集重叠的 159 条（它们在结构里已是候选，补录表无需重复）。
"""
import json, os, shutil
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, 'data')

def load(p): return json.load(open(p, encoding='utf-8'))

pure = load(os.path.join(D, 'evo_structure_pure_sc.json'))
sora = load(os.path.join(D, 'evo_structure_sora_sc.json'))
add = load(os.path.join(D, 'additional_voice_sc.json'))

pv = set()
for sc, fs in pure.items():
    for fn, f in fs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('voice_id'): pv.add(t['voice_id'])

def bare(v):
    v = (v or '').strip()
    v = v[2:] if v.startswith('ch') else v
    return v[:-1] if v.endswith('V') else v

add_text = {}
for it in add:
    v = bare(it.get('voice_id'))
    if v and it.get('text'):
        add_text.setdefault(v, it['text'])

sora_text = {}
for sc, fs in sora.items():
    for fn, f in fs.items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                if t.get('voice_id'):
                    sora_text.setdefault(t['voice_id'], t['text'])

at9 = set()
for ln in open(os.path.join(D, 'at9_names_sc.csv'), encoding='utf-8-sig'):
    v = ln.strip().strip('"').rstrip('Vv').strip()
    if len(v) == 10 and v.isdigit():
        at9.add(v)

unref = sorted(at9 - pv)
rows = []
no_text = []
src_cnt = {'additional转写': 0, 'sora结构行': 0}
for v in unref:
    if v in add_text:
        rows.append({'voice_id': 'ch' + v + 'V', 'text': add_text[v], 'text_source': 'sora_voice_transcribe'})
        src_cnt['additional转写'] += 1
    elif v in sora_text:
        rows.append({'voice_id': 'ch' + v + 'V', 'text': sora_text[v], 'text_source': 'sora_structure_line'})
        src_cnt['sora结构行'] += 1
    else:
        no_text.append(v)

# 备份 + 写出
bak = os.path.join(D, 'additional_voice_sora_sc.json')
if not os.path.exists(bak):
    shutil.copy2(os.path.join(D, 'additional_voice_sc.json'), bak)
    print('备份 -> additional_voice_sora_sc.json')
with open(os.path.join(D, 'additional_voice_sc.json'), 'w', encoding='utf-8') as f:
    json.dump(rows, f, ensure_ascii=False)
with open(os.path.join(D, 'unreferenced_untranscribed_sc.json'), 'w', encoding='utf-8') as f:
    json.dump({'count': len(no_text), 'note': 'AT9存在但无任何文本来源的未引用录音, 待听辨转录',
               'vids': no_text}, f, ensure_ascii=False)

print(f'未引用集 {len(unref)} = AT9 {len(at9)} - pure引用 {len(pv)}')
print(f'新 additional_voice_sc.json: {len(rows)} 条  来源: {src_cnt}')
print(f'未转录侧表 unreferenced_untranscribed_sc.json: {len(no_text)} 支')
drop = len(add_text) - src_cnt['additional转写']
print(f'清理与pure引用重叠: {drop} 条（结构内已有, 候选不受影响）')

# 清 rt 缓存
import glob
for p in glob.glob(os.path.join(HERE, 'review_agent', 'review_pack', '.rt_cache_*.pkl*')):
    os.remove(p)
    print('清缓存', os.path.basename(p))

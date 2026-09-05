#!/usr/bin/env python3
"""用 EVO 原生数据(pure 结构)重建 bank 依赖的两份资产：

  evo_speaker_names_sc.json  banks/char_id↔bank/tname (身份层; tname 为原生表原样保留)
  evo_bank_index_sc.json     lines/speaker_slots/display_names/scenes/special_occurrences (档案层)

语义来源:
  speaker 0x100+n = 全局角色ID -> T_NAME[n] (身份投票, 纯度=主导char_id占比)
  speaker 0x8-0x5F = 场景演员槽 (有char_id身份的bank出现在此=乱入记录)
  speaker_name = pure 行自带(cast表解析), 代替旧"文本鉴别"
"""
import json, os, shutil
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, 'data')

pure = json.load(open(os.path.join(D, 'evo_structure_pure_sc.json'), encoding='utf-8'))
# manual 文本鉴别表(中文名唯一记录源, 原自 prepare_evo_speaker_data.py)
MANUAL_CN = {'022': '蕾恩', '028': '桃乐茜', '030': '乔丝特', '062': '卡西乌斯',
             '015': '怀斯曼教授', '020': '瘦狼瓦尔特', '054': '阿尔巴教授', '014': '理查德'}
cast_table = json.load(open(os.path.join(D, 'evo_cast_table.json'), encoding='utf-8'))  # cast -> {bank,charid,name}
_prev = os.path.join(D, 'evo_speaker_names_prev_sc.json')
old_kb = json.load(open(_prev if os.path.exists(_prev) else os.path.join(D, 'evo_speaker_names_sc.json'), encoding='utf-8'))
tname = old_kb['tname']                                   # 原生 T_NAME 表, 原样保留
tname_zh = {}
_p = os.path.join(D, 'speaker_names_t_name_zh_sc.json')
if os.path.exists(_p):
    tname_zh = {int(k): v for k, v in json.load(open(_p, encoding='utf-8')).items()
                if isinstance(v, dict)}
else:
    _p2 = os.path.join(D, 'speaker_names_t_name_zh_sc.json')

def zh_of(cid):
    v = tname_zh.get(cid)
    if isinstance(v, dict):
        return v.get('zh') or v.get('sc') or v.get('cn') or ''
    return v or ''

# ---------- 聚合 ----------
banks = defaultdict(lambda: {
    'lines': 0, 'scenes': set(), 'slots': Counter(),
    'charid_votes': Counter(), 'name_votes': Counter(), 'casts': set(),
    'special': [], 'examples': [], 'namebox': Counter(),
})
_GENERIC_TAIL = 'の声'
_PUNCT_BAD = set('。！？!?.…、，')

def _name_like(t):
    t = (t or '').strip().replace(chr(13), '').replace(chr(10), '')
    if not (1 <= len(t) <= 10): return None
    if any(c in _PUNCT_BAD for c in t): return None
    return t

for sc, funcs in pure.items():
    for fn, f in funcs.items():
        for lab, blk in f['blocks'].items():
            _prev = None
            for t in blk:
                vid = t.get('voice_id')
                # 名框投票: 前行无vid且像名字, 本行带vid且speaker一致 -> 名字归本行bank
                if vid and _prev is not None:
                    nm = _name_like(_prev['text'])
                    if nm and _prev.get('speaker') == t.get('speaker'):
                        banks[vid[:3]]['namebox'][nm] += 1
                _prev = t if not vid else None
                if not vid:
                    continue
                b = vid[:3]
                e = banks[b]
                e['lines'] += 1
                e['scenes'].add(sc)
                sp_raw = t.get('speaker') or ''
                e['slots'][sp_raw] += 1
                try:
                    sp = int(sp_raw, 16)
                except ValueError:
                    sp = None
                if sp is not None and 0x100 <= sp < 0x120:
                    e['charid_votes'][sp - 0x100] += 1
                nm = (t.get('speaker_name') or '').strip()
                if nm:
                    e['name_votes'][nm] += 1
                if t.get('cast'):
                    e['casts'].add(t['cast'])
                if len(e['examples']) < 3 and t['text'].strip():
                    e['examples'].append(t['text'][:40])
                # 乱入记录在身份判定后补记(先存原始行)
                if sp is not None and 0x8 <= sp < 0x60:
                    e['special'].append({'scene': sc, 'function': fn,
                                         'talk_num': t.get('talk_num'),
                                         'speaker': sp_raw, 'text': t['text'][:48]})

# ---------- 名框投票(evo_native_talks_final: ②剥离前的原生行, 含解析身份) ----------
_ntp = os.path.join(D, 'evo_native_talks_final.json')
if os.path.exists(_ntp):
    nt = json.load(open(_ntp, encoding='utf-8'))
    PUNCT2 = set('。！？!?.…、，#')
    for _scn, _rows in nt.items():
        _rows = sorted(_rows, key=lambda r: r.get('off') or 0)
        _slot_nb = {}   # speaker槽 -> (名字, 行序): 名框标的是同槽位台词(群体喊话类可跨槽, 辅以邻接)
        _adj_prev = None
        def _vote(_bank, _nm):
            banks.setdefault(_bank, {'lines': 0, 'scenes': set(), 'slots': Counter(),
                                     'charid_votes': Counter(), 'name_votes': Counter(),
                                     'casts': set(), 'special': [], 'examples': [],
                                     'namebox': Counter()})['namebox'][_nm] += 1
        for _i, _r in enumerate(_rows):
            _sp = _r.get('speaker')
            if _r.get('marker') and _r.get('bank'):
                _nb = _slot_nb.get(_sp)
                if _nb is not None and _i - _nb[1] <= 8:
                    _vote(_r['bank'], _nb[0])
                elif _adj_prev is not None:
                    _vote(_r['bank'], _adj_prev)   # 无同槽名框时退回邻接(群体名框跨槽场景)
                _slot_nb.pop(_sp, None)
                _adj_prev = None
            else:
                _tx = (_r.get('text') or '').strip()
                if 1 <= len(_tx) <= 10 and not any(c in _tx for c in PUNCT2):
                    _slot_nb[_sp] = (_r.get('name') or _tx, _i)
                    _adj_prev = _r.get('name') or _tx
                else:
                    _adj_prev = None

# ---------- 判定与产出 ----------
kb_banks, cid2bank, bank2cid = {}, {}, {}
index = {}
for b, e in sorted(banks.items()):
    total = e['charid_votes'].total() if hasattr(Counter, 'total') else sum(e['charid_votes'].values())
    total = sum(e['charid_votes'].values())
    cid, n = (e['charid_votes'].most_common(1)[0] if e['charid_votes'] else (None, 0))
    purity = n / total if total else 0
    if cid is not None and purity >= 0.95 and n >= 5 and n >= 0.1 * e['lines']:   # charid票须占总量≥10%(防小样本误判, 如レン模仿エステル)
        status = 'charid+T_NAME' if purity == 1.0 else f'charid({purity:.0%})'
        jpn = tname[cid] if cid < len(tname) else ''
        cn = zh_of(cid)
        bank2cid[b] = str(cid)
        cid2bank.setdefault(str(cid), b)
    else:
        # cast表反查优先: cast->bank纯度1.0(人工校验表), 高于行级speaker_name投票
        cands = [v['name'] for v in cast_table.values()
                 if v.get('bank') == b and v.get('name')]
        if cands:
            nm2 = Counter(cands).most_common(1)[0][0]
            status, jpn = f'cast_table({len(cands)}个cast码)', nm2
            cn = MANUAL_CN.get(b, '')
        elif e['namebox']:
            uniq = Counter({k: v for k, v in e['namebox'].items() if not k.endswith(_GENERIC_TAIL)})
            tot = sum(uniq.values())
            if uniq and uniq.most_common(1)[0][1] >= 3:
                nm3, n3 = uniq.most_common(1)[0]
                if n3 >= 3 and n3 / tot >= 0.5:
                    status, jpn, cn = f'namebox_vote({n3}/{tot}票)', nm3, ''
                else:
                    status, jpn, cn = 'unidentified', '', ''
            else:
                status, jpn, cn = 'unidentified', '', ''
        else:
            nm, _ = (e['name_votes'].most_common(1)[0] if e['name_votes'] else ('', 0))
            if nm:
                status, jpn, cn = 'speaker_name(native)', nm, ''
            else:
                ob = (old_kb.get('banks') or {}).get(b) or {}
                if ob.get('jpn') and ob.get('status') in ('text_verified', 'manual', 'charid'):
                    status, jpn, cn = f'inherited({ob["status"]})', ob['jpn'], ob.get('cn', '')
                else:
                    status, jpn, cn = 'unidentified', '', ''
    special = e['special'] if b in bank2cid else []   # 仅主角团(char_id身份)的演员槽出现算乱入
    kb_banks[b] = {'jpn': jpn, 'cn': cn, 'status': status,
                   'char_id': bank2cid.get(b),
                   'lines': e['lines'], 'scene_count': len(e['scenes']),
                   'special_count': len(special),
                   'top_slots': dict(e['slots'].most_common(10)),
                   'cast_codes': sorted(e['casts'])}
    index[b] = {'identity_jp': jpn, 'identity_cn': cn, 'identity_status': status,
                'lines': e['lines'], 'scene_count': len(e['scenes']),
                'speaker_slots': dict(e['slots'].most_common(16)),
                'display_names_seen': (dict(e['namebox'].most_common(10)) or
                                       dict(e['name_votes'].most_common(10))),
                'scenes': sorted(e['scenes'])[:60],
                'example_lines': e['examples'],
                'special_identity_occurrences': special[:800]}

# ---------- 备份与写出 ----------
for name in ('evo_speaker_names_sc.json', 'evo_bank_index_sc.json'):
    src = os.path.join(D, name)
    dst = os.path.join(D, name.replace('_sc.json', '_prev_sc.json'))
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copy2(src, dst)
        print('备份 ->', os.path.basename(dst))

new_kb = {'banks': kb_banks, 'char_id_to_bank': cid2bank, 'bank_to_char_id': bank2cid,
          'tname': tname}
for k in ('slot_kinds', 'cast_semantics', 'chip_names', 'native_talks'):
    if k in old_kb:
        new_kb[k] = old_kb[k]          # 原生静态分析附表原样保留
json.dump(new_kb, open(os.path.join(D, 'evo_speaker_names_sc.json'), 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(index, open(os.path.join(D, 'evo_bank_index_sc.json'), 'w', encoding='utf-8'), ensure_ascii=False)

named = sum(1 for v in kb_banks.values() if v['status'].startswith('charid'))
named2 = sum(1 for v in kb_banks.values() if v['status'] == 'speaker_name(native)')
print(f'bank总数 {len(kb_banks)}: charid身份 {named} + 原生speaker_name {named2} + unidentified {len(kb_banks)-named-named2}')
print(f'char_id↔bank 映射 {len(cid2bank)} 对; 乱入记录总量 {sum(len(v["special_identity_occurrences"]) for v in index.values())}')

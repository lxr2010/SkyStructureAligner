#!/usr/bin/env python3
"""EVO 说话人知识库（统一入口，供 s2/s4/s6/s7 使用）。

数据: data/evo_speaker_names_{game}.json (prepare_evo_speaker_data.py 生成)
      banks / char_id_to_bank / bank_to_char_id / tname

语义（经 EVO 日文本体原始剧本验证）:
  speaker 0xFE/0xFF  旁白/系统
  speaker 0x100+n    全局角色ID: T_NAME[n]（与场景字符串表无关, 跨场景不变）
  speaker 0x8-0x5F   场景演员槽: 选角随场景, 身份以 voice bank 为准
  voice_id[:3]       角色语音 bank, 与角色一一对应(char_id_to_bank 100%一致)
"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
_cache = {}

def _load(game):
    if game not in _cache:
        p = os.path.join(HERE, 'data', f'evo_speaker_names_{game}.json')
        _cache[game] = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else None
    return _cache[game]

def _cast_table(game='sc'):
    if 'cast' not in _cache:
        p = os.path.join(HERE, 'data', 'evo_cast_table.json')
        _cache['cast'] = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    return _cache['cast']

def resolve(speaker, voice_id=None, game='sc'):
    """(speaker, voice_id) -> {kind, name_jp, name_cn, bank, char_id, status}"""
    kb = _load(game)
    out = {'kind': '', 'name_jp': '', 'name_cn': '', 'bank': None, 'char_id': None, 'status': ''}
    if kb is None:
        return out
    bank = voice_id[:3] if voice_id else None
    out['bank'] = bank
    try:
        sp = int(speaker, 16)
    except (ValueError, TypeError):
        sp = None
    if sp == 0xFE:
        out.update(kind='narration', name_jp='', name_cn='')
        return out
    if sp == 0xFF:
        out.update(kind='system', name_jp='', name_cn='')
        return out
    if sp is not None and 0x100 <= sp < 0x120:
        cid = sp & 0xFF
        tname = kb['tname']
        name = tname[cid] if cid < len(tname) else ''
        b = kb['char_id_to_bank'].get(str(cid))
        out.update(kind='charid', char_id=cid, name_jp=name,
                   status='charid+T_NAME')
        if b:
            out['bank'] = out['bank'] or b
        bi = kb['banks'].get(b or '', {})
        out['name_cn'] = bi.get('cn', '')
        return out
    # 演员槽 / 其它: bank 定身份
    if bank and bank in kb['banks']:
        bi = kb['banks'][bank]
        out.update(kind='actor_slot' if (sp is not None and 0 <= sp < 0x60) else 'other',
                   name_jp=bi.get('jpn', ''), name_cn=bi.get('cn', ''),
                   char_id=int(bi['char_id']) if bi.get('char_id') else None,
                   status=bi.get('status', ''))
        return out
    out['kind'] = 'actor_slot' if (sp is not None and 0 <= sp < 0x60) else 'unknown'
    return out

def bank_name(bank, game='sc'):
    kb = _load(game)
    if not kb or not bank:
        return '', ''
    bi = kb['banks'].get(bank, {})
    jp = bi.get('jpn', '')
    cn = bi.get('cn', '')
    if not jp:
        # cast表兜底(EVO原生marker推导)
        for c, v in _cast_table(game).items():
            if v.get('bank') == bank and v.get('name'):
                return v['name'], v.get('name_cn', '')
    return jp, cn

def special_count(bank, game='sc'):
    kb = _load(game)
    if not kb or not bank:
        return 0
    return kb['banks'].get(bank, {}).get('special_count', 0)

def lookup_bank(scene, function, talk_num, game='sc'):
    """(场景,函数,talk_num) -> bank；数据由 evo_talk_bank_index_{game}.json 提供"""
    p = os.path.join(HERE, 'data', f'evo_talk_bank_index_{game}.json')
    if not os.path.exists(p):
        return None
    if 'talkidx' not in _cache:
        _cache['talkidx'] = json.load(open(p, encoding='utf-8'))
    return _cache['talkidx'].get('%s/%s/%s' % (scene, function, talk_num))

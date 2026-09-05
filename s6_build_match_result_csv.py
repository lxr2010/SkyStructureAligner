#!/usr/bin/env python3
"""生成 FC match_result.csv 式 16 列详表（完全自推导，不依赖任何现成匹配结果）。

数据源（全部从 Demo 原始数据推导）:
  remake2nd_demo/py     日文反编译脚本 -> 台词/行号/说话人 (Cmd_text_00/06/13)
  remake2nd_demo_sc/py  简中反编译脚本 -> 中文翻译（同文件同行号对齐）
  add_struct(array2=[INT(5),...])     -> 附加结构行号（normalized_args 关联，参照 scena_voice_kuro_extractor）
  my_match_result_sc.csv (s4)         -> 匹配结果
  script_data_sc.json                 -> voice_id -> OldScriptId/OldCharacterId
  evo_structure_sc/additional_voice   -> OldVoiceText

用法: python s6_build_match_result_csv.py [sc]
输出: W/match_result_sc_detailed.csv
"""
import ast, json, csv, sys, os, re
from collections import defaultdict, deque, Counter
from synonyms import normalize

from paths import W, resolve, require
import evo_speaker_info as evo_speaker
GAME = (sys.argv[1].lower() if len(sys.argv) > 1 else 'sc')
assert GAME == 'sc', '目前仅支持 sc'
# 反编译目录可用命令行覆盖: python s6... sc [jp_dir] [sc_dir]（供 run.py 调用）
_args = [a for a in sys.argv[1:] if not a.startswith('-') and a.lower() != 'sc']
JP_DIR = _args[0] if _args and len(_args) >= 1 else os.path.join(W, 'remake2nd_demo', 'py')
SC_DIR = _args[1] if _args and len(_args) >= 2 else os.path.join(W, 'remake2nd_demo_sc', 'py')
# 00/06=普通对话 13=带立绘对话(UNKNOWN_05_13 为反编译别名) 08=分支选项/系统文本
OK_CMDS = ('Cmd_text_00', 'Cmd_text_06', 'Cmd_text_13', 'UNKNOWN_05_13', 'Cmd_text_08')

# ---------- 提取（参照 scena_voice_kuro_extractor + s1 的文本规则） ----------
def node_value(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('INT', 'FLOAT'):
        if node.args and isinstance(node.args[0], ast.Constant):
            return node.args[0].value
    return ast.unparse(node)

def norm_key(elts):
    """normalized_args: 与 Command 前缀(5,funcid) + 参数值 对齐的键"""
    return ','.join(str(node_value(e)) for e in elts
                    if not (isinstance(e, ast.Call) and getattr(e.func, 'id', '') == 'UNDEF'))

def parse_one(path):
    """解析单个 .py: {'commands': [...], 'addstruct': {key: line}, 'dispnames': [(sid,seq,name,line)]}"""
    tree = ast.parse(open(path, encoding='utf-8').read())
    cmds, adds, dispnames = [], {}, []
    _disp_seq = {}   # sid -> 出现序号(用于日中配对)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id == 'CallFunction':
            # chr_set_display_name(INT(sid), "名", ...) — 运行时改名, 提取用于日中翻译对照
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'chr_set_display_name' \
                    and len(node.args) > 1 and isinstance(node.args[1], ast.List) and len(node.args[1].elts) >= 2:
                e0, e1 = node.args[1].elts[0], node.args[1].elts[1]
                if isinstance(e0, ast.Call) and getattr(e0.func, 'id', '') == 'INT' \
                        and e0.args and isinstance(e0.args[0], ast.Constant) \
                        and isinstance(e1, ast.Constant) and isinstance(e1.value, str) and e1.value:
                    sid = e0.args[0].value
                    _disp_seq[sid] = _disp_seq.get(sid, 0) + 1
                    dispnames.append((sid, _disp_seq[sid], e1.value, node.lineno))
        if node.func.id == 'Command':
            if not node.args or not isinstance(node.args[0], ast.Constant): continue
            ct = node.args[0].value
            if ct not in OK_CMDS or len(node.args) < 2 or not isinstance(node.args[1], ast.List): continue
            elts = node.args[1].elts
            funcid = 13 if ct == 'UNKNOWN_05_13' else int(ct[-2:])
            spk = None; parts = []; rid = None
            for e in elts:
                if isinstance(e, ast.Call) and getattr(e.func, 'id', '') == 'INT' and isinstance(e.args[0], ast.Constant):
                    if spk is None: spk = e.args[0].value
                elif isinstance(e, ast.Constant) and isinstance(e.value, str):
                    parts.append(e.value)
            _is_int = lambda e: isinstance(e, ast.Call) and getattr(e.func, 'id', '') == 'INT' and e.args and isinstance(e.args[0], ast.Constant)
            for a, b in zip(elts, elts[1:]):
                if _is_int(a) and a.args[0].value == 11 and _is_int(b):
                    rid = b.args[0].value
                    break
            text = re.sub(r'<[^>]*>', '', ''.join(parts))
            cmds.append({'line': node.lineno, 'funcid': funcid, 'spk': spk, 'rid': rid, 'text': text,
                         'key': f'5,{funcid},' + norm_key(elts)})
        elif node.func.id == 'add_struct':
            for kw in node.keywords:
                if kw.arg == 'array2' and isinstance(kw.value, ast.List) and kw.value.elts:
                    e0 = kw.value.elts[0]
                    if (isinstance(e0, ast.Call) and getattr(e0.func, 'id', '') == 'INT'
                            and e0.args and isinstance(e0.args[0], ast.Constant) and e0.args[0].value == 5):
                        adds.setdefault(norm_key(kw.value.elts), node.lineno)
                        break
    cmds.sort(key=lambda c: c['line'])
    return {'commands': cmds, 'addstruct': adds, 'dispnames': dispnames}

CACHE_VER = 'v3'   # 提取器变更(如新增rid/dispnames字段)时递增, 使缓存失效
CACHE_PATH = os.path.join(W, 's6_extract_cache.json')

def extract_dir_cached(d, cache, tag):
    """带逐文件缓存(mtime+size)的提取；cache 为可变 dict，原位更新"""
    changed = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.py'): continue
        st = os.stat(os.path.join(d, fn))
        sig = f'{CACHE_VER}:{st.st_mtime_ns}:{st.st_size}'
        entry = cache.get(tag, {}).get(fn)
        if entry and entry.get('_sig') == sig:
            continue
        cache.setdefault(tag, {})[fn] = {'_sig': sig, **parse_one(os.path.join(d, fn))}
        changed += 1
    return {fn: {'commands': e['commands'], 'addstruct': e['addstruct'], 'dispnames': e.get('dispnames', [])}
            for fn, e in cache.get(tag, {}).items()}, changed

cache = {}
if os.path.exists(CACHE_PATH):
    try:
        cache = json.load(open(CACHE_PATH, encoding='utf-8'))
    except Exception:
        cache = {}
print('提取日文/简中台词(带缓存)...')
jp, ch1 = extract_dir_cached(JP_DIR, cache, 'jp')
sc, ch2 = extract_dir_cached(SC_DIR, cache, 'sc')
if ch1 or ch2 or not os.path.exists(CACHE_PATH):
    json.dump(cache, open(CACHE_PATH, 'w', encoding='utf-8'), ensure_ascii=False)
print(f'  缓存: jp重解析{ch1} sc重解析{ch2} / 各{len(jp)},{len(sc)}文件')

# ---------- 日中动态显示名对照(chr_set_display_name 按场景×ID×序号配对) ----------
_disp_jp2sc = {}
for fn in set(jp) & set(sc):
    jp_dn = jp[fn].get('dispnames', [])
    sc_dn = sc[fn].get('dispnames', [])
    sc_map = {(sid, seq): name for sid, seq, name, _ in sc_dn}
    for sid, seq, jp_name, _ in jp_dn:
        sc_name = sc_map.get((sid, seq))
        if sc_name and jp_name:
            _disp_jp2sc[jp_name] = sc_name
if _disp_jp2sc:
    print(f'  动态显示名日中对照: {len(_disp_jp2sc)} 条')

def cf(text):
    return normalize(text).replace('\u3046\u3099', 'う').replace('ヴ', 'う') if text else ''

# ---------- s4 匹配结果 ----------
mine = list(csv.DictReader(open(require('my_match_result_sc.csv'), encoding='utf-8')))
queues_exact, queues_norm = {}, {}
for r in mine:
    queues_exact.setdefault((r['Scene'], r['RemakeVoiceText']), deque()).append(r)
    queues_norm.setdefault((r['Scene'], cf(r['RemakeVoiceText'])), deque()).append(r)
consumed = 0
def take_my_row(scene, text):
    global consumed
    q = queues_exact.get((scene, text))
    if q: consumed += 1; return q.popleft()
    qn = queues_norm.get((scene, cf(text)))
    if qn: consumed += 1; return qn.popleft()
    return None

# ---------- 场景/函数编号解释（审查表直观说明） ----------
CITY = {'Rolent': '洛连特', 'Bose': '波尔斯', 'Ruan': '卢安', 'Zeiss': '蔡斯',
        'Grancel': '格兰赛尔', 'Manoria': '马诺里亚', 'event': '事件演出', 'Event': '事件演出',
        'map': '系统', 'map1': '系统'}
EVO_TYPE = {'T': '主线剧情场景', 'C': '城镇/街区场景', 'E': '事件/演出场景',
            'R': '街道/野外场景', 'A': '系统/特殊场景', 'S': '系统'}
FUNC_PREFIX = [('TK_', '选项/单次对话(编号_地名_说话人)'), ('QS', '测验/问答关卡(SELECT_=选项分支)'),
               ('EV_', '剧情演出'), ('ST_', '剧情流程'), ('TALK_', '对话'), ('LP_', '看板/路牌'),
               ('EVENT_', '事件流程控制'), ('BELF_', '求证/辩证事件'), ('BLACKJACK', '小游戏(21点)'),
               ('CHR_', '人物控制'), ('EMO_', '表情'), ('SOUND_', '音频'), ('CAM_', '镜头'),
               ('DOF_', '景深'), ('AV_', '音频/视角'), ('PTRT_', '注视控制')]

def find_sora():
    for cand in ('SoraVoiceScripts-zhenjian', 'sora-voice-matcher/SoraVoiceScripts'):
        p = os.path.join(W, cand, 'cn.sc', 'py')
        if os.path.isdir(p): return p
    return None

evo_meta = {}   # scene -> (中文注释地名, MapName)
_sora = find_sora()
if _sora:
    for fn in os.listdir(_sora):
        if not fn.endswith('.py'): continue
        cn, mp = '', ''
        raw = open(os.path.join(_sora, fn), 'rb').read()
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            content = raw.decode('gbk', errors='replace')
        for i, line in enumerate(content.split('\n')):
            if i > 40: break
            s = line.strip()
            if not cn and s.startswith('#') and 'Function' not in s and 'end' not in s.lower():
                c_ = s.lstrip('#').strip()
                if c_ and c_.lower() != 'other': cn = c_
            if 'MapName' in s and '=' in s:
                mp = s.split('=', 1)[1].strip().strip(',').strip('\'"')
            if cn and mp: break
        evo_meta[fn[:-3]] = (cn, mp)

def evo_scene_cn(escn):
    cn, mp = evo_meta.get(escn) or evo_meta.get(re.sub(r'_\d+$', '', escn), ('', ''))
    if mp in ('Other', 'other', 'a'): mp = ''
    return cn or CITY.get(mp, mp or '未标注')

# Remake场景 -> EVO场景（唯一锚点投票），用于反推地名
remake_st = json.load(open(require('remake_structure_sc.json'), encoding='utf-8'))
evo_occ = defaultdict(list); remake_occ = defaultdict(list)
for sc_, funcs_ in json.load(open(require('evo_structure_sc.json'), encoding='utf-8')).items():
    for fn_, f_ in funcs_.items():
        for lab_, blk_ in f_['blocks'].items():
            for t in blk_:
                if t.get('text'):
                    evo_occ[cf(t['text'])].append(sc_)
for sc_, funcs_ in remake_st.items():
    for fn_, f_ in funcs_.items():
        for lab_, blk_ in f_['blocks'].items():
            for t in blk_:
                remake_occ[cf(t.get('text') or '')].append(sc_)
scene_corr = {}
votes_by_scene = defaultdict(Counter)
for n_, rscenes in remake_occ.items():
    if len(rscenes) == 1 and len(evo_occ.get(n_, [])) == 1:
        votes_by_scene[rscenes[0]][evo_occ[n_][0]] += 1
for rsc, c in votes_by_scene.items():
    escn, n = c.most_common(1)[0]
    if n >= 3: scene_corr[rsc] = escn

def remake_note(scene, func):
    parts = []
    m = re.match(r'mp(\d)', scene)
    if m: parts.append('mp0xxx=序章/系统段' if m.group(1) == '0' else f'mp{m.group(1)}xxx≈第{m.group(1)}章段')
    if scene.endswith('_ev'): parts.append('_ev=影片/事件版')
    if scene.endswith('_extk'): parts.append('_extk=附加对话')
    corr = scene_corr.get(scene)
    if corr: parts.append(f'锚点对应EVO {corr}（{evo_scene_cn(corr)}）')
    for pre, mean in FUNC_PREFIX:
        if func.startswith(pre):
            parts.append(f'{pre}前缀={mean}')
            break
    return '；'.join(parts)

def evo_note(escn):
    if not escn: return ''
    letter = escn[0] if escn[0].isalpha() else '?'
    return f'{letter}={EVO_TYPE.get(letter, "其他")}；{evo_scene_cn(escn)}'

# ---------- Old* 数据 ----------
sd_by_vid = {}
_sd_p = resolve('script_data_sc.json')
for x in (json.load(open(_sd_p, encoding='utf-8')) if _sd_p else []):
    v = x.get('voice_id') or ''
    if v.endswith('V'):
        k = v[:-1]
        if k not in sd_by_vid or (sd_by_vid[k].get('script_id', -1) < 0 <= x.get('script_id', -1)):
            sd_by_vid[k] = x
vid_text = {}
vid_to_evoblk = {}
evo_key = defaultdict(list)     # (char, norm) -> [vid]  供审查表展示被拒候选
for sc_, funcs_ in json.load(open(require('evo_structure_sc.json'), encoding='utf-8')).items():
    for fn_, f_ in funcs_.items():
        for lab_, blk_ in f_['blocks'].items():
            for t in blk_:
                if t.get('voice_id'):
                    vid_text[t['voice_id']] = t['text']
                    vid_to_evoblk[t['voice_id']] = (sc_, fn_, lab_)
                    n = cf(t['text'])
                    if n: evo_key[(t['voice_id'][:3], n)].append(t['voice_id'])
for x in json.load(open(require('additional_voice_sc.json'), encoding='utf-8')):
    vid_text.setdefault(x['voice_id'][:-1], x['text'])

# Remake 语音表(t_voice.tbl -> json): id -> {f:文件名, t:字幕文本}；无则 RemakeVoiceFilename 列留空
_tv_p = resolve(f't_voice_{GAME}.json')
t_voice = json.load(open(_tv_p, encoding='utf-8')) if _tv_p else {}

# EVO 角色显示名(char_names 由 review_agent/build_char_names.py 生成) 与 前缀归属/共用标注
_cn_p = resolve(f'char_names_{GAME}.json')
char_names = json.load(open(_cn_p, encoding='utf-8')) if _cn_p else {}
_eps = resolve(f'evo_prefix_stats_{GAME}.json')
prefix_stats = json.load(open(_eps, encoding='utf-8')) if _eps else {}
_vli = resolve(f'voice_lookup_index_{GAME}.json')
_vli_d = json.load(open(_vli, encoding='utf-8')) if _vli else {}
vote_shared = set(_vli_d.get('evo_shared_prefixes', []))
# 行级实体归属(按 场景×日文py行号, 与 RemakeScenaScriptLineno 同坐标系): 判定"本行实体"是否投票分裂
_vli_ent = _vli_d.get('entities', {})
_row_entity = {}
for _f, _recs in (_vli_d.get('lines', {}) or {}).items():
    for _ln, _r in _recs.items():
        _ek = _r.get('entity_key')
        if _ek:
            _row_entity[(_f, int(_ln))] = _ek

def row_entity_split(scene, line):
    """本行所属实体是否存在段内投票分裂(多人共用实锤); 非该实体分裂不株连"""
    ek = _row_entity.get((scene, int(line)))
    if not ek:
        return False
    return bool(_vli_ent.get(ek, {}).get('has_multi_shared_group'))

def evo_char_display(prefix):
    # 优先: EVO日文本体推导的说话人知识库(char-id槽位100%一致 + T_NAME + 文本鉴别)
    jp, _cn = evo_speaker.bank_name(prefix, GAME)
    if jp:
        return jp
    e = char_names.get(prefix) or {}
    return e.get('jpn') or e.get('eng') or ''

# 中日名字互译(speaker_names_t_name_zh 由 table_sc.pac 的 t_name.tbl 提取, 键与日文表对齐)
_tn_p = resolve(f'speaker_names_t_name_{GAME}.json')
_tn = json.load(open(_tn_p, encoding='utf-8')) if _tn_p else {}
_tnz_p = resolve(f'speaker_names_t_name_zh_{GAME}.json')
_tnz = json.load(open(_tnz_p, encoding='utf-8')) if _tnz_p else {}
def _jp_zh_by_code():
    m = {}
    for k, v in _tn.items():
        z = _tnz.get(k)
        if z and z.get('jp') and v.get('jp'):
            m[v['jp']] = z['jp']
    return m
_JP2ZH = _jp_zh_by_code()
def display_zh(jp_name, speaker_code=''):
    """日文显示名 -> 中文; 优先简中py同名调用直取, 次选t_name同码, 兜底日中名对照"""
    if not jp_name:
        return ''
    # 1) 简中py里 chr_set_display_name 的直接翻译(最准确)
    if jp_name in _disp_jp2sc:
        return _disp_jp2sc[jp_name]
    # 2) t_name 同说话人码且日文名吻合
    z = _tnz.get(str(speaker_code)) if speaker_code != '' else None
    if z and _tn.get(str(speaker_code), {}).get('jp') == jp_name and z.get('jp'):
        return z['jp']
    # 3) 日中名对照兜底
    return _JP2ZH.get(jp_name, '')
def evo_char_display_zh(prefix, jp_name=''):
    # 优先: 知识库内嵌中文名(文本鉴别确认的少数角色)
    _jp, cn = evo_speaker.bank_name(prefix, GAME)
    if cn and not jp_name:
        return cn
    if not jp_name:
        e = char_names.get(prefix) or {}
        jp_name = e.get('jpn') or e.get('eng') or ''
    return display_zh(jp_name)

def evo_speaker_notes(prefix, char_id, scene='', line=''):
    """EVO侧说话人备注: 身份判定来源 + 前缀归属 + 特殊出现(演员槽乱入等) + 实体投票分裂"""
    if not prefix:
        return ''
    parts = []
    # 说话人知识库(EVO日文本体验证): 身份与特殊出现
    kb = evo_speaker._load(GAME) or {}
    bi = kb.get('banks', {}).get(prefix)
    if bi:
        if bi.get('jpn'):
            tag = {'charid': '身份=char_id+T_NAME', 'text_verified': '身份=文本鉴别'}.get(
                bi.get('status'), '身份=已命名')
            parts.append(tag)
        else:
            parts.append('身份=未鉴别')
        sp_n = bi.get('special_count', 0)
        if sp_n:
            parts.append(f'特殊出现{sp_n}条(主角团以演员槽登场)')
    kind = prefix_stats.get(prefix, {}).get('kind')
    if kind == 'main':
        parts.append('main=单角色配音')
    elif kind == 'shared':
        parts.append('shared=多全局ID共用')
    elif kind == 'npc':
        parts.append('npc=群众/广播类(无全局ID)')
    if prefix in vote_shared:
        if scene and line and row_entity_split(scene, line):
            parts.append('本行实体投票分裂=多人共用实锤')
        else:
            parts.append('该前缀存在分裂记录(他段共用,本行未必)')
    if char_id:
        try:
            ci = int(char_id, 16)
            parts.append(f'char_id {char_id}=' + ('全局角色ID' if ci >= 0x100 else '场景局部槽/系统标记'))
        except ValueError:
            pass
    return '; '.join(parts)

def evo_struct(vid):
    b = vid_to_evoblk.get(vid)
    return f'{b[0]}/{b[1]}/{b[2]}' if b else ''

norm_chars = defaultdict(set)   # norm -> {EVO角色码}
for (c_, n_) in evo_key:
    norm_chars[n_].add(c_)

# ---------- 组装 ----------
COLS = ['RemakeVoiceID', 'RemakeOriginalVoiceID', 'RemakeScenaScriptFilename', 'RemakeScenaScriptLineno',
        'RemakeScenaScriptAddStructLineno', 'RemakeScenaScriptTranslationLineno',
        'RemakeScenaScriptTranslationAddStructLineno', 'RemakeFunction', 'RemakeBlock', 'RemakeVoiceFilename', 'VoiceReuseAlert',
        'OldScriptId', 'OldCharacterId', 'OldVoiceFilename', 'MatchType',
        'SpeakerCheck', 'SpeakerNote', 'RemakeSpeakerID', 'RemakeSpeakerName', 'RemakeSpeakerNameTranslation',
        'RemakeCharacterDisplay', 'RemakeCharacterDisplayTranslation',
        'EvoCharacterDisplay', 'EvoCharacterDisplayTranslation', 'EvoSpeakerNotes',
        'RemakeVoiceCategory', 'RemakeVoiceTranslation',
        'RemakeVoiceText', 'OldVoiceText', 'EvoScene', 'EvoFunction', 'EvoBlock',
        'Annotation']
out = []
stat = Counter()
# RemakeVoiceID: 全部用 NewIdStart(默认10000) 起的合成号（纯行标识，原始ID见 RemakeOriginalVoiceID 列）
# 原始内嵌语音表ID存在复用(同语音多行)且部分复用组匹配到不同EVO语音，不能直接还原——交由 voice_reuse_review 人工裁定
NEW_ID_START = {'fc': 100000, 'sc': 100000, '3rd': 100000}
_flag = [a for a in sys.argv if a.startswith('--new-id-start')]
if _flag:
    NEW_ID_START[GAME] = int(_flag[-1].split('=', 1)[-1]) if '=' in _flag[-1] else int(sys.argv[sys.argv.index(_flag[-1]) + 1])
syn_seq = NEW_ID_START[GAME]
n_orig_id = 0
review_m = {}   # (scene, line) -> 对应的 s4 行（审查表用）
for fn in sorted(jp):
    scene = fn[:-3]
    jp_cmds = jp[fn]['commands']
    sc_cmds = {c['line']: c for c in sc.get(fn, {'commands': []})['commands']}
    sc_adds = sc.get(fn, {'addstruct': {}})['addstruct']
    for c in jp_cmds:
        if not c['text']: continue
        as_line = jp[fn]['addstruct'].get(c['key'])
        scc = sc_cmds.get(c['line'])
        row = dict.fromkeys(COLS, '')
        _rid = c.get('rid')
        row['RemakeVoiceID'] = syn_seq
        syn_seq += 1
        if _rid:
            row['RemakeOriginalVoiceID'] = _rid
            n_orig_id += 1
        row['RemakeScenaScriptFilename'] = scene
        row['RemakeScenaScriptLineno'] = c['line']
        row['RemakeScenaScriptAddStructLineno'] = as_line or ''
        row['RemakeScenaScriptTranslationLineno'] = scc['line'] if scc else ''
        row['RemakeScenaScriptTranslationAddStructLineno'] = (sc_adds.get(scc['key']) if scc else None) or ''
        row['RemakeVoiceCategory'] = 'voice'
        row['RemakeVoiceText'] = c['text']
        row['RemakeVoiceTranslation'] = scc['text'] if scc else ''
        anno = []
        m = take_my_row(scene, c['text'])
        if m is not None:
            review_m[(scene, c['line'])] = m
            row['RemakeFunction'] = m['Function']   # 所属结构函数（TK_/EV_/QS_/ST_…）
            row['RemakeBlock'] = m['Block']         # 所属基本块（Loc_xxx/_entry）
            _rid = m.get('RemakeVoiceId')
            if _rid:
                row['RemakeVoiceFilename'] = t_voice.get(str(_rid), {}).get('f', '')
            row['SpeakerCheck'] = m['SpeakerMatch'] if m['SpeakerMatch'] != '对应' else ''
            row['SpeakerNote'] = m.get('SpeakerNote') or ''   # 说话人不确定性/共用前缀说明(s4)
            row['RemakeSpeakerID'] = m.get('Speaker') or ''
            _tn_spk = _tn.get(str(row['RemakeSpeakerID']), {})
            row['RemakeSpeakerName'] = _tn_spk.get('jp', '')   # t_name 说话人正式角色名
            row['RemakeSpeakerNameTranslation'] = _tnz.get(str(row['RemakeSpeakerID']), {}).get('jp', '')
            row['RemakeCharacterDisplay'] = m.get('RemakeDisplay') or ''   # 运行时显示名(变装/匿名)
            row['RemakeCharacterDisplayTranslation'] = display_zh(
                row['RemakeCharacterDisplay'], m.get('Speaker') or '')
        if m is None:
            row['MatchType'] = 'unmatched'
            anno.append('结构未覆盖')
        else:
            vid = m['MyVoiceId']
            if m['MatchType'] == '唯一' and vid:
                ent = sd_by_vid.get(vid)
                if ent and ent.get('script_id', -1) >= 0:
                    row['MatchType'] = 'matched'
                    row['OldScriptId'] = str(ent['script_id'])
                    row['OldCharacterId'] = ent.get('character_id', '')
                else:
                    row['MatchType'] = 'voiceonly'
                row['OldVoiceFilename'] = 'ch' + vid + 'V'
                row['OldVoiceText'] = vid_text.get(vid, '')
                row['EvoCharacterDisplay'] = evo_char_display(vid[:3])
                row['EvoCharacterDisplayTranslation'] = evo_char_display_zh(
                    vid[:3], row['EvoCharacterDisplay'])
                row['EvoSpeakerNotes'] = evo_speaker_notes(
                    vid[:3], row['OldCharacterId'], scene, c['line'])
                anno.append(f"结构匹配:{m['MatchType']}({m['Source']}) {m['Block']}")
            elif m['MatchType'] == '多候选':
                cands = [x for x in m['Candidates'].split('|') if x]
                row['MatchType'] = 'multi'
                if cands:
                    row['OldVoiceFilename'] = 'ch' + cands[0] + 'V'
                    row['OldVoiceText'] = vid_text.get(cands[0], '')
                anno.append('结构匹配多候选:' + m['Candidates'])
            else:
                row['MatchType'] = 'unmatched'
                anno.append(f"结构匹配:{m['MatchType']} {m['Block']}")
        row['Annotation'] = '; '.join(anno)
        # EVO 侧结构（additional 语音无脚本结构，留空）
        if row['OldVoiceFilename']:
            b = vid_to_evoblk.get(row['OldVoiceFilename'][2:-1])
            if b:
                row['EvoScene'], row['EvoFunction'], row['EvoBlock'] = b
        stat[row['MatchType']] += 1
        out.append(row)

# ---------- 同源语音复用冲突：同 RemakeOriginalVoiceID 的多行匹配到不同 EVO 语音 -> 打标 + 专用校对表 ----------
rid_groups = defaultdict(list)
for r in out:
    if r['RemakeOriginalVoiceID']:
        rid_groups[r['RemakeOriginalVoiceID']].append(r)
reuse_rows = []
n_alert = 0
for rid, rs in rid_groups.items():
    if len(rs) < 2: continue
    distinct = {r['OldVoiceFilename'] for r in rs} - {''}
    if len(distinct) < 2: continue   # 匹配一致(或全空)的复用无需人工
    n_alert += len(rs)
    members = '; '.join(f"{r['RemakeScenaScriptFilename']}:{r['RemakeScenaScriptLineno']}->{r['OldVoiceFilename'] or '未匹配'}" for r in rs)
    for r in rs:
        r['VoiceReuseAlert'] = f'同源语音x{len(rs)}行匹配{len(distinct)}个EVO语音'
        reuse_rows.append({
            'RemakeVoiceID': r['RemakeVoiceID'], 'RemakeOriginalVoiceID': rid,
            'RemakeScenaScriptFilename': r['RemakeScenaScriptFilename'], 'RemakeScenaScriptLineno': r['RemakeScenaScriptLineno'],
            'RemakeFunction': r['RemakeFunction'], 'RemakeVoiceText': r['RemakeVoiceText'],
            'RemakeVoiceTranslation': r['RemakeVoiceTranslation'], 'RemakeVoiceFilename': r['RemakeVoiceFilename'],
            'OldVoiceFilename': r['OldVoiceFilename'], 'OldVoiceText': r['OldVoiceText'],
            'MatchType': r['MatchType'], 'GroupMembers': members, 'Verdict': '',
        })

OUT = os.path.join(W, 'match_result_sc_detailed.csv')
with open(OUT, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=COLS)
    w.writeheader(); w.writerows(out)

REUSE_COLS = ['RemakeVoiceID', 'RemakeOriginalVoiceID', 'RemakeScenaScriptFilename', 'RemakeScenaScriptLineno',
              'RemakeFunction', 'RemakeVoiceText', 'RemakeVoiceTranslation', 'RemakeVoiceFilename',
              'OldVoiceFilename', 'OldVoiceText', 'MatchType', 'GroupMembers', 'Verdict']
REUSE_OUT = os.path.join(W, f'voice_reuse_review_{GAME}.csv')
with open(REUSE_OUT, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=REUSE_COLS)
    w.writeheader(); w.writerows(reuse_rows)

# ---------- 说话人校对清单（长表：一行一个候选，经 RemakeVoiceID 关联主表） ----------
REVIEW = os.path.join(W, f'speaker_review_{GAME}.csv')
REVIEW_COLS = ['RemakeVoiceID', 'RemakeOriginalVoiceID', 'RemakeScenaScriptFilename', 'RemakeScenaScriptLineno',
               'RemakeFunction', 'RemakeBlock', 'RemakeVoiceFilename', 'RemakeNote', 'ReviewReason', 'SpeakerChar', 'SpeakerNote',
               'RemakeVoiceText', 'RemakeVoiceTranslation',
               'CandRole', 'CandVoiceId', 'CandChar', 'CandVoiceText',
               'CandEvoScene', 'CandEvoFunction', 'CandEvoBlock', 'EvoNote', 'Verdict']

review_rows = []
for r in out:
    if not r['SpeakerCheck']:
        continue
    scene = r['RemakeScenaScriptFilename']
    line = r['RemakeScenaScriptLineno']
    m = review_m[(scene, line)]   # 对应 s4 行(SpeakerChar/Candidates/SpeakerMatch)
    reason = m['SpeakerMatch'].split('(')[0]
    cv = r['OldVoiceFilename'][2:-1] if r['OldVoiceFilename'] else ''
    # 候选集合：当前匹配(若有) + 被拒候选
    entries = []
    if cv:
        entries.append((cv, f'当前匹配({reason})'))
    if reason == '同文本异角色':
        n = cf(r['RemakeVoiceText'])
        others = sorted(norm_chars.get(n, set()) - ({m['SpeakerChar']} if m['SpeakerChar'] else set()))
        for c in others:
            for v in sorted(evo_key[(c, n)]):
                entries.append((v, '被拒候选·同文本异角色'))
    else:
        for v in m['Candidates'].split('|'):
            if v and v != cv:
                entries.append((v, '被拒候选·其他'))
    for v, role in entries:
        b = vid_to_evoblk.get(v)
        review_rows.append({
            'RemakeVoiceID': r['RemakeVoiceID'],
            'RemakeScenaScriptFilename': scene,
            'RemakeScenaScriptLineno': line,
            'RemakeFunction': r['RemakeFunction'],
            'RemakeBlock': r['RemakeBlock'],
            'RemakeVoiceFilename': r['RemakeVoiceFilename'],
            'RemakeNote': remake_note(scene, r['RemakeFunction']),
            'ReviewReason': reason,
            'SpeakerChar': m['SpeakerChar'],
            'SpeakerNote': m.get('SpeakerNote') or '',
            'RemakeVoiceText': r['RemakeVoiceText'],
            'RemakeVoiceTranslation': r['RemakeVoiceTranslation'],
            'CandRole': role,
            'CandVoiceId': v,
            'CandChar': v[:3],
            'CandVoiceText': (vid_text.get(v) or '').replace('\n', '\\n'),
            'CandEvoScene': b[0] if b else '', 'CandEvoFunction': b[1] if b else '', 'CandEvoBlock': b[2] if b else '',
            'EvoNote': evo_note(b[0]) if b else '',
            'Verdict': '',   # 审查者填写
        })

with open(REVIEW, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=REVIEW_COLS)
    w.writeheader(); w.writerows(review_rows)

leftover = len(mine) - consumed
has_trans = sum(1 for r in out if r['RemakeVoiceTranslation'])
has_add = sum(1 for r in out if r['RemakeScenaScriptAddStructLineno'])
n_review = sum(1 for r in out if r['SpeakerCheck'])
print(f'总行: {len(out)}  (提取自 {len(jp)} 个脚本)')
print(f'MatchType: {dict(stat)}')
print(f'RemakeVoiceID: 合成ID {syn_seq - NEW_ID_START[GAME]} 行 (起始{NEW_ID_START[GAME]}), 其中带原始语音ID {n_orig_id} 行(见RemakeOriginalVoiceID列)')
print(f'同源语音复用冲突: {n_alert} 行 -> {os.path.basename(REUSE_OUT)}')
print(f'说话人待校对: {n_review} 台词行 -> {len(review_rows)} 候选行(长表) -> {os.path.basename(REVIEW)}')
print(f'中文翻译: {has_trans}/{len(out)} = {has_trans/len(out)*100:.1f}%   add_struct行号: {has_add}')
print(f's4行未消耗: {leftover} (应为0)')
print(f'输出: {OUT}')

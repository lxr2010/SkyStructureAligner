"""校对Agent工具套组(rt) v2 —— 四组命令, 一条命令一个干净JSON

【领包 / 提交】
  claim <代理ID> [场景 函数]     领块+工作包(租约防撞; 无场景参数=领队首待办)
  pack <场景> <函数>             重取工作包(不占租约, 丢包时用)
  release <场景> <函数>          异常中断时释放租约
  submitmap <场景> <函数> '<{"行号":"OK",..}>'|-    整块 OK/UNRESOLVED(服务端回填id, 已裁定跳过)
  submitmany '<[裁定,..]>'|-                       提交裁定(单条也用它; 唯一提交通道)

【证据四件套】标准流程: runcheck 发现 issue → vid 查证 → findmany 检索 → rowhint 夹逼
  runcheck <场景> <函数>         块体检: 序号断裂/倒序/跨场景/take缺口候选/文本低相似/复用分歧
  vid <10位语音ID>               take档案: 结构定位/官方复用structure_refs/补录文本/AT9存在性
                                 (matched_by_pipeline是我方结果=循环证据, 勿当复用依据)
  find <文本> [--char 003] [--scene 047|T0700] [--evoscene T0131_1] [--limit 8]
                                 归一化检索(带过滤零命中自动无过滤兜底; --scene 兼容两种格式)
  findmany '<[["文本","003"],..]>'|-               批量检索(整批一个进程, 优先用这个)
  rowhint <场景> <函数> <行号|ID>  行级夹逼: 邻锚区间+缺口候选+组冲突警报

【说话人】
  speaker <场景> <行号>          行级辨析(说话人/显示名/EVO匹配/不确定性)
  speaker --entity <ID|显示名>   实体级 per-scene 前缀映射;  --list <ID> 列实体
  bank <三位码|完整vid>          bank 全场景角色档案(身份/槽位/显示名/特殊出现)

【运营】(主智能体/人工用, 校对代理勿用)
  autook                         全库确定性自检, 通过块整块自动写入OK
"""
import csv, json, os, re, sys
from collections import defaultdict
try:  # 控制台可能为GBK代码页, 强制UTF-8输出防日文乱码
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from paths import W, resolve
import evo_speaker_info as espeaker

GAME = 'sc'
SUF = '_sc'
_here = os.path.dirname(os.path.abspath(__file__))
_det_p = resolve('match_result_sc_detailed.csv')
det = None          # 懒加载: 仅需账本的命令使用
def _det():
    global det
    if det is None:
        det = list(csv.DictReader(open(_det_p, encoding='utf-8')))
    return det

_det_by_id_cache = None   # 懒加载: id -> 行, 提交校验用(未匹配行禁OK/WRONG)
def _det_by_id():
    global _det_by_id_cache
    if _det_by_id_cache is None:
        _det_by_id_cache = {str(r['RemakeVoiceID']): r for r in _det()}
    return _det_by_id_cache

remake_st = None    # 懒加载: 仅 pack 使用
def _remake_st():
    global remake_st
    if remake_st is None:
        remake_st = json.load(open(resolve(f'remake_structure{SUF}.json'), encoding='utf-8'))
    return remake_st

# ---------- 索引(带pickle缓存: 构建约4s, unpickle约0.2s) ----------
_CACHE_VER = 6   # 代码变更(如索引结构/归一化规则)时递增
_CACHE_F = os.path.join(_here, 'review_pack', f'.rt_cache_v{_CACHE_VER}.pkl')

def _src_newer_than_cache():
    for _n in ('evo_structure_sc.json', 'additional_voice_sc.json', 'match_result_sc_detailed.csv'):
        _p = resolve(_n)
        if _p and os.path.getmtime(_p) > os.path.getmtime(_CACHE_F):
            return True
    return False

def _wait_for_building_cache(timeout=30):
    """另一进程正在建缓存(tmp文件新鲜)时等它完成, 避免重复构建"""
    import glob as _glob, time as _time
    for _ in range(timeout * 2):
        _tmps = [f for f in _glob.glob(_CACHE_F + '.tmp*') if os.path.exists(f)]
        if not _tmps:
            return os.path.exists(_CACHE_F)   # 无tmp: 要么已建完, 要么没人建
        newest = max(os.path.getmtime(f) for f in _tmps)
        if _time.time() - newest > 60:
            return False   # tmp过期(建缓存进程死了), 自己重建
        _time.sleep(0.5)
    return False   # 超时, 自己重建

vid_idx = {}          # vid -> {scene,func,block,talk_num,speaker,text}
norm_list = []        # [(norm, vid_or_None, scene, func, text, voiced)]
vid_pool = {}         # (录音组, take) -> {vid: text}  [rowhint 区间候选]
unvoiced_norms = set()  # 无语音行的norm集合(用于do_find快速区分)
vid_occ = defaultdict(list)   # vid -> ['scene/func', ...]  结构内出现处(官方复用证据, 非循环)
at9_set = set()               # AT9 音频文件清单(bare vid)——未引用录音存在性

_use_cache = os.path.exists(_CACHE_F) and not _src_newer_than_cache()
if not _use_cache and _wait_for_building_cache():
    _use_cache = os.path.exists(_CACHE_F) and not _src_newer_than_cache()
if _use_cache:
    import pickle
    with open(_CACHE_F, 'rb') as _f:
        vid_idx, norm_list, unvoiced_norms, extra, BLOCK_KEYS, vid_occ, at9_set, det_slim = pickle.load(_f)
    from synonyms import normalize as _norm
    def cnorm(t):
        return re.sub(r'\s+', '', _norm(t).replace('\u3046\u3099', 'う').replace('ヴ', 'う')) if t else ''
else:
    evo = json.load(open(resolve(f'evo_structure{SUF}.json'), encoding='utf-8'))
    for _sc, _fns in evo.items():
        for _fn, _f in _fns.items():
            for _lab, _blk in _f['blocks'].items():
                for t in _blk:
                    if t.get('voice_id'):
                        vid = t['voice_id']
                        vid_occ[vid].append(f'{_sc}/{_fn}')
                        vid_idx[vid] = {'scene': _sc, 'func': _fn, 'block': _lab,
                                        'talk_num': t['talk_num'], 'speaker': t['speaker'], 'text': t['text'],
                                        'msg_id': t.get('msg_id'), 'cast': t.get('cast'),
                                        'speaker_kind': t.get('speaker_kind'),
                                        'speaker_name': t.get('speaker_name'),
                                        'speaker_name_cn': t.get('speaker_name_cn'),
                                        'bank': t.get('bank') or vid[:3]}
    try:
        from synonyms import normalize as _norm
        _norm('')
    except Exception:
        _norm = lambda x: x
    extra = {}
    _p = resolve('additional_voice_sc.json')
    if _p:
        for _it in json.load(open(_p, encoding='utf-8')):
            _v = _it.get('voice_id', '')
            _v = _v[2:] if _v.startswith('ch') else _v
            _v = _v[:-1] if _v.endswith('V') else _v
            if _v and _v not in vid_idx and _it.get('text'):
                extra[_v] = _it['text']
    BLOCK_KEYS = {(r['RemakeScenaScriptFilename'], r['RemakeFunction']) for r in _det()}
    def cnorm(t):
        return re.sub(r'\s+', '', _norm(t).replace('\u3046\u3099', 'う').replace('ヴ', 'う')) if t else ''
    for vid, info in vid_idx.items():
        norm_list.append((cnorm(info['text']), vid, info['scene'], info['func'], info['text'], True))
    for _v, _t in extra.items():
        norm_list.append((cnorm(_t), _v, 'additional', '', _t, True))
    _voiced_norms = {x[0] for x in norm_list}
    for _sc, _fns in evo.items():
        for _fn, _f in _fns.items():
            for _lab, _blk in _f['blocks'].items():
                for t in _blk:
                    if not t.get('voice_id') and t.get('text'):
                        _n = cnorm(t['text'])
                        if _n and _n not in _voiced_norms:
                            norm_list.append((_n, None, _sc, _fn, t['text'], False))
                            unvoiced_norms.add(_n)
    # AT9 文件清单: 未引用录音(缺口take)存在性的唯一证据
    for _p in (resolve('at9_names_sc.csv'), os.path.join(W, 'at9_names_sc.csv')):
        if _p and os.path.isfile(_p):
            with open(_p, encoding='utf-8-sig') as _f:
                for _ln in _f:
                    _v = _ln.strip().strip('"').rstrip('Vv').strip()
                    if len(_v) == 10 and _v.isdigit():
                        at9_set.add(_v)
            break
    import pickle
    os.makedirs(os.path.dirname(_CACHE_F), exist_ok=True)
    _tmp = _CACHE_F + f'.tmp{os.getpid()}'
    with open(_tmp, 'wb') as _f:
        pickle.dump((vid_idx, norm_list, unvoiced_norms, extra, BLOCK_KEYS, vid_occ, at9_set,
                 [(r['RemakeScenaScriptFilename'], r['RemakeScenaScriptLineno'], r['OldVoiceFilename'][2:-1])
                  for r in _det() if r.get('OldVoiceFilename')]), _f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(_tmp, _CACHE_F)   # 原子改名, 防并发读到半写文件

# (录音组, take序号) -> {vid: text}: rowhint 区间候选(派生自 norm_list, 不入缓存)
for _e in norm_list:
    if _e[1] and _e[5]:
        vid_pool.setdefault((_e[1][3:6], _e[1][6:]), {})[_e[1]] = _e[4]

def out(msg):
    print(json.dumps(msg, ensure_ascii=False, indent=1))

# ---------- 待办/认领 ----------
_CLAIMS = os.path.join(_here, 'review_pack', 'claims.json')
_LOCKF = os.path.join(_here, 'review_pack', '.claims.lock')
LEASE_STALE = 45 * 60  # 租约过期秒数(异常中断的代理可被重新认领; 最长块实测约35分钟)

def _done_ids():
    p = os.path.join(_here, 'review_pack', 'verdicts.jsonl')
    done = set()
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if not line: continue
            try: done.add(str(json.loads(line)['RemakeVoiceID']))
            except Exception: pass
    return done

def _claims_read():
    if not os.path.exists(_CLAIMS): return {}
    try: return json.load(open(_CLAIMS, encoding='utf-8'))
    except Exception: return {}

def _locked_update(fn):
    """claims.json 的跨进程读-改-写(独立锁文件首字节互斥); fn(claims)->新claims或None"""
    if not os.path.exists(_LOCKF):
        with open(_LOCKF, 'wb') as f0:
            f0.write(b'\x00')
    f = open(_LOCKF, 'r+b')
    try:
        try:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            def _unlock():
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            def _unlock():
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        claims = _claims_read()
        new = fn(claims)
        if new is not None:
            tmp = _CLAIMS + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as tf:
                json.dump(new, tf, ensure_ascii=False)
            os.replace(tmp, _CLAIMS)
        _unlock()
    finally:
        f.close()

def _ranked_pending():
    """返回 [(key,(total,matched,flags,pending)),..] 仅含未完成块, 排序同todo优先级"""
    done = _done_ids()
    todo = defaultdict(lambda: [0, 0, 0, 0])
    for r in _det():
        k = (r['RemakeScenaScriptFilename'], r['RemakeFunction'])
        todo[k][0] += 1
        if r['OldVoiceFilename']: todo[k][1] += 1
        if r['VoiceReuseAlert'] or r['SpeakerCheck']: todo[k][2] += 1
        if str(r['RemakeVoiceID']) not in done: todo[k][3] += 1
    return sorted(((k, v) for k, v in todo.items() if v[3] > 0),
                  key=lambda x: (x[1][2] == 0, x[1][1] == 0, -x[1][2]))

def cmd_claim(agent, scene=None, func=None):
    """直接领任务包: 认领待办块(租约防并发撞块)并返回精简工作包。
    claim <代理ID>            -> 自动取队列首块
    claim <代理ID> <场景 函数> -> 认领指定块(主智能体分配模式; 块名不存在直接报错, 不写租约)"""
    import time
    res = {}
    if scene and func and (scene, func) not in BLOCK_KEYS:
        out({'error': f'块不存在: {scene}/{func} (检查场景/函数名拼写)', 'hint': '无参 claim 可自动领队首块'}); return
    def fn(claims):
        now = time.time()
        claims = {k: v for k, v in claims.items() if now - v.get('ts', 0) < LEASE_STALE}
        if scene and func:
            key = f'{scene}||{func}'
            holder = claims.get(key)
            if holder and holder.get('agent') != agent:
                res['error'] = f'{scene}/{func} 已被 {holder["agent"]} 认领({int((now - holder["ts"]) / 60)}分钟前); 异常中断可用 release 释放'
                return claims
            claims[key] = {'agent': agent, 'ts': now}
            res['claimed'] = key
            return claims
        busy = {k for k, v in claims.items() if v.get('agent') != agent}
        for (s, f), _v in _ranked_pending():
            k = f'{s}||{f}'
            if k in busy:
                continue
            claims[k] = {'agent': agent, 'ts': now}
            res['claimed'] = k
            return claims
        res['error'] = '没有可领取的待办块(全部已完成或被其他代理认领)'
        return claims
    _locked_update(fn)
    if res.get('error'):
        out(res); return
    s, f = res['claimed'].split('||')
    pack = _get_pack(s, f, _done_ids())
    if pack is None:
        out({'error': f'认领块 {s}/{f} 不在匹配表中'}); return
    pack['claimed_by'] = agent
    out(pack)

def cmd_release(scene, func):
    def fn(claims):
        claims.pop(f'{scene}||{func}', None)
        return claims
    _locked_update(fn)
    out({'released': f'{scene}/{func}'})

# ---------- 子命令 ----------
def _voice_text(v):
    """返回 (text, scene, func, block): evo结构优先, 其次additional补充表; 无则None"""
    info = vid_idx.get(v)
    if info:
        return info['text'], info['scene'], info['func'], info.get('block', '')
    if v in extra:
        return extra[v], 'additional', '', ''
    return None

def _get_pack(scene, func, done):
    """精简工作包: 行内直接合并已匹配vid的EVO信息, 剔除冗余列(约为旧版体积1/5)"""
    rows = [r for r in _det() if r['RemakeScenaScriptFilename'] == scene and r['RemakeFunction'] == func]
    if not rows:
        return None
    rseq = []
    for fn, f in _remake_st().get(scene, {}).items():
        for lab, blk in f['blocks'].items():
            for t in blk:
                rseq.append((t['text'], t.get('speaker'), t.get('rid')))
    rp = 0
    slim = []
    for r in rows:
        while rp < len(rseq) and rseq[rp][0] != r['RemakeVoiceText']:
            rp += 1
        spk = rseq[rp][1] if rp < len(rseq) else None
        rrid = rseq[rp][2] if rp < len(rseq) else None
        if rp < len(rseq):
            rp += 1
        item = {'id': r['RemakeVoiceID'], 'line': int(r['RemakeScenaScriptLineno']),
                'text': r['RemakeVoiceText'], 'spk': spk}
        if rrid: item['rrid'] = rrid
        if r.get('OldCharacterId'): item['evo_char'] = r['OldCharacterId']
        if r.get('RemakeVoiceFilename'): item['rvfile'] = r['RemakeVoiceFilename']
        if r.get('RemakeBlock'): item['rblock'] = r['RemakeBlock']
        v = r['OldVoiceFilename']
        if v:
            vv = v[2:-1] if v.startswith('ch') else v
            info = vid_idx.get(vv)
            item['vid'] = vv
            item['evo_seq'] = vv[6:]
            if info:
                item['evo_text'] = info['text']
                item['evo_scene'] = info['scene']
                item['evo_func'] = info['func']
                item['evo_block'] = info.get('block', '')
                item['talk_num'] = info.get('talk_num')
                item['evo_speaker'] = info.get('speaker')
                item['evo_speaker_kind'] = info.get('speaker_kind')
                if info.get('speaker_name'):
                    item['evo_speaker_name'] = info['speaker_name']
                _bjp, _bcn = espeaker.bank_name(vv[:3], GAME)
                if _bjp:
                    item['evo_bank_name'] = _bjp
                if _bcn:
                    item['evo_bank_name_cn'] = _bcn
                _spn = espeaker.special_count(vv[:3], GAME)
                if _spn:
                    item['evo_special'] = _spn
            elif vv in extra:
                item['evo_text'] = extra[vv]
                item['evo_scene'] = 'additional'
            else:
                item['evo_text'] = '(不在evo结构/补充表)'
        if r['VoiceReuseAlert']: item['reuse_alert'] = r['VoiceReuseAlert']
        if r['SpeakerCheck']: item['spk_check'] = r['SpeakerCheck']
        if r.get('RemakeOriginalVoiceID'): item['orig_rid'] = r['RemakeOriginalVoiceID']
        if str(r['RemakeVoiceID']) in done: item['done'] = True
        slim.append(item)
    return {'scene': scene, 'function': func, 'total': len(rows),
            'matched': sum(1 for r in rows if r['OldVoiceFilename']),
            'pending': sum(1 for r in rows if str(r['RemakeVoiceID']) not in done),
            'rows': slim,
            '字段说明': 'id=裁定主键; line=Remake行号; spk=remake侧说话人码(参考,勿作检索过滤); evo_char=旧角色码(EVO三位,findmany过滤/说话人核对用); rvfile=remake语音文件名; rblock/evo_block=新旧块标签; vid=已匹配语音ID(evo_seq=录音序号, evo_text/scene/func/talk_num=EVO侧定位); evo_speaker/kind=EVO侧speaker与语义(charid=全局角色ID/actor_slot=场景演员槽/narration/system); evo_speaker_name=EVO说话人日文名(T_NAME/知识库); evo_bank_name(_cn)=语音bank角色名(中); evo_special>0=该角色有演员槽乱入记录; rrid/orig_rid=复用组键; done=true已有裁定(跳过)'}

def cmd_pack(scene, func):
    pack = _get_pack(scene, func, _done_ids())
    if pack is None:
        out({'error': f'未找到 {scene}/{func}'}); return
    out(pack)

def cmd_vid(vid):
    vid = vid.lstrip('ch').rstrip('V')
    refs = [f"{f}:{ln}" for f, ln, v in det_slim if v == vid]
    _bjp, _bcn = espeaker.bank_name(vid[:3], GAME)
    struct_refs = vid_occ.get(vid, [])
    base = {'vid': vid, 'char': vid[:3], 'voice_scene': vid[3:6], 'seq': vid[6:],
            'bank_name': _bjp, 'bank_name_cn': _bcn,
            'bank_special_count': espeaker.special_count(vid[:3], GAME),
            'matched_by_pipeline': refs[:5],
            '_warn_matched_by_pipeline': '此字段=我方匹配结果(循环证据, 不能当官方复用依据)',
            'structure_refs': struct_refs,
            'official_reuse': len(struct_refs) > 1,
            'at9_exists': vid in at9_set}
    info = vid_idx.get(vid)
    if info:
        out({'vid': vid, 'found': True, 'source': 'evo', **info, **base}); return

    if vid in extra:
        base.pop('_warn_matched_by_pipeline', None)
        out({'vid': vid, 'found': True, 'source': 'additional', 'text': extra[vid],
             'unreferenced_take': True, **base,
             'note': '未被EVO脚本引用的录音(补录表)——若行位缺口吻合可作 gap-fill 候选, 须核对文本相似度'}); return
    hint = ('AT9音频存在但结构/补录均无引用——未引用录音, 行位缺口吻合时可听辨裁定'
            if vid in at9_set else '不在evo_structure/补录表/AT9清单——可能不存在或为其他游戏')
    out({'vid': vid, 'found': False, 'unreferenced_take': vid in at9_set, 'note': hint})

def do_find(text, char=None, scene=None, limit=8, evoscene=None):
    from rapidfuzz import fuzz
    n = cnorm(text)
    # 参数校验: 错误输入报错而非静默空结果
    errs = []
    if not n or len(n) < 2:
        errs.append(f'查询文本归一化后不足2字符: {text!r}')
    if scene is not None and isinstance(scene, str) and re.match(r'^[A-Za-z]', scene):
        evoscene = scene; scene = None   # 容错: --scene T0700 视为 --evoscene
    if char is not None and (not isinstance(char, str) or not re.fullmatch(r'\d{3}', str(char))):
        errs.append(f'--char 须为3位数字角色码(vid前3位, 如001), 收到: {char!r}')
    if scene is not None and (not isinstance(scene, str) or not re.fullmatch(r'\d{3}', str(scene))):
        errs.append(f'--scene 须为3位数字语音场景(vid第4-6位, 如047), 收到: {scene!r}——EVO结构场景名请用 --evoscene')
    if evoscene is not None:
        _valid_evoscenes = {info['scene'] for info in vid_idx.values()} | {'additional'}
        if evoscene not in _valid_evoscenes:
            errs.append(f'--evoscene {evoscene!r} 不是有效EVO结构场景名(如T0131_1, 可用vid命令查看已有场景)')
    if errs:
        return {'error': '; '.join(errs)}
    hits = []
    has_unvoiced_exact = False
    for nt, vid, sc, fn, raw, voiced in norm_list:
        if scene and (vid is None or vid[3:6] != scene): continue
        if evoscene and sc != evoscene: continue
        if char and (vid is None or vid[:3] != char): continue
        if nt == n:
            sim = 100.0
            if not voiced: has_unvoiced_exact = True
        else:
            sim = fuzz.ratio(n, nt)
            if sim < 60: continue
        h = {'vid': vid, 'sim': round(sim, 1), 'scene': sc, 'func': fn, 'text': raw[:60]}
        if not voiced:
            h['voiced'] = False
            h['note'] = 'EVO有此台词行但未配音(NO_VOICE)——结构验证参考, 不可配语音'
        hits.append(h)
    hits.sort(key=lambda x: (-x['sim'], x.get('voiced', True) is False))
    out = {'query': text, 'norm': n, 'hits': hits[:limit],
           'unvoiced_exact_match': has_unvoiced_exact}
    # 双程兜底: 带过滤且无 sim==100 有声命中时, 自动补一轮无过滤检索
    if (char or scene or evoscene) and not any(h['sim'] == 100 and h.get('voiced', True) for h in hits):
        unhits = []
        for nt, vid, sc, fn, raw, voiced in norm_list:
            if nt == n:
                sim = 100.0
            else:
                sim = fuzz.ratio(n, nt)
                if sim < 60: continue
            if not voiced: continue
            unhits.append({'vid': vid, 'sim': round(sim, 1), 'scene': sc, 'func': fn, 'text': raw[:60]})
        unhits.sort(key=lambda x: -x['sim'])
        if unhits:
            out['unfiltered_fallback'] = unhits[:5]
            out['note_fallback'] = '过滤检索无全等命中, 已自动追加无过滤检索(前5)'
    return out

def cmd_find(text, char=None, scene=None, limit=8, evoscene=None):
    r = do_find(text, char, scene, limit, evoscene)
    r['note'] = ('sim=100为归一化全等; 过滤: --char=vid前3位角色码, --scene=vid第4-6位语音场景数字3位(如047), '
                 '--evoscene=EVO结构场景名(如T0131_1)。注意: 结果hits里的scene字段是EVO结构场景名, 与--scene过滤值不是同一体系')
    out(r)

USAGES = {
    'submitmany-single': "单条裁定也用 submitmany: submitmany '[{\"RemakeVoiceID\":\"102345\",\"task\":\"A\",\"verdict\":\"OK\",\"reason\":\"文本全等\"}]'",
    'submitmany': "submitmany '<JSON数组>'（外层单引号）或 submitmany - 后接 heredoc(<<'EOF' ... EOF)。例: [{\"RemakeVoiceID\":\"102345\",\"task\":\"A\",\"verdict\":\"OK\",\"reason\":\"文本全等\"}]",
    'submitmap': "submitmap <场景> <函数> '<映射JSON>' 或 submitmap <场景> <函数> - 后接 heredoc。服务端回填id、自动跳过已裁定行; 仅OK/UNRESOLVED, 特殊裁定走submitmany。例: {\"44548\":\"OK\",\"44549\":\"UNRESOLVED\"}",
    'findmany': "findmany '<JSON数组>' 或 findmany - 后接 heredoc。元素: \"文本\" 或 [\"文本\",\"角色码\",\"语音场景3位\",\"EVO场景名\"]（后三项可选）。角色码=vid前3位; 语音场景=vid第4-6位数字(如047); EVO场景名=如T0131_1(与--evoscene同义)。例: [[\"おはよう\",\"001\"],[\"……\",null,\"047\"]]",
}

def _usage_err(cmd, err):
    return {'error': err, 'usage': USAGES.get(cmd, '见 rt.py 文件头用法')}

def _raw_arg(rest):
    """批量命令取参: 任一参数为'-'时读stdin(字节层UTF-8); 否则剩余参数空格连接(容错漏引号被shell拆散); 无参数返回None"""
    if '-' in rest:
        return sys.stdin.buffer.read().decode('utf-8')
    if not rest:
        return None
    return ' '.join(rest)

def _jload(raw):
    """容错JSON解析: 本机heredoc会混入\r等控制字符, 首次失败后去\r重试, 再失败去全部控制符重试"""
    try:
        return json.loads(raw)
    except Exception:
        pass
    try:
        return json.loads(raw.replace('\r', ''))
    except Exception:
        pass
    return json.loads(''.join(ch for ch in raw if ch >= ' ' or ch == '\t'))

def cmd_findmany(raw):
    """批量检索: 一个进程跑整批find, 消灭逐条调用的重复数据加载(~2.2s/次)
    原始数据仅加载一次, 消灭逐条调用的重复加载(~2.2s/次)
    raw: JSON数组; 元素为 文本 或 [文本,角色码] 或 [文本,角色码,场景码]"""
    try:
        items = _jload(raw)
    except Exception as e:
        out(_usage_err('findmany', f'JSON解析失败: {e}')); return
    if not isinstance(items, list):
        out({'error': 'findmany 需要 JSON 数组'}); return
    results = []
    for it in items:
        if isinstance(it, str):
            text, ch, sc, ev = it, None, None, None
        elif isinstance(it, list) and it and isinstance(it[0], str):
            text = it[0]
            ch = it[1] if len(it) > 1 else None
            sc = it[2] if len(it) > 2 else None
            ev = it[3] if len(it) > 3 else None
        else:
            results.append({'error': f'非法元素: {it!r}'})
            continue
        results.append(do_find(text, ch, sc, evoscene=ev))
    out({'results': results, 'count': len(results),
         'note': 'sim=100为归一化全等; 元素[文本,char,scene,evoscene]均可选: char=角色码3位, scene=语音场景数字3位(vid第4-6位), evoscene=EVO结构场景名(如T0131_1); 结果hits的scene字段是EVO结构场景名'})

def cmd_runcheck(scene, func):
    rows = [r for r in _det() if r['RemakeScenaScriptFilename'] == scene and r['RemakeFunction'] == func]
    matched = [r for r in rows if r['OldVoiceFilename']]
    issues = []
    vids = [(r, r['OldVoiceFilename'][2:-1]) for r in matched]
    # 序号连续性(按Remake行序)
    for i in range(1, len(vids)):
        a, b = vids[i-1][1], vids[i][1]
        if a[3:6] == b[3:6] and a[:3] == b[:3]:
            gap = int(b[6:]) - int(a[6:])
            if gap < 0:
                issues.append({'type': '序号倒序', 'at': vids[i][0]['RemakeScenaScriptLineno'],
                               'a': a, 'b': b, 'note': '同场景同角色, 录音序号倒退'})
            elif gap > 30:
                issues.append({'type': '序号断裂', 'at': vids[i][0]['RemakeScenaScriptLineno'],
                               'a': a, 'b': b, 'gap': gap, 'note': '同场景跳号过大, 检查中间是否漏配'})
            elif gap >= 2:
                # take缺口: 断口序号若在补录表/结构池中, 附候选(缺口填回须核对文本相似度)
                for t in range(int(a[6:]) + 1, int(b[6:])):
                    tk = f'{t:04d}'
                    for gv, gtxt in vid_pool.get((a[3:6], tk), {}).items():
                        issues.append({'type': 'take缺口候选', 'at': vids[i][0]['RemakeScenaScriptLineno'],
                                       'a': a, 'b': b, 'gap_vid': gv,
                                       'gap_text': (gtxt or '')[:36], 'gap_source': '补录' if gv in extra else '结构',
                                       'note': '断口序号存在录音——若缺口位台词与gap_text相似可考虑改配, 须vid查证'})
                        break
        elif a[3:6] != b[3:6]:
            issues.append({'type': '跨场景', 'at': vids[i][0]['RemakeScenaScriptLineno'],
                           'a': a, 'b': b, 'note': '相邻行跳到另一EVO场景(确认是否官方复用/事件切换)'})
    # 夹心跨组: 行i的组与前后邻行组都不同, 且前后邻同组、序号递增 → 前后邻之间是缺口(经典缺口异常位)
    for i in range(1, len(vids) - 1):
        _p, _c, _n = vids[i-1][1], vids[i][1], vids[i+1][1]
        if _p[3:6] == _n[3:6] and _c[3:6] != _p[3:6] and int(_n[6:]) > int(_p[6:]):
            _ta, _tb = int(_p[6:]), int(_n[6:])
            _fired = False
            for _t in range(_ta + 1, _tb):
                _tk = f'{_t:04d}'
                for _gv, _gtxt in vid_pool.get((_p[3:6], _tk), {}).items():
                    issues.append({'type': 'take缺口候选', 'at': vids[i][0]['RemakeScenaScriptLineno'],
                                   'cur': _c, 'nbr_a': _p, 'nbr_b': _n, 'gap_vid': _gv,
                                   'gap_text': (_gtxt or '')[:36],
                                   'gap_source': '补录' if _gv in extra else '结构',
                                   'note': '现配为跨组支且邻行序号在此断开——缺口录音存在, vid查证文本相似度后裁定'})
                    _fired = True
                    break
                if _fired: break
    # 文本相似
    from rapidfuzz import fuzz
    for r in matched:
        v = r['OldVoiceFilename'][2:-1]
        vt = _voice_text(v)
        if not vt: continue
        sim = fuzz.ratio(cnorm(r['RemakeVoiceText']), cnorm(vt[0]))
        if sim < 70 and len(cnorm(r['RemakeVoiceText'])) >= 6:
            issues.append({'type': '文本低相似', 'at': r['RemakeScenaScriptLineno'],
                           'remake': r['RemakeVoiceText'][:30], 'evo': vt[0][:30], 'sim': round(sim, 1)})
    # 复用组
    from collections import Counter
    rid_groups = defaultdict(list)
    for r in rows:
        if r.get('RemakeOriginalVoiceID'):
            rid_groups[r['RemakeOriginalVoiceID']].append(r)
    for rid, rs in rid_groups.items():
        distinct = {r['OldVoiceFilename'] for r in rs} - {''}
        if len(rs) > 1 and len(distinct) > 1:
            issues.append({'type': '复用组分歧', 'rid': rid,
                           'members': [f"{r['RemakeScenaScriptLineno']}->{r['OldVoiceFilename'] or '未配'}" for r in rs]})
    out({'scene': scene, 'function': func, 'checked': len(matched), 'issues': issues,
         'note': 'issues为空=体检通过; 逐条按任务书规则判定'})

def append_locked(path, text):
    """并发安全追加(verdicts.jsonl 多代理同时提交): 锁文件 + O_APPEND"""
    import time
    lock = path + '.lock'
    _ms = None
    try:
        import msvcrt as _ms
    except ImportError:
        pass
    with open(lock, 'w') as _lf:
        if _ms:
            for _ in range(50):
                try:
                    _ms.locking(_lf.fileno(), _ms.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        try:
            with open(path, 'a', encoding='utf-8') as _f:
                _f.write(text)
        finally:
            if _ms:
                try: _ms.locking(_lf.fileno(), _ms.LK_UNLCK, 1)
                except Exception: pass

def check_verdict(v):
    """裁定JSON校验: 返回错误串或None"""
    if not isinstance(v, dict):
        return '裁定必须是JSON对象'
    rid = v.get('RemakeVoiceID')
    if rid is None or str(rid).strip() == '':
        return '缺少 RemakeVoiceID'
    vd = v.get('verdict')
    if vd not in ('OK', 'WRONG', 'SUSPECT', 'FOUND', 'CANDIDATES', 'NO_VOICE', 'UNRESOLVED'):
        return 'verdict 必须是 OK/WRONG/SUSPECT/FOUND/CANDIDATES/NO_VOICE/UNRESOLVED 之一'
    if v.get('task') is not None and v.get('task') not in ('A', 'B'):
        return 'task 只能是 A(已配行) 或 B(未配行), 可省略'
    cv_ = str(v.get('correct_vid') or '')
    if vd in ('WRONG', 'FOUND') and cv_:
        d = re.sub(r'\D', '', cv_)
        if len(d) != 10:
            return 'correct_vid 须为10位数字'
    return None

def cmd_submitmany(raw):
    """批量提交: 一个进程写整批裁定(单次加锁一次IO), raw=JSON数组或stdin内容"""
    try:
        items = json.loads(raw)
    except Exception as e:
        out(_usage_err('submitmany', f'JSON解析失败: {e}')); return
    if not isinstance(items, list):
        out({'error': 'submitmany 需要 JSON 数组'}); return
    ok_lines, errors = [], []
    for i, v in enumerate(items):
        err = check_verdict(v)
        if err is None:
            # 表级校验: id须存在于主表; 未匹配行(无现配)禁OK/WRONG
            row = _det_by_id().get(str(v.get('RemakeVoiceID')))
            if row is None:
                err = '主表中无此RemakeVoiceID(行号不是id, 勿提交)'
            elif not row.get('OldVoiceFilename') and v.get('verdict') in ('OK', 'WRONG'):
                err = '未匹配行无现配, 禁OK/WRONG; 应为 FOUND(+correct_vid)/CANDIDATES/NO_VOICE/UNRESOLVED'
        if err:
            errors.append({'index': i,
                           'RemakeVoiceID': v.get('RemakeVoiceID') if isinstance(v, dict) else None,
                           'error': err})
        else:
            ok_lines.append(json.dumps(v, ensure_ascii=False) + '\n')
    if ok_lines:
        append_locked(os.path.join(_here, 'review_pack', 'verdicts.jsonl'), ''.join(ok_lines))
    out({'ok': len(ok_lines), 'errors': errors,
         'saved': [json.loads(l)['RemakeVoiceID'] for l in ok_lines]})

def cmd_submitmap(scene, func, raw):
    """整块批量提交OK/UNRESOLVED: raw=JSON映射{"行号或RemakeVoiceID":"verdict"} 或 '-'读stdin
    服务端回填 RemakeVoiceID/task/canned reason, 已裁定行自动跳过;
    仅支持 OK/UNRESOLVED, 需证据或自定义reason的裁定走 submitmany"""
    try:
        mapping = _jload(raw)
    except Exception as e:
        out(_usage_err('submitmap', f'JSON解析失败: {e}')); return
    if not isinstance(mapping, dict):
        out({'error': 'submitmap 需要 JSON 对象 {"行号或id": "verdict"}'}); return
    rows = [r for r in _det() if r['RemakeScenaScriptFilename'] == scene and r['RemakeFunction'] == func]
    if not rows:
        out({'error': f'未找到 {scene}/{func}'}); return
    done = _done_ids()
    by_line = {r['RemakeScenaScriptLineno']: r for r in rows}
    by_id = {str(r['RemakeVoiceID']): r for r in rows}
    ok_lines, errors, skipped = [], [], 0
    for k, verdict in mapping.items():
        r = by_line.get(str(k)) or by_id.get(str(k))
        if r is None:
            errors.append({'key': k, 'error': '不在块内(既非行号也非RemakeVoiceID)'}); continue
        if verdict not in ('OK', 'UNRESOLVED'):
            errors.append({'key': k, 'error': f'submitmap 仅支持 OK/UNRESOLVED, {verdict} 请走 submitmany'}); continue
        if verdict == 'OK' and not r['OldVoiceFilename']:
            errors.append({'key': k, 'error': '未匹配行无现配不能OK; 该行走 submitmany 给 FOUND/NO_VOICE/UNRESOLVED/CANDIDATES'}); continue
        rid = str(r['RemakeVoiceID'])
        if rid in done:
            skipped += 1; continue
        task = 'A' if r['OldVoiceFilename'] else 'B'
        reason = ('submitmap: 行内text==evo_text且无涉及issue' if verdict == 'OK'
                  else 'submitmap: find无命中/泛用短句, EVO无对应语音')
        ok_lines.append(json.dumps({'RemakeVoiceID': rid, 'task': task, 'verdict': verdict,
                                    'reason': reason}, ensure_ascii=False) + '\n')
    if ok_lines:
        append_locked(os.path.join(_here, 'review_pack', 'verdicts.jsonl'), ''.join(ok_lines))
    out({'ok': len(ok_lines), 'skipped_done': skipped, 'errors': errors})

def cmd_autook():
    """批量确定性自检(主智能体编排用): 扫描全部未完成块, 判据与autocheck完全一致
    (全匹配+无标记+文本达标+runcheck零异常), 通过的块pending行整块自动写入OK。
    一个进程完成全部扫描与写入, 省去逐块LLM裁定(约占待办量20%)"""
    import io, contextlib
    from rapidfuzz import fuzz
    done = _done_ids()
    blocks = defaultdict(list)
    for r in _det():
        blocks[(r['RemakeScenaScriptFilename'], r['RemakeFunction'])].append(r)
    lines_out, skipped = [], []
    ok_blocks = ok_rows = 0
    remain_blocks = remain_lines = 0
    for (s, f), rows in sorted(blocks.items()):
        pend = [r for r in rows if str(r['RemakeVoiceID']) not in done]
        if not pend:
            continue
        remain_blocks += 1
        remain_lines += len(pend)
        bad = None
        un = [r for r in pend if not r['OldVoiceFilename']]
        if un:
            bad = f'{len(un)}行未匹配'
        else:
            fl = [r for r in pend if r['SpeakerCheck'] or r['VoiceReuseAlert']]
            if fl:
                bad = f'{len(fl)}行带标记'
        if not bad:
            for r in pend:
                v = r['OldVoiceFilename']
                v = v[2:-1] if v.startswith('ch') else v
                vt = _voice_text(v)
                if not vt:
                    bad = f"行{r['RemakeScenaScriptLineno']}语音不在结构/补充表"
                    break
                sim = fuzz.ratio(cnorm(r['RemakeVoiceText']), cnorm(vt[0]))
                n = cnorm(r['RemakeVoiceText'])
                content_len = len(re.sub(r'[^0-9A-Za-zぁ-んァ-ヶ一-龯]', '', n))
                if sim < 100 and not (content_len >= 8 and sim >= 90):
                    bad = f"行{r['RemakeScenaScriptLineno']}文本相似{sim:.0f}"
                    break
        if not bad:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cmd_runcheck(s, f)
            rc = json.loads(buf.getvalue())
            if rc['issues']:
                bad = '体检:' + ','.join(i['type'] for i in rc['issues'][:3])
        if bad:
            skipped.append({'block': f'{s}/{f}', 'lines': len(pend), 'why': bad})
            continue
        for r in pend:
            lines_out.append(json.dumps({'RemakeVoiceID': str(r['RemakeVoiceID']), 'task': 'A',
                                         'verdict': 'OK',
                                         'reason': 'autook: 全匹配+体检零异常+文本达标+无标记'},
                                        ensure_ascii=False) + '\n')
        ok_blocks += 1
        ok_rows += len(pend)
    if lines_out:
        append_locked(os.path.join(_here, 'review_pack', 'verdicts.jsonl'), ''.join(lines_out))
    out({'auto_ok_blocks': ok_blocks, 'auto_ok_rows': ok_rows,
         'remaining_blocks': remain_blocks, 'remaining_lines': remain_lines,
         'skipped_sample': skipped[:15], 'skipped_count': len(skipped)})

def cmd_rowhint(scene, func, key):
    """行级结构提示: 给定块内行(行号或RemakeVoiceID), 返回其锚点区间内的候选take
    优化1(锚点对齐器)的工具化出口, 供 find/findmany 之后的结构确认用。"""
    rows = [r for r in _det() if r['RemakeScenaScriptFilename'] == scene and r['RemakeFunction'] == func]
    if not rows:
        out({'error': f'未找到块 {scene}/{func}'}); return
    rows.sort(key=lambda r: int(r['RemakeScenaScriptLineno']))
    row = next((r for r in rows if str(r['RemakeVoiceID']) == str(key)), None)         or next((r for r in rows if r['RemakeScenaScriptLineno'] == str(key)), None)
    if row is None:
        out({'error': f'行 {key} 不在块内(行号或RemakeVoiceID)'}); return
    from rapidfuzz import fuzz
    n = cnorm(row['RemakeVoiceText'])
    anchors = []
    for r in rows:
        vf = r['OldVoiceFilename']
        if vf:
            v = vf[2:-1] if vf.startswith('ch') else vf
            anchors.append((int(r['RemakeScenaScriptLineno']), v))
    line = int(row['RemakeScenaScriptLineno'])
    cur = None
    vf = row['OldVoiceFilename']
    if vf:
        cur = vf[2:-1] if vf.startswith('ch') else vf
    # 组定位 v2: 邻锚众数组优先(当前vid可能是跨场景支, 组会错)
    from collections import Counter as _C2
    _gcnt = _C2(a[1][3:6] for a in anchors)
    g_nbr, _ = (_gcnt.most_common(1)[0] if _gcnt else (None, 0))
    g_cur = cur[3:6] if cur else None
    g = g_nbr or g_cur
    group_conflict = bool(g_cur and g_nbr and g_cur != g_nbr)
    cand_g = [a for a in anchors if a[1][3:6] == g]
    lo = max((a for a in cand_g if a[0] < line), default=None, key=lambda a: a[0])
    hi = min((a for a in cand_g if a[0] > line), default=None, key=lambda a: a[0])
    res = {'scene': scene, 'function': func, 'line': row['RemakeScenaScriptLineno'],
           'rid': row['RemakeVoiceID'], 'text': row['RemakeVoiceText'][:60],
           'cur_vid': cur, 'group': g, 'group_conflict': group_conflict,
           'lo_anchor': lo, 'hi_anchor': hi}
    if group_conflict:
        res['note_group'] = f'当前vid组{g_cur}与邻锚众数组{g_nbr}不一致——现配可能是跨场景支, 区间按邻锚组计算'
    if not (lo and hi):
        res['note'] = '无同组双侧锚点, 区间不可用'
        out(res); return
    ta, tb = int(lo[1][6:]), int(hi[1][6:])
    used = {a[1][6:] for a in anchors if a[1][3:6] == g}
    cands = []
    for t in range(ta + 1, tb):
        tk = f'{t:04d}'
        if tk in used: continue
        for vid, text in vid_pool.get((g, tk), {}).items():
            s = fuzz.ratio(n, cnorm(text)) if n else 0
            cands.append({'vid': vid, 'take': tk, 'sim': round(s, 1), 'text': text[:50]})
    # 缺口探针: 区间内既不在结构锚也不在候选的序号, 若 AT9 文件存在则列出(未引用录音)
    gap_takes = []
    for t in range(ta + 1, tb):
        tk = f'{t:04d}'
        if tk in used: continue
        hit = next((c for c in cands if c['take'] == tk), None)
        if hit: continue
        for b in _banks_for_group(g):
            gv = b + g + tk
            if gv in at9_set:
                gap_takes.append({'vid': gv, 'take': tk, 'source': 'at9未引用',
                                  'note': 'AT9音频存在但无结构/补录文本, 需听辨', 'at9_exists': True})
    cands.sort(key=lambda x: -x['sim'])
    res['interval'] = f'{ta:04d}-{tb:04d}'
    res['candidates'] = cands[:6]
    if gap_takes:
        res['gap_takes'] = gap_takes[:6]
    out(res)

def _banks_for_group(g):
    """组内出现过的bank集合(用于缺口探针枚举同组各bank的AT9文件)"""
    _bs = set()
    for (_eg, _tk), _vs in vid_pool.items():
        if _eg == g:
            for _v in _vs: _bs.add(_v[:3])
    return _bs or {b for b in ('001','003','004','005','006','007','008')}


def _status_label(s):
    if s == 'charid+T_NAME':
        return 'char_id+T_NAME(原生speaker投票100%一致)'
    if s.startswith('charid('):
        return '原生speaker投票' + s[7:-1]
    if s.startswith('cast_table'):
        return 'cast表反查(' + s[11:-1] + ')'
    if s.startswith('speaker_name'):
        return '原生speaker_name'
    if s.startswith('namebox_vote'):
        return '名框投票(' + s[13:-1] + ')'
    if s.startswith('inherited('):
        return '继承自静态分析(' + s[10:-1] + ')'
    if s == 'text_verified':
        return '文本鉴别(台词自称/文体)'
    return '未鉴别'

def cmd_bank(code):
    """EVO bank(=vid前3位) -> 全场景角色档案: 身份/槽位分布/显示名/场景列表/特殊出现(演员槽乱入)。
    数据: data/evo_bank_index_{game}.json + evo_speaker_names_{game}.json (EVO日文本体推导)"""
    code = re.sub(r'\D', '', code)[:3].zfill(3)
    bi = {}
    _p = resolve(f'evo_bank_index_{GAME}.json')
    if _p:
        bi = json.load(open(_p, encoding='utf-8')).get(code, {})
    jp, cn = espeaker.bank_name(code, GAME)
    if not bi and not jp:
        out({'bank': code, 'found': False, 'note': '未知bank码(未在任何语音行中出现)'}); return
    res = {'bank': code, 'found': True,
           'name_jp': jp, 'name_cn': cn,
           'identity_status': _status_label((espeaker._load(GAME) or {}).get('banks', {}).get(code, {}).get('status', '')),
           'lines': bi.get('lines'), 'scene_count': bi.get('scene_count'),
           'speaker_slots': bi.get('speaker_slots'),
           'display_names_seen': bi.get('display_names_seen'),
           'scenes': bi.get('scenes'),
           'special_count': len(bi.get('special_identity_occurrences', []))}
    sp = bi.get('special_identity_occurrences', [])
    if sp:
        res['special_examples'] = [{'scene': x['scene'], 'function': x['function'],
                                    'talk_num': x['talk_num'], 'speaker': x['speaker'],
                                    'text': x['text']} for x in sp[:5]]
        res['special_note'] = '主角团以场景演员槽登场的记录(乱入/临时登场), 共%d条' % len(sp)
    kb = espeaker._load(GAME) or {}
    cid = (kb.get('bank_to_char_id') or {}).get(code)
    if cid:
        tn = kb.get('tname', [])
        res['char_id'] = int(cid)
        if int(cid) < len(tn) and tn[int(cid)]:
            res['tname'] = tn[int(cid)]
    out(res)

def cmd_speaker(a, b=None):
    """说话人辨析: 行级(场景,py行号)或实体级(--entity key)。
    数据源: s7_build_voice_lookup.py 生成的 voice_lookup_index_sc.json。
    返回: status(CONFIRMED/NO_VOICE/MULTI_SPEAKER/MULTI_OPTION/UNCERTAIN/NOT_FOUND)、
    说话人ID/名字、运行时显示名(变装/匿名,日/中)、语音号、该行实际EVO匹配、
    实体EVO映射(全局一致 global_unanimous / 场景依赖 scene_dependent / 多人共用前缀标注)。
    规范: docs/voice_lookup.md"""
    try:
        from voice_lookup_query import VoiceLookup
    except ImportError as e:
        out({'error': f'查询模块不可用: {e}'}); return
    try:
        lk = VoiceLookup(GAME)
    except SystemExit as e:
        out({'error': str(e),
             'fix': 'uv run python s7_build_voice_lookup.py --game sc --py-dir <日文py> [...]'}); return
    if a == '--entity':
        if not b:
            out({'error': '用法: speaker --entity <说话人ID|显示名(日文)>; 列出全部: speaker --list <ID>'}); return
        out(lk.entity(b)); return
    if a == '--list':
        try:
            out({'entities': lk.list_entities(int(b)) if b else lk.list_entities()})
        except ValueError:
            out({'entities': lk.list_entities()}); return
        return
    if a is None or b is None or not b.isdigit():
        out({'error': '用法: speaker <场景> <行号> | speaker --entity <key> | speaker --list <ID>'}); return
    res = lk.lookup(a, int(b))
    # EVO侧说话人知识库补全: 行级EVO speaker语义 + bank身份
    em = (res or {}).get('evo_match') or {}
    vid = em.get('voice_file') or em.get('voice_id')
    if vid and isinstance(vid, str) and len(vid) >= 10:
        _r = espeaker.resolve(em.get('speaker') or '0x0', vid, GAME)
        if _r['kind']:
            res['evo_speaker_kind'] = _r['kind']
        if _r['name_jp']:
            res['evo_speaker_name'] = _r['name_jp']
        if _r['name_cn']:
            res['evo_speaker_name_cn'] = _r['name_cn']
    pfx = em.get('prefix')
    if pfx:
        _bjp, _bcn = espeaker.bank_name(pfx, GAME)
        if _bjp:
            res.setdefault('evo_match', {})['identity_jp'] = _bjp
        if _bcn:
            res['evo_match']['identity_cn'] = _bcn
    out(res)


if __name__ == '__main__':
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__); sys.exit(0)
    if args[0] in ('help', '-h', '--help'):
        print(__doc__); sys.exit(0)
    cmd, rest = args[0], args[1:]
    if cmd == 'claim':
        if not rest:
            out({'error': '缺少代理ID参数', 'usage': 'claim <代理ID> [场景 函数]'})
        else:
            cmd_claim(rest[0], rest[1] if len(rest) > 2 else None, rest[2] if len(rest) > 2 else None)
    elif cmd == 'release':
        cmd_release(rest[0], rest[1])
    elif cmd == 'pack':
        cmd_pack(rest[0], rest[1])
    elif cmd == 'vid':
        if not rest:
            out({'error': '缺少语音ID参数', 'usage': 'vid <10位语音ID>'})
        else:
            cmd_vid(rest[0])
    elif cmd == 'rowhint':
        if len(rest) < 3:
            out(_usage_err('rowhint', '用法: rowhint <场景> <函数> <行号|RemakeVoiceID>'))
        else:
            cmd_rowhint(rest[0], rest[1], rest[2])
    elif cmd == 'find':
        _kw = {}
        pos = []
        i = 0
        while i < len(rest):
            if rest[i] == '--char': _kw['char'] = rest[i+1]; i += 2
            elif rest[i] == '--scene': _kw['scene'] = rest[i+1]; i += 2
            elif rest[i] == '--evoscene': _kw['evoscene'] = rest[i+1]; i += 2
            elif rest[i] == '--limit': _kw['limit'] = int(rest[i+1]); i += 2
            else: pos.append(rest[i]); i += 1
        cmd_find(' '.join(pos), **_kw)
    elif cmd == 'findmany':
        raw = _raw_arg(rest)
        if raw is None:
            out(_usage_err('findmany', '缺少 JSON 数组参数'))
        else:
            cmd_findmany(raw)
    elif cmd == 'submitmany':
        raw = _raw_arg(rest)
        if raw is None:
            out(_usage_err('submitmany', '缺少 JSON 数组参数'))
        else:
            cmd_submitmany(raw)
    elif cmd == 'submitmap':
        if len(rest) < 2:
            out(_usage_err('submitmap', '缺少 <场景> <函数> 位置参数'))
        else:
            raw = _raw_arg(rest[2:])
            if raw is None:
                out(_usage_err('submitmap', '缺少 JSON 映射参数'))
            else:
                cmd_submitmap(rest[0], rest[1], raw)
    elif cmd == 'runcheck':
        cmd_runcheck(rest[0], rest[1])
    elif cmd == 'autook':
        cmd_autook()
    elif cmd == 'bank':
        cmd_bank(rest[0] if rest else '')
    elif cmd == 'speaker':
        cmd_speaker(rest[0] if rest else None, rest[1] if len(rest) > 1 else None)
    else:
        import difflib
        _known = ['claim', 'pack', 'release', 'submitmap', 'submitmany',
                  'runcheck', 'vid', 'find', 'findmany', 'rowhint', 'speaker', 'bank', 'autook']
        _sug = difflib.get_close_matches(cmd, _known, n=2)
        out({'error': f'未知命令 {cmd}',
             'closest': _sug,
             'commands': '领包/提交: claim pack release submitmap submitmany | 证据: runcheck vid find findmany rowhint | 说话人: speaker bank | 运营: autook',
             'hint': '单条裁定也用 submitmany; 逐条 submit/autocheck/todo 已移除'})

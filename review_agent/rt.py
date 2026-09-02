#!/usr/bin/env python3
"""校对Agent专用工具套组(rt)——为弱模型设计：一条命令一个干净JSON，不碰文件编码、不写代码。

用法:
  uv run python rt.py todo [--n 5]                    # 待办块队列(按verdicts过滤,仅未完成)
  uv run python rt.py claim <代理ID> [场景 函数]        # 直接领任务包: 认领待办块+返回工作包(租约防撞块)
  uv run python rt.py release <场景> <函数>            # 释放认领(代理异常中断时用)
  uv run python rt.py pack <场景> <函数>               # 工作包(块内全部行, 行内含已匹配vid的evo信息)
  uv run python rt.py vid <语音ID10位>                # 语音详情: 结构定位+msg原文行+被引用处
  uv run python rt.py find <文本> [--char 003] [--scene 047] [--evoscene T0131_1] [--limit 8]   # 归一化检索EVO台词(--scene=语音场景数字3位即vid第4-6位; --evoscene=EVO结构场景名; 两者不同体系)
  uv run python rt.py findmany '<[[文本,角色],..]>' | - # 批量检索(一个进程跑整批, 消灭重复数据加载)
  uv run python rt.py runcheck <场景> <函数>           # 块级自动体检: 序号连续性/跳场景/复用/文本相似
  uv run python rt.py submit '<verdict JSON>'          # 校验并追加裁定到 review_pack/verdicts.jsonl
  uv run python rt.py submitmany '[{verdict},..]' | -  # 批量提交(一个进程整批写入, 单次加锁)
  uv run python rt.py submitmap <场景> <函数> '{"行号":"OK",..}' | -  # 整块批量OK/UNRESOLVED(服务端回填id,已裁定自动跳过)
  uv run python rt.py autook                          # 批量自检: 全部待办块中通过确定性体检的整块自动OK
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

GAME = 'sc'
SUF = '_sc'
_here = os.path.dirname(os.path.abspath(__file__))
_det = resolve('match_result_sc_detailed.csv')
det = list(csv.DictReader(open(_det, encoding='utf-8')))
evo = json.load(open(resolve(f'evo_structure{SUF}.json'), encoding='utf-8'))
remake_st = json.load(open(resolve(f'remake_structure{SUF}.json'), encoding='utf-8'))
SORA_CANDS = [os.path.join(W, 'SoraVoiceScripts-zhenjian', 'cn.sc'),
              os.path.join(W, 'sora-voice-matcher', 'SoraVoiceScripts', 'cn.sc')]
SORA = next((d for d in SORA_CANDS if os.path.isdir(d)), None)

# ---------- 索引(带pickle缓存: 构建约4s, unpickle约0.2s) ----------
_CACHE_VER = 3   # 代码变更(如索引结构/归一化规则)时递增
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
unvoiced_norms = set()  # 无语音行的norm集合(用于do_find快速区分)

_use_cache = os.path.exists(_CACHE_F) and not _src_newer_than_cache()
if not _use_cache and _wait_for_building_cache():
    _use_cache = os.path.exists(_CACHE_F) and not _src_newer_than_cache()
if _use_cache:
    import pickle
    with open(_CACHE_F, 'rb') as _f:
        vid_idx, norm_list, unvoiced_norms, extra, BLOCK_KEYS = pickle.load(_f)
    from synonyms import normalize as _norm
    def cnorm(t):
        return re.sub(r'\s+', '', _norm(t).replace('\u3046\u3099', 'う').replace('ヴ', 'う')) if t else ''
else:
    for _sc, _fns in evo.items():
        for _fn, _f in _fns.items():
            for _lab, _blk in _f['blocks'].items():
                for t in _blk:
                    if t.get('voice_id'):
                        vid = t['voice_id']
                        vid_idx[vid] = {'scene': _sc, 'func': _fn, 'block': _lab,
                                        'talk_num': t['talk_num'], 'speaker': t['speaker'], 'text': t['text']}
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
    BLOCK_KEYS = {(r['RemakeScenaScriptFilename'], r['RemakeFunction']) for r in det}
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
    import pickle
    os.makedirs(os.path.dirname(_CACHE_F), exist_ok=True)
    _tmp = _CACHE_F + f'.tmp{os.getpid()}'
    with open(_tmp, 'wb') as _f:
        pickle.dump((vid_idx, norm_list, unvoiced_norms, extra, BLOCK_KEYS), _f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(_tmp, _CACHE_F)   # 原子改名, 防并发读到半写文件

_msg_cache = {}
def msg_line(vid):
    """在 out.msg 中定位 vid 的原文行(字节级安全, CP932)"""
    scene = vid_idx.get(vid, {}).get('scene', '')
    base = re.sub(r'_\d+$', '', scene)
    for name in (scene, base):
        mp = os.path.join(SORA, 'out.msg', name + '.txt') if SORA else None
        if not (mp and os.path.exists(mp)):
            continue
        if name not in _msg_cache:
            raw = open(mp, 'rb').read()
            idx = []
            i = 0
            while True:
                i = raw.find(vid.encode(), i)
                if i < 0: break
                ls = raw.rfind(b'\n', 0, i) + 1
                le = raw.find(b'\n', i)
                idx.append(raw[ls:le if le > 0 else len(raw)].decode('cp932', errors='replace').strip())
                i += 1
            _msg_cache[name] = idx
        for ln in _msg_cache.get(name, []):
            if vid in ln:
                return {'file': name + '.txt', 'line': ln[:200]}
    return None

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
    for r in det:
        k = (r['RemakeScenaScriptFilename'], r['RemakeFunction'])
        todo[k][0] += 1
        if r['OldVoiceFilename']: todo[k][1] += 1
        if r['VoiceReuseAlert'] or r['SpeakerCheck']: todo[k][2] += 1
        if str(r['RemakeVoiceID']) not in done: todo[k][3] += 1
    return sorted(((k, v) for k, v in todo.items() if v[3] > 0),
                  key=lambda x: (x[1][2] == 0, x[1][1] == 0, -x[1][2]))

def cmd_todo(n=5):
    ranked = _ranked_pending()
    rows = [{'scene': s, 'function': f, 'total': t, 'matched': m, 'flags': a, 'pending': p}
            for (s, f), (t, m, a, p) in ranked[:n]]
    out({'todo': rows, 'pending_blocks': len(ranked),
         '说明': '仅含未完成块(已按verdicts.jsonl过滤); flags>0优先; matched=0为全未匹配块(B类)'})

def cmd_claim(agent, scene=None, func=None):
    """直接领任务包: 认领待办块(租约防并发撞块)并返回精简工作包。
    claim <代理ID>            -> 自动取队列首块
    claim <代理ID> <场景 函数> -> 认领指定块(主智能体分配模式; 块名不存在直接报错, 不写租约)"""
    import time
    res = {}
    if scene and func and (scene, func) not in BLOCK_KEYS:
        out({'error': f'块不存在: {scene}/{func} (检查场景/函数名拼写)', 'hint': '用 todo 查看有效块名'}); return
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
    rows = [r for r in det if r['RemakeScenaScriptFilename'] == scene and r['RemakeFunction'] == func]
    if not rows:
        return None
    rseq = []
    for fn, f in remake_st.get(scene, {}).items():
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
            '字段说明': 'id=裁定主键; line=Remake行号; spk=remake侧说话人码(参考,勿作检索过滤); evo_char=旧角色码(EVO三位,findmany过滤/说话人核对用); rvfile=remake语音文件名; rblock/evo_block=新旧块标签; vid=已匹配语音ID(evo_seq=录音序号, evo_text/scene/func/talk_num=EVO侧定位); rrid/orig_rid=复用组键; done=true已有裁定(跳过)'}

def cmd_pack(scene, func):
    pack = _get_pack(scene, func, _done_ids())
    if pack is None:
        out({'error': f'未找到 {scene}/{func}'}); return
    out(pack)

def cmd_vid(vid):
    vid = vid.lstrip('ch').rstrip('V')
    refs = [f"{r['RemakeScenaScriptFilename']}:{r['RemakeScenaScriptLineno']}" for r in det if r['OldVoiceFilename'] and r['OldVoiceFilename'][2:-1] == vid]
    base = {'vid': vid, 'char': vid[:3], 'voice_scene': vid[3:6], 'seq': vid[6:],
            'referenced_by_remake': refs[:5], 'msg': msg_line(vid)}
    info = vid_idx.get(vid)
    if info:
        out({'vid': vid, 'found': True, 'source': 'evo', **info, **base}); return
    if vid in extra:
        out({'vid': vid, 'found': True, 'source': 'additional', 'text': extra[vid], **base}); return
    out({'vid': vid, 'found': False, 'note': '不在evo_structure也不在additional补充表——可能是script_data或新录音'})

def do_find(text, char=None, scene=None, limit=8, evoscene=None):
    from rapidfuzz import fuzz
    n = cnorm(text)
    # 参数校验: 错误输入报错而非静默空结果
    errs = []
    if not n or len(n) < 2:
        errs.append(f'查询文本归一化后不足2字符: {text!r}')
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
    return {'query': text, 'norm': n, 'hits': hits[:limit],
            'unvoiced_exact_match': has_unvoiced_exact}

def cmd_find(text, char=None, scene=None, limit=8, evoscene=None):
    r = do_find(text, char, scene, limit, evoscene)
    r['note'] = ('sim=100为归一化全等; 过滤: --char=vid前3位角色码, --scene=vid第4-6位语音场景数字3位(如047), '
                 '--evoscene=EVO结构场景名(如T0131_1)。注意: 结果hits里的scene字段是EVO结构场景名, 与--scene过滤值不是同一体系')
    out(r)

USAGES = {
    'submit': "submit '<JSON>'（单条裁定, JSON整体用单引号包裹）。例: {\"RemakeVoiceID\":\"102345\",\"task\":\"A\",\"verdict\":\"OK\",\"reason\":\"文本全等\"}",
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
    rows = [r for r in det if r['RemakeScenaScriptFilename'] == scene and r['RemakeFunction'] == func]
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
        elif a[3:6] != b[3:6]:
            issues.append({'type': '跨场景', 'at': vids[i][0]['RemakeScenaScriptLineno'],
                           'a': a, 'b': b, 'note': '相邻行跳到另一EVO场景(确认是否官方复用/事件切换)'})
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

def cmd_autocheck(scene, func):
    """确定性预检: 全匹配+体检零异常+文本全等+无标记 -> 直接批量OK(与Agent裁定等价, 零token)
    返回 auto_ok=True 或未过原因列表"""
    rows = [r for r in det if r['RemakeScenaScriptFilename'] == scene and r['RemakeFunction'] == func]
    if not rows:
        out({'error': f'未找到 {scene}/{func}'}); return
    reasons = []
    unmatched = [r for r in rows if not r['OldVoiceFilename']]
    if unmatched:
        reasons.append(f'{len(unmatched)}行未匹配(需LLM寻配)')
    flagged = [r for r in rows if r['SpeakerCheck'] or r['VoiceReuseAlert']]
    if flagged:
        reasons.append(f'{len(flagged)}行带审查标记')
    # 文本全等(在evo结构或补充表中的)
    from rapidfuzz import fuzz
    for r in rows:
        v = r['OldVoiceFilename'][2:-1] if r['OldVoiceFilename'].startswith('ch') else r['OldVoiceFilename']
        vt = _voice_text(v)
        if vt:
            sim = fuzz.ratio(cnorm(r['RemakeVoiceText']), cnorm(vt[0]))
            n = cnorm(r['RemakeVoiceText'])
            content_len = len(re.sub(r'[^0-9A-Za-zぁ-んァ-ヶ一-龯]', '', n))
            if sim < 100 and not (content_len >= 8 and sim >= 90):
                reasons.append(f"行{r['RemakeScenaScriptLineno']}文本相似{sim:.0f}低于阈值")
                break
    # runcheck复用(只取issues, 不重复submit)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cmd_runcheck(scene, func)
    rc = json.loads(buf.getvalue())
    if rc['issues']:
        reasons.append(f"体检{len(rc['issues'])}项异常: " + '; '.join(i['type'] for i in rc['issues'][:3]))
    if reasons:
        out({'scene': scene, 'function': func, 'auto_ok': False, 'reasons': reasons})
        return
    for r in rows:
        cmd_submit(json.dumps({'RemakeVoiceID': r['RemakeVoiceID'], 'task': 'A', 'verdict': 'OK',
                               'reason': 'autocheck: 全匹配+体检零异常+文本全等+无标记'}, ensure_ascii=False))
    out({'scene': scene, 'function': func, 'auto_ok': True, 'auto_ok_lines': len(rows)})

def append_locked(path, line):
    """跨进程安全追加：锁住文件首字节作互斥区再写，防止并发 submit 交错写坏 JSONL"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        try:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            f.seek(0, os.SEEK_END)
            f.write(line)
            f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except ImportError:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line)
            f.flush()
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def check_verdict(v):
    need = {'RemakeVoiceID', 'verdict'}
    if not isinstance(v, dict) or not need.issubset(v):
        return f'缺少字段: {need - set(v if isinstance(v, dict) else {})}'
    if v['verdict'] not in ('OK', 'WRONG', 'SUSPECT', 'FOUND', 'CANDIDATES', 'NO_VOICE', 'UNRESOLVED'):
        return f'verdict非法: {v["verdict"]}'
    if v['verdict'] == 'WRONG' and not v.get('correct_vid'):
        return 'WRONG必须给correct_vid'
    return None

def cmd_submit(raw):
    try:
        v = _jload(raw)
    except Exception as e:
        out(_usage_err('submit', f'JSON解析失败: {e}')); return
    err = check_verdict(v)
    if err:
        out({'error': err, '要求': 'RemakeVoiceID, verdict∈{OK,WRONG,SUSPECT,FOUND,CANDIDATES,NO_VOICE,UNRESOLVED}'}); return
    append_locked(os.path.join(_here, 'review_pack', 'verdicts.jsonl'),
                  json.dumps(v, ensure_ascii=False) + '\n')
    out({'ok': True, 'saved': v['RemakeVoiceID'], 'verdict': v['verdict']})

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
    rows = [r for r in det if r['RemakeScenaScriptFilename'] == scene and r['RemakeFunction'] == func]
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
    for r in det:
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

if __name__ == '__main__':
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__); sys.exit(0)
    cmd, rest = args[0], args[1:]
    if cmd == 'todo':
        cmd_todo(int(rest[1]) if rest and rest[0]=='--n' and len(rest)>1 else (int(rest[0]) if rest and rest[0].isdigit() else 5))
    elif cmd == 'claim':
        cmd_claim(rest[0], rest[1] if len(rest) > 2 else None, rest[2] if len(rest) > 2 else None)
    elif cmd == 'release':
        cmd_release(rest[0], rest[1])
    elif cmd == 'pack':
        cmd_pack(rest[0], rest[1])
    elif cmd == 'vid':
        cmd_vid(rest[0])
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
    elif cmd == 'submit':
        raw = _raw_arg(rest)
        if raw is None:
            out(_usage_err('submit', '缺少单条裁定 JSON 参数'))
        else:
            cmd_submit(raw)
    elif cmd == 'autocheck':
        cmd_autocheck(rest[0], rest[1])
    elif cmd == 'autook':
        cmd_autook()
    else:
        out({'error': f'未知命令 {cmd}', '可用': 'todo claim release pack vid find findmany runcheck submit submitmany submitmap autocheck autook'})

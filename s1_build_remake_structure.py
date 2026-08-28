#!/usr/bin/env python3
"""用 ast 重建 Remake 剧情结构（含顺序边 fallthrough）。

块尾: JUMP / JumpWhenFalse / JumpWhenTrue / Return
边:   jump=无条件, cond_false=假分支, cond_true=真分支,
      next=纯顺序fallthrough
      (JumpWhenFalse 的真分支 / JumpWhenTrue 的假分支 也记为顺序边 cond_true/cond_false)
"""
import ast, glob, json, re
from collections import OrderedDict, Counter
W = '/var/minis/workspace'
sys_path = None

def parse_remake_ast(path):
    src = open(path, encoding='utf-8').read()
    tree = ast.parse(src)
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ('set_current_function', 'Label', 'Command', 'JUMP',
                                'JumpWhenFalse', 'JumpWhenTrue'):
                nodes.append((node.lineno, node.col_offset, 'call', node))
        elif isinstance(node, ast.Return):
            nodes.append((node.lineno, node.col_offset, 'return', node))
    nodes.sort(key=lambda x: (x[0], x[1]))

    funcs = OrderedDict(); cur_func = None; cur_block = None
    cur_order = []; cur_end = {}
    def ensure_block():
        nonlocal cur_block
        if cur_func is not None and cur_block is None:
            cur_block = '_entry'
            funcs[cur_func]['blocks'].setdefault('_entry', [])
            if '_entry' not in cur_order:
                cur_order.append('_entry')
    def add_edge(f, t, ty):
        funcs[cur_func]['edges'].append({'f': f, 't': t, 'type': ty})
    def flush_order():
        nonlocal cur_order, cur_end
        for i, lab in enumerate(cur_order):
            if i == len(cur_order) - 1:
                break
            nxt = cur_order[i + 1]
            if cur_end.get(lab) is None:  # 无 JUMP/Return 结尾 = 纯顺序 fallthrough
                add_edge(lab, nxt, 'next')
        cur_order = []; cur_end = {}

    for lineno, col, kind, node in nodes:
        if kind == 'return':
            if cur_func is not None and cur_block is not None:
                cur_end[cur_block] = 'return'
            cur_block = None
            continue
        name = node.func.id
        if name == 'set_current_function':
            if cur_func is not None:
                flush_order()
            cur_func = node.args[0].value
            funcs.setdefault(cur_func, {'blocks': OrderedDict(), 'edges': []})
            cur_block = None
        elif name == 'Label':
            cur_block = node.args[0].value
            funcs[cur_func]['blocks'].setdefault(cur_block, [])
            cur_order.append(cur_block)
        elif name == 'Command':
            if cur_func is None or len(node.args) < 2: continue
            cmd_type = node.args[0].value
            if cmd_type not in ('Cmd_text_00', 'Cmd_text_06'): continue
            if not isinstance(node.args[1], ast.List): continue
            spk = None; texts = []
            for elt in node.args[1].elts:
                if isinstance(elt, ast.Call) and getattr(elt.func, 'id', '') == 'INT' and elt.args and isinstance(elt.args[0], ast.Constant):
                    if spk is None: spk = elt.args[0].value
                elif isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    texts.append(elt.value)
            text = ''.join(texts)
            text = re.sub(r'<[^>]*>', '', text)  # 去所有 <...> 控制码标记
            if text:
                ensure_block()
                funcs[cur_func]['blocks'][cur_block].append({'speaker': spk, 'text': text})
        elif name == 'JUMP':
            if cur_func is not None and cur_block is not None:
                add_edge(cur_block, node.args[0].value, 'jump')
                cur_end[cur_block] = 'jump'; cur_block = None
        elif name == 'JumpWhenFalse':
            if cur_func is not None and cur_block is not None:
                add_edge(cur_block, node.args[0].value, 'cond_false')
                # 真分支 fallthrough，cur_block 不变（块内继续）
        elif name == 'JumpWhenTrue':
            if cur_func is not None and cur_block is not None:
                add_edge(cur_block, node.args[0].value, 'cond_true')
                # 假分支 fallthrough，cur_block 不变
    if cur_func is not None:
        flush_order()
    return funcs

if __name__ == '__main__':
    import sys
    sys.path.insert(0, '/var/minis/workspace/TrailsInTheSkyRemakeScriptAligner')
    from synonyms import normalize
    remake_structure = {}
    for f in glob.glob(f'{W}/remake_jp/mp*.py'):
        scene = f.split('/')[-1].replace('.py', '')
        try:
            funcs = parse_remake_ast(f)
            if funcs: remake_structure[scene] = funcs
        except Exception as e:
            print(f'ERR {scene}: {e}')
    json.dump(remake_structure, open(f'{W}/remake_structure.json', 'w'), ensure_ascii=False)
    nf = nb = ne = nl = 0
    for sc, funcs in remake_structure.items():
        nf += len(funcs)
        for fn, f in funcs.items():
            nb += len(f['blocks']); ne += len(f['edges']); nl += sum(len(b) for b in f['blocks'].values())
    print(f'Remake: {len(remake_structure)}场景 函数{nf} 块{nb} 边{ne} 台词{nl}')
    rc = Counter(e['type'] for sc in remake_structure.values() for f in sc.values() for e in f['edges'])
    print('边类型:', dict(rc))

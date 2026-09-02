#!/usr/bin/env python3
"""轻量校对Agent Runner——直连 OpenAI 兼容 API(默认GLM-5.3-Flash), 工具循环只执行白名单 rt.py 命令。

用法:
  set RT_BASE_URL=https://open.bigmodel.cn/api/paas/v4     (或其它兼容网关)
  set RT_API_KEY=<你的key>
  set RT_MODEL=glm-5.3-flash                                (默认)
  uv run python agent_runner.py --blocks 3 --game sc        # 自动领块跑3个

也可用 .env 里的 DEEPSEEK: set RT_BASE_URL=https://api.deepseek.com & set RT_MODEL=deepseek-chat
"""
import json, os, subprocess, sys, time, argparse
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent
sys.path.insert(0, str(HERE.parent))

BASE_URL = os.environ.get('RT_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4')
API_KEY = os.environ.get('RT_API_KEY', '')
MODEL = os.environ.get('RT_MODEL', 'glm-5.3-flash')

TASK_BOOK = open(HERE / 'REVIEW_AGENT_FLASH.md', encoding='utf-8').read()

TOOLS = [{
    'type': 'function',
    'function': {
        'name': 'rt',
        'description': '校对工具套组。action: todo/pack/vid/find/runcheck/submit。args: 各命令的位置参数列表。',
        'parameters': {
            'type': 'object',
            'properties': {
                'action': {'type': 'string', 'enum': ['todo', 'pack', 'vid', 'find', 'runcheck', 'submit']},
                'args': {'type': 'array', 'items': {'type': 'string'}},
            },
            'required': ['action', 'args'],
        },
    },
}]

ALLOWED = {'todo', 'pack', 'vid', 'find', 'runcheck', 'submit'}

def run_rt(action, args):
    if action not in ALLOWED:
        return json.dumps({'error': f'action不允许: {action}'}, ensure_ascii=False)
    cmd = [sys.executable, str(HERE / 'rt.py'), action] + [str(a) for a in args]
    env = dict(os.environ, SKYSA_HOME=str(HERE / 'data'), PYTHONIOENCODING='utf-8')
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd=str(HERE))
        out = r.stdout.strip()
        return out if out else json.dumps({'error': r.stderr.strip()[:300]}, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({'error': '命令超时(120s)'}, ensure_ascii=False)

def chat(messages, tool_msgs=None):
    """OpenAI 兼容 /chat/completions（工具调用）。"""
    import urllib.request
    body = {'model': MODEL, 'messages': messages, 'tools': TOOLS, 'temperature': 0.1,
            'max_tokens': 8192}
    req = urllib.request.Request(
        BASE_URL.rstrip('/') + '/chat/completions',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {API_KEY}'})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())

def run_agent(n_blocks, game='sc'):
    sys_msg = {'role': 'system', 'content': TASK_BOOK + f'\n\n当前游戏: {game}。请用 rt 工具完成 {n_blocks} 个块的校对。'}
    messages = [sys_msg]
    tool_calls_total = 0
    t0 = time.time()
    print(f'== 校对Agent 启动: {MODEL} @ {BASE_URL} ==')
    while True:
        rsp = chat(messages)
        msg = rsp['choices'][0]['message']
        messages.append(msg)
        if not msg.get('tool_calls'):
            print('[agent]', (msg.get('content') or '')[:500])
            break
        for tc in msg['tool_calls']:
            fn = tc['function']
            try:
                args = json.loads(fn['arguments'] or '{}')
            except json.JSONDecodeError:
                args = {'action': '?', 'args': []}
            action, a = args.get('action'), args.get('args', [])
            print(f'  > rt {action} {" ".join(map(str, a))[:100]}')
            result = run_rt(action, a)
            tool_calls_total += 1
            messages.append({'role': 'tool', 'tool_call_id': tc['id'], 'content': result[:6000]})
        # 安全阀
        if tool_calls_total > 200:
            print('== 工具调用超200次, 停止 =='); break
        if time.time() - t0 > 1800:
            print('== 超30分钟, 停止 =='); break
    usage = rsp.get('usage', {})
    print(f'== 结束: {tool_calls_total}次工具调用, {time.time()-t0:.0f}s, tokens: {usage.get("prompt_tokens","?")}+{usage.get("completion_tokens","?")} ==')

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--blocks', type=int, default=1)
    ap.add_argument('--game', default='sc')
    a = ap.parse_args()
    if not API_KEY:
        sys.exit('需要环境变量 RT_API_KEY（可选 RT_BASE_URL / RT_MODEL，默认 GLM-5.3-Flash）')
    run_agent(a.blocks, a.game)

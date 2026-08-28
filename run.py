#!/usr/bin/env python3
"""SkyStructureAligner 一键运行：自动下载数据资产 + 完整匹配流水线。

用法:
  uv run python run.py --game sc --py-dir <反编译的Remake台词py目录>
  uv run python run.py --game sc --py-dir py/ --py-dir-sc py_sc/   (附带中文翻译列)
  uv run python run.py --game sc --py-dir py/ --skip-download       (数据已就绪)

流水线:
  s1 重建 Remake 结构 -> derive_speaker_map 推导说话人映射 ->
  s4 结构匹配 -> s6 生成 FC match_result.csv 式 16 列详表 + 说话人审查表

数据资产(首次自动从 GitHub Release 下载到 data/):
  evo_structure{,_sc,_3rd}.json   additional_voice_{fc,sc,3rd}.json   speaker_map_{fc,sc}.json

EVO 侧结构也可本地重建(需 SoraVoiceScripts):
  uv run python s2_build_evo_structure.py sc
"""
import argparse, os, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, 'data')
RELEASE_TAG = 'v1.0.0'
BASE = f'https://github.com/lxr2010/SkyStructureAligner/releases/download/{RELEASE_TAG}'
ASSETS = {
    'fc':  ['evo_structure.json', 'additional_voice_fc.json', 'speaker_map_fc.json'],
    'sc':  ['evo_structure_sc.json', 'additional_voice_sc.json', 'speaker_map_sc.json'],
    '3rd': ['evo_structure_3rd.json', 'additional_voice_3rd.json'],
}

def download(name):
    dst = os.path.join(DATA_DIR, name)
    if os.path.exists(dst):
        print(f'  已存在: {dst}')
        return
    url = f'{BASE}/{name}'
    print(f'  下载 {url}')
    os.makedirs(DATA_DIR, exist_ok=True)
    urllib.request.urlretrieve(url, dst)

def run(cmd):
    print(f'>>> {" ".join(cmd)}')
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        sys.exit(f'步骤失败: {" ".join(cmd)}')

def main():
    ap = argparse.ArgumentParser(description='SkyStructureAligner 一键匹配')
    ap.add_argument('--game', required=True, choices=['fc', 'sc', '3rd'], help='fc=空轨1st(Remake), sc=2nd(Demo/正式), 3rd')
    ap.add_argument('--py-dir', default=None, help='Remake 日文反编译 py 目录(s1 输入；--download-only 时可省)')
    ap.add_argument('--py-dir-sc', default=None, help='简中反编译 py 目录(s6 翻译列, 可选)')
    ap.add_argument('--skip-download', action='store_true', help='跳过资产下载')
    ap.add_argument('--download-only', action='store_true', help='仅下载数据资产')
    args = ap.parse_args()

    if not args.skip_download:
        print(f'== 准备数据资产 ({args.game}) ==')
        for name in ASSETS[args.game]:
            download(name)
    if args.download_only:
        return
    if not args.py_dir:
        sys.exit('需要 --py-dir（Remake 反编译 py 目录）')

    py = args.py_dir
    env = dict(os.environ, SKYSA_HOME=DATA_DIR)
    print('== s1 重建 Remake 结构 ==')
    run([sys.executable, 's1_build_remake_structure.py', args.game, os.path.abspath(py)])
    if args.game == 'sc':
        print('== 推导说话人映射 ==')
        run([sys.executable, 'derive_speaker_map.py', 'sc'])
    print('== s4 结构匹配 ==')
    run([sys.executable, 's4_generate_match_result.py', args.game])
    if args.game == 'sc':
        print('== s6 生成详表与审查表 ==')
        cmd = [sys.executable, 's6_build_match_result_csv.py', 'sc',
               os.path.abspath(args.py_dir)]
        if args.py_dir_sc:
            cmd.append(os.path.abspath(args.py_dir_sc))
        run(cmd)
    print('\n完成。输出(位于 data/):')
    out = {'sc': ['my_match_result_sc.csv', 'match_result_sc_detailed.csv', 'speaker_review_sc.csv']}
    for f in out.get(args.game, [f'my_match_result_{args.game}.csv']):
        print(f'  {os.path.join(DATA_DIR, f)}')

if __name__ == '__main__':
    main()

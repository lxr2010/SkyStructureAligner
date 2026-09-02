"""语音检查页面的本地服务器（路径可用参数指定）：
  - http://127.0.0.1:8613/                     -> --root（默认 SkyStructureAligner 目录，页面与 CSV）
  - http://127.0.0.1:8613/review_agent/         -> 检查页面 match_voice_checker.html
  - http://127.0.0.1:8613/localvoice/<名>.wav   -> --voice（可多次指定，按顺序查找）

用法示例（在任意目录执行均可）：
  python voice_check_server.py
  python voice_check_server.py --voice G:\\sc_demo_voice\\voice\\wav --voice H:\\fc_voice
  python voice_check_server.py --root . --port 9000
"""
import argparse
import http.server
import os
import socketserver
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))          # .../SkyStructureAligner/review_agent
ROOT = os.path.dirname(HERE)                               # .../SkyStructureAligner
VOICE_DIRS = [r"G:\sc_demo_voice\voice\wav"]
PORT = 8613


class Handler(http.server.SimpleHTTPRequestHandler):
    voice_dirs = VOICE_DIRS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=Handler.root_dir, **kwargs)

    def translate_path(self, path):
        p = urllib.parse.urlsplit(path).path
        if p.startswith("/localvoice/"):
            # 只取文件名，防止路径穿越；在多个语音目录里按顺序找
            name = os.path.basename(urllib.parse.unquote(p[len("/localvoice/"):]))
            for d in Handler.voice_dirs:
                fp = os.path.join(d, name)
                if os.path.isfile(fp):
                    return fp
            return os.path.join(Handler.voice_dirs[0], name)   # 不存在 → 404
        return super().translate_path(path)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="语音检查页面服务器")
    ap.add_argument("--root", default=ROOT, help="页面与 CSV 所在目录")
    ap.add_argument("--voice", action="append", default=None,
                    help="本地语音目录（可多次传入，按顺序查找；默认 G:\\sc_demo_voice\\voice\\wav）")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    Handler.root_dir = args.root
    if args.voice:
        Handler.voice_dirs = args.voice

    print(f"serving {args.root} at http://127.0.0.1:{args.port}/")
    for d in Handler.voice_dirs:
        mark = "✓" if os.path.isdir(d) else "✗(不存在)"
        print(f"  /localvoice/ -> {d} {mark}")

    with Server(("127.0.0.1", args.port), Handler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()

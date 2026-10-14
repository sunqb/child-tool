#!/usr/bin/env bash
# 带 no-cache 的静态服务，根目录 = 本脚本所在目录的上一级（workspace 根）
# 用途：8899 电脑入口 http://localhost:8899/dictation-tool/ 的等效替代
# 用法: ./serve-root.sh [端口] [绑定地址]
set -euo pipefail

PORT="${1:-8899}"
BIND="${2:-127.0.0.1}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "启动(workspace根, no-cache): http://${BIND}:${PORT}  Ctrl+C 停止"
exec python3 - "$PORT" "$BIND" "$DIR" <<'PYEOF'
import http.server, sys

port, bind, directory = int(sys.argv[1]), sys.argv[2], sys.argv[3]

class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

Handler.extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                          ".js": "text/javascript; charset=utf-8",
                          ".json": "application/json; charset=utf-8"}
http.server.ThreadingHTTPServer.allow_reuse_address = True
with http.server.ThreadingHTTPServer((bind, port),
        lambda *a, **kw: Handler(*a, directory=directory, **kw)) as httpd:
    httpd.serve_forever()
PYEOF

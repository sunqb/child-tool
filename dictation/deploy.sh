#!/usr/bin/env bash
# 听写工具静态站点启动脚本（本地 / 内网）
# 用法:
#   ./deploy.sh            # 本机访问  http://127.0.0.1:8080
#   ./deploy.sh 8081       # 指定端口
#   ./deploy.sh 8080 0.0.0.0   # 绑定内网 IP，供局域网内其他设备访问
set -euo pipefail

PORT="${1:-8080}"
BIND="${2:-127.0.0.1}"
DIR="$(cd "$(dirname "$0")" && pwd)"

# 启动前自检：关键文件齐全
for f in index.html parent.html kid.html data/wordbank.json js/store.js; do
  [ -f "$DIR/$f" ] || { echo "缺少文件: $f"; exit 1; }
done

# wordbank.json 必须是合法 JSON
python3 -c "import json,sys; json.load(open('$DIR/data/wordbank.json', encoding='utf-8'))" \
  || { echo "wordbank.json 不是合法 JSON"; exit 1; }

echo "启动听写工具: http://${BIND}:${PORT}  (Ctrl+C 停止)"

# 带 no-cache 头的静态服务：避免浏览器缓存旧 html/js/json，用户普通刷新即拿新版
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

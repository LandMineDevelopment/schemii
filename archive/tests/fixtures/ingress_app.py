import json
import os
import sys
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from schemii.http_access import http_access_policy
from schemii.http_common import make_local_app_handler


class UnusedPostgresService:
    pass


base_handler = make_local_app_handler(
    Path("/fixture"),
    UnusedPostgresService(),
    "fixture-session-token",
    server_id="ingress-fixture",
    access_policy=http_access_policy(os.environ, "SCHEMII"),
)


class Handler(base_handler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send_bytes(200, b"trusted ingress fixture", "text/plain; charset=utf-8")
        elif path == "/api/readiness":
            self.send_json(200, {"ready": True})
        elif path == "/api/download":
            self.send_bytes(
                200,
                b"fixture-download\n",
                "text/plain; charset=utf-8",
                {"Content-Disposition": 'attachment; filename="fixture.txt"'},
            )
        elif path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.end_headers()
            for sequence in range(3):
                self.wfile.write(json.dumps({"sequence": sequence}).encode("ascii") + b"\n")
                self.wfile.flush()
                time.sleep(0.1)
            self.close_connection = True
        elif not self._handle_common_get(path):
            self.send_error(404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/body":
            self.send_error(404)
            return
        body = self._body_or_error(20 * 1024 * 1024)
        if body is not None:
            self.send_json(200, {"bytes": len(json.dumps(body, separators=(",", ":")).encode("utf-8"))})


server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
    sys.exit(0)

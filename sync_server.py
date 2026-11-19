"""Учебный REST-сервер для одного общего журнала."""
import argparse
import hmac
import json
import os
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ledger import validate_rows


def create_server(address, database, token):
    token = ("Bearer " + token).encode()
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY, revision INTEGER, rows TEXT)")
        db.execute("INSERT OR IGNORE INTO ledger VALUES (1, 0, '[]')")

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data):
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def authorized(self):
            if self.path != "/ledger":
                self.reply(404, {"error": "not_found"})
                return False
            if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), token):
                self.reply(401, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            with closing(sqlite3.connect(database)) as db:
                revision, rows = db.execute("SELECT revision, rows FROM ledger WHERE id=1").fetchone()
            self.reply(200, {"revision": revision, "rows": json.loads(rows)})

        def do_PUT(self):
            if not self.authorized():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8_000_000:
                    raise ValueError("invalid length")
                data = json.loads(self.rfile.read(size))
                revision, rows = data["revision"], validate_rows(data["rows"])
                if type(revision) is not int or not 0 <= revision < 2**63 - 1:
                    raise ValueError("invalid revision")
            except (KeyError, TypeError, ValueError):
                self.reply(400, {"error": "invalid_data"})
                return
            with closing(sqlite3.connect(database)) as db, db:
                changed = db.execute("UPDATE ledger SET rows=?, revision=revision+1 WHERE id=1 AND revision=?",
                                     (json.dumps(rows, ensure_ascii=False), revision)).rowcount
            if changed:
                self.reply(200, {"revision": revision + 1, "rows": rows})
            else:
                self.reply(409, {"error": "revision_conflict"})

    return ThreadingHTTPServer(address, Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="sync.sqlite3")
    args = parser.parse_args()
    token = os.environ.get("EXPENSE_SYNC_TOKEN", "").strip()
    if len(token) < 16:
        parser.error("Задайте EXPENSE_SYNC_TOKEN длиной от 16 символов.")
    with create_server((args.host, args.port), args.db, token) as server:
        print(f"REST API: http://{args.host}:{args.port}/ledger")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

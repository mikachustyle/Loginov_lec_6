import csv
import tempfile
import unittest
from pathlib import Path
from threading import Thread

from ledger import amount_to_cents, export_csv, operation, summarize
from sync_client import sync
from sync_server import create_server


class LedgerTests(unittest.TestCase):
    def test_amounts_and_balance(self):
        rows = [operation("income", "Продажи", "Оплата заказа", "2200,25", "2026-11-19"),
                operation("expense", "Офис", "Бумага", "300.20", "2026-11-19")]
        self.assertEqual(summarize(rows)[:3], (220025, 30020, 190005))

    def test_invalid_amounts(self):
        for raw in ("0", "-1", "NaN", "1.009", "1e5"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                amount_to_cents(raw)

    def test_invalid_category_and_date(self):
        with self.assertRaises(ValueError):
            operation("income", "Офис", "Тест", "2", "2026-11-19")
        with self.assertRaises(ValueError):
            operation("income", "Продажи", "Тест", "2", "2026-02-30")

    def test_export_csv(self):
        row = operation("expense", "Офис", "=1+1", "12.20", "2026-11-19")
        with tempfile.TemporaryDirectory() as path:
            target = Path(path) / "result.csv"
            export_csv(target, [row])
            with target.open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(list(csv.reader(stream, delimiter=";"))[1][-2:], ["'=1+1", "12,20"])

    def test_sync_upload_download_conflict_and_deletion(self):
        with tempfile.TemporaryDirectory() as path:
            token = "token-for-local-test"
            with create_server(("127.0.0.1", 0), Path(path) / "book.sqlite3", token) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{server.server_port}"
                    empty = {"revision": 0, "rows": [], "dirty": False}
                    row = operation("income", "Услуги", "Консультация", "100", "2026-11-19")
                    pending = {**empty, "rows": [row], "dirty": True}
                    saved = sync(pending, url, token)
                    self.assertEqual(saved["revision"], 1)
                    self.assertEqual(sync(empty, url, token), saved)
                    with self.assertRaisesRegex(ValueError, "другая версия"):
                        sync(pending, url, token)
                    with self.assertRaisesRegex(ValueError, "отклонил токен"):
                        sync(empty, url, "incorrect")
                    deleted = sync({**saved, "rows": [], "dirty": True}, url, token)
                    self.assertEqual(deleted["revision"], 2)
                    self.assertEqual(sync(empty, url, token)["rows"], [])
                finally:
                    server.shutdown()
                    thread.join()

    def test_insecure_remote_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            sync({"revision": 0, "rows": [], "dirty": False}, "http://example.test", "token")


if __name__ == "__main__":
    unittest.main()

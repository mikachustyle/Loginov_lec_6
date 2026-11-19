import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ledger import validate_rows


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sync(book, url, token):
    target = urlsplit(url)
    if target.username or target.password or target.query or target.fragment or not target.hostname:
        raise ValueError("Введите адрес сервера без логина, пароля и параметров.")
    if target.scheme != "https" and not (
        target.scheme == "http" and target.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("Для удалённого сервера требуется HTTPS.")
    if not token.strip():
        raise ValueError("Введите токен сервера.")
    body = json.dumps({"revision": book["revision"], "rows": book["rows"]},
                      ensure_ascii=False).encode() if book["dirty"] else None
    request = Request(url.rstrip("/") + "/ledger", data=body,
                      method="PUT" if body is not None else "GET",
                      headers={"Authorization": f"Bearer {token.strip()}",
                               "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=6) as response:
            data = json.loads(response.read(8_000_001))
    except HTTPError as error:
        if error.code == 409:
            raise ValueError("На сервере есть другая версия. Экспортируйте локальные правки в CSV, затем обновите журнал с сервера.") from error
        if error.code == 401:
            raise ValueError("Сервер отклонил токен.") from error
        raise
    if type(data["revision"]) is not int or data["revision"] < 0:
        raise ValueError("Сервер вернул неверную версию журнала.")
    return {"rows": validate_rows(data["rows"]), "revision": data["revision"], "dirty": False}

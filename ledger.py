import csv
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

CATEGORIES = {
    "expense": ("Офис", "Зарплаты", "Маркетинг", "Логистика", "Налоги", "Прочее"),
    "income": ("Продажи", "Услуги", "Возвраты", "Прочее"),
}
KINDS = {"expense": "Расход", "income": "Доход"}


def money(cents):
    return f"{Decimal(cents) / 100:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def amount_to_cents(raw):
    raw = raw.strip().replace(",", ".")
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,2})?", raw):
        raise ValueError("Введите сумму цифрами, до двух знаков после запятой.")
    value = int(Decimal(raw) * 100)
    if value == 0:
        raise ValueError("Сумма должна быть больше нуля.")
    return value


def operation(kind, category, description, amount, day, entry_id=None):
    row = dict(id=entry_id or uuid4().hex, kind=kind, category=category,
               description=description.strip(), cents=amount_to_cents(amount), day=day.strip())
    validate_rows([row])
    return row


def validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("Допустимо до 5000 операций.")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id","kind","category","description","cents","day"}:
            raise ValueError("Неверный формат операции.")
        entry_id = row["id"]
        if not isinstance(entry_id, str) or UUID(entry_id).hex != entry_id or entry_id in seen:
            raise ValueError("Неверный или повторяющийся ID операции.")
        seen.add(entry_id)
        kind = row["kind"]
        if not isinstance(kind, str) or row["category"] not in CATEGORIES.get(kind, ()):
            raise ValueError("Выберите категорию для этого типа операции.")
        description = row["description"]
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 160:
            raise ValueError("Введите описание длиной от 1 до 160 символов.")
        if type(row["cents"]) is not int or not 0 < row["cents"] < 10**11:
            raise ValueError("Неверная сумма операции.")
        day = row["day"]
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError("Введите дату в формате ГГГГ-ММ-ДД.")
    return rows


def summarize(rows):
    categories = {kind: defaultdict(int) for kind in KINDS}
    for row in rows:
        categories[row["kind"]][row["category"]] += row["cents"]
    income = sum(categories["income"].values())
    expense = sum(categories["expense"].values())
    return income, expense, income - expense, categories


def export_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Дата", "Тип", "Категория", "Описание", "Сумма, руб."])
        for row in rows:
            description = row["description"]
            if description.lstrip().startswith(("=", "+", "-", "@")) or description.startswith(("\t","\r","\n")):
                description = "'" + description
            writer.writerow([row["day"], KINDS[row["kind"]], row["category"],
                             description, f"{Decimal(row['cents']) / 100:.2f}".replace(".", ",")])

import os
from datetime import date
from pathlib import Path
from threading import Thread

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, StringProperty
from kivy.storage.jsonstore import JsonStore
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy.uix.recycleview.views import RecycleDataViewBehavior

from ledger import CATEGORIES, KINDS, export_csv, money, operation, summarize, validate_rows
from sync_client import sync


class OperationRow(RecycleDataViewBehavior, BoxLayout):
    entry_id = StringProperty()
    title = StringProperty()
    subtitle = StringProperty()
    amount = StringProperty()


class Editor(Popup):
    entry_id = StringProperty()
    kind = StringProperty("expense")
    choices = ListProperty(list(CATEGORIES["expense"]))

    def set_kind(self, label):
        kind = "income" if label == "Доход" else "expense"
        if kind != self.kind:
            self.kind = kind
            self.choices = list(CATEGORIES[kind])
            self.ids.category.text = self.choices[0]

    def save(self):
        try:
            row = operation(self.kind, self.ids.category.text, self.ids.description.text,
                            self.ids.amount.text, self.ids.day.text, self.entry_id)
            App.get_running_app().save_row(row)
        except (ValueError, OSError) as error:
            self.ids.error.text = str(error)
            return
        self.dismiss()


class SyncDialog(Popup):
    def run_sync(self, refresh=False):
        app = App.get_running_app()
        app.start_sync(self.ids.url.text.strip(), self.ids.token.text, refresh)
        self.dismiss()


class LedgerApp(App):
    balance = StringProperty("0,00 ₽")
    income = StringProperty("0,00 ₽")
    expense = StringProperty("0,00 ₽")
    status = StringProperty("")
    busy = BooleanProperty(False)

    def build(self):
        if os.name == "nt":
            Window.size = (440, 750)
        self.title = "Бизнес-журнал"
        folder = Path(os.getenv("LEC6_DATA_DIR") or self.user_data_dir)
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.store = JsonStore(str(folder / "ledger.json"))
        self.book = (self.store.get("book") if self.store.exists("book")
                     else {"revision": 0, "rows": [], "dirty": False, "endpoint": ""})
        validate_rows(self.book["rows"])
        return Builder.load_file(str(Path(__file__).with_name("journal.kv")))

    def on_start(self):
        self.refresh()

    def update(self, book):
        validate_rows(book["rows"])
        self.store.put("book", **book)
        self.book = book
        self.refresh()

    def refresh(self):
        income, expense, balance, _ = summarize(self.book["rows"])
        self.income, self.expense, self.balance = map(money, (income, expense, balance))
        rows = sorted(reversed(self.book["rows"]), key=lambda row: row["day"], reverse=True)
        self.root.ids.operations.data = [
            dict(entry_id=r["id"], title=r["description"],
                 subtitle=f'{r["day"]} · {r["category"]}',
                 amount=("+" if r["kind"] == "income" else "−") + money(r["cents"]))
            for r in rows
        ]
        if not rows:
            self.status = "Журнал пуст. Добавьте первую операцию."

    def edit(self, entry_id=""):
        if self.busy:
            return
        editor = Editor(entry_id=entry_id)
        editor.ids.day.text = date.today().isoformat()
        if entry_id:
            row = next(item for item in self.book["rows"] if item["id"] == entry_id)
            editor.ids.kind.text = KINDS[row["kind"]]
            editor.ids.category.text = row["category"]
            editor.ids.description.text = row["description"]
            editor.ids.amount.text = f'{row["cents"] // 100}.{row["cents"] % 100:02}'
            editor.ids.day.text = row["day"]
        editor.open()

    def save_row(self, row):
        rows = [item for item in self.book["rows"] if item["id"] != row["id"]] + [row]
        self.update({**self.book, "rows": rows, "dirty": True})
        self.status = "Операция сохранена."

    def delete_row(self, editor):
        rows = [item for item in self.book["rows"] if item["id"] != editor.entry_id]
        try:
            self.update({**self.book, "rows": rows, "dirty": True})
        except OSError as error:
            editor.ids.error.text = str(error)
            return
        self.status = "Операция удалена."
        editor.dismiss()

    def export(self):
        try:
            path = self.folder / "operations.csv"
            export_csv(path, self.book["rows"])
            self.status = f"CSV сохранён: {path}"
        except OSError as error:
            self.status = f"Не удалось экспортировать: {error}"

    def analytics(self):
        from kivy_garden.matplotlib.backend_kivyagg import FigureCanvasKivyAgg
        from matplotlib.figure import Figure

        _, _, _, categories = summarize(self.book["rows"])
        figure = Figure(figsize=(5, 6), layout="constrained", facecolor="#f6f8fc")
        for i, kind in enumerate(("expense", "income"), 1):
            axis = figure.add_subplot(2, 1, i)
            values = sorted(categories[kind].items(), key=lambda entry: entry[1])
            axis.set_title("Расходы по категориям" if kind == "expense" else "Доходы по категориям")
            if values:
                axis.barh([name for name, _ in values], [cents / 100 for _, cents in values],
                          color="#2960ba" if kind == "income" else "#bf553b")
                axis.set_xlabel("Рубли")
            else:
                axis.text(.5, .5, "Нет операций", ha="center", va="center", transform=axis.transAxes)
                axis.set_axis_off()
        Popup(title="Аналитика", content=FigureCanvasKivyAgg(figure),
              size_hint=(.96, .88)).open()

    def sync_dialog(self):
        dialog = SyncDialog()
        dialog.ids.url.text = self.book.get("endpoint") or os.getenv("EXPENSE_SYNC_URL", "http://127.0.0.1:8765")
        dialog.open()

    def start_sync(self, url, token, refresh=False):
        url = url.rstrip("/")
        endpoint = self.book.get("endpoint")
        if self.busy:
            return
        if endpoint and endpoint != url:
            self.status = "Для другого сервера запустите приложение с отдельной папкой данных."
            return
        if refresh and self.book["dirty"]:
            try:
                export_csv(self.folder / "before_sync.csv", self.book["rows"])
            except OSError as error:
                self.status = f"Не удалось сохранить резервный CSV: {error}"
                return
        snapshot = {**self.book, "dirty": False} if refresh else self.book
        self.busy = True
        self.status = "Синхронизация…"

        def finished(result, error):
            self.busy = False
            if error:
                self.status = f"Синхронизация: {error}"
                return
            try:
                self.update({**result, "endpoint": url})
            except OSError as failure:
                self.status = f"Сервер ответил, но не удалось сохранить журнал: {failure}"
                return
            self.status = ("Данные получены. Резервная копия правок: before_sync.csv."
                           if refresh and snapshot is not original and original["dirty"]
                           else "Журнал синхронизирован.")

        original = self.book
        def task():
            try:
                result, error = sync(snapshot, url, token), None
            except (ValueError, OSError, KeyError, TypeError) as failure:
                result, error = None, str(failure)
            Clock.schedule_once(lambda _: finished(result, error))

        Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    LedgerApp().run()

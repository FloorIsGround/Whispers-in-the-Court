"""Non-blocking account and model settings. Worker threads never call Tk."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from .ai.base import AICancelled, AIError
from .ai.chatgpt import ChatGPTClient
from .drawer import GOLD, NAVY, TEXT, TEXT_DIM


class ChatGPTSettings(tk.Frame):
    def __init__(self, parent, *, auth, model, on_model, on_connection, on_busy):
        super().__init__(parent, bg=NAVY)
        self.auth, self.model = auth, model
        self.on_model, self.on_connection, self.on_busy = on_model, on_connection, on_busy
        self.events = queue.Queue()
        self.busy = False
        self._accounts, self._models = [], []
        self._alive = True
        self.note = tk.StringVar(value="Connect a ChatGPT account to use its eligible plan allowance.")
        tk.Label(self, text="ChatGPT account", bg=NAVY, fg=GOLD, anchor="w").pack(fill="x")
        self.accounts = ttk.Combobox(self, state="readonly")
        self.accounts.pack(fill="x", pady=3)
        self.accounts.bind("<<ComboboxSelected>>", self._select)
        buttons = tk.Frame(self, bg=NAVY)
        buttons.pack(fill="x", pady=4)
        self.connect = ttk.Button(buttons, text="Continue with ChatGPT", command=self._connect)
        self.connect.grid(row=0, column=0, sticky="w", pady=2)
        self.add = ttk.Button(
            buttons, text="Add account", command=lambda: self._run(lambda: self.auth.sign_in(""))
        )
        self.add.grid(row=0, column=1, sticky="w", padx=4, pady=2)
        self.logout = ttk.Button(buttons, text="Sign out", command=self._logout)
        self.logout.grid(row=1, column=0, sticky="w", pady=2)
        self.cancel = ttk.Button(
            buttons, text="Cancel sign-in", command=self.auth.cancel_login, state="disabled"
        )
        self.cancel.grid(row=1, column=1, sticky="w", padx=4, pady=2)
        tk.Label(self, text="Model", bg=NAVY, fg=GOLD, anchor="w").pack(fill="x", pady=(4, 0))
        self.models = ttk.Combobox(self, state="readonly")
        self.models.pack(fill="x", pady=3)
        self.models.bind("<<ComboboxSelected>>", self._model_selected)
        row = tk.Frame(self, bg=NAVY)
        row.pack(fill="x", pady=3)
        self.refresh = ttk.Button(row, text="Refresh models / retry connection", command=self._load_models)
        self.refresh.pack(side="left")
        ttk.Button(
            row, text="ChatGPT usage", command=lambda: webbrowser.open("https://chatgpt.com/settings/usage")
        ).pack(side="left", padx=4)
        self.note_label = tk.Label(
            self, textvariable=self.note, bg=NAVY, fg=TEXT, justify="left", anchor="w", wraplength=565
        )
        self.note_label.pack(fill="x", pady=5)
        self.privacy_label = tk.Label(
            self,
            text="Game context is sent to OpenAI. No API key is required for an eligible plan.\n"
            "Voice: disabled. Typed conversations work without a speech service.",
            bg=NAVY,
            fg=TEXT_DIM,
            justify="left",
            anchor="w",
            wraplength=565,
        )
        self.privacy_label.pack(fill="x", pady=3)
        self.bind("<Configure>", self._resize, add=True)
        self.bind("<Destroy>", self._destroyed, add=True)
        self._refresh_accounts()
        self._poll_id = self.after(100, self._poll)

    def _resize(self, event):
        for label in (self.note_label, self.privacy_label):
            label.configure(wraplength=max(200, event.width - 10))

    def _destroyed(self, event):
        if event.widget is self:
            self._alive = False
            if getattr(self, "_poll_id", None):
                self.after_cancel(self._poll_id)
            if self.busy:
                self.auth.cancel_login()

    def _refresh_accounts(self):
        try:
            active, self._accounts = self.auth.accounts()
            self.accounts["values"] = [item["label"] for item in self._accounts]
            self.accounts.set("")
            for i, item in enumerate(self._accounts):
                if item["id"] == active:
                    self.accounts.current(i)
                    self.note.set(
                        "Connected. Refresh models to select one."
                        if item["signed_in"] and item["plan_enabled"]
                        else "Continue with ChatGPT to enable this account's plan usage."
                    )
        except AIError as exc:
            self.note.set(str(exc))

    def _selected(self):
        i = self.accounts.current()
        return self._accounts[i]["id"] if 0 <= i < len(self._accounts) else ""

    def _select(self, _event=None):
        account_id = self._selected()
        self._models = []
        self.models.set("")
        self.model = ""
        self.on_model("")
        self._run(lambda: (self.auth.select(account_id), "Account selected. Refresh models.")[1])

    def _connect(self):
        account_id = self._selected()
        self._run(lambda: self.auth.sign_in(account_id))

    def _logout(self):
        account_id = self._selected()
        self._run(lambda: self.auth.sign_out(account_id))

    def _run(self, operation, *, catalog=False):
        if self.busy:
            return
        self.busy = True
        self.note.set(
            "Loading available models..."
            if catalog
            else "Connecting... Complete sign-in in your browser. This can take up to 15 minutes."
        )
        self._set_enabled(False)
        if not catalog:
            self.on_busy()

        def work():
            try:
                value = operation()
                self.events.put((catalog, value, None))
            except (AIError, AICancelled) as exc:
                self.events.put((catalog, None, str(exc)))
            except Exception:
                # Avoid exposing provider responses or credential paths in generic exceptions.
                self.events.put(
                    (catalog, None, "The connection operation failed. Check your network and try again.")
                )
            finally:
                # This callback only touches Court Brain, never Tk. It must run even
                # when the settings page is closed while authorization is pending.
                self.on_connection()

        threading.Thread(target=work, name="chatgpt-settings", daemon=True).start()

    def _set_enabled(self, enabled):
        for widget in (self.connect, self.add, self.logout, self.refresh):
            widget.configure(state="normal" if enabled else "disabled")
        for widget in (self.accounts, self.models):
            widget.configure(state="readonly" if enabled else "disabled")
        self.cancel.configure(state="disabled" if enabled else "normal")

    def _load_models(self):
        def load():
            client = ChatGPTClient(self.auth)
            try:
                return client.models()
            finally:
                client.close()

        self._run(load, catalog=True)

    def _model_selected(self, _event=None):
        i = self.models.current()
        if 0 <= i < len(self._models):
            self.model = self._models[i]["id"]
            self.on_model(self.model)

    def _poll(self):
        if not self._alive:
            return
        try:
            catalog, result, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self._set_enabled(True)
            self._refresh_accounts()
            if error:
                self.note.set(error)
            elif catalog:
                self._models = result
                self.models["values"] = [f"{item['name']} ({item['id']})" for item in result]
                self.models.set("")
                for i, item in enumerate(result):
                    if item["id"] == self.model:
                        self.models.current(i)
                if self.models.current() < 0:
                    self.model = ""
                    self.on_model("")
                self.note.set(
                    "Choose a model, then save the AI settings."
                    if result
                    else "No models are available for this account."
                )
            else:
                self.note.set(result)
        self._poll_id = self.after(100, self._poll)

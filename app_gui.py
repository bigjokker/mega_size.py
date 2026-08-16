"""MEGA Size Inspector — paste links, inspect, download only what you tick."""

from __future__ import annotations

import os
import sys


def _rerun_with_venv() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        venv_python = os.path.join(here, ".venv", "Scripts", "python.exe")
    else:
        venv_python = os.path.join(here, ".venv", "bin", "python")
    if not os.path.isfile(venv_python):
        return
    try:
        same = os.path.samefile(sys.executable, venv_python)
    except OSError:
        same = os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(
            os.path.abspath(venv_python)
        )
    if same:
        return
    import subprocess

    raise SystemExit(
        subprocess.call([venv_python, os.path.abspath(__file__), *sys.argv[1:]])
    )


_rerun_with_venv()

import json
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from mega_core import (
    Filters,
    InspectResult,
    apply_filters,
    breakdown_for,
    format_timestamp,
    inspect_url,
)
from mega_crypto import HAS_CRYPTO, download_time_seconds, format_duration, format_size, sanitize_filename
from mega_download import DownloadCancelled, DownloadItem, download_selected, items_from_nodes
from mega_links import ParsedLink, extract_from_file, extract_mega_links, parse_mega_url

APP_TITLE = "MEGA Size Inspector"
ACCENT = "#D9272E"
BG = "#1a1a1a"
PANEL = "#242424"
RECENT_LIMIT = 15


def app_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "MegaSizeInspector")
    else:
        path = os.path.join(os.path.expanduser("~"), ".mega_size_inspector")
    os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(app_dir(), "settings.json")


def recents_path() -> str:
    return os.path.join(app_dir(), "recents.json")


def default_download_dir() -> str:
    home = os.path.expanduser("~")
    for name in ("Downloads", "downloads"):
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    return home


def load_settings() -> dict:
    data = {"last_download_dir": "", "recents": [], "saved_links": []}
    try:
        with open(settings_path(), "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            for key in data:
                if key in loaded:
                    data[key] = loaded[key]
    except Exception:
        pass
    if not data["recents"]:
        try:
            with open(recents_path(), "r", encoding="utf-8") as handle:
                old = json.load(handle)
            if isinstance(old, list):
                data["recents"] = old
        except Exception:
            pass
    return data


def save_settings(data: dict) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception:
        pass


def load_recents() -> list[dict]:
    recents = load_settings().get("recents") or []
    return recents if isinstance(recents, list) else []


def save_recents(items: list[dict]) -> None:
    data = load_settings()
    data["recents"] = items[:RECENT_LIMIT]
    save_settings(data)


def parsed_from_saved(entry: dict) -> ParsedLink | None:
    url = (entry or {}).get("url") or ""
    return parse_mega_url(url)


class MegaApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x740")
        self.minsize(920, 620)
        self.configure(fg_color=BG)

        self.links: list[ParsedLink] = []
        self.results: dict[str, InspectResult] = {}
        self.inspect_errors: dict[str, str] = {}
        self.active_url: str | None = None
        self.checks: dict[str, set[str]] = {}
        self.node_by_id: dict[str, object] = {}
        self.inspect_cancel = threading.Event()
        self.download_cancel = threading.Event()
        self.busy = False
        self.last_download_dir = default_download_dir()

        self._build_style()
        self._build_ui()
        self._load_session()
        self._refresh_recents_menu()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self._focus_paste)

    def _build_style(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.Treeview",
            background="#1f1f1f",
            fieldbackground="#1f1f1f",
            foreground="#f2f2f2",
            rowheight=24,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Dark.Treeview.Heading",
            background="#2a2a2a",
            foreground="#dddddd",
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Dark.Treeview",
            background=[("selected", "#3a3a3a")],
            foreground=[("selected", "#ffffff")],
        )

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0)
        header.pack(fill="x")
        ctk.CTkLabel(
            header,
            text="MEGA Size Inspector",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left", padx=16, pady=12)
        self.status_var = tk.StringVar(value="Paste MEGA links. Nothing downloads until you choose files.")
        ctk.CTkLabel(header, textvariable=self.status_var, text_color="#bbbbbb").pack(
            side="right", padx=16
        )

        top = ctk.CTkFrame(self, fg_color=BG)
        top.pack(fill="x", padx=12, pady=(10, 6))

        self.paste_box = ctk.CTkTextbox(top, height=72, wrap="word")
        self.paste_box.pack(fill="x")
        self.paste_box.insert("1.0", "Paste a page, a list, or one MEGA link here…")
        self.paste_box.bind("<FocusIn>", self._clear_placeholder)
        self.paste_box.bind("<Control-Return>", lambda _e: self.on_extract_box())

        buttons = ctk.CTkFrame(top, fg_color="transparent")
        buttons.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(
            buttons, text="Paste links", fg_color=ACCENT, hover_color="#b51f26",
            command=self.on_paste_clipboard, width=130,
        ).pack(side="left")
        ctk.CTkButton(buttons, text="Use text above", command=self.on_extract_box, width=130).pack(
            side="left", padx=6
        )
        ctk.CTkButton(buttons, text="Open file…", command=self.on_open_file, width=110).pack(
            side="left"
        )
        self.recents_menu = ctk.CTkOptionMenu(buttons, values=["Recent links"], command=self.on_recent)
        self.recents_menu.pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="Clear list", fg_color="#444", command=self.on_clear_links, width=90).pack(
            side="left"
        )

        mid = ctk.CTkFrame(self, fg_color=BG)
        mid.pack(fill="both", expand=True, padx=12, pady=6)
        mid.grid_columnconfigure(0, weight=2)
        mid.grid_columnconfigure(1, weight=3)
        mid.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(mid, fg_color=PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ctk.CTkLabel(left, text="Links found", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=10, pady=(8, 4)
        )
        link_wrap = tk.Frame(left, bg=PANEL)
        link_wrap.pack(fill="both", expand=True, padx=8, pady=4)
        self.link_tree = ttk.Treeview(
            link_wrap,
            columns=("kind", "key", "url"),
            show="headings",
            selectmode="extended",
            style="Dark.Treeview",
        )
        self.link_tree.heading("kind", text="Type")
        self.link_tree.heading("key", text="Key")
        self.link_tree.heading("url", text="Link")
        self.link_tree.column("kind", width=70, stretch=False)
        self.link_tree.column("key", width=70, stretch=False)
        self.link_tree.column("url", width=360)
        yscroll = ttk.Scrollbar(link_wrap, orient="vertical", command=self.link_tree.yview)
        self.link_tree.configure(yscrollcommand=yscroll.set)
        self.link_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.link_tree.bind("<<TreeviewSelect>>", self.on_link_select)

        left_btns = ctk.CTkFrame(left, fg_color="transparent")
        left_btns.pack(fill="x", padx=8, pady=8)
        self.inspect_btn = ctk.CTkButton(
            left_btns, text="Inspect selected", fg_color=ACCENT, hover_color="#b51f26",
            command=self.on_inspect_selected,
        )
        self.inspect_btn.pack(side="left")
        ctk.CTkButton(left_btns, text="Inspect all", command=self.on_inspect_all, width=110).pack(
            side="left", padx=6
        )
        self.cancel_inspect_btn = ctk.CTkButton(
            left_btns, text="Cancel", fg_color="#555", command=self.on_cancel_inspect, width=80, state="disabled"
        )
        self.cancel_inspect_btn.pack(side="left")

        right = ctk.CTkFrame(mid, fg_color=PANEL)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.size_var = tk.StringVar(value="—")
        self.meta_var = tk.StringVar(value="Inspect a link to see its size and files.")
        self.warn_var = tk.StringVar(value="")
        ctk.CTkLabel(right, textvariable=self.size_var, font=ctk.CTkFont(size=32, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 0)
        )
        ctk.CTkLabel(right, textvariable=self.meta_var, text_color="#cccccc").pack(anchor="w", padx=12)
        ctk.CTkLabel(right, textvariable=self.warn_var, text_color="#e0b04a", wraplength=520).pack(
            anchor="w", padx=12, pady=(0, 4)
        )

        self.breakdown_var = tk.StringVar(value="")
        ctk.CTkLabel(right, textvariable=self.breakdown_var, text_color="#9ad").pack(anchor="w", padx=12)

        tools = ctk.CTkFrame(right, fg_color="transparent")
        tools.pack(fill="x", padx=10, pady=6)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_tree())
        ctk.CTkEntry(tools, textvariable=self.search_var, placeholder_text="Search files…", width=180).pack(
            side="left"
        )
        self.sort_var = tk.StringVar(value="Size")
        ctk.CTkOptionMenu(
            tools, values=["Size", "Name", "Date"], variable=self.sort_var,
            command=lambda _v: self.refresh_tree(), width=90,
        ).pack(side="left", padx=6)
        self.folders_only = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            tools, text="Folders only", variable=self.folders_only, command=self.refresh_tree
        ).pack(side="left")
        ctk.CTkButton(tools, text="Tick visible", command=self.on_tick_visible, width=100).pack(
            side="left", padx=6
        )
        ctk.CTkButton(tools, text="Clear ticks", command=self.on_clear_ticks, width=90).pack(side="left")

        filter_row = ctk.CTkFrame(right, fg_color="transparent")
        filter_row.pack(fill="x", padx=10, pady=(0, 6))
        self.ext_var = tk.StringVar()
        self.min_var = tk.StringVar()
        ctk.CTkLabel(filter_row, text="Ext").pack(side="left")
        ctk.CTkEntry(filter_row, textvariable=self.ext_var, placeholder_text=".mkv,.mp4", width=110).pack(
            side="left", padx=4
        )
        ctk.CTkLabel(filter_row, text="Min").pack(side="left")
        ctk.CTkEntry(filter_row, textvariable=self.min_var, placeholder_text="500MB", width=80).pack(
            side="left", padx=4
        )
        self.mbps_var = tk.StringVar(value="100")
        ctk.CTkLabel(filter_row, text="Mbps").pack(side="left")
        ctk.CTkEntry(filter_row, textvariable=self.mbps_var, width=60).pack(side="left", padx=4)
        ctk.CTkButton(filter_row, text="Apply filters", command=self.refresh_tree, width=110).pack(
            side="left", padx=6
        )
        self.eta_var = tk.StringVar(value="")
        ctk.CTkLabel(right, textvariable=self.eta_var, text_color="#aaa").pack(anchor="w", padx=12)

        tree_wrap = tk.Frame(right, bg=PANEL)
        tree_wrap.pack(fill="both", expand=True, padx=8, pady=4)
        self.file_tree = ttk.Treeview(
            tree_wrap,
            columns=("sel", "name", "size", "date"),
            show="headings",
            selectmode="browse",
            style="Dark.Treeview",
        )
        self.file_tree.heading("sel", text="")
        self.file_tree.heading("name", text="Name")
        self.file_tree.heading("size", text="Size")
        self.file_tree.heading("date", text="Date")
        self.file_tree.column("sel", width=36, stretch=False, anchor="center")
        self.file_tree.column("name", width=340)
        self.file_tree.column("size", width=100, stretch=False, anchor="e")
        self.file_tree.column("date", width=130, stretch=False)
        fy = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=fy.set)
        self.file_tree.pack(side="left", fill="both", expand=True)
        fy.pack(side="right", fill="y")
        self.file_tree.bind("<Button-1>", self.on_tree_click)

        bottom = ctk.CTkFrame(right, fg_color="transparent")
        bottom.pack(fill="x", padx=10, pady=8)
        self.download_btn = ctk.CTkButton(
            bottom,
            text="Download selected…",
            fg_color=ACCENT,
            hover_color="#b51f26",
            command=self.on_download_selected,
            state="disabled",
        )
        self.download_btn.pack(side="left")
        self.cancel_dl_btn = ctk.CTkButton(
            bottom, text="Cancel download", fg_color="#555", command=self.on_cancel_download,
            width=140, state="disabled",
        )
        self.cancel_dl_btn.pack(side="left", padx=6)
        ctk.CTkButton(bottom, text="Export CSV…", command=self.on_export_csv, width=110).pack(side="left")
        self.progress = ctk.CTkProgressBar(bottom, width=220)
        self.progress.set(0)
        self.progress.pack(side="right", padx=8)
        self.progress_label = ctk.CTkLabel(bottom, text="")
        self.progress_label.pack(side="right")

        if not HAS_CRYPTO:
            self.warn_var.set("Install pycryptodome to decrypt names and enable downloads.")

    def _focus_paste(self):
        self.paste_box.focus_set()

    def _clear_placeholder(self, _event=None):
        text = self.paste_box.get("1.0", "end").strip()
        if text.startswith("Paste a page"):
            self.paste_box.delete("1.0", "end")

    def set_status(self, text: str):
        self.status_var.set(text)

    def _checked(self, url: str | None = None) -> set[str]:
        key = url or self.active_url
        if not key:
            return set()
        return self.checks.setdefault(key, set())

    def _save_session(self) -> None:
        data = load_settings()
        data["last_download_dir"] = self.last_download_dir or ""
        data["saved_links"] = [
            {
                "url": item.url,
                "kind": item.kind,
                "handle": item.handle,
                "key": item.key,
                "source": item.source,
            }
            for item in self.links
        ]
        save_settings(data)

    def _load_session(self) -> None:
        data = load_settings()
        saved_dir = data.get("last_download_dir") or ""
        if saved_dir and os.path.isdir(saved_dir):
            self.last_download_dir = saved_dir
        restored: list[ParsedLink] = []
        seen: set[tuple[str, str]] = set()
        for entry in data.get("saved_links") or []:
            if not isinstance(entry, dict):
                continue
            parsed = parsed_from_saved(entry)
            if not parsed:
                continue
            key = (parsed.kind, parsed.handle)
            if key in seen:
                continue
            seen.add(key)
            restored.append(parsed)
        if restored:
            self.links = restored
            self.refresh_link_tree()
            self.set_status(f"Restored {len(restored)} saved link(s). Inspect to load sizes.")

    def on_close(self):
        self._save_session()
        self.destroy()

    def add_links(self, parsed: list[ParsedLink]):
        if not parsed:
            self.set_status("No MEGA links found in that text.")
            return
        existing = {(item.kind, item.handle): i for i, item in enumerate(self.links)}
        added = 0
        for item in parsed:
            key = (item.kind, item.handle)
            if key in existing:
                old = self.links[existing[key]]
                if item.has_key and not old.has_key:
                    self.links[existing[key]] = item
                continue
            existing[key] = len(self.links)
            self.links.append(item)
            added += 1
        self.refresh_link_tree()
        self._save_session()
        self.set_status(f"Added {added} link(s). {len(self.links)} in the list.")

    def refresh_link_tree(self):
        self.link_tree.delete(*self.link_tree.get_children())
        for index, item in enumerate(self.links):
            err = self.inspect_errors.get(item.url, "")
            if item.url in self.results:
                tag = "ok"
            elif err and "quota" in err.lower():
                tag = "quota"
            elif err:
                tag = "fail"
            else:
                tag = ""
            self.link_tree.insert(
                "",
                "end",
                iid=f"link-{index}",
                values=(item.kind, "yes" if item.has_key else "no", item.display_url),
                tags=(tag,),
            )
        self.link_tree.tag_configure("ok", foreground="#8fd18f")
        self.link_tree.tag_configure("fail", foreground="#e09090")
        self.link_tree.tag_configure("quota", foreground="#e0b04a")

    def selected_links(self) -> list[ParsedLink]:
        picked = []
        for iid in self.link_tree.selection():
            try:
                index = int(str(iid).split("-", 1)[1])
                picked.append(self.links[index])
            except (IndexError, ValueError):
                continue
        return picked

    def on_paste_clipboard(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        if not text.strip():
            self.set_status("Clipboard is empty.")
            return
        self.paste_box.delete("1.0", "end")
        self.paste_box.insert("1.0", text)
        self.add_links(extract_mega_links(text))

    def on_extract_box(self):
        text = self.paste_box.get("1.0", "end")
        self.add_links(extract_mega_links(text))

    def on_open_file(self):
        path = filedialog.askopenfilename(
            title="Open a text or HTML file of MEGA links",
            filetypes=[
                ("Text / HTML", "*.txt *.html *.htm *.md *.csv"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.add_links(extract_from_file(path))
        except OSError as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def on_clear_links(self):
        self.links.clear()
        self.results.clear()
        self.inspect_errors.clear()
        self.active_url = None
        self.checks.clear()
        self.refresh_link_tree()
        self.refresh_tree()
        self.size_var.set("—")
        self.meta_var.set("Inspect a link to see its size and files.")
        self.warn_var.set("")
        self.breakdown_var.set("")
        self.eta_var.set("")
        self._save_session()
        self.set_status("List cleared.")

    def on_recent(self, value: str):
        if not value or value == "Recent links" or " — " not in value:
            return
        url = value.split(" — ", 1)[-1]
        parsed = extract_mega_links(url)
        self.add_links(parsed)

    def _refresh_recents_menu(self):
        recents = load_recents()
        values = ["Recent links"]
        for item in recents:
            label = f"{item.get('size', '?')} — {item.get('url', '')}"
            values.append(label)
        self.recents_menu.configure(values=values)
        self.recents_menu.set("Recent links")

    def remember_recent(self, result: InspectResult):
        recents = [item for item in load_recents() if item.get("url") != result.link.url]
        recents.insert(
            0,
            {"url": result.link.url, "size": format_size(result.total_size)},
        )
        save_recents(recents)
        self._refresh_recents_menu()

    def on_link_select(self, _event=None):
        picked = self.selected_links()
        if len(picked) != 1:
            return
        if picked[0].url in self.results:
            self.show_result(picked[0].url)
            return
        if picked[0].url in self.inspect_errors:
            self.active_url = None
            self.size_var.set("—")
            self.meta_var.set("This link could not be inspected.")
            self.warn_var.set(self.inspect_errors[picked[0].url])
            self.breakdown_var.set("")
            self.eta_var.set("")
            self.file_tree.delete(*self.file_tree.get_children())
            self._update_download_button()

    def on_inspect_selected(self):
        targets = self.selected_links() or list(self.links)
        self.start_inspect(targets)

    def on_inspect_all(self):
        self.start_inspect(list(self.links))

    def start_inspect(self, targets: list[ParsedLink]):
        if self.busy:
            return
        if not targets:
            self.set_status("Add some MEGA links first.")
            return
        self.busy = True
        self.inspect_cancel.clear()
        self.inspect_btn.configure(state="disabled")
        self.cancel_inspect_btn.configure(state="normal")
        thread = threading.Thread(target=self._inspect_worker, args=(targets,), daemon=True)
        thread.start()

    def on_cancel_inspect(self):
        self.inspect_cancel.set()
        self.set_status("Stopping inspect…")

    def _inspect_worker(self, targets: list[ParsedLink]):
        last_url = None
        failed: list[tuple[str, str]] = []
        for index, link in enumerate(targets, start=1):
            if self.inspect_cancel.is_set():
                break
            if link.url in self.results:
                last_url = link.url
                self.after(0, self.set_status, f"Inspecting {index}/{len(targets)}… already done")
                continue
            self.after(0, self.set_status, f"Inspecting {index}/{len(targets)}…")
            try:
                result = inspect_url(link)
                self.results[link.url] = result
                self.inspect_errors.pop(link.url, None)
                last_url = link.url
                self.after(0, self.remember_recent, result)
            except Exception as exc:
                short = str(exc).split("\n", 1)[0]
                self.inspect_errors[link.url] = str(exc)
                failed.append((link.display_url, short))
                self.after(0, self.refresh_link_tree)
        self.after(0, self._inspect_done, last_url, failed)

    def _inspect_done(self, last_url: str | None, failed: list[tuple[str, str]] | None = None):
        failed = failed or []
        self.busy = False
        self.inspect_btn.configure(state="normal")
        self.cancel_inspect_btn.configure(state="disabled")
        self.refresh_link_tree()
        if last_url:
            for index, item in enumerate(self.links):
                if item.url == last_url:
                    self.link_tree.selection_set(f"link-{index}")
                    break
            self.show_result(last_url)
        ok = len(self.results)
        if failed and not self.inspect_cancel.is_set():
            quota = [(u, s) for u, s in failed if "quota" in s.lower()]
            dead = [(u, s) for u, s in failed if "quota" not in s.lower()]
            self.set_status(
                f"Inspect finished. {ok} ok, {len(dead)} missing, {len(quota)} quota-blocked."
            )
            parts = []
            if dead:
                parts.append("Not on MEGA anymore:")
                parts.extend(f"• {url}" for url, _ in dead[:6])
            if quota:
                parts.append("Valid link, MEGA transfer quota is used up:")
                parts.extend(f"• {url}" for url, _ in quota[:6])
            messagebox.showwarning(APP_TITLE, "\n".join(parts))
        elif self.inspect_cancel.is_set():
            self.set_status("Inspect stopped.")
        else:
            self.set_status("Inspect finished. Tick files, then click Download selected.")

    def current_result(self) -> InspectResult | None:
        if self.active_url:
            return self.results.get(self.active_url)
        return None

    def current_filters(self) -> Filters:
        ext_raw = self.ext_var.get().strip()
        extensions = [part.strip() for part in ext_raw.split(",") if part.strip()] if ext_raw else None
        sort_name = self.sort_var.get().lower()
        return Filters(
            extensions=extensions,
            min_size=self.min_var.get().strip() or None,
            search=self.search_var.get(),
            folders_only=self.folders_only.get(),
            sort_key="size" if sort_name == "size" else "date" if sort_name == "date" else "name",
            sort_desc=sort_name != "name",
        )

    def show_result(self, url: str):
        result = self.results.get(url)
        if not result:
            return
        self.active_url = url
        self.size_var.set(format_size(result.total_size))
        kind = "folder" if result.is_folder else "file"
        key_txt = "key present" if result.link.has_key else "no key"
        names = "names decrypted" if result.names_decrypted else "names hidden"
        self.meta_var.set(
            f"{kind} · {result.file_count} files · {result.folder_count} folders · {key_txt} · {names}"
        )
        self.warn_var.set(result.warning or "")
        self.refresh_tree()

    def refresh_tree(self):
        self.file_tree.delete(*self.file_tree.get_children())
        self.node_by_id.clear()
        result = self.current_result()
        if not result:
            self.breakdown_var.set("")
            self.eta_var.set("")
            self._update_download_button()
            return

        try:
            filters = self.current_filters()
            visible_files = apply_filters(result, filters)
        except ValueError as exc:
            self.set_status(str(exc))
            visible_files = []

        visible_handles = {node.handle for node in visible_files}
        current = self._checked()
        current &= visible_handles
        self._fill_tree(result.roots, "", visible_handles, filters.folders_only)

        rows = breakdown_for(visible_files)
        total = sum(size for _c, _n, size in rows) or 1
        parts = [f"{cat} {count} ({100 * size / total:.0f}%)" for cat, count, size in rows]
        scope = "filtered" if len(visible_files) != result.file_count else "all"
        self.breakdown_var.set(f"{scope}: " + "   ".join(parts) if parts else "No files match the filters.")

        try:
            mbps = float(self.mbps_var.get() or 0)
        except ValueError:
            mbps = 0
        filtered_size = sum(node.size for node in visible_files)
        eta = download_time_seconds(filtered_size, mbps)
        if eta is not None and visible_files:
            label = "filtered total" if filtered_size != result.total_size else "total"
            self.eta_var.set(f"ETA at {mbps:g} Mbps ({label}): ~{format_duration(eta)}")
        else:
            self.eta_var.set("")
        self._update_download_button()

    def _fill_tree(self, nodes, parent, visible_handles: set[str], folders_only: bool):
        ordered = sorted(nodes, key=lambda n: (not n.is_folder, -n.rollup_size, n.name.lower()))
        for node in ordered:
            if node.is_folder:
                child_files = [c for c in self._walk(node) if not c.is_folder]
                if visible_handles and not any(c.handle in visible_handles for c in child_files):
                    if child_files:
                        continue
                mark = ""
                iid = f"dir:{node.handle}"
                self.node_by_id[iid] = node
                self.file_tree.insert(
                    parent,
                    "end",
                    iid=iid,
                    values=(mark, node.name, format_size(node.rollup_size), format_timestamp(node.timestamp)),
                    open=True,
                )
                self._fill_tree(node.children, iid, visible_handles, folders_only)
            else:
                if folders_only or node.handle not in visible_handles:
                    continue
                iid = f"file:{node.handle}"
                self.node_by_id[iid] = node
                mark = "☑" if node.handle in self._checked() else "☐"
                self.file_tree.insert(
                    parent,
                    "end",
                    iid=iid,
                    values=(mark, node.name, format_size(node.size), format_timestamp(node.timestamp)),
                )

    def _walk(self, node):
        yield node
        for child in node.children:
            yield from self._walk(child)

    def on_tree_click(self, event):
        row = self.file_tree.identify_row(event.y)
        col = self.file_tree.identify_column(event.x)
        if not row:
            return
        if col != "#1" and not row.startswith("file:"):
            return
        node = self.node_by_id.get(row)
        if not node:
            return
        checked = self._checked()
        if getattr(node, "is_folder", False):
            files = [n for n in self._walk(node) if not n.is_folder]
            handles = {n.handle for n in files}
            if handles and handles.issubset(checked):
                checked -= handles
            else:
                checked |= handles
        else:
            if node.handle in checked:
                checked.discard(node.handle)
            else:
                checked.add(node.handle)
        self.refresh_tree()
        return "break"

    def on_tick_visible(self):
        result = self.current_result()
        if not result:
            return
        checked = self._checked()
        for node in apply_filters(result, self.current_filters()):
            checked.add(node.handle)
        self.refresh_tree()

    def on_clear_ticks(self):
        if self.active_url:
            self.checks[self.active_url] = set()
        self.refresh_tree()

    def _share_folder_name(self, result: InspectResult) -> str:
        name = ""
        if result.roots:
            name = result.roots[0].name or ""
        if not name or "(encrypted)" in name.lower():
            name = result.link.handle
        return sanitize_filename(name) or "share"

    def collect_download_items(self) -> list[DownloadItem]:
        items: list[DownloadItem] = []
        used: dict[str, str] = {}
        for url, result in self.results.items():
            handles = self.checks.get(url) or set()
            if not handles:
                continue
            nodes = [node for node in result.files if node.handle in handles and node.file_key_a32]
            items.extend(items_from_nodes(nodes, result.link, result.folder_handle))
            label = self._share_folder_name(result)
            if label in used.values():
                label = f"{label}_{sanitize_filename(result.link.handle)}"
            used[url] = label
        sources = {item.source_url for item in items}
        if len(sources) <= 1:
            return items
        prefixed: list[DownloadItem] = []
        for item in items:
            folder = used.get(item.source_url) or "share"
            prefixed.append(
                DownloadItem(
                    name=item.name,
                    relative_path=f"{folder}/{item.relative_path}",
                    size=item.size,
                    file_handle=item.file_handle,
                    file_key_a32=item.file_key_a32,
                    folder_handle=item.folder_handle,
                    source_url=item.source_url,
                )
            )
        return prefixed

    def _update_download_button(self):
        items = self.collect_download_items()
        result = self.current_result()
        if self.busy:
            self.download_btn.configure(state="disabled")
            return
        if items:
            sources = {item.source_url for item in items}
            extra = f" from {len(sources)} links" if len(sources) > 1 else ""
            size = sum(item.size for item in items)
            self.download_btn.configure(
                state="normal",
                text=f"Download selected ({len(items)} · {format_size(size)}{extra})…",
            )
            return
        if result and not result.link.has_key:
            self.download_btn.configure(state="disabled", text="No key — inspect only")
            return
        self.download_btn.configure(state="disabled", text="Tick files to download")

    def on_download_selected(self):
        if self.busy:
            return
        items = self.collect_download_items()
        if not items:
            messagebox.showinfo(
                APP_TITLE,
                "Tick the files you want first. You can inspect several links, "
                "tick files in each, then download them all at once.",
            )
            return
        dest = filedialog.askdirectory(
            title="Save selected files to…",
            initialdir=self.last_download_dir or default_download_dir(),
        )
        if not dest:
            self.set_status("Download cancelled — no folder chosen.")
            return
        self.last_download_dir = dest
        self._save_session()
        self.busy = True
        self.download_cancel.clear()
        self.download_btn.configure(state="disabled")
        self.cancel_dl_btn.configure(state="normal")
        thread = threading.Thread(
            target=self._download_worker, args=(items, dest), daemon=True
        )
        thread.start()

    def on_cancel_download(self):
        self.download_cancel.set()
        self.set_status("Stopping download…")

    def _download_worker(self, items, dest):
        def progress(info):
            self.after(0, self._show_progress, info)

        try:
            download_selected(
                items,
                dest,
                progress_cb=progress,
                cancel_event=self.download_cancel,
                skip_existing=True,
            )
            if self.download_cancel.is_set():
                self.after(0, self.set_status, "Download cancelled.")
            else:
                self.after(0, self.set_status, f"Downloaded {len(items)} file(s) to {dest}")
        except DownloadCancelled:
            self.after(0, self.set_status, "Download cancelled.")
        except Exception as exc:
            self.after(0, lambda e=str(exc): messagebox.showerror(APP_TITLE, e))
            self.after(0, self.set_status, f"Download failed: {exc}")
        self.after(0, self._download_done)

    def _show_progress(self, info: dict):
        total = info.get("total_size") or 1
        self.progress.set(info.get("total_written", 0) / total)
        self.progress_label.configure(
            text=f"{info['index']}/{info['count']}  {info['name']}"
        )

    def _download_done(self):
        self.busy = False
        self.cancel_dl_btn.configure(state="disabled")
        self.progress.set(0)
        self.progress_label.configure(text="")
        self._update_download_button()

    def on_export_csv(self):
        result = self.current_result()
        if not result:
            self.set_status("Inspect a link first.")
            return
        path = filedialog.asksaveasfilename(
            title="Export file list",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="mega_structure.csv",
        )
        if not path:
            return
        import csv
        from datetime import datetime

        files = apply_filters(result, self.current_filters())
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["path", "type", "size_bytes", "size_human", "ts_iso", "handle"])
            for node in files:
                ts_iso = ""
                if node.timestamp is not None:
                    try:
                        ts_iso = datetime.fromtimestamp(node.timestamp).isoformat(timespec="seconds")
                    except Exception:
                        ts_iso = ""
                writer.writerow(
                    [node.path, "File", node.size, format_size(node.size), ts_iso, node.handle]
                )
        self.set_status(f"Saved {path}")


def main():
    app = MegaApp()
    app.mainloop()


if __name__ == "__main__":
    main()

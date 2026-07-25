"""
gui.py
Tkinter/ttk front-end for ATHIOS MTU Test.

Deliberately plain: standard ttk widgets, the Windows "vista" theme,
Segoe UI, no colors/gradients/animations beyond the one branded Ko-fi
button the user explicitly asked to keep as a plain colored button.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from typing import Optional

from mtu_engine import (
    CANDIDATE_COUNT,
    CONFIRMATION_ROUNDS,
    ESTIMATED_SEARCH_STEPS,
    MtuEngine,
    MtuResult,
    NetworkError,
    PingStatus,
    TestRecord,
)

APP_TITLE = "ATHIOS MTU Test"
KOFI_URL = "https://ko-fi.com/W7W116MME5"
KOFI_COLOR = "#72a4f2"

STATUS_LABELS = {
    PingStatus.SUCCESS: "OK",
    PingStatus.PARTIAL: "OK (partial loss)",
    PingStatus.FRAGMENTATION_NEEDED: "Fragmented",
    PingStatus.TIMEOUT: "Timed out",
}
PHASE_LABELS = {"search": "Search", "confirm": "Confirm"}


def resource_path(relative: str) -> str:
    """Resolve a bundled asset both when run from source and from a
    PyInstaller-frozen exe (which unpacks data files into sys._MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


class MtuTestApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("580x640")
        self.root.minsize(540, 560)

        self._apply_native_style()
        self._set_icon()

        self.task_queue: "queue.Queue" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.last_result: Optional[MtuResult] = None

        self._build_layout()
        self.root.after(80, self._poll_queue)

    # ------------------------------------------------------------- UI setup

    def _apply_native_style(self) -> None:
        self.root.option_add("*Font", "{Segoe UI} 9")
        style = ttk.Style(self.root)
        for theme in ("vista", "winnative"):
            try:
                style.theme_use(theme)
                break
            except tk.TclError:
                continue  # not on Windows (e.g. during development) - keep default theme

    def _set_icon(self) -> None:
        try:
            self.root.iconbitmap(resource_path("assets/icon.ico"))
        except tk.TclError:
            pass  # icon file missing or platform doesn't support .ico

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        # -- header: small logo + title -----------------------------------
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        try:
            self._logo_img = tk.PhotoImage(file=resource_path("assets/logo.png"))
            ttk.Label(header, image=self._logo_img).pack(side="left", padx=(0, 8))
        except tk.TclError:
            pass
        ttk.Label(header, text=APP_TITLE, font=("Segoe UI", 12, "bold")).pack(side="left")

        # -- start button ---------------------------------------------------
        self.start_btn = ttk.Button(outer, text="Start Test", command=self._on_start)
        self.start_btn.pack(fill="x", pady=(0, 8))

        # -- progress bar + status line --------------------------------------
        self.progress = ttk.Progressbar(outer, mode="determinate", maximum=ESTIMATED_SEARCH_STEPS)
        self.progress.pack(fill="x", pady=(0, 4))

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(outer, textvariable=self.status_var, foreground="#444444").pack(anchor="w", pady=(0, 10))

        # -- result summary --------------------------------------------------
        results = ttk.LabelFrame(outer, text="Result", padding=10)
        results.pack(fill="x", pady=(0, 10))

        self.mtu_var = tk.StringVar(value="—")
        self.payload_var = tk.StringVar(value="—")
        self.latency_var = tk.StringVar(value="—")

        self._result_row(results, 0, "Best MTU:", self.mtu_var, big=True)
        self._result_row(results, 1, "Payload Size:", self.payload_var)
        self._result_row(results, 2, "Average Latency:", self.latency_var)

        # -- history table ------------------------------------------------
        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True, pady=(0, 10))

        columns = ("payload", "phase", "result", "latency")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=8)
        headings = {
            "payload": "Payload (bytes)",
            "phase": "Phase",
            "result": "Result",
            "latency": "Avg RTT (ms)",
        }
        widths = {"payload": 110, "phase": 80, "result": 140, "latency": 100}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # -- footer: copy + ko-fi -------------------------------------------
        footer = ttk.Frame(outer)
        footer.pack(fill="x")

        self.copy_btn = ttk.Button(footer, text="Copy Result", command=self._on_copy, state="disabled")
        self.copy_btn.pack(side="left")

        # Plain tk.Button (not ttk) so the exact requested brand color
        # (#72a4f2) always renders regardless of the active ttk theme.
        kofi_btn = tk.Button(
            footer,
            text="☕ Support me on Ko-fi",
            bg=KOFI_COLOR,
            fg="white",
            activebackground=KOFI_COLOR,
            activeforeground="white",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=5,
            cursor="hand2",
            command=lambda: webbrowser.open(KOFI_URL),
        )
        kofi_btn.pack(side="right")

    def _result_row(self, parent: ttk.LabelFrame, row: int, label: str, var: tk.StringVar, big: bool = False) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        font = ("Segoe UI", 13, "bold") if big else ("Segoe UI", 9)
        ttk.Label(parent, textvariable=var, font=font).grid(row=row, column=1, sticky="w", padx=(8, 0), pady=2)

    # ------------------------------------------------------------- actions

    def _on_start(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        self.tree.delete(*self.tree.get_children())
        self.mtu_var.set("—")
        self.payload_var.set("—")
        self.latency_var.set("—")
        self.copy_btn.configure(state="disabled")
        self.start_btn.configure(state="disabled")
        self.progress.configure(maximum=ESTIMATED_SEARCH_STEPS, value=0)
        self.status_var.set("Testing... (binary search)")

        self.worker = threading.Thread(target=self._run_engine, daemon=True)
        self.worker.start()

    def _run_engine(self) -> None:
        """Runs on the worker thread - must never touch tk widgets directly."""
        engine = MtuEngine(on_step=lambda rec: self.task_queue.put(("step", rec)))
        try:
            result = engine.run()
            self.task_queue.put(("done", result))
        except NetworkError as exc:
            self.task_queue.put(("error", str(exc)))
        except Exception as exc:  # keep the UI alive even on an unexpected bug
            self.task_queue.put(("error", f"Unexpected error: {exc}"))

    def _on_copy(self) -> None:
        if not self.last_result:
            return
        r = self.last_result
        text = (
            f"ATHIOS MTU Test result\n"
            f"Best MTU: {r.mtu}\n"
            f"Payload size: {r.payload}\n"
            f"Average latency: {r.avg_latency_ms:.1f} ms"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    # ------------------------------------------------------------- queue polling
    # All widget updates happen here, on the main thread, driven by messages
    # the worker thread drops into task_queue. This keeps tkinter (which is
    # not thread-safe) happy while the actual pinging runs in the background.

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.task_queue.get_nowait()
                if kind == "step":
                    self._handle_step(payload)
                elif kind == "done":
                    self._handle_done(payload)
                elif kind == "error":
                    self._handle_error(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _handle_step(self, record: TestRecord) -> None:
        status_label = STATUS_LABELS.get(record.result.status, record.result.status.name)
        latency = f"{record.result.avg_rtt_ms:.0f}" if record.result.avg_rtt_ms is not None else "—"
        self.tree.insert(
            "", "end",
            values=(record.payload, PHASE_LABELS.get(record.phase, record.phase), status_label, latency),
        )
        self.tree.yview_moveto(1)

        if record.phase == "confirm":
            self.status_var.set(f"Re-testing {record.payload} bytes to confirm accuracy...")
        else:
            self.status_var.set(f"Testing... (binary search) - trying {record.payload} bytes")

        # The exact number of confirmation rounds isn't known until the
        # search phase ends (it depends on how many candidates qualify), so
        # the bar's max is extended once confirmation starts. It's an
        # approximation, not an exact countdown.
        if record.phase == "confirm" and self.progress["maximum"] == ESTIMATED_SEARCH_STEPS:
            self.progress.configure(maximum=ESTIMATED_SEARCH_STEPS + CONFIRMATION_ROUNDS * CANDIDATE_COUNT)
        self.progress.configure(value=self.progress["value"] + 1)

    def _handle_done(self, result: MtuResult) -> None:
        self.last_result = result
        self.mtu_var.set(str(result.mtu))
        self.payload_var.set(f"{result.payload} bytes")
        self.latency_var.set(f"{result.avg_latency_ms:.1f} ms")
        self.status_var.set("Done.")
        self.progress.configure(value=self.progress["maximum"])
        self.copy_btn.configure(state="normal")
        self.start_btn.configure(state="normal")

    def _handle_error(self, message: str) -> None:
        self.status_var.set("Error.")
        self.start_btn.configure(state="normal")
        messagebox.showerror(APP_TITLE, message)

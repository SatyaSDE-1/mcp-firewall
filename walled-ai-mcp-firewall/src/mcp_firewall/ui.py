
"""Always-on-top Tkinter approval popups. Tk must own the main thread."""
import json
import queue
import signal
import time
import tkinter as tk
from tkinter import ttk

from .models import ApprovalRecord, RequestContext

MAX_PARAMS_SHOWN = 4000


class PopupUI:
    def __init__(self, timeout_s: float):
        self._timeout = timeout_s
        self._q: queue.Queue = queue.Queue()      # thread-safe bridge from asyncio thread
        self._wins: dict[str, dict] = {}
        self.decider = None                       # set by main: (request_id, allow) -> None
        self.root: tk.Tk | None = None

    # ---- called from the asyncio thread
    def post_request(self, ctx: RequestContext): self._q.put(("req", ctx))
    def post_resolved(self, rec: ApprovalRecord): self._q.put(("done", rec.request.id))
    def stop(self): self._q.put(("quit", None))

    # ---- main thread
    def run(self):
        self.root = tk.Tk()
        self.root.withdraw()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self.stop())
        self.root.after(100, self._poll)
        self.root.mainloop()

    def _poll(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "req": self._show(payload)
                elif kind == "done": self._close(payload)
                elif kind == "quit":
                    self.root.quit()
                    return
        except queue.Empty:
            pass
        self._tick()
        self.root.after(100, self._poll)

    def _show(self, ctx: RequestContext):
        w = tk.Toplevel(self.root)
        w.title("MCP Firewall: approval required")
        off = 30 * len(self._wins)
        w.geometry(f"580x440+{200 + off}+{120 + off}")
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", lambda: self._answer(ctx.id, False))  # X = Block

        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        rows = [("Source", ctx.source), ("MCP server", ctx.server_name),
                ("Method", ctx.method), ("Tool", ctx.tool_name or "-")]
        for i, (k, v) in enumerate(rows):
            ttk.Label(f, text=k + ":", font=("TkDefaultFont", 10, "bold")).grid(row=i, column=0, sticky="w")
            ttk.Label(f, text=v).grid(row=i, column=1, sticky="w", padx=8)

        ttk.Label(f, text="Parameters:", font=("TkDefaultFont", 10, "bold")).grid(row=4, column=0, sticky="nw", pady=(8, 0))
        txt = tk.Text(f, height=12, wrap="word")
        s = json.dumps(ctx.params, indent=2, default=str)
        if len(s) > MAX_PARAMS_SHOWN:
            s = s[:MAX_PARAMS_SHOWN] + "\n... (truncated)"
        txt.insert("1.0", s)
        txt.configure(state="disabled")
        txt.grid(row=5, column=0, columnspan=2, sticky="nsew")
        f.rowconfigure(5, weight=1)
        f.columnconfigure(1, weight=1)

        countdown = ttk.Label(f, foreground="#b45309")
        countdown.grid(row=6, column=0, columnspan=2, sticky="w", pady=6)

        bar = ttk.Frame(f)
        bar.grid(row=7, column=0, columnspan=2, sticky="e")
        block = ttk.Button(bar, text="Block", command=lambda: self._answer(ctx.id, False))
        allow = ttk.Button(bar, text="Allow", command=lambda: self._answer(ctx.id, True))
        block.pack(side="right", padx=4)
        allow.pack(side="right", padx=4)
        block.focus_set()  # safe default

        self._wins[ctx.id] = {"w": w, "label": countdown, "deadline": ctx.received_at + self._timeout,
                              "buttons": (allow, block)}
        w.lift()
        w.focus_force()
        w.bell()

    def _tick(self):
        now = time.time()
        for d in self._wins.values():
            d["label"].configure(text=f"Auto-blocks in {max(0, int(d['deadline'] - now))}s")

    def _answer(self, rid: str, allow: bool):
        d = self._wins.get(rid)
        if d:
            for b in d["buttons"]:
                b.state(["disabled"])  # no double clicks
        if self.decider:
            self.decider(rid, allow)
        self._close(rid)

    def _close(self, rid: str):
        d = self._wins.pop(rid, None)
        if d:
            d["w"].destroy()
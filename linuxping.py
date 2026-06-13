#!/usr/bin/env python3
"""
linuxping.py — LinuxPing for Linux
Real ICMP ping + TCP port ping, tiled panels, domain support,
persistent config, descriptive logging, downtime tracking, false-positive-resistant down/up thresholds, profile-based host groups, editable panel targets, outage reports, global outage summary, save-on-exit test folders.
Requires: python3-tkinter (sudo dnf install python3-tkinter)
Run with: sudo python3 linuxping.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import subprocess, socket, threading, time, re, json, os, csv, tempfile, shutil
from datetime import datetime
from pathlib import Path

# ── Config file ──────────────────────────────────────────────────────────────
CONFIG_PATH = Path.home() / ".config" / "linuxping" / "config.json"
LOG_DIR     = Path.home() / ".config" / "linuxping" / "logs"

DEFAULT_CONFIG = {
    "interval_ms":    1000,
    "timeout_s":      2,
    "cols":           2,
    # Keep the main screen blank on startup. Use profiles to save/load host groups.
    "default_hosts":  [],
    "profiles":       {},
    "last_profile":   "",
    "rtt_warn_ms":    150,
    "rtt_crit_ms":    300,
    "fail_threshold":  5,   # Official DOWN after this many consecutive failures
    "recover_threshold": 2, # Official UP after this many consecutive successful checks
    "log_enabled":    True,
    "log_dir":        str(LOG_DIR),
    # Logs are written to a temporary run folder first. On exit, you decide whether to save them as a named test directory.
    "prompt_save_logs_on_exit": True,
}

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            # Fill missing keys with defaults
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            # Force a blank startup. Existing profiles are preserved.
            cfg["default_hosts"] = []
            if not isinstance(cfg.get("profiles"), dict):
                cfg["profiles"] = {}
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

# ── Palette ──────────────────────────────────────────────────────────────────
C_BG       = "#1e1e1e"
C_PANEL    = "#252525"
C_BORDER   = "#383838"
C_TEXT     = "#d4d4d4"
C_MUTED    = "#888888"
C_UP       = "#1D9E75"
C_UP_BG    = "#0a2e20"
C_DOWN     = "#E24B4A"
C_DOWN_BG  = "#2e0a0a"
C_RTO      = "#EF9F27"
C_RTO_BG   = "#2e1f00"
C_IDLE     = "#888888"
C_IDLE_BG  = "#252525"
C_BTN      = "#2d2d2d"
C_BTN_HOV  = "#3a3a3a"
C_INPUT_BG = "#2a2a2a"
C_WARN     = "#EF9F27"
C_CRIT     = "#E24B4A"

STATUS_COLORS = {
    "up":   (C_UP,   C_UP_BG),
    "down": (C_DOWN, C_DOWN_BG),
    "rto":  (C_RTO,  C_RTO_BG),
    "dns":  (C_DOWN, C_DOWN_BG),
    "idle": (C_IDLE, C_IDLE_BG),
}

# ── Helpers ──────────────────────────────────────────────────────────────────

def now_str():
    return datetime.now().strftime("%H:%M:%S")

def now_full():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def fmt_duration(seconds):
    """Human-readable duration: 5s, 1m 23s, 2h 5m 10s"""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s"

def fmt_rtt(ms):
    """Pretty RTT: <1ms, 42.3ms, 1.2s"""
    if ms is None:
        return "-"
    if ms < 1:
        return "<1ms"
    if ms < 1000:
        return f"{ms:.1f}ms"
    return f"{ms/1000:.2f}s"

def icmp_ping(host, timeout=2):
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), host],
            capture_output=True, text=True, timeout=timeout + 2
        )
        if result.returncode == 0:
            m   = re.search(r"time[=<]([\d.]+)", result.stdout)
            rtt = float(m.group(1)) if m else 0.0
            ip_m = re.search(r"PING\s+\S+\s+\(([^)]+)\)", result.stdout)
            resolved = ip_m.group(1) if ip_m else None
            return True, round(rtt, 2), resolved
        else:
            out = result.stdout + result.stderr
            if "unknown host" in out.lower() or "name or service not known" in out.lower():
                return "dns", None, None
            return "rto", None, None
    except subprocess.TimeoutExpired:
        return "rto", None, None
    except Exception:
        return "rto", None, None

def tcp_ping(host, port, timeout=2):
    try:
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout) as s:
            rtt  = (time.perf_counter() - start) * 1000
            peer = s.getpeername()[0]
            return True, round(rtt, 2), peer
    except socket.timeout:
        return "rto", None, None
    except socket.gaierror:
        return "dns", None, None
    except Exception:
        return False, None, None

# ── Logger ───────────────────────────────────────────────────────────────────

class PanelLogger:
    def __init__(self, host_str, cfg):
        self.host_str   = host_str
        self.cfg        = cfg
        self._file      = None
        self._writer    = None
        self._lock      = threading.Lock()
        self._open()

    def _open(self):
        if not self.cfg.get("log_enabled"):
            return
        log_dir = Path(self.cfg.get("log_dir", str(LOG_DIR)))
        log_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-.]", "_", self.host_str)
        date = datetime.now().strftime("%Y%m%d")
        path = log_dir / f"{safe}_{date}.csv"
        is_new = not path.exists()
        self._file   = open(path, "a", newline="")
        self._writer = csv.writer(self._file)
        if is_new:
            self._writer.writerow(["timestamp", "event", "rtt_ms", "detail"])
            self._file.flush()

    def write(self, event, rtt_ms, detail):
        if not self._writer:
            return
        with self._lock:
            try:
                self._writer.writerow([now_full(), event, rtt_ms or "", detail])
                self._file.flush()
            except Exception:
                pass

    def close(self):
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass

# ── Panel ────────────────────────────────────────────────────────────────────

class PingPanel:
    def __init__(self, parent_frame, host_str, mode, app, remove_cb, cfg):
        self.app         = app
        self.cfg         = cfg
        self.host_str    = host_str
        self.mode        = mode
        self.remove_cb   = remove_cb
        self.running     = False
        self.thread      = None
        self.resolved_ip = None
        self.logger      = PanelLogger(host_str, cfg)
        self.editing     = False

        self._parse_target(host_str, mode)

        # Stats
        self.sent      = 0
        self.recv      = 0
        self.lost      = 0
        self.last_rtt  = None
        self.min_rtt   = None
        self.max_rtt   = None
        self.sum_rtt   = 0.0
        self.status    = "idle"

        # Downtime / stability tracking
        self.down_since         = None   # epoch when officially marked down
        self.last_down_ts       = None   # last formatted down timestamp
        self.last_up_ts         = None
        self.consecutive_fail   = 0      # used to avoid false DOWN on one missed ping
        self.consecutive_success = 0     # used to confirm recovery
        self.last_fail_reason   = None

        # Outage history for this block. Official outages only.
        self.outage_history     = []
        self.current_outage     = None
        self.outage_details_visible = False

        self._build(parent_frame)

    def _parse_target(self, host_str, mode):
        """Apply a host/mode to this panel without touching the UI."""
        self.host_str = host_str.strip()
        self.mode = mode.lower().strip() if mode else "icmp"
        if self.mode not in ("icmp", "tcp"):
            self.mode = "icmp"

        if ":" in self.host_str and self.mode == "tcp":
            parts = self.host_str.rsplit(":", 1)
            self.host = parts[0].strip()
            try:
                self.port = int(parts[1])
            except ValueError:
                self.port = 443
                self.host_str = f"{self.host}:443"
        else:
            self.host = self.host_str
            self.port = None

    def _target_display(self):
        return self.host_str + f"  [{self.mode.upper()}]"

    def _refresh_header_text(self):
        if hasattr(self, "host_label"):
            self.host_label.config(text=self._target_display())

    # ── Build UI ──────────────────────────────────────────────────────────

    def _build(self, parent):
        self.frame = tk.Frame(parent, bg=C_PANEL, bd=0,
                              highlightthickness=1,
                              highlightbackground=C_BORDER)

        # Header
        self.header = tk.Frame(self.frame, bg=C_IDLE_BG, pady=5)
        self.header.pack(fill=tk.X)

        self.dot = tk.Label(self.header, text="●", fg=C_IDLE,
                            bg=C_IDLE_BG, font=("monospace", 10))
        self.dot.pack(side=tk.LEFT, padx=(8, 4))

        host_disp = self.host_str + f"  [{self.mode.upper()}]"
        self.host_label = tk.Label(self.header, text=host_disp,
                                   fg=C_TEXT, bg=C_IDLE_BG,
                                   font=("monospace", 10, "bold"),
                                   cursor="xterm")
        self.host_label.pack(side=tk.LEFT)
        self.host_label.bind("<Double-Button-1>", lambda e: self._start_inline_edit())

        # Keep the header clean so action buttons do not get squeezed.
        # Current status is shown in the outage/status row instead.
        self.status_label = tk.Label(self.header, text="idle",
                                     fg=C_MUTED, bg=C_IDLE_BG,
                                     font=("monospace", 9))

        # Resolved IP + downtime info
        self.sub_label = tk.Label(self.frame, text="", fg=C_MUTED,
                                  bg=C_PANEL, font=("monospace", 9))
        self.sub_label.pack(anchor=tk.W, padx=14)

        # Stats
        sf = tk.Frame(self.frame, bg=C_PANEL)
        sf.pack(fill=tk.X, padx=6, pady=(2, 2))
        self.lbl_sent = self._stat(sf, "SENT",  "0")
        self.lbl_recv = self._stat(sf, "RECV",  "0")
        self.lbl_loss = self._stat(sf, "LOSS",  "0%")
        self.lbl_rtt  = self._stat(sf, "RTT",   "-")
        self.lbl_min  = self._stat(sf, "MIN",   "-")
        self.lbl_max  = self._stat(sf, "MAX",   "-")
        self.lbl_avg  = self._stat(sf, "AVG",   "-")

        # Compact per-panel status/outage summary.
        # The full outage table is now a global dropdown above all blocks.
        of = tk.Frame(self.frame, bg=C_PANEL)
        of.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.outage_summary = tk.Label(
            of, text="Status: idle  |  Outages: 0  |  Last Down: -  |  Last Up: -  |  Last Duration: -",
            fg=C_MUTED, bg=C_PANEL, font=("monospace", 8), anchor="w"
        )
        self.outage_summary.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Log
        lf = tk.Frame(self.frame, bg=C_PANEL)
        lf.pack(fill=tk.BOTH, padx=6, pady=(0, 4))
        self.log = tk.Text(lf, height=7, bg=C_BG, fg=C_TEXT,
                           font=("monospace", 9), bd=0,
                           state=tk.DISABLED, wrap=tk.WORD,
                           insertbackground=C_TEXT)
        sb = tk.Scrollbar(lf, command=self.log.yview,
                          bg=C_PANEL, troughcolor=C_BG)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.pack(fill=tk.BOTH, expand=True)

        self.log.tag_config("ok",    foreground=C_UP)
        self.log.tag_config("warn",  foreground=C_WARN)
        self.log.tag_config("crit",  foreground=C_CRIT)
        self.log.tag_config("rto",   foreground=C_RTO)
        self.log.tag_config("loss",  foreground=C_DOWN)
        self.log.tag_config("dns",   foreground=C_DOWN)
        self.log.tag_config("event", foreground="#56b6e8")
        self.log.tag_config("ts",    foreground=C_MUTED)

        # Buttons
        bf = tk.Frame(self.frame, bg=C_PANEL)
        bf.pack(fill=tk.X, padx=6, pady=(0, 6))
        self.btn_toggle = self._btn(bf, "▶  Start", self.toggle)
        self.btn_toggle.pack(side=tk.LEFT, padx=(0, 4))
        self._btn(bf, "✎  Edit", self._start_inline_edit).pack(side=tk.LEFT, padx=(0, 4))
        self._btn(bf, "↺  Clear", self.clear).pack(side=tk.LEFT, padx=(0, 4))
        self._btn(bf, "✕", self._remove, fg=C_DOWN).pack(side=tk.LEFT)

    def _stat(self, parent, label, val):
        f = tk.Frame(parent, bg=C_PANEL)
        f.pack(side=tk.LEFT, padx=4)
        tk.Label(f, text=label, fg=C_MUTED, bg=C_PANEL,
                 font=("monospace", 7)).pack()
        lbl = tk.Label(f, text=val, fg=C_TEXT, bg=C_PANEL,
                       font=("monospace", 11, "bold"))
        lbl.pack()
        return lbl

    def _btn(self, parent, text, cmd, fg=C_TEXT):
        return tk.Button(parent, text=text, command=cmd,
                         bg=C_BTN, fg=fg, activebackground=C_BTN_HOV,
                         activeforeground=fg, relief=tk.FLAT,
                         font=("monospace", 9), padx=8, pady=2,
                         bd=0, cursor="hand2")

    def _start_inline_edit(self):
        if self.editing:
            return
        was_running = self.running
        if was_running:
            self.stop()

        self.editing = True
        self.host_label.pack_forget()

        edit_frame = tk.Frame(self.header, bg=self.header.cget("bg"))
        edit_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        host_var = tk.StringVar(value=self.host_str)
        mode_var = tk.StringVar(value=self.mode)

        entry = tk.Entry(edit_frame, textvariable=host_var, bg=C_INPUT_BG, fg=C_TEXT,
                         insertbackground=C_TEXT, font=("monospace", 10),
                         relief=tk.FLAT, bd=3, width=24)
        entry.pack(side=tk.LEFT, padx=(0, 4))

        mode_box = ttk.Combobox(edit_frame, textvariable=mode_var,
                                values=["icmp", "tcp"], width=5,
                                state="readonly", font=("monospace", 9))
        mode_box.pack(side=tk.LEFT, padx=(0, 4))

        def cancel():
            edit_frame.destroy()
            self.host_label.pack(side=tk.LEFT)
            self.editing = False
            if was_running:
                self.start()

        def apply():
            new_host = host_var.get().strip()
            new_mode = mode_var.get().strip().lower()
            if not new_host:
                messagebox.showwarning("Missing host", "Enter a host or host:port.", parent=self.frame)
                return
            if new_mode == "tcp" and ":" not in new_host:
                messagebox.showwarning("Missing TCP port",
                                       "For TCP mode, use host:port, for example 192.168.1.1:443.",
                                       parent=self.frame)
                return

            old_target = self.host_str
            try:
                self.logger.close()
            except Exception:
                pass
            self._parse_target(new_host, new_mode)
            self.logger = PanelLogger(self.host_str, self.cfg)
            self.clear()
            self._refresh_header_text()
            self._set_status("idle")
            self._log("event", f"Target changed from {old_target} to {self.host_str} [{self.mode.upper()}]")

            edit_frame.destroy()
            self.host_label.pack(side=tk.LEFT)
            self.editing = False
            if was_running:
                self.start()

        tk.Button(edit_frame, text="Save", command=apply, bg=C_UP_BG, fg=C_UP,
                  relief=tk.FLAT, font=("monospace", 9), padx=8, bd=0,
                  cursor="hand2").pack(side=tk.LEFT, padx=(0, 3))
        tk.Button(edit_frame, text="Cancel", command=cancel, bg=C_BTN, fg=C_TEXT,
                  relief=tk.FLAT, font=("monospace", 9), padx=8, bd=0,
                  cursor="hand2").pack(side=tk.LEFT)

        entry.bind("<Return>", lambda e: apply())
        entry.bind("<Escape>", lambda e: cancel())
        entry.focus_set()
        entry.selection_range(0, tk.END)

    # ── Outage dropdown / report helpers ──────────────────────────────────

    def _toggle_outage_details(self):
        # Kept for compatibility with older callbacks; per-panel outage tables
        # were replaced by the global outage summary dropdown.
        return

    def _refresh_outage_summary(self):
        status_text = {
            "up": "online",
            "down": "officially down",
            "rto": "unstable",
            "dns": "DNS down",
            "idle": "idle",
        }.get(self.status, self.status)
        last = self.outage_history[-1] if self.outage_history else None
        if self.current_outage:
            down_time = self.current_outage.get("down_time", "-")
            duration = fmt_duration(time.time() - self.current_outage.get("down_epoch", time.time()))
            summary = (f"Status: {status_text}  |  Outages: {len(self.outage_history) + 1}  |  "
                       f"Down Since: {down_time}  |  Down For: {duration}")
        elif last:
            summary = (f"Status: {status_text}  |  Outages: {len(self.outage_history)}  |  "
                       f"Last Down: {last.get('down_time', '-')}  |  "
                       f"Last Up: {last.get('up_time', '-')}  |  Last Duration: {last.get('duration', '-')}")
        else:
            summary = f"Status: {status_text}  |  Outages: 0  |  Last Down: -  |  Last Up: -  |  Last Duration: -"

        if hasattr(self, "outage_summary"):
            self.outage_summary.config(text=summary)
        try:
            self.app.refresh_global_outage_summary()
        except Exception:
            pass

    def finalize_open_outage(self):
        """Close an ongoing outage for report purposes when the app exits."""
        if not self.current_outage:
            return None
        up_epoch = time.time()
        duration_seconds = int(up_epoch - self.current_outage.get("down_epoch", up_epoch))
        outage = {
            "target": self.host_str,
            "mode": self.mode,
            "down_time": self.current_outage.get("down_time", ""),
            "up_time": "still down when test ended",
            "duration_seconds": duration_seconds,
            "duration": fmt_duration(duration_seconds),
            "failures": self.current_outage.get("failures", self.consecutive_fail),
            "status": "still_down_at_end",
        }
        if not self.outage_history or self.outage_history[-1] != outage:
            self.outage_history.append(outage)
        self.current_outage = None
        self._refresh_outage_summary()
        return outage

    # ── Logging ───────────────────────────────────────────────────────────

    def _log(self, tag, msg):
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, now_str() + "  ", "ts")
        self.log.insert(tk.END, msg + "\n", tag)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > 500:
            self.log.configure(state=tk.NORMAL)
            self.log.delete("1.0", "50.0")
            self.log.configure(state=tk.DISABLED)

    # ── Status ────────────────────────────────────────────────────────────

    def _set_status(self, new_status, rtt=None):
        """Update visual status and log official DOWN/UP transitions.

        Important behavior:
        - A single missed probe does NOT officially mark the host down.
        - Official DOWN is only logged when the probe loop calls this with
          new_status in ("down", "dns") after the configured failure threshold.
        - Official UP is only logged after the configured recovery threshold.
        """
        old_status = self.status
        self.status = new_status
        fg, bg = STATUS_COLORS.get(new_status, (C_IDLE, C_IDLE_BG))

        status_text = {
            "up":   "online",
            "down": "officially down",
            "rto":  "unstable / timeout",
            "dns":  "DNS down",
            "idle": "idle",
        }.get(new_status, new_status)

        # ── Transition events ──
        went_down = old_status not in ("down", "dns") and new_status in ("down", "dns")
        came_back = old_status in ("down", "dns") and new_status == "up"

        if went_down:
            down_epoch = time.time()
            self.down_since   = down_epoch
            self.last_down_ts = now_full()
            self.current_outage = {
                "target": self.host_str,
                "mode": self.mode,
                "down_time": self.last_down_ts,
                "down_epoch": down_epoch,
                "failures": self.consecutive_fail,
                "reason": self.last_fail_reason or new_status,
            }
            msg = (f"[!] HOST DOWN  —  {self.host_str}  officially marked down at "
                   f"{self.last_down_ts} after {self.consecutive_fail} consecutive failures")
            self.frame.after(0, lambda: self._log("event", msg))
            self.logger.write("DOWN", None,
                              f"Official down after {self.consecutive_fail} consecutive failures")

        if came_back and self.down_since:
            up_epoch          = time.time()
            downtime          = up_epoch - self.down_since
            self.last_up_ts   = now_full()
            dur               = fmt_duration(downtime)
            outage = {
                "target": self.host_str,
                "mode": self.mode,
                "down_time": self.current_outage.get("down_time", self.last_down_ts) if self.current_outage else self.last_down_ts,
                "up_time": self.last_up_ts,
                "duration_seconds": int(downtime),
                "duration": dur,
                "failures": self.current_outage.get("failures", self.consecutive_fail) if self.current_outage else self.consecutive_fail,
                "reason": self.current_outage.get("reason", "") if self.current_outage else "",
                "status": "recovered",
            }
            self.outage_history.append(outage)
            self.current_outage = None
            try:
                self.app.record_outage(outage)
            except Exception:
                pass
            msg = (f"[✓] HOST BACK  —  {self.host_str}  officially recovered at {self.last_up_ts}  "
                   f"|  was down for {dur}  |  rtt={fmt_rtt(rtt)}")
            self.frame.after(0, lambda: self._log("event", msg))
            self.logger.write("UP", rtt, f"Recovered at {self.last_up_ts}, was down for {dur}")
            self.down_since = None

        def _apply():
            self.header.config(bg=bg)
            self.dot.config(fg=fg, bg=bg)
            self.host_label.config(bg=bg)
            self.status_label.config(text=status_text, fg=fg, bg=bg)
            self.frame.config(highlightbackground=fg if new_status != "idle" else C_BORDER)

            # Sub-label: show resolved IP + failure/recovery state
            parts = []
            if self.resolved_ip and self.resolved_ip != self.host:
                parts.append(f"→ {self.resolved_ip}")
            if self.status == "rto" and self.consecutive_fail:
                threshold = self.cfg.get("fail_threshold", 5)
                parts.append(f"⚠ fail {self.consecutive_fail}/{threshold}")
            if self.down_since and new_status in ("down", "dns"):
                elapsed = fmt_duration(time.time() - self.down_since)
                parts.append(f"⚠ down for {elapsed}")
            self.sub_label.config(text="  ".join(parts) if parts else "")
            self._refresh_outage_summary()

        self.frame.after(0, _apply)
        self.app.update_global_stats()

    def _update_stats(self):
        loss_pct = f"{round(self.lost / self.sent * 100)}%" if self.sent else "0%"
        avg      = round(self.sum_rtt / self.recv, 1) if self.recv else None
        rtt_val  = self.last_rtt or 0

        warn_ms = self.cfg.get("rtt_warn_ms", 150)
        crit_ms = self.cfg.get("rtt_crit_ms", 300)
        rtt_fg  = C_CRIT if rtt_val >= crit_ms else (C_WARN if rtt_val >= warn_ms else C_UP)
        if self.last_rtt is None:
            rtt_fg = C_MUTED

        def _apply():
            self.lbl_sent.config(text=str(self.sent))
            self.lbl_recv.config(text=str(self.recv),
                                 fg=C_UP if self.lost == 0 else C_DOWN)
            self.lbl_loss.config(text=loss_pct,
                                 fg=C_DOWN if self.lost > 0 else C_UP)
            self.lbl_rtt.config(text=fmt_rtt(self.last_rtt), fg=rtt_fg)
            self.lbl_min.config(text=fmt_rtt(self.min_rtt))
            self.lbl_max.config(text=fmt_rtt(self.max_rtt))
            self.lbl_avg.config(text=fmt_rtt(avg))
        self.frame.after(0, _apply)

    # ── Probe loop ────────────────────────────────────────────────────────

    def _probe_loop(self):
        timeout = self.cfg.get("timeout_s", 2)
        interval = self.cfg.get("interval_ms", 1000) / 1000.0
        fail_threshold = max(1, int(self.cfg.get("fail_threshold", 5)))
        recover_threshold = max(1, int(self.cfg.get("recover_threshold", 2)))

        while self.running:
            t_start = time.time()
            self.sent += 1

            if self.mode == "tcp":
                result, rtt, resolved = tcp_ping(self.host, self.port, timeout)
            else:
                result, rtt, resolved = icmp_ping(self.host, timeout)

            if resolved:
                self.resolved_ip = resolved

            warn_ms = self.cfg.get("rtt_warn_ms", 150)
            crit_ms = self.cfg.get("rtt_crit_ms", 300)

            if result is True:
                self.recv     += 1
                self.last_rtt  = rtt
                self.sum_rtt  += rtt
                self.min_rtt   = min(rtt, self.min_rtt) if self.min_rtt else rtt
                self.max_rtt   = max(rtt, self.max_rtt) if self.max_rtt else rtt
                self.consecutive_success += 1
                self.consecutive_fail = 0
                self.last_fail_reason = None

                # If already officially down, require multiple good replies before recovery.
                if self.status in ("down", "dns"):
                    if self.consecutive_success >= recover_threshold:
                        self._set_status("up", rtt)
                    else:
                        self._set_status(self.status, rtt)
                else:
                    self._set_status("up", rtt)

                ip_info = f" ({self.resolved_ip})" if self.resolved_ip and self.resolved_ip != self.host else ""

                if self.status in ("down", "dns") and self.consecutive_success < recover_threshold:
                    tag = "warn"
                    msg = (f"recovery check {self.consecutive_success}/{recover_threshold} from "
                           f"{self.host_str}{ip_info}  rtt={fmt_rtt(rtt)}")
                elif rtt >= crit_ms:
                    tag = "crit"
                    msg = f"reply from {self.host_str}{ip_info}  rtt={fmt_rtt(rtt)}  ⚠ HIGH LATENCY"
                elif rtt >= warn_ms:
                    tag = "warn"
                    msg = f"reply from {self.host_str}{ip_info}  rtt={fmt_rtt(rtt)}  (elevated)"
                else:
                    tag = "ok"
                    msg = f"reply from {self.host_str}{ip_info}  rtt={fmt_rtt(rtt)}"

                self.logger.write("REPLY", rtt, f"rtt={fmt_rtt(rtt)}")

            else:
                self.lost += 1
                self.last_rtt = None
                self.consecutive_fail += 1
                self.consecutive_success = 0

                if result == "dns":
                    self.last_fail_reason = "dns"
                    tag = "dns"
                    base_msg = f"DNS resolution failed for {self.host_str}"
                    self.logger.write("DNS_FAIL", None, "DNS resolution failed")
                elif result == "rto":
                    self.last_fail_reason = "rto"
                    tag = "rto"
                    base_msg = f"request timeout  —  no reply from {self.host_str} within {timeout}s"
                    self.logger.write("TIMEOUT", None, f"No reply within {timeout}s")
                else:
                    self.last_fail_reason = "down"
                    tag = "loss"
                    base_msg = f"no response from {self.host_str}  (host unreachable)"
                    self.logger.write("UNREACHABLE", None, "Host unreachable")

                # Before the threshold, show an unstable warning only.
                if self.consecutive_fail < fail_threshold:
                    self._set_status("rto")
                    msg = f"{base_msg}  |  fail {self.consecutive_fail}/{fail_threshold} before official DOWN"
                else:
                    official_status = "dns" if self.last_fail_reason == "dns" else "down"
                    self._set_status(official_status)
                    msg = f"{base_msg}  |  OFFICIAL DOWN threshold reached ({self.consecutive_fail}/{fail_threshold})"

            self.frame.after(0, lambda t=tag, m=msg: self._log(t, m))
            self._update_stats()

            elapsed  = time.time() - t_start
            sleep_t  = max(0, interval - elapsed)
            time.sleep(sleep_t)

    # ── Controls ──────────────────────────────────────────────────────────

    def toggle(self):
        if self.running:
            self.running = False
            self._set_status("idle")
            self.btn_toggle.config(text="▶  Start")
        else:
            self.running = True
            self.btn_toggle.config(text="⏸  Stop")
            self.thread = threading.Thread(target=self._probe_loop, daemon=True)
            self.thread.start()

    def start(self):
        if not self.running:
            self.toggle()

    def stop(self):
        if self.running:
            self.toggle()

    def clear(self):
        self.sent = self.recv = self.lost = 0
        self.last_rtt = self.min_rtt = self.max_rtt = None
        self.sum_rtt  = 0.0
        self.down_since = None
        self.consecutive_fail = 0
        self.consecutive_success = 0
        self.last_fail_reason = None
        self.outage_history = []
        self.current_outage = None
        self.resolved_ip = None
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)
        self._update_stats()
        self._refresh_outage_summary()

    def _remove(self):
        self.running = False
        self.logger.close()
        self.frame.grid_forget()
        self.frame.destroy()
        self.remove_cb(self)

    def get_config_entry(self):
        return {"host": self.host_str, "mode": self.mode}

# ── Settings dialog ───────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg, on_save):
        super().__init__(parent)
        self.cfg     = dict(cfg)
        self.on_save = on_save
        self.title("Settings")
        self.configure(bg=C_BG)
        self.resizable(False, False)
        self.grab_set()
        self._build()

    def _row(self, parent, label, row):
        tk.Label(parent, text=label, fg=C_MUTED, bg=C_BG,
                 font=("monospace", 10), anchor="w", width=22
                 ).grid(row=row, column=0, sticky="w", padx=12, pady=4)

    def _entry(self, parent, var, row, width=10):
        e = tk.Entry(parent, textvariable=var, bg=C_INPUT_BG, fg=C_TEXT,
                     insertbackground=C_TEXT, font=("monospace", 10),
                     relief=tk.FLAT, bd=4, width=width)
        e.grid(row=row, column=1, sticky="w", padx=12, pady=4)
        return e

    def _build(self):
        f = tk.Frame(self, bg=C_BG)
        f.pack(padx=16, pady=12)

        tk.Label(f, text="Ping Settings", fg=C_TEXT, bg=C_BG,
                 font=("monospace", 12, "bold")).grid(
                 row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        self.v_interval = tk.StringVar(value=str(self.cfg["interval_ms"]))
        self.v_timeout  = tk.StringVar(value=str(self.cfg["timeout_s"]))
        self.v_cols     = tk.StringVar(value=str(self.cfg["cols"]))
        self.v_warn     = tk.StringVar(value=str(self.cfg["rtt_warn_ms"]))
        self.v_crit     = tk.StringVar(value=str(self.cfg["rtt_crit_ms"]))
        self.v_fail     = tk.StringVar(value=str(self.cfg.get("fail_threshold", 5)))
        self.v_recover  = tk.StringVar(value=str(self.cfg.get("recover_threshold", 2)))
        self.v_log      = tk.BooleanVar(value=self.cfg["log_enabled"])
        self.v_logdir   = tk.StringVar(value=self.cfg["log_dir"])

        rows = [
            ("Interval (ms)",          self.v_interval),
            ("Timeout (s)",            self.v_timeout),
            ("Default columns",        self.v_cols),
            ("RTT warn threshold (ms)",self.v_warn),
            ("RTT crit threshold (ms)",self.v_crit),
            ("Down after failures",   self.v_fail),
            ("Recover after replies", self.v_recover),
        ]
        for i, (lbl, var) in enumerate(rows, start=1):
            self._row(f, lbl, i)
            self._entry(f, var, i)

        # Log enabled
        self._row(f, "Enable CSV logging", 8)
        tk.Checkbutton(f, variable=self.v_log, bg=C_BG, fg=C_TEXT,
                       selectcolor=C_PANEL, activebackground=C_BG,
                       font=("monospace", 10)
                       ).grid(row=8, column=1, sticky="w", padx=12)

        # Log dir
        self._row(f, "Log directory", 9)
        log_frame = tk.Frame(f, bg=C_BG)
        log_frame.grid(row=9, column=1, sticky="w", padx=12, pady=4)
        tk.Entry(log_frame, textvariable=self.v_logdir, bg=C_INPUT_BG,
                 fg=C_TEXT, insertbackground=C_TEXT,
                 font=("monospace", 10), relief=tk.FLAT, bd=4, width=28
                 ).pack(side=tk.LEFT)
        tk.Button(log_frame, text="Browse", command=self._browse,
                  bg=C_BTN, fg=C_TEXT, relief=tk.FLAT,
                  font=("monospace", 9), padx=6, bd=0, cursor="hand2"
                  ).pack(side=tk.LEFT, padx=(4, 0))

        # Default hosts section
        tk.Label(f, text="Default Hosts (one per line, host or host:port  mode)",
                 fg=C_TEXT, bg=C_BG, font=("monospace", 10, "bold")
                 ).grid(row=10, column=0, columnspan=2, sticky="w",
                        pady=(12, 4), padx=12)

        self.hosts_text = tk.Text(f, height=6, bg=C_INPUT_BG, fg=C_TEXT,
                                  insertbackground=C_TEXT,
                                  font=("monospace", 10), bd=4,
                                  relief=tk.FLAT, width=40)
        self.hosts_text.grid(row=11, column=0, columnspan=2,
                             padx=12, pady=4, sticky="ew")

        hint = tk.Label(f, text="  e.g.  8.8.8.8  icmp    or    192.168.1.1:80  tcp",
                        fg=C_MUTED, bg=C_BG, font=("monospace", 8))
        hint.grid(row=12, column=0, columnspan=2, sticky="w", padx=12)

        for h in self.cfg.get("default_hosts", []):
            self.hosts_text.insert(tk.END, f"{h['host']}  {h['mode']}\n")

        # Buttons
        bf = tk.Frame(f, bg=C_BG)
        bf.grid(row=13, column=0, columnspan=2, pady=(12, 0))
        tk.Button(bf, text="Save", command=self._save,
                  bg=C_UP_BG, fg=C_UP, relief=tk.FLAT,
                  font=("monospace", 10, "bold"), padx=16, pady=4,
                  bd=0, cursor="hand2").pack(side=tk.LEFT, padx=4)
        tk.Button(bf, text="Cancel", command=self.destroy,
                  bg=C_BTN, fg=C_TEXT, relief=tk.FLAT,
                  font=("monospace", 10), padx=16, pady=4,
                  bd=0, cursor="hand2").pack(side=tk.LEFT, padx=4)

        open_log_btn = tk.Button(bf, text="Open log folder",
                                 command=self._open_log_folder,
                                 bg=C_BTN, fg=C_MUTED, relief=tk.FLAT,
                                 font=("monospace", 10), padx=16, pady=4,
                                 bd=0, cursor="hand2")
        open_log_btn.pack(side=tk.LEFT, padx=4)

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.v_logdir.get())
        if d:
            self.v_logdir.set(d)

    def _open_log_folder(self):
        log_dir = Path(self.v_logdir.get())
        log_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(log_dir)])

    def _save(self):
        try:
            self.cfg["interval_ms"]  = int(self.v_interval.get())
            self.cfg["timeout_s"]    = int(self.v_timeout.get())
            self.cfg["cols"]         = int(self.v_cols.get())
            self.cfg["rtt_warn_ms"]  = int(self.v_warn.get())
            self.cfg["rtt_crit_ms"]  = int(self.v_crit.get())
            self.cfg["fail_threshold"] = max(1, int(self.v_fail.get()))
            self.cfg["recover_threshold"] = max(1, int(self.v_recover.get()))
            self.cfg["log_enabled"]  = self.v_log.get()
            self.cfg["log_dir"]      = self.v_logdir.get()

            hosts = []
            for line in self.hosts_text.get("1.0", tk.END).strip().splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                h    = parts[0]
                mode = parts[1].lower() if len(parts) > 1 else "icmp"
                if mode not in ("icmp", "tcp"):
                    mode = "icmp"
                hosts.append({"host": h, "mode": mode})
            self.cfg["default_hosts"] = hosts

            save_config(self.cfg)
            self.on_save(self.cfg)
            self.destroy()
        except ValueError as e:
            messagebox.showerror("Invalid value", str(e), parent=self)

# ── App ───────────────────────────────────────────────────────────────────────

class VmPingApp:
    def __init__(self, root):
        self.root   = root
        self.panels = []
        self.cfg    = load_config()

        # Each run writes to a temporary folder first.
        # On exit, the user chooses whether to save it as a named test folder.
        self.base_log_dir = Path(self.cfg.get("log_dir", str(LOG_DIR)))
        self.session_started = datetime.now()
        self.session_temp_dir = Path(tempfile.mkdtemp(prefix="linuxping_test_"))
        self.outage_report_path = self.session_temp_dir / "down_events.csv"
        self.cfg["log_dir"] = str(self.session_temp_dir / "per_target")
        self._init_outage_report()
        self.global_outage_visible = False

        root.title("LinuxPing — Visual Multi Ping v12")
        root.configure(bg=C_BG)
        root.geometry("1150x780")
        root.minsize(600, 400)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_toolbar()
        self._build_global_stats()
        self._build_global_outage_summary()
        self._build_scroll_area()

        # Startup is intentionally blank. Load a profile when needed.
        self.update_global_stats()

    def _safe_name(self, name):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
        return safe.strip("._-") or "test"

    def _init_outage_report(self):
        self.session_temp_dir.mkdir(parents=True, exist_ok=True)
        with open(self.outage_report_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "target", "mode", "down_time", "up_time", "duration_seconds",
                "duration", "failures", "reason", "status"
            ])

    def record_outage(self, outage):
        with open(self.outage_report_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                outage.get("target", ""), outage.get("mode", ""),
                outage.get("down_time", ""), outage.get("up_time", ""),
                outage.get("duration_seconds", ""), outage.get("duration", ""),
                outage.get("failures", ""), outage.get("reason", ""),
                outage.get("status", ""),
            ])
        self.refresh_global_outage_summary()

    def _write_session_summary(self):
        summary_path = self.session_temp_dir / "session_summary.csv"
        with open(summary_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "target", "mode", "sent", "received", "lost", "loss_percent",
                "min_rtt_ms", "max_rtt_ms", "avg_rtt_ms", "official_outages"
            ])
            for p in self.panels:
                loss_pct = round(p.lost / p.sent * 100, 2) if p.sent else 0
                avg = round(p.sum_rtt / p.recv, 2) if p.recv else ""
                writer.writerow([
                    p.host_str, p.mode, p.sent, p.recv, p.lost, loss_pct,
                    p.min_rtt or "", p.max_rtt or "", avg,
                    len(p.outage_history) + (1 if p.current_outage else 0),
                ])

    def _finalize_reports(self):
        # Close any target that is still officially down, so the test report is complete.
        for p in self.panels:
            outage = p.finalize_open_outage()
            if outage:
                self.record_outage(outage)
        self._write_session_summary()

    def _save_test_logs(self):
        self._finalize_reports()
        test_name = simpledialog.askstring(
            "Save test logs",
            "Enter test directory name, for example Client_A_WAN_Test:",
            parent=self.root,
        )
        if not test_name:
            return False
        safe = self._safe_name(test_name)
        stamp = self.session_started.strftime("%Y%m%d_%H%M%S")
        dest = self.base_log_dir / f"{safe}_{stamp}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        final_dest = dest
        while final_dest.exists():
            final_dest = self.base_log_dir / f"{safe}_{stamp}_{counter}"
            counter += 1
        shutil.move(str(self.session_temp_dir), str(final_dest))
        messagebox.showinfo("Test logs saved", f"Saved test logs to:\n{final_dest}", parent=self.root)
        return True

    def _discard_test_logs(self):
        try:
            shutil.rmtree(self.session_temp_dir, ignore_errors=True)
        except Exception:
            pass

    def _on_close(self):
        self._stop_all()
        for p in self.panels:
            try:
                p.logger.close()
            except Exception:
                pass

        if self.cfg.get("log_enabled", True):
            answer = messagebox.askyesnocancel(
                "Save test logs?",
                "Do you want to save this test log folder before closing?\n\n"
                "Yes = save as a named test directory\nNo = close and discard this run\nCancel = return to LinuxPing",
                parent=self.root,
            )
            if answer is None:
                return
            if answer is True:
                saved = self._save_test_logs()
                if not saved:
                    return
            else:
                self._discard_test_logs()
        else:
            self._discard_test_logs()

        # Save only settings/profiles. Do not autosave the current host panels.
        real_cfg = dict(self.cfg)
        real_cfg["log_dir"] = str(self.base_log_dir)
        real_cfg["cols"] = self._normalize_cols()
        real_cfg["default_hosts"] = []
        save_config(real_cfg)
        self.root.destroy()

    def _build_toolbar(self):
        # Row 1: host controls
        tb = tk.Frame(self.root, bg=C_BG, pady=8)
        tb.pack(fill=tk.X, padx=12)

        tk.Label(tb, text="Host / host:port", fg=C_MUTED,
                 bg=C_BG, font=("monospace", 10)).pack(side=tk.LEFT, padx=(0, 4))

        self.host_var = tk.StringVar()
        entry = tk.Entry(tb, textvariable=self.host_var,
                         bg=C_INPUT_BG, fg=C_TEXT,
                         insertbackground=C_TEXT,
                         font=("monospace", 11),
                         relief=tk.FLAT, bd=4, width=26)
        entry.pack(side=tk.LEFT, padx=(0, 6))
        entry.bind("<Return>", lambda e: self._on_add())

        self.mode_var = tk.StringVar(value="icmp")
        ttk.Combobox(tb, textvariable=self.mode_var,
                     values=["icmp", "tcp"], width=5,
                     state="readonly", font=("monospace", 10)
                     ).pack(side=tk.LEFT, padx=(0, 8))

        for text, cmd in [("Add",        self._on_add),
                           ("Start all",  self._start_all),
                           ("Stop all",   self._stop_all),
                           ("Clear stats", self._clear_all),
                           ("New blank",  self._new_blank)]:
            tk.Button(tb, text=text, command=cmd,
                      bg=C_BTN, fg=C_TEXT,
                      activebackground=C_BTN_HOV, activeforeground=C_TEXT,
                      relief=tk.FLAT, font=("monospace", 10),
                      padx=10, pady=3, bd=0, cursor="hand2"
                      ).pack(side=tk.LEFT, padx=2)

        # Cols
        tk.Label(tb, text="  cols:", fg=C_MUTED,
                 bg=C_BG, font=("monospace", 10)).pack(side=tk.LEFT, padx=(10, 2))
        self.cols_var = tk.StringVar(value=str(self.cfg.get("cols", 2)))
        ttk.Combobox(tb, textvariable=self.cols_var,
                     values=["1", "2", "3", "4"], width=2,
                     state="readonly", font=("monospace", 10)
                     ).pack(side=tk.LEFT)
        self.cols_var.trace_add("write", lambda *a: self._retile())

        # Settings button
        tk.Button(tb, text="⚙  Settings", command=self._open_settings,
                  bg=C_BTN, fg=C_MUTED,
                  activebackground=C_BTN_HOV, activeforeground=C_TEXT,
                  relief=tk.FLAT, font=("monospace", 10),
                  padx=10, pady=3, bd=0, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=2)

        # Row 2: profile controls
        pb = tk.Frame(self.root, bg=C_BG, pady=2)
        pb.pack(fill=tk.X, padx=12)

        tk.Label(pb, text="Profile", fg=C_MUTED,
                 bg=C_BG, font=("monospace", 10)).pack(side=tk.LEFT, padx=(0, 4))

        self.profile_var = tk.StringVar(value=self.cfg.get("last_profile", ""))
        self.profile_combo = ttk.Combobox(pb, textvariable=self.profile_var,
                                          values=self._profile_names(), width=24,
                                          font=("monospace", 10))
        self.profile_combo.pack(side=tk.LEFT, padx=(0, 6))
        self.profile_combo.bind("<Return>", lambda e: self._save_profile())

        for text, cmd in [("Load profile", self._load_profile),
                          ("Save profile", self._save_profile),
                          ("Delete profile", self._delete_profile)]:
            tk.Button(pb, text=text, command=cmd,
                      bg=C_BTN, fg=C_TEXT,
                      activebackground=C_BTN_HOV, activeforeground=C_TEXT,
                      relief=tk.FLAT, font=("monospace", 10),
                      padx=10, pady=3, bd=0, cursor="hand2"
                      ).pack(side=tk.LEFT, padx=2)

        tk.Label(pb, text="  Save/load groups of IPs or domains. Startup stays blank.",
                 fg=C_MUTED, bg=C_BG, font=("monospace", 9)).pack(side=tk.LEFT, padx=(8, 0))

    def _build_global_stats(self):
        sf = tk.Frame(self.root, bg=C_BG)
        sf.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.lbl_g_up   = tk.Label(sf, text="↑ 0 up",   fg=C_UP,   bg=C_BG, font=("monospace", 10))
        self.lbl_g_down = tk.Label(sf, text="↓ 0 down", fg=C_DOWN, bg=C_BG, font=("monospace", 10))
        self.lbl_g_rto  = tk.Label(sf, text="⚠ 0 rto",  fg=C_RTO,  bg=C_BG, font=("monospace", 10))

        for l in [self.lbl_g_up, self.lbl_g_down, self.lbl_g_rto]:
            l.pack(side=tk.LEFT, padx=(0, 14))

        # Outage summary control is placed in the main status row so it is always visible.
        self.btn_global_outages = tk.Button(
            sf, text="▼ Outage Summary", command=self._toggle_global_outages,
            bg=C_BTN, fg=C_TEXT, activebackground=C_BTN_HOV, activeforeground=C_TEXT,
            relief=tk.FLAT, font=("monospace", 10), padx=10, pady=3, bd=0, cursor="hand2"
        )
        self.btn_global_outages.pack(side=tk.LEFT, padx=(4, 8))

        self.lbl_global_outage_count = tk.Label(
            sf, text="Official outage events: 0", fg=C_MUTED, bg=C_BG, font=("monospace", 10)
        )
        self.lbl_global_outage_count.pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_g_time = tk.Label(sf, text="", fg=C_MUTED, bg=C_BG, font=("monospace", 10))
        self.lbl_g_time.pack(side=tk.RIGHT, padx=10)

        tk.Frame(self.root, bg=C_BORDER, height=1).pack(fill=tk.X, padx=12, pady=(2, 4))
        self._tick_clock()

    def _build_global_outage_summary(self):
        # Collapsible global outage table. It stays below the top status row and above ping blocks.
        self.global_outage_frame = tk.Frame(self.root, bg=C_BG, highlightthickness=1, highlightbackground=C_BORDER)

        title_row = tk.Frame(self.global_outage_frame, bg=C_BG)
        title_row.pack(fill=tk.X, padx=6, pady=(4, 0))
        tk.Label(title_row, text="Official Outage Summary", fg=C_TEXT, bg=C_BG,
                 font=("monospace", 10, "bold")).pack(side=tk.LEFT)
        tk.Label(title_row, text="Shows only official DOWN events after threshold is reached.",
                 fg=C_MUTED, bg=C_BG, font=("monospace", 8)).pack(side=tk.LEFT, padx=(10, 0))

        text_wrap = tk.Frame(self.global_outage_frame, bg=C_BG)
        text_wrap.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.global_outage_text = tk.Text(
            text_wrap, height=7, bg=C_BG, fg=C_TEXT, font=("monospace", 9),
            bd=0, state=tk.DISABLED, wrap=tk.NONE, insertbackground=C_TEXT
        )
        sb_y = tk.Scrollbar(text_wrap, orient="vertical", command=self.global_outage_text.yview, bg=C_BG, troughcolor=C_BG)
        sb_x = tk.Scrollbar(self.global_outage_frame, orient="horizontal", command=self.global_outage_text.xview, bg=C_BG, troughcolor=C_BG)
        self.global_outage_text.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
        self.global_outage_text.tag_config("header", foreground=C_MUTED)
        self.global_outage_text.tag_config("ongoing", foreground=C_DOWN)
        self.global_outage_text.tag_config("recovered", foreground=C_UP)
        self.global_outage_text.tag_config("muted", foreground=C_MUTED)
        sb_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.global_outage_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb_x.pack(fill=tk.X, padx=6, pady=(0, 4))
        self.refresh_global_outage_summary()

    def _toggle_global_outages(self):
        self.global_outage_visible = not self.global_outage_visible
        if self.global_outage_visible:
            self.global_outage_frame.pack(fill=tk.X, padx=12, pady=(0, 6))
            self.btn_global_outages.config(text="▲ Outage Summary")
        else:
            self.global_outage_frame.pack_forget()
            self.btn_global_outages.config(text="▼ Outage Summary")
        self.refresh_global_outage_summary()

    def _collect_outage_rows(self):
        rows = []
        for p in self.panels:
            rows.extend(p.outage_history)
            if p.current_outage:
                rows.append({
                    "target": p.host_str,
                    "mode": p.mode,
                    "down_time": p.current_outage.get("down_time", "-"),
                    "up_time": "ONGOING",
                    "duration": fmt_duration(time.time() - p.current_outage.get("down_epoch", time.time())),
                    "duration_seconds": int(time.time() - p.current_outage.get("down_epoch", time.time())),
                    "failures": p.current_outage.get("failures", p.consecutive_fail),
                    "reason": p.current_outage.get("reason", ""),
                    "status": "ongoing",
                })
        def sort_key(r):
            return r.get("down_time", "")
        return sorted(rows, key=sort_key, reverse=True)

    def refresh_global_outage_summary(self):
        if not hasattr(self, "global_outage_text"):
            return
        rows = self._collect_outage_rows()
        count = len(rows)
        ongoing = sum(1 for r in rows if r.get("up_time") == "ONGOING" or r.get("status") == "ongoing")
        label = f"Official outage events: {count}"
        if ongoing:
            label += f"  |  Ongoing: {ongoing}"
        self.lbl_global_outage_count.config(text=label)

        self.global_outage_text.configure(state=tk.NORMAL)
        self.global_outage_text.delete("1.0", tk.END)
        if not rows:
            self.global_outage_text.insert(tk.END, "No official outage events yet.\n", "muted")
        else:
            self.global_outage_text.insert(
                tk.END,
                f"{'HOST':<28} {'MODE':<5} {'DOWN TIME':<21} {'UP TIME':<23} {'DURATION':<10} {'FAILS':<5} {'STATUS'}\n",
                "header",
            )
            self.global_outage_text.insert(tk.END, "-" * 105 + "\n", "header")
            for r in rows[:200]:
                tag = "ongoing" if r.get("up_time") == "ONGOING" or r.get("status") == "ongoing" else "recovered"
                self.global_outage_text.insert(
                    tk.END,
                    f"{r.get('target','-'):<28} {r.get('mode','-'):<5} "
                    f"{r.get('down_time','-'):<21} {r.get('up_time','-'):<23} "
                    f"{r.get('duration','-'):<10} {str(r.get('failures','')):<5} {r.get('status','')}\n",
                    tag,
                )
        self.global_outage_text.configure(state=tk.DISABLED)

    def _tick_clock(self):
        self.lbl_g_time.config(text=now_full())
        try:
            self.refresh_global_outage_summary()
        except Exception:
            pass
        self.root.after(1000, self._tick_clock)

    def _build_scroll_area(self):
        container = tk.Frame(self.root, bg=C_BG)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        self.canvas = tk.Canvas(container, bg=C_BG, bd=0, highlightthickness=0)
        sb = tk.Scrollbar(container, orient="vertical",
                          command=self.canvas.yview, bg=C_BG, troughcolor=C_BG)
        self.canvas.configure(yscrollcommand=sb.set)

        self.panel_frame = tk.Frame(self.canvas, bg=C_BG)
        self.panel_frame.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        self.canvas_win = self.canvas.create_window(
            (0, 0), window=self.panel_frame, anchor="nw")
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self.canvas_win, width=e.width))

        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(seq, self._on_scroll)

    def _on_scroll(self, e):
        if e.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif e.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(-1 * (e.delta // 120), "units")

    def _normalize_cols(self, value=None):
        """Return a safe column count and keep the column selector valid."""
        if value is None:
            value = self.cols_var.get()
        try:
            cols = int(value)
        except (TypeError, ValueError):
            cols = int(DEFAULT_CONFIG.get("cols", 2))

        # The toolbar only supports 1-4 columns, so clamp anything loaded
        # from Settings/config to that same range.
        cols = max(1, min(4, cols))

        if hasattr(self, "cols_var") and self.cols_var.get() != str(cols):
            self.cols_var.set(str(cols))
        return cols

    def _retile(self):
        cols = self._normalize_cols()

        # Persist toolbar column changes immediately in runtime config.
        # Without this, changing columns from the top bar can be lost on close.
        self.cfg["cols"] = cols

        # Capture the existing grid size before clearing. Tk keeps old column
        # weights/uniform groups even after grid_forget(), which is why moving
        # from 4 columns back to 2/1 can visually keep the old 4-column layout.
        old_cols, old_rows = self.panel_frame.grid_size()

        for widget in self.panel_frame.winfo_children():
            widget.grid_forget()

        # Fully reset previous columns and rows before applying the new layout.
        reset_cols = max(old_cols, 4, len(self.panels), cols)
        reset_rows = max(old_rows, (len(self.panels) // max(cols, 1)) + 2)
        for col in range(reset_cols):
            self.panel_frame.columnconfigure(col, weight=0, uniform="", minsize=0)
        for row in range(reset_rows):
            self.panel_frame.rowconfigure(row, weight=0, minsize=0)

        for col in range(cols):
            self.panel_frame.columnconfigure(col, weight=1, uniform="panel_col")

        for idx, p in enumerate(self.panels):
            p.frame.grid(row=idx // cols, column=idx % cols,
                         sticky="nsew", padx=3, pady=3)

        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass

    def _add_panel(self, host, mode):
        p = PingPanel(self.panel_frame, host, mode, self,
                      self._remove_panel, self.cfg)
        self.panels.append(p)
        self._retile()
        self.update_global_stats()

    def _on_add(self):
        host = self.host_var.get().strip()
        if not host:
            return
        self._add_panel(host, self.mode_var.get())
        self.host_var.set("")

    def _remove_panel(self, panel):
        if panel in self.panels:
            self.panels.remove(panel)
        self._retile()
        self.update_global_stats()
        self.refresh_global_outage_summary()

    def _start_all(self):
        for p in self.panels: p.start()

    def _stop_all(self):
        for p in self.panels: p.stop()

    def _clear_all(self):
        for p in self.panels: p.clear()
        self.refresh_global_outage_summary()

    def _profile_names(self):
        return sorted(self.cfg.get("profiles", {}).keys())

    def _refresh_profile_combo(self):
        if hasattr(self, "profile_combo"):
            self.profile_combo.configure(values=self._profile_names())

    def _current_host_entries(self):
        return [p.get_config_entry() for p in self.panels]

    def _remove_all_panels(self):
        for p in list(self.panels):
            p.running = False
            try:
                p.logger.close()
            except Exception:
                pass
            try:
                p.frame.grid_forget()
                p.frame.destroy()
            except Exception:
                pass
        self.panels.clear()
        self._retile()
        self.update_global_stats()

    def _new_blank(self):
        if self.panels:
            if not messagebox.askyesno("New blank session",
                                       "Remove all current panels and start blank?",
                                       parent=self.root):
                return
        self._stop_all()
        self._remove_all_panels()
        self.profile_var.set("")
        self.cfg["last_profile"] = ""
        save_config(self.cfg)

    def _load_profile(self):
        name = self.profile_var.get().strip()
        profiles = self.cfg.get("profiles", {})
        if not name:
            messagebox.showwarning("No profile selected",
                                   "Select or type a profile name first.",
                                   parent=self.root)
            return
        if name not in profiles:
            messagebox.showwarning("Profile not found",
                                   f"Profile '{name}' does not exist yet.",
                                   parent=self.root)
            return

        self._stop_all()
        self._remove_all_panels()
        for h in profiles.get(name, []):
            host = h.get("host", "").strip()
            if host:
                self._add_panel(host, h.get("mode", "icmp"))
        self.cfg["last_profile"] = name
        save_config(self.cfg)
        self._refresh_profile_combo()

    def _save_profile(self):
        name = self.profile_var.get().strip()
        if not name:
            messagebox.showwarning("Profile name required",
                                   "Type a profile name before saving.",
                                   parent=self.root)
            return
        if not self.panels:
            if not messagebox.askyesno("Save empty profile",
                                       "There are no hosts in this session. Save an empty profile?",
                                       parent=self.root):
                return

        self.cfg.setdefault("profiles", {})[name] = self._current_host_entries()
        self.cfg["last_profile"] = name
        self.cfg["cols"] = int(self.cols_var.get())
        # Keep startup blank even after saving a profile.
        self.cfg["default_hosts"] = []
        save_config(self.cfg)
        self._refresh_profile_combo()
        messagebox.showinfo("Profile saved",
                            f"Saved {len(self.panels)} host(s) to profile: {name}",
                            parent=self.root)

    def _delete_profile(self):
        name = self.profile_var.get().strip()
        profiles = self.cfg.get("profiles", {})
        if not name or name not in profiles:
            messagebox.showwarning("Profile not found",
                                   "Select an existing profile to delete.",
                                   parent=self.root)
            return
        if not messagebox.askyesno("Delete profile",
                                   f"Delete profile '{name}'?\nThe current panels will not be removed.",
                                   parent=self.root):
            return
        del profiles[name]
        if self.cfg.get("last_profile") == name:
            self.cfg["last_profile"] = ""
        save_config(self.cfg)
        self.profile_var.set("")
        self._refresh_profile_combo()

    def _open_settings(self):
        settings_cfg = dict(self.cfg)
        settings_cfg["log_dir"] = str(self.base_log_dir)
        SettingsDialog(self.root, settings_cfg, self._on_settings_saved)

    def _on_settings_saved(self, new_cfg):
        self.base_log_dir = Path(new_cfg.get("log_dir", str(LOG_DIR)))
        self.cfg = new_cfg
        self.cfg["log_dir"] = str(self.session_temp_dir / "per_target")

        cols = self._normalize_cols(new_cfg.get("cols", 2))
        self.cfg["cols"] = cols
        self.cols_var.set(str(cols))
        self._retile()

        # Update cfg reference in all panels while keeping logs in the temporary test folder.
        for p in self.panels:
            p.cfg = self.cfg

    def update_global_stats(self):
        up   = sum(1 for p in self.panels if p.status == "up")
        down = sum(1 for p in self.panels if p.status in ("down", "dns"))
        rto  = sum(1 for p in self.panels if p.status == "rto")
        self.root.after(0, lambda: [
            self.lbl_g_up.config(text=f"↑ {up} up"),
            self.lbl_g_down.config(text=f"↓ {down} down"),
            self.lbl_g_rto.config(text=f"⚠ {rto} rto"),
        ])


if __name__ == "__main__":
    root = tk.Tk()
    app = VmPingApp(root)
    root.mainloop()

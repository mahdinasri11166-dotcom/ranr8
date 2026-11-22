# -*- coding: utf-8 -*-
"""
GGUF Runner — Unified desktop hub
Sidebar drawer: Chat | Models | Agent | OSINT/Case | Settings | Doctor
Team: Mehdi Tariri · Mohsen Tarifii
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
    _HAS_CTK = True
except ImportError:
    ctk = None  # type: ignore
    _HAS_CTK = False

from config_loader import (
    ROOT,
    list_models,
    load_ini,
    save_ini,
    load_profile,
    workspace_dir,
    sessions_dir,
    identity_system_suffix,
    load_approval_policy,
)
from agent_tools import execute_tool, needs_approval, tools_schema_for_prompt
try:
    from intent_router import route_intent, persian_tool_examples
except Exception:
    def route_intent(text): return None
    def persian_tool_examples(): return ""

from app_logging import get_logger
log = get_logger("hub")

def _read_version() -> str:
    try:
        import json
        from config_loader import ROOT as _R
        return str(json.loads((_R / "version.json").read_text(encoding="utf-8")).get("version") or "3.8.0")
    except Exception:
        return "3.8.0"

APP_VERSION = _read_version()



def _safe_int(val, default=0, lo=None, hi=None):
    try:
        n = int(float(str(val).strip()))
    except Exception:
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _safe_float(val, default=0.0, lo=None, hi=None):
    try:
        n = float(str(val).strip())
    except Exception:
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


from engine import LocalEngine
from model_router import DualModelRouter, load_router_config, save_router_config, resolve_model_path, extract_tool_json

class ApprovalDialog(ctk.CTkToplevel if _HAS_CTK else object):
    def __init__(self, master, tool_name: str, arguments: dict, require_yes: bool = True):
        super().__init__(master)
        self.result = False
        self.title("تأیید ابزار")
        self.geometry("480x320")
        self.transient(master)
        self.grab_set()
        ctk.CTkLabel(self, text=f"اجرای ابزار: {tool_name}", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
        box = ctk.CTkTextbox(self, height=160)
        box.pack(fill="both", expand=True, padx=12, pady=6)
        box.insert("1.0", json.dumps(arguments, ensure_ascii=False, indent=2))
        box.configure(state="disabled")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=10)
        ctk.CTkButton(row, text="رد", fg_color="#555", command=self._no).pack(side="left", padx=8)
        ctk.CTkButton(row, text="تأیید", fg_color=UI["accent"], command=self._ok).pack(side="left", padx=8)

    def _ok(self):
        self.result = True
        self.destroy()

    def _no(self):
        self.result = False
        self.destroy()


# Unified visual tokens
UI = {
    # Grok-like graphical shell
    "bg": "#050505",
    "panel": "#0e0e0e",
    "panel2": "#141414",
    "card": "#181818",
    "border": "#2e2e2e",
    "accent": "#c8f542",
    "accent2": "#60a5fa",
    "ok": "#4ade80",
    "warn": "#fbbf24",
    "danger": "#f87171",
    "muted": "#8b8b8b",
    "text": "#fafafa",
    "nav_hover": "#1c1c1c",
    "nav_active": "#222222",
    "user_bubble": "#1e293b",
    "asst_bubble": "#111111",
    "system": "#78716c",
}


class AppHub(ctk.CTk if _HAS_CTK else object):
    NAV = [
        ("home", "⌂  خانه"),
        ("chat", "◎  چت"),
        ("term", "›_  ترمینال"),
        ("models", "◈  مدل‌ها"),
        ("sessions", "☰  تاریخچه"),
        ("agent", "✦  ایجنت"),
        ("osint", "◉  OSINT"),
        ("git", "⑂  Git"),
        ("rag", "▣  RAG"),
        ("plugins", "⧉  Plugins"),
        ("settings", "⚙  تنظیمات"),
        ("doctor", "✚  Doctor"),
        ("api", "⇄  API"),
        ("about", "ℹ  درباره"),
    ]

    def __init__(self):
        super().__init__()
        self.title(f"GGUF Runner  ·  v{APP_VERSION}  ·  تیم توسعهٔ نو")
        self.geometry("1280x820")
        self.minsize(1000, 640)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        try:
            self.configure(fg_color=UI["bg"])
        except Exception:
            pass
        self._rtl = True

        self.cfg = load_ini()
        self.engine = LocalEngine()
        self.router = DualModelRouter()
        self.messages: list[dict] = []
        self.attachments: list[Path] = []
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_title = f"Chat {self.session_id}"
        self.agent_mode = True
        self.osint_mode = False
        self.busy = False
        self._asst_started = False
        self._token_buf = []
        self._token_flush_id = None
        self.system_prompt = self.cfg.get("system_prompt") or "You are a helpful assistant."
        self.current_page = "chat"
        self.drawer_open = True
        self._model_loading = False
        self._last_send_ts = 0.0
        self.term_cwd = workspace_dir()
        self._restored_session = False
        self._restored_session = False
        self._token_buf = []
        self._token_flush_id = None
        self._filter_after = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        # Drawer
        self.drawer = ctk.CTkFrame(self, width=236, corner_radius=0, fg_color=UI["panel"])
        self.drawer.grid(row=0, column=0, sticky="nsw")
        self.drawer.grid_propagate(False)

        self.brand_bar = ctk.CTkFrame(self.drawer, height=4, fg_color=UI["accent"], corner_radius=0)
        self.brand_bar.pack(fill="x", side="top")

        ctk.CTkLabel(
            self.drawer,
            text="GGUF",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=UI["accent"],
        ).pack(pady=(18, 4), padx=12)
        ctk.CTkLabel(self.drawer, text=f"v{APP_VERSION}", text_color="gray").pack(pady=(0, 12))

        self.nav_scroll = ctk.CTkScrollableFrame(self.drawer, fg_color="transparent", height=520)
        self.nav_scroll.pack(fill="both", expand=True, padx=6, pady=4)
        self.nav_btns = {}
        for key, label in self.NAV:
            b = ctk.CTkButton(
                self.nav_scroll,
                text=label,
                anchor="e",
                height=38,
                corner_radius=8,
                fg_color="transparent",
                text_color=UI["text"],
                hover_color=UI["nav_hover"],
                command=lambda k=key: self.show_page(k),
            )
            b.pack(fill="x", padx=10, pady=3)
            self.nav_btns[key] = b

        ctk.CTkButton(
            self.drawer,
            text="☰  بستن/باز کردن کشو",
            height=32,
            fg_color="#333",
            command=self.toggle_drawer,
        ).pack(side="bottom", fill="x", padx=10, pady=12)

        # Main area
        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color=UI["bg"])
        self.main.grid(row=0, column=1, sticky="nsew", padx=8, pady=8)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        # Global top bar (always visible above pages)
        self.topbar = ctk.CTkFrame(self.main, height=52, fg_color=UI["card"], corner_radius=16, border_width=1, border_color=UI["border"])
        self.topbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.topbar.grid_propagate(False)
        self.top_dot = ctk.CTkLabel(self.topbar, text="●", text_color=UI["accent"], font=ctk.CTkFont(size=12))
        self.top_dot.pack(side="left", padx=(14, 4), pady=10)
        self.top_model = ctk.CTkLabel(self.topbar, text="مدل: —", font=ctk.CTkFont(size=13, weight="bold"))
        self.top_model.pack(side="left", padx=4, pady=10)
        self.top_badges = ctk.CTkLabel(self.topbar, text="", text_color=UI["muted"])
        self.top_badges.pack(side="left", padx=8)
        self.top_session = ctk.CTkLabel(self.topbar, text="", text_color=UI["muted"])
        self.top_session.pack(side="left", padx=8)
        ctk.CTkButton(self.topbar, text="مدل‌ها", width=70, height=28, fg_color=UI["panel"],
                      command=lambda: self.show_page("models")).pack(side="right", padx=6)
        ctk.CTkButton(self.topbar, text="چت", width=60, height=28, fg_color=UI["accent"],
                      command=lambda: self.show_page("chat")).pack(side="right", padx=4)


        self.pages: dict[str, ctk.CTkFrame] = {}
        self._build_home_page()
        self._build_chat_page()
        self._build_term_page()
        self._build_models_page()
        self._build_sessions_page()
        self._build_agent_page()
        self._build_osint_page()
        self._build_git_page()
        self._build_rag_page()
        self._build_plugins_page()
        self._build_settings_page()
        self._build_doctor_page()
        self._build_api_page()
        self._build_about_page()

        # status bar
        self.status = ctk.CTkLabel(self, text="  ●  آماده", anchor="w", height=30, text_color=UI["muted"], fg_color=UI["panel"], font=ctk.CTkFont(size=12))
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 4))

        self.show_page("chat")
        self.after(200, self._refresh_models_ui)
        self.after(400, self._try_autoload_model)
        self.after(900, self._restore_last_session_soft)
        self.after(500, self._sync_router_switch)
        self.after(800, self._maybe_first_run)
        self.bind_all("<Control-Return>", lambda e: self._on_send())
        self.bind_all("<Control-n>", lambda e: self._handle_slash("/new"))
        self.bind_all("<Control-s>", lambda e: self._handle_slash("/save"))
        self.bind_all("<Control-m>", lambda e: self.show_page("models"))
        self.bind_all("<Control-b>", lambda e: self.toggle_drawer())
        self._log_chat(
            "system",
            f"GGUF Runner v{APP_VERSION}\n"
            "از کشو سمت چپ: مدل‌ها → افزودن مدل → لود، سپس چت کنید.\n"
            "Agent / OSINT را از پایین صفحه چت روشن کنید.",
        )

    # ---------- drawer ----------
    def toggle_drawer(self):
        if self.drawer_open:
            self.drawer.grid_remove()
            self.drawer_open = False
        else:
            self.drawer.grid(row=0, column=0, sticky="nsw")
            self.drawer_open = True


    def _refresh_topbar(self):
        try:
            name = self.engine.model_path.name if self.engine.model_path else "—"
            self.top_model.configure(text=f"مدل: {name}")
            badges = []
            if self.agent_mode:
                badges.append("Agent ON")
            if self.osint_mode:
                badges.append("OSINT ON")
            if self.busy:
                badges.append("Busy…")
            self.top_badges.configure(text="  ·  ".join(badges) if badges else "آماده")
            try:
                st = getattr(self, "session_title", "") or ""
                self.top_session.configure(text=(f"·  {st[:36]}" if st else ""))
            except Exception:
                pass
        except Exception:
            pass

    def show_page(self, key: str, force: bool = False):
        if key not in self.pages:
            key = "home"
        # skip heavy work if same page and not forced
        same = (getattr(self, "current_page", None) == key) and not force
        self.current_page = key
        try:
            self._refresh_topbar()
        except Exception:
            pass
        for k, fr in self.pages.items():
            try:
                if k == key:
                    fr.grid(row=1, column=0, sticky="nsew")
                else:
                    fr.grid_remove()
            except Exception:
                pass
        for k, b in self.nav_btns.items():
            try:
                if k == key:
                    b.configure(fg_color=UI["nav_active"], text_color=UI["accent"], hover_color=UI["nav_hover"])
                else:
                    b.configure(fg_color="transparent", text_color=UI["text"], hover_color=UI["nav_hover"])
            except Exception:
                pass
        try:
            self.status.configure(text=f"{key}  ·  v{APP_VERSION}")
        except Exception:
            pass
        if same and key not in ("sessions", "models", "doctor", "git", "plugins"):
            return
        if key == "models":
            self._refresh_models_ui()
        elif key == "sessions":
            self._refresh_sessions_ui()
        elif key == "home":
            self._refresh_home()
        elif key == "git":
            self._refresh_git()
        elif key == "plugins":
            self._refresh_plugins()
        elif key == "doctor":
            self._run_doctor_ui()


    def _build_home_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["home"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_columnconfigure(1, weight=1)

        header = ctk.CTkFrame(fr, fg_color=UI["card"], corner_radius=14)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(
            header,
            text="GGUF Runner",
            font=ctk.CTkFont(size=26, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(14, 0))
        ctk.CTkLabel(
            header,
            text="چت · مدل · ایجنت · OSINT در یک پنجره  ·  تیم توسعهٔ نو",
            text_color=UI["muted"],
        ).pack(anchor="w", padx=18, pady=(4, 14))

        # quick action cards
        actions = ctk.CTkFrame(fr, fg_color="transparent")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        for text, color, cmd in [
            ("➕  افزودن مدل", UI["accent"], lambda: (self.show_page("models"), self._add_model())),
            ("💬  شروع چت", UI["accent2"], lambda: self.show_page("chat")),
            ("🔍  OSINT", "#0ea5e9", lambda: self.show_page("osint")),
            ("🩺  Doctor", "#64748b", lambda: self.show_page("doctor")),
        ]:
            ctk.CTkButton(
                actions, text=text, width=150, height=42, corner_radius=10,
                fg_color=color, hover_color=color, command=cmd,
            ).pack(side="left", padx=6)

        left = ctk.CTkFrame(fr, fg_color=UI["card"], corner_radius=14)
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 6), pady=6)
        right = ctk.CTkFrame(fr, fg_color=UI["card"], corner_radius=14)
        right.grid(row=2, column=1, sticky="nsew", padx=(6, 0), pady=6)
        fr.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(left, text="وضعیت سیستم", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=14, pady=(12, 6))
        self.home_info = ctk.CTkTextbox(left, height=180, fg_color=UI["panel2"], corner_radius=10)
        self.home_info.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        ctk.CTkLabel(right, text="میانبرها و نکات", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=14, pady=(12, 6))
        tips_txt = (
            "Ctrl+Enter   ارسال پیام\n"
            "Ctrl+N         سشن جدید\n"
            "Ctrl+S         ذخیره سشن\n"
            "Ctrl+M        صفحه مدل‌ها\n"
            "Ctrl+B         باز/بسته کشو\n\n"
            "۱) مدل .gguf را در models\\ بگذارید\n"
            "۲) از صفحه مدل‌ها Load کنید\n"
            "۳) Agent/OSINT را از چت روشن کنید"
        )
        ctk.CTkLabel(right, text=tips_txt, justify="left", text_color=UI["muted"]).pack(anchor="w", padx=14, pady=8)
        ctk.CTkButton(right, text="رفتن به تنظیمات", fg_color=UI["panel"], command=lambda: self.show_page("settings")).pack(anchor="w", padx=14, pady=8)


    def _refresh_home(self):
        models = list_models()
        lines = [
            f"نسخه: {APP_VERSION}",
            f"تعداد مدل‌ها: {len(models)}",
            f"مدل فعال: {self.engine.model_path.name if self.engine.model_path else '—'}",
            f"Agent: {'ON' if self.agent_mode else 'OFF'} | OSINT: {'ON' if self.osint_mode else 'OFF'}",
            f"صفحه فعلی: {getattr(self, 'current_page', '—')}",
            f"Workspace: {workspace_dir()}",
            f"سشن: {getattr(self, 'session_id', '—')}",
        ]
        self._refresh_topbar()
        if not models:
            lines.append("")
            lines.append("هنوز مدلی ندارید → دکمه «افزودن مدل» را بزنید.")
        marker = ROOT / "workspace" / ".first_run_done.json"
        if not marker.exists():
            lines.append("")
            lines.append("پیشنهاد: از تنظیمات → ویزارد اولین اجرا را یک‌بار طی کنید.")
        self.home_info.configure(state="normal")
        self.home_info.delete("1.0", "end")
        self.home_info.insert("1.0", "\n".join(lines))
        self.home_info.configure(state="disabled")

    def _build_sessions_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["sessions"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(fr, fg_color=UI["card"], corner_radius=14)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(top, text="☰  تاریخچه گفتگوها", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", padx=14, pady=12)
        ctk.CTkButton(top, text="تازه‌سازی", width=88, height=32, fg_color=UI["panel"],
                      command=lambda: self._refresh_sessions_ui(force=True)).pack(side="right", padx=8, pady=10)
        ctk.CTkButton(top, text="＋ جدید", width=88, height=32, fg_color=UI["accent"], text_color="#0a0a0a",
                      command=lambda: (self._handle_slash("/new"), self.show_page("chat"))).pack(side="right", padx=4, pady=10)

        filt = ctk.CTkFrame(fr, fg_color="transparent")
        filt.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.session_filter = ctk.CTkEntry(filt, placeholder_text="جستجو در تاریخچه…", height=36)
        self.session_filter.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.session_filter.bind("<KeyRelease>", lambda e: self._schedule_session_filter())
        ctk.CTkButton(filt, text="پاک‌سازی فیلتر", width=100, height=36, fg_color=UI["panel"],
                      command=self._clear_session_filter).pack(side="right")

        self.sessions_list = ctk.CTkScrollableFrame(fr, fg_color=UI["panel2"], corner_radius=14)
        self.sessions_list.grid(row=2, column=0, sticky="nsew")
        self._session_cache = []
        self._session_filter_after = None

    def _schedule_session_filter(self):
        if self._session_filter_after:
            try:
                self.after_cancel(self._session_filter_after)
            except Exception:
                pass
        self._session_filter_after = self.after(180, lambda: self._refresh_sessions_ui(force=False))

    def _clear_session_filter(self):
        try:
            self.session_filter.delete(0, "end")
        except Exception:
            pass
        self._refresh_sessions_ui(force=True)

    def _refresh_sessions_ui(self, force: bool = True):
        q = ""
        try:
            q = (self.session_filter.get() or "").strip().lower()
        except Exception:
            q = ""
        if force or not getattr(self, "_session_cache", None):
            files = sorted(sessions_dir().glob("session_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
            cache = []
            for f in files[:80]:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    nmsg = len(data.get("messages") or [])
                    model = Path(str(data.get("model") or "")).name
                    title = (data.get("title") or "").strip() or f.stem
                    preview = ""
                    for m in (data.get("messages") or [])[:4]:
                        if m.get("role") == "user" and m.get("content"):
                            preview = str(m.get("content"))[:80].replace("\n", " ")
                            break
                    mtime = f.stat().st_mtime
                    cache.append({"path": f, "title": title, "nmsg": nmsg, "model": model, "preview": preview, "mtime": mtime})
                except Exception:
                    cache.append({"path": f, "title": f.name, "nmsg": 0, "model": "", "preview": "", "mtime": 0})
            self._session_cache = cache
        else:
            cache = self._session_cache

        for w in self.sessions_list.winfo_children():
            w.destroy()
        shown = 0
        for item in cache:
            hay = f"{item['title']} {item['model']} {item['preview']} {item['path'].name}".lower()
            if q and q not in hay:
                continue
            shown += 1
            row = ctk.CTkFrame(self.sessions_list, fg_color=UI["card"], corner_radius=12)
            row.pack(fill="x", pady=4, padx=4)
            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            ctk.CTkLabel(left, text=item["title"], anchor="w", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
            meta = f"{item['nmsg']} پیام"
            if item["model"]:
                meta += f"  ·  {item['model']}"
            ctk.CTkLabel(left, text=meta, anchor="w", text_color=UI["muted"]).pack(anchor="w")
            if item["preview"]:
                ctk.CTkLabel(left, text=item["preview"], anchor="w", text_color=UI["muted"]).pack(anchor="w")
            btns = ctk.CTkFrame(row, fg_color="transparent")
            btns.pack(side="right", padx=8)
            ctk.CTkButton(btns, text="باز کردن", width=72, height=30, fg_color=UI["accent"], text_color="#0a0a0a",
                          command=lambda p=item["path"]: self._load_session(p)).pack(side="top", pady=2)
            ctk.CTkButton(btns, text="تغییر نام", width=72, height=28, fg_color=UI["panel"],
                          command=lambda p=item["path"]: self._rename_session(p)).pack(side="top", pady=2)
            ctk.CTkButton(btns, text="حذف", width=72, height=28, fg_color="#3f1d1d",
                          command=lambda p=item["path"]: self._delete_session(p)).pack(side="top", pady=2)
        if shown == 0:
            ctk.CTkLabel(self.sessions_list, text="موردی نیست. چت را ذخیره کنید یا فیلتر را عوض کنید.",
                         text_color=UI["muted"]).pack(anchor="w", pady=16, padx=12)

    def _rename_session(self, path: Path):
        try:
            from tkinter import simpledialog
            data = json.loads(path.read_text(encoding="utf-8"))
            cur = (data.get("title") or path.stem)
            new = simpledialog.askstring("تغییر نام سشن", "عنوان جدید:", initialvalue=str(cur))
            if not new:
                return
            data["title"] = new.strip()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._session_cache = []
            self._refresh_sessions_ui(force=True)
        except Exception as e:
            messagebox.showerror("خطا", str(e))


    def _load_session(self, path: Path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.messages = list(data.get("messages") or [])
            self.session_id = str(data.get("session_id") or path.stem.replace("session_", "") or self.session_id)
            self.session_title = (data.get("title") or path.stem)
            # redraw transcript
            try:
                self.chat_box.configure(state="normal")
                self.chat_box.delete("1.0", "end")
                self.chat_box.configure(state="disabled")
            except Exception:
                pass
            for msg in self.messages:
                role = msg.get("role") or "assistant"
                content = msg.get("content") or ""
                if content:
                    self._log_chat(role, content)
            self.show_page("chat")
            self.set_status(f"سشن بارگذاری شد: {self.session_title}")
            self._refresh_topbar()
        except Exception as e:
            messagebox.showerror("سشن", str(e))


    def _delete_session(self, path: Path):
        if not messagebox.askyesno("حذف", f"حذف شود؟\n{path.name}"):
            return
        try:
            path.unlink()
            self._refresh_sessions_ui()
        except Exception as e:
            messagebox.showerror("خطا", str(e))

    # ---------- CHAT ----------

    def _build_chat_page(self):
        """Grok-like chat: transcript + composer with attach."""
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["chat"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_rowconfigure(1, weight=1)

        # Header bar
        head = ctk.CTkFrame(fr, fg_color=UI["card"], corner_radius=16, height=52)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        head.grid_propagate(False)
        self.model_label = ctk.CTkLabel(head, text="مدل: —", font=ctk.CTkFont(size=13, weight="bold"))
        self.model_label.pack(side="left", padx=16, pady=12)
        ctk.CTkButton(head, text="＋ سشن", width=70, height=30, fg_color=UI["panel"], hover_color=UI["nav_hover"],
                      command=lambda: self._handle_slash("/new")).pack(side="right", padx=6)
        ctk.CTkButton(head, text="ذخیره", width=64, height=30, fg_color=UI["panel"], hover_color=UI["nav_hover"],
                      command=lambda: self._handle_slash("/save")).pack(side="right", padx=2)
        ctk.CTkButton(head, text="خروجی", width=64, height=30, fg_color=UI["panel"], hover_color=UI["nav_hover"],
                      command=self._export_chat).pack(side="right", padx=2)
        self.osint_sw = ctk.CTkSwitch(head, text="OSINT", width=70, command=self._toggle_osint)
        self.osint_sw.pack(side="right", padx=8)
        self.agent_sw = ctk.CTkSwitch(head, text="Agent", width=70, command=self._toggle_agent)
        self.agent_sw.pack(side="right", padx=4)
        try:
            self.agent_sw.select()  # auto tool-use ON by default
            self.agent_mode = True
        except Exception:
            pass

        # Transcript
        self.chat_box = ctk.CTkTextbox(
            fr, wrap="word", font=ctk.CTkFont(size=15),
            fg_color=UI["panel2"], corner_radius=18, border_width=1, border_color=UI["border"],
            text_color=UI["text"],
        )
        self.chat_box.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        self.chat_box.configure(state="disabled")

        # Composer card
        composer = ctk.CTkFrame(fr, fg_color=UI["card"], corner_radius=20, border_width=1, border_color=UI["border"])
        composer.grid(row=2, column=0, sticky="ew")
        composer.grid_columnconfigure(0, weight=1)

        self.attach_label = ctk.CTkLabel(composer, text="", text_color=UI["muted"], anchor="w")
        self.attach_label.grid(row=0, column=0, columnspan=4, sticky="ew", padx=14, pady=(10, 0))

        self.input = ctk.CTkTextbox(composer, height=72, font=ctk.CTkFont(size=14),
                                   fg_color=UI["panel2"], corner_radius=12)
        self.input.grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(6, 8))
        self.input.bind("<Control-Return>", lambda e: self._on_send())

        tools = ctk.CTkFrame(composer, fg_color="transparent")
        tools.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=(0, 10))
        ctk.CTkButton(tools, text="＋ فایل", width=80, height=34, corner_radius=10,
                      fg_color=UI["panel"], hover_color=UI["nav_hover"],
                      command=self._attach_files).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="تصویر/PDF", width=90, height=34, corner_radius=10,
                      fg_color=UI["panel"], hover_color=UI["nav_hover"],
                      command=self._attach_docs).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="پاک پیوست", width=80, height=34, corner_radius=10,
                      fg_color=UI["panel"], hover_color=UI["nav_hover"],
                      command=self._clear_attachments).pack(side="left", padx=3)
        ctk.CTkButton(tools, text="توقف", width=70, height=34, corner_radius=10,
                      fg_color="#333", command=self._on_stop).pack(side="right", padx=3)
        self.send_btn = ctk.CTkButton(tools, text="ارسال  ↑", width=100, height=34, corner_radius=10,
                                      fg_color=UI["accent"], text_color="#0a0a0a", hover_color="#bef264",
                                      command=self._on_send)
        self.send_btn.pack(side="right", padx=3)

        # quick chips
        chips = ctk.CTkFrame(fr, fg_color="transparent")
        chips.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        for lab, cmd in [("پاک کردن چت", "clear"), ("وضعیت", "status"), ("مدل‌ها", "models"), ("Agent روشن", "agent on")]:
            ctk.CTkButton(chips, text=lab, height=28, width=90, corner_radius=14,
                          fg_color=UI["panel"], hover_color=UI["nav_hover"],
                          command=lambda c=cmd: self._chat_cmd_btn(c)).pack(side="left", padx=3)

    def _attach_files(self):
        paths = filedialog.askopenfilenames(
            title="افزودن فایل به چت",
            filetypes=[
                ("All supported", "*.txt *.md *.py *.json *.csv *.log *.pdf *.png *.jpg *.jpeg *.webp"),
                ("Text", "*.txt *.md *.py *.json *.csv *.log"),
                ("All", "*.*"),
            ],
        )
        for pth in paths:
            path = Path(pth)
            if path not in self.attachments:
                self.attachments.append(path)
        self._refresh_attach_label()

    def _attach_docs(self):
        paths = filedialog.askopenfilenames(
            title="تصویر یا PDF",
            filetypes=[("Docs", "*.pdf *.png *.jpg *.jpeg *.webp *.gif"), ("All", "*.*")],
        )
        for pth in paths:
            path = Path(pth)
            if path not in self.attachments:
                self.attachments.append(path)
        self._refresh_attach_label()

    def _clear_attachments(self):
        self.attachments.clear()
        self._refresh_attach_label()

    def _refresh_attach_label(self):
        if not self.attachments:
            self.attach_label.configure(text="")
            return
        names = ", ".join(p.name for p in self.attachments[:6])
        more = f"  (+{len(self.attachments)-6})" if len(self.attachments) > 6 else ""
        self.attach_label.configure(text=f"پیوست‌ها ({len(self.attachments)}): {names}{more}")

    def _read_attachments_text(self) -> str:
        """Extract text from attachments for context injection."""
        chunks = []
        for path in self.attachments[:8]:
            try:
                suffix = path.suffix.lower()
                if suffix in {".txt", ".md", ".py", ".json", ".csv", ".log", ".yml", ".yaml", ".toml"}:
                    text = path.read_text(encoding="utf-8", errors="replace")[:12000]
                    chunks.append(f"[FILE {path.name}]\n{text}")
                elif suffix == ".pdf":
                    try:
                        from file_extract import extract_text
                        text = (extract_text(str(path)) or "")[:12000]
                    except Exception:
                        text = "(pdf extract unavailable)"
                    chunks.append(f"[PDF {path.name}]\n{text}")
                elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                    chunks.append(f"[IMAGE {path.name}] path={path} (binary attached — describe if vision unavailable)")
                else:
                    # best-effort text
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")[:8000]
                        chunks.append(f"[FILE {path.name}]\n{text}")
                    except Exception:
                        chunks.append(f"[FILE {path.name}] (binary)")
            except Exception as e:
                chunks.append(f"[FILE {path.name}] error: {e}")
        return "\n\n".join(chunks)


    def _log_chat(self, role: str, text: str):
        if role == "tool" and text and len(text) > 6000:
            text = text[:6000] + "\n… [truncated]"
        # visual role markers
        if role == "user":
            head = "┌─ You"
            body_prefix = "│ "
        elif role == "assistant":
            head = "┌─ Assistant"
            body_prefix = "│ "
        elif role == "system":
            head = "· System"
            body_prefix = "  "
        else:
            head = f"┌─ {role}"
            body_prefix = "│ "
        lines = str(text).splitlines() or [""]
        body = "\n".join(body_prefix + ln for ln in lines)
        block = f"\n{head}\n{body}\n"
        try:
            self.chat_box.configure(state="normal")
            self.chat_box.insert("end", block)
            try:
                line_count = int(float(self.chat_box.index("end-1c").split(".")[0]))
                if line_count > 3000:
                    self.chat_box.delete("1.0", f"{line_count - 2200}.0")
            except Exception:
                pass
            self.chat_box.see("end")
            self.chat_box.configure(state="disabled")
        except Exception:
            pass


    def _toggle_agent(self):
        self.agent_mode = bool(self.agent_sw.get())
        self._refresh_topbar()
        self._log_chat("system", "Agent روشن" if self.agent_mode else "Agent خاموش")

    def _toggle_osint(self):
        self.osint_mode = bool(self.osint_sw.get())
        self._refresh_topbar()
        self._log_chat("system", "OSINT روشن" if self.osint_mode else "OSINT خاموش")

    def _on_stop(self):
        self.busy = False


    def _get_input_text(self) -> str:
        try:
            return (self.input.get("1.0", "end") or "").strip()
        except Exception:
            try:
                return (self.input.get() or "").strip()
            except Exception:
                return ""

    def _clear_input(self):
        try:
            self.input.delete("1.0", "end")
        except Exception:
            try:
                self.input.delete(0, "end")
            except Exception:
                pass

    def _on_send(self):
        if self.busy:
            return
        import time as _time
        now = _time.time()
        if now - getattr(self, "_last_send_ts", 0) < 0.35:
            return
        self._last_send_ts = now
        text = self._get_input_text()
        if not text:
            return
        self._clear_input()
        if text.startswith("/"):
            self._handle_slash(text)
            return
        if self._try_chat_command(text):
            return
        if not self.engine.llm:
            # No local model: try Persian intent → tool first
            self.messages.append({"role": "user", "content": text})
            self._log_chat("user", text)
            try:
                routed = route_intent(text)
            except Exception:
                routed = None
            if routed:
                self.busy = True
                self.send_btn.configure(state="disabled")
                self.set_status("اجرای ابزار…")
                if self._run_routed_tool(routed):
                    self.busy = False
                    try:
                        self.send_btn.configure(state="normal")
                    except Exception:
                        pass
                    self.set_status("آماده")
                    return
            self.busy = True
            self.send_btn.configure(state="disabled")
            self.set_status("بدون مدل محلی — پاسخ از RAG + وب…")
            threading.Thread(target=self._generate_no_model, args=(text,), daemon=True).start()
            return
        att_ctx = self._read_attachments_text()
        if att_ctx:
            text = text + "\n\n# Attachments\n" + att_ctx
            self._log_chat("system", f"پیوست: {len(self.attachments)} فایل")
            self.attachments.clear()
            self._refresh_attach_label()
        self.messages.append({"role": "user", "content": text})
        self._log_chat("user", text)
        try:
            from tool_budget import reset_turn
            reset_turn()
        except Exception:
            pass
        # Persian natural language → tool (fast path; model then summarizes)
        try:
            routed = route_intent(text)
        except Exception:
            routed = None
        if routed:
            self.busy = True
            self._asst_started = False
            self.send_btn.configure(state="disabled")
            self.set_status("اجرای ابزار…")
            if self._run_routed_tool(routed):
                return
        self.busy = True
        self._asst_started = False
        self.send_btn.configure(state="disabled")
        self.set_status("در حال پاسخ…")
        threading.Thread(target=self._generate, daemon=True).start()


    def _generate_no_model(self, query: str):
        """Fallback when no GGUF is loaded: answer from books (RAG) + web search."""
        parts = []
        parts.append("⚠️ مدل محلی لود نشده است. پاسخ بر اساس جستجوی محلی (کتاب‌ها) و اینترنت ساخته شده.\n")
        # 1) Local RAG from books/
        try:
            from rag_local import rag_search, build_index, INDEX_PATH
            if not INDEX_PATH.exists():
                try:
                    build_index()
                except Exception:
                    pass
            res = rag_search(query, top_k=5)
            hits = []
            if isinstance(res, dict):
                hits = res.get("results") or res.get("hits") or []
                if not res.get("ok") and not hits:
                    hits = []
            if hits:
                parts.append("## از کتاب‌ها / دانش محلی\n")
                for i, h in enumerate(hits[:5], 1):
                    if isinstance(h, dict):
                        src = h.get("source") or h.get("id") or ""
                        snip = (h.get("text") or h.get("snippet") or "")[:500]
                        parts.append(f"{i}. ({src})\n{snip}\n")
                    else:
                        parts.append(f"{i}. {str(h)[:500]}\n")
            else:
                parts.append("## دانش محلی\nنتیجه‌ای در books/ و memory یافت نشد. فایل‌های txt/md را در پوشه books قرار دهید و ایندکس بسازید.\n")
        except Exception as e:
            parts.append(f"## دانش محلی\nخطا در RAG: {e}\n")

        # 2) Web search (public DuckDuckGo via OSINT tool)
        try:
            res = execute_tool("osint_search", {"query": query, "max_results": 5})
            if not res.get("ok"):
                res = execute_tool("osint_duckduckgo", {"query": query, "max_results": 5})
            if res.get("ok"):
                data = res.get("results") or res.get("items") or res.get("data") or res
                parts.append("## نتایج جستجوی وب\n")
                if isinstance(data, list):
                    for i, item in enumerate(data[:5], 1):
                        if isinstance(item, dict):
                            title = item.get("title") or item.get("name") or ""
                            url = item.get("url") or item.get("href") or item.get("link") or ""
                            snip = item.get("snippet") or item.get("body") or item.get("description") or ""
                            parts.append(f"{i}. {title}\n{url}\n{str(snip)[:300]}\n")
                        else:
                            parts.append(f"{i}. {str(item)[:400]}\n")
                elif isinstance(data, dict) and data.get("results"):
                    for i, item in enumerate(data["results"][:5], 1):
                        parts.append(f"{i}. {item}\n")
                elif isinstance(data, str):
                    parts.append(data[:2000])
                else:
                    parts.append(str(data)[:2000])
            else:
                err = res.get("error") or res.get("message") or "جستجوی وب در دسترس نیست (اینترنت یا پروکسی را بررسی کنید)"
                parts.append(f"## جستجوی وب\n{err}\n")
        except Exception as e:
            parts.append(f"## جستجوی وب\nخطا: {e}\n")
        parts.append("\n---\nبرای پاسخ کامل‌تر یک فایل .gguf در پوشه models قرار دهید و از صفحه «مدل‌ها» لود کنید.")
        reply = "\n".join(parts)
        self.messages.append({"role": "assistant", "content": reply})
        def _done():
            self._log_chat("assistant", reply)
            self.busy = False
            self.send_btn.configure(state="normal")
            self.set_status("آماده | مدل: — (حالت RAG+وب)")
        try:
            self.after(0, _done)
        except Exception:
            _done()

    def _build_messages(self):
        sys_content = (self.system_prompt or "You are a helpful assistant.") + identity_system_suffix()
        # Auto tools: always advertise tools when a model is loaded (agent auto)
        if self.engine.llm or self.agent_mode or self.osint_mode:
            sys_content += "\n\n" + tools_schema_for_prompt(include_osint=True)
            try:
                sys_content += "\n" + persian_tool_examples()
            except Exception:
                pass
            sys_content += (
                "\nکاربر به فارسی دستور می‌دهد. اگر کاری واقعی لازم است فقط JSON ابزار بده،"
                " بدون توضیح اضافه قبل از فراخوانی ابزار."
                "\nاگر خواست برنامه بسازد: کد کامل را در arguments.code به make_code_file بده"
                " (language مثل python/javascript). فایل در workspace/exports ذخیره می‌شود."
                "\nبرای ورد: make_document fmt=docx — برای PDF: fmt=pdf."
            )
        if self.osint_mode:
            sys_content += "\nOSINT mode: use public-source tools only; never invent private data."
        out = [{"role": "system", "content": sys_content}]
        msgs = self.messages[-28:]
        if len(self.messages) > 36:
            try:
                from session_memory import extractive_summary
                summ = extractive_summary(self.messages[:-20], max_chars=800)
                if summ:
                    out.append({"role": "system", "content": "[خلاصه قدیمی]\n" + summ})
            except Exception:
                pass
        out.extend(msgs)
        return out

    def _generate(self):
        try:
            # Dual router: optional small model suggests tool JSON first
            if self.agent_mode and getattr(self, "router", None) and self.router.enabled():
                try:
                    from agent_tools import tools_schema_for_prompt
                    err = self.router.ensure_tool_model(
                        n_ctx=2048,
                        n_gpu_layers=_safe_int(self.cfg.get("n_gpu_layers", 0), 0, -1, 999),
                        n_threads=_safe_int(self.cfg.get("threads", 4), 4, 1, 64),
                    )
                    if not err:
                        plan = self.router.plan_tools(self.messages[-8:], tools_schema_for_prompt(include_osint=True))
                        parsed = extract_tool_json(plan)
                        if parsed:
                            self.after(0, lambda: self._log_chat("system", "[Router] tool=" + str(parsed.get("tool"))))
                            self.after(0, lambda p=json.dumps(parsed, ensure_ascii=False): self._maybe_tool(p))
                            # still continue main model for user-facing answer
                except Exception:
                    pass
            stream = self.engine.chat(
                self._build_messages(),
                temperature=_safe_float(self.cfg.get("temperature", 0.7), 0.7, 0, 2),
                max_tokens=_safe_int(self.cfg.get("max_tokens", 1024), 1024, 16, 8192),
                top_p=_safe_float(self.cfg.get("top_p", 0.9), 0.9, 0, 1),
                stream=True,
            )
            full = []
            for chunk in stream:
                if not self.busy:
                    break
                try:
                    choices = chunk.get("choices") or [{}]
                    delta = (choices[0] or {}).get("delta") or {}
                    token = delta.get("content") or ""
                except Exception:
                    token = ""
                if token:
                    full.append(token)
                    self.after(0, lambda t=token: self._append_token(t))
            reply = "".join(full).strip()
            try:
                self.after(0, self._flush_tokens)
            except Exception:
                pass
            self._asst_started = False
            if reply:
                self.messages.append({"role": "assistant", "content": reply})
                if len(self.messages) > 300:
                    self.messages = self.messages[-200:]
                try:
                    self.after(0, self._autosave_session)
                except Exception:
                    pass
                if self.engine.llm or self.agent_mode or self.osint_mode:
                    self.after(0, lambda: self._maybe_tool(reply))
        except Exception as e:
            err = f"Error: {e}\n{traceback.format_exc()[:400]}"
            self.after(0, lambda: self._log_chat("system", err))
        finally:
            self.busy = False
            def _done():
                self.send_btn.configure(state="normal")
                name = self.engine.model_path.name if self.engine.model_path else "—"
                self.set_status(f"آماده | مدل: {name}")
            self.after(0, _done)

    def _append_token(self, token: str):
        if not hasattr(self, "_token_buf") or self._token_buf is None:
            self._token_buf = []
        self._token_buf.append(token)
        if getattr(self, "_token_flush_id", None) is None:
            self._token_flush_id = self.after(40, self._flush_tokens)

    def _flush_tokens(self):
        self._token_flush_id = None
        buf = getattr(self, "_token_buf", None) or []
        self._token_buf = []
        if not buf:
            return
        chunk = "".join(buf)
        try:
            self.chat_box.configure(state="normal")
            if not self._asst_started:
                self.chat_box.insert("end", "\n[دستیار]\n")
                self._asst_started = True
            self.chat_box.insert("end", chunk)
            self.chat_box.see("end")
            self.chat_box.configure(state="disabled")
        except Exception:
            pass

    def _run_routed_tool(self, routed: dict):
        """Execute heuristic intent route (Persian NL → tool)."""
        if not routed:
            return False
        name = routed.get("tool")
        args = routed.get("arguments") or {}
        if not name:
            return False
        self._log_chat("tool", f"تشخیص خودکار نیت: {routed.get('reason')} → {name}")
        if needs_approval(name):
            try:
                dlg = ApprovalDialog(self, name, args)
                self.wait_window(dlg)
                if not getattr(dlg, "result", False):
                    self._log_chat("tool", "رد شد")
                    return True
            except Exception as e:
                self._log_chat("system", f"Approval: {e}")
                return True
        try:
            result = execute_tool(name, args if isinstance(args, dict) else {})
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        self._log_chat("tool", __import__("json").dumps(result, ensure_ascii=False, indent=2)[:4000])
        self.messages.append({"role": "user", "content": f"[tool result: {name}]\n{__import__('json').dumps(result, ensure_ascii=False)}"})
        # ask model to summarize if loaded
        if self.engine.llm and not self.busy:
            self.busy = True
            self._asst_started = False
            try:
                self.send_btn.configure(state="disabled")
            except Exception:
                pass
            import threading
            threading.Thread(target=self._generate, daemon=True).start()
        elif not self.engine.llm:
            # no model: show short Persian note
            ok = result.get("ok")
            self._log_chat("system", "انجام شد." if ok else f"خطا: {result.get('error')}")
        return True

    def _maybe_tool(self, reply: str):
        text = (reply or "").strip()
        # strip markdown fences
        if "```" in text:
            import re as _re
            m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.S)
            if m:
                text = m.group(1)
        if not (text.startswith("{") and "tool" in text):
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                text = text[start : end + 1]
            else:
                return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return
        name = obj.get("tool") or obj.get("name")
        args = obj.get("arguments") or obj.get("args") or {}
        if not name:
            return
        self._log_chat("tool", f"برنامه ابزار: {name}\nآرگومان‌ها: {json.dumps(args, ensure_ascii=False)[:500]}")
        if needs_approval(name):
            try:
                dlg = ApprovalDialog(self, name, args)
                self.wait_window(dlg)
                if not getattr(dlg, "result", False):
                    self._log_chat("tool", "رد شد")
                    return
            except Exception as e:
                self._log_chat("system", f"Approval error: {e}")
                return
        try:
            result = execute_tool(name, args if isinstance(args, dict) else {})
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        self._log_chat("tool", json.dumps(result, ensure_ascii=False, indent=2)[:4000])
        self.messages.append({"role": "user", "content": f"[tool result: {name}]\n{json.dumps(result, ensure_ascii=False)}"})
        if self.engine.llm and not self.busy:
            self.busy = True
            self._asst_started = False
            self.send_btn.configure(state="disabled")
            threading.Thread(target=self._generate, daemon=True).start()

    def _handle_slash(self, text: str):
        low = text.lower().strip()
        if low in ("/help", "/?"):
            self._log_chat(
                "system",
                "دستورات:\n"
                "/new  /save  /doctor\n"
                "/download URL | /downloads\n"
                "/proxy add|list|on|off|test\n"
                "/shell COMMAND\n"
                "/api METHOD URL\n"
                "/monitor [cpu|mem|disk|net|proc|ports|app]\n"
                "/learn TOPIC  /translate TEXT  /ocr PATH\n"
                "/audit domain  /hash PATH  /netlocal",
            )
        elif low == "/new":
            self.messages.clear()
            self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.session_title = f"Chat {self.session_id}"
            self.chat_box.configure(state="normal")
            self.chat_box.delete("1.0", "end")
            self.chat_box.configure(state="disabled")
            self._log_chat("system", "سشن جدید")
        elif low == "/save":
            path = sessions_dir() / f"session_{self.session_id}.json"
            path.write_text(
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "title": getattr(self, "session_title", None) or f"Chat {self.session_id}",
                        "messages": self.messages,
                        "model": str(self.engine.model_path or ""),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self._log_chat("system", f"ذخیره شد: {path.name}")
        elif low == "/doctor":
            self.show_page("doctor")
        elif low.startswith("/download ") or low.startswith("/dl "):
            url = text.split(None, 1)[1].strip() if " " in text else ""
            if not url:
                self._log_chat("system", "مثال: /download https://example.com/file.zip")
            else:
                res = execute_tool("download_start", {"url": url})
                self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:2000])
        elif low in ("/downloads", "/dlstatus"):
            res = execute_tool("download_list", {})
            lines = []
            for j in (res.get("jobs") or []):
                lines.append(
                    f"{j.get('id')}  {j.get('status')}  {j.get('progress_bar')}  "
                    f"{j.get('speed_human', '')}  {Path(j.get('path') or '').name}"
                )
            self._log_chat("system", "\n".join(lines) if lines else "دانلودی نیست")
        elif low.startswith("/proxy "):
            parts = text.split()
            if len(parts) >= 3 and parts[1].lower() == "add":
                res = execute_tool("proxy_add", {"proxy": parts[2]})
            elif len(parts) >= 2 and parts[1].lower() == "list":
                res = execute_tool("proxy_list", {})
            elif len(parts) >= 2 and parts[1].lower() in ("on", "enable"):
                res = execute_tool("proxy_enable", {"enabled": True})
            elif len(parts) >= 2 and parts[1].lower() in ("off", "disable"):
                res = execute_tool("proxy_enable", {"enabled": False})
            elif len(parts) >= 2 and parts[1].lower() == "test":
                res = execute_tool("proxy_test", {"proxy": parts[2] if len(parts) > 2 else ""})
            else:
                res = {"ok": False, "error": "usage: /proxy add URL | list | on | off | test [URL]"}
            self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:2000])
        elif low.startswith("/shell ") or low.startswith("/cmd "):
            cmd = text.split(None, 1)[1].strip() if " " in text else ""
            if not cmd:
                self._log_chat("system", "مثال: /shell dir")
            else:
                res = execute_tool("shell_run", {"command": cmd, "timeout": 120})
                out = (res.get("stdout") or "")
                if res.get("stderr"):
                    out = out + "\n" + str(res.get("stderr"))
                if not out.strip():
                    out = json.dumps(res, ensure_ascii=False, indent=2)
                self._log_chat("system", out[:4000])
        elif low.startswith("/api "):
            parts = text.split(None, 2)
            if len(parts) < 3:
                self._log_chat("system", "مثال: /api GET https://httpbin.org/get")
            else:
                method, rest = parts[1], parts[2]
                url, body = rest, None
                if " {" in rest:
                    try:
                        u, j = rest.split(" {", 1)
                        body = json.loads("{" + j)
                        url = u.strip()
                    except Exception:
                        body = None
                args = {"url": url, "method": method}
                if body is not None:
                    args["json_body"] = body
                res = execute_tool("api_request", args)
                self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:4000])
        elif low == "/monitor" or low.startswith("/monitor "):
            sub = (text.split(None, 1)[1].strip().lower() if " " in text else "system")
            tool_map = {
                "system": "monitor_system",
                "sys": "monitor_system",
                "cpu": "monitor_cpu",
                "mem": "monitor_memory",
                "memory": "monitor_memory",
                "ram": "monitor_memory",
                "disk": "monitor_disk",
                "net": "monitor_network",
                "network": "monitor_network",
                "proc": "monitor_processes",
                "process": "monitor_processes",
                "processes": "monitor_processes",
                "ports": "monitor_ports",
                "port": "monitor_ports",
                "uptime": "monitor_uptime",
                "sensors": "monitor_sensors",
                "app": "monitor_app",
            }
            tname = tool_map.get(sub, "monitor_system")
            res = execute_tool(tname, {})
            self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:4500])
        elif low.startswith("/learn "):
            topic = text.split(None, 1)[1].strip() if " " in text else ""
            if not topic:
                self._log_chat("system", "مثال: /learn شبکه عصبی")
            else:
                self._log_chat("system", f"در حال یادگیری: {topic} …")
                res = execute_tool("learn_topic", {"topic": topic, "max_pages": 3, "include_youtube": True})
                self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:4500])
        elif low.startswith("/translate "):
            rest = text.split(None, 1)[1].strip() if " " in text else ""
            target, body = "fa", rest
            if "|" in rest[:12]:
                target, body = rest.split("|", 1)
                target, body = target.strip(), body.strip()
            res = execute_tool("translate", {"text": body, "target": target})
            self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:3000])
        elif low.startswith("/ocr "):
            src = text.split(None, 1)[1].strip() if " " in text else ""
            args = {"url": src} if src.startswith("http") else {"path": src}
            res = execute_tool("ocr_image", args)
            self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:3000])
        elif low.startswith("/audit "):
            target = text.split(None, 1)[1].strip() if " " in text else ""
            res = execute_tool("cyber_quick_audit", {"url_or_domain": target})
            self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:4500])
        elif low.startswith("/hash "):
            path = text.split(None, 1)[1].strip() if " " in text else ""
            res = execute_tool("cyber_hash_file", {"path": path})
            self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:2000])
        elif low in ("/netlocal", "/net-local"):
            res = execute_tool("cyber_net_local", {})
            self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:4000])
        elif low in ("/exports", "/export"):
            res = execute_tool("list_exports", {})
            self._log_chat("system", json.dumps(res, ensure_ascii=False, indent=2)[:3000])
        else:
            self._log_chat("system", f"دستور ناشناخته: {text}")





    # ---------- MODELS ----------
    def _build_models_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["models"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(fr, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(hdr, text="مدیریت مدل‌های GGUF", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(hdr, text="فایل .gguf را با «افزودن مدل» انتخاب کنید یا در پوشه models کپی کنید.", text_color="gray").pack(anchor="w")
        row = ctk.CTkFrame(fr, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", pady=4)
        ctk.CTkButton(row, text="➕  افزودن مدل…", fg_color=UI["accent"], command=self._add_model).pack(side="left", padx=4)
        ctk.CTkButton(row, text="📂  باز کردن پوشه models", command=self._open_models_folder).pack(side="left", padx=4)
        ctk.CTkButton(row, text="🔄  تازه‌سازی", command=self._refresh_models_ui).pack(side="left", padx=4)
        ctk.CTkButton(row, text="تخلیه مدل", fg_color="#555", command=self._unload_model).pack(side="left", padx=4)
        ctk.CTkButton(row, text="اطلاعات مدل", command=self._inspect_selected).pack(side="left", padx=4)
        ctk.CTkButton(row, text="پیشنهاد لود", command=self._advise_load_btn).pack(side="left", padx=4)
        self.models_filter = ctk.CTkEntry(row, placeholder_text="جستجوی نام مدل…", width=180)
        self.models_filter.pack(side="right", padx=4)
        self._filter_after = None
        self.models_filter.bind("<KeyRelease>", self._on_models_filter_key)

        self.models_list = ctk.CTkScrollableFrame(fr)
        self.models_list.grid(row=2, column=0, sticky="nsew", pady=8)

        row2 = ctk.CTkFrame(fr, fg_color="transparent")
        row2.grid(row=3, column=0, sticky="ew")
        ctk.CTkLabel(row2, text="ctx").pack(side="left")
        self.ctx_var = ctk.StringVar(value=str(self.cfg.get("ctx_size") or 4096))
        ctk.CTkEntry(row2, textvariable=self.ctx_var, width=80).pack(side="left", padx=4)
        ctk.CTkLabel(row2, text="GPU layers").pack(side="left", padx=(12, 0))
        self.ngl_var = ctk.StringVar(value=str(self.cfg.get("n_gpu_layers") or 0))
        ctk.CTkEntry(row2, textvariable=self.ngl_var, width=60).pack(side="left", padx=4)
        ctk.CTkButton(row2, text="CPU", width=50, fg_color="#555", command=lambda: self.ngl_var.set("0")).pack(side="left", padx=2)
        ctk.CTkButton(row2, text="GPU ۲۰", width=60, command=lambda: self.ngl_var.set("20")).pack(side="left", padx=2)
        ctk.CTkButton(row2, text="GPU ۳۵", width=60, command=lambda: self.ngl_var.set("35")).pack(side="left", padx=2)
        ctk.CTkButton(row2, text="همه GPU (-1)", width=100, fg_color="#6c5ce7", command=lambda: self.ngl_var.set("-1")).pack(side="left", padx=2)
        self.load_status = ctk.CTkLabel(fr, text="", text_color="gray")
        self.load_status.grid(row=4, column=0, sticky="w", pady=6)

    def _refresh_models_ui(self):
        for w in self.models_list.winfo_children():
            w.destroy()
        models = list_models()
        q = ""
        try:
            q = (self.models_filter.get() or "").strip().lower()
        except Exception:
            pass
        if q:
            models = [m for m in models if q in m.name.lower()]
        if not models:
            ctk.CTkLabel(
                self.models_list,
                text="هنوز مدلی نیست. «افزودن مدل» را بزنید یا فایل .gguf را در models\\ بگذارید.",
                text_color="#e94560",
            ).pack(anchor="w", pady=8)
            self.model_label.configure(text="مدل: —")
            self._refresh_topbar()
            return
        for p in models:
            mb = p.stat().st_size / (1024 * 1024)
            row = ctk.CTkFrame(self.models_list)
            row.pack(fill="x", pady=4, padx=4)
            # display name from sidecar if any
            try:
                from model_editor import load_meta
                _m = load_meta(p)
                label = f"{_m.get('display_name') or p.stem}  ·  {p.name}  ({mb:.1f} MB)"
            except Exception:
                label = f"{p.name}  ({mb:.1f} MB)"
            ctk.CTkLabel(row, text=label, anchor="w").pack(side="left", padx=8)
            ctk.CTkButton(row, text="لود", width=70, command=lambda path=p: self._load_model(path)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="ویرایش", width=70, fg_color="#6c5ce7", command=lambda path=p: self._open_model_editor(path)).pack(side="right", padx=4)
            ctk.CTkButton(row, text="حذف", width=70, fg_color="#555", command=lambda path=p: self._delete_model(path)).pack(
                side="right", padx=4
            )
        if self.engine.model_path:
            self.model_label.configure(text=f"مدل: {self.engine.model_path.name}")
            self._refresh_topbar()

    def _add_model(self):
        paths = filedialog.askopenfilenames(
            title="انتخاب فایل‌های GGUF",
            filetypes=[("GGUF models", "*.gguf"), ("All files", "*.*")],
        )
        if not paths:
            return
        dest_dir = ROOT / "models"
        dest_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for src in paths:
            src_p = Path(src)
            dest = dest_dir / src_p.name
            try:
                shutil.copy2(src_p, dest)
                copied.append(src_p.name)
            except Exception as e:
                messagebox.showerror("خطا", f"کپی ناموفق {src_p.name}:\n{e}")
        self._refresh_models_ui()
        if copied:
            messagebox.showinfo("افزودن مدل", "کپی شد:\n" + "\n".join(copied))
            self.load_status.configure(text=f"{len(copied)} فایل اضافه شد")

    def _open_models_folder(self):
        import os
        import subprocess
        d = ROOT / "models"
        d.mkdir(parents=True, exist_ok=True)
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(d))
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:
            messagebox.showerror("خطا", str(e))

    def _delete_model(self, path: Path):
        if not messagebox.askyesno("حذف", f"حذف شود؟\n{path.name}"):
            return
        try:
            if self.engine.model_path and self.engine.model_path.resolve() == path.resolve():
                self.engine.unload()
                self.model_label.configure(text="مدل: —")
                self._refresh_topbar()
            path.unlink()
            self._refresh_models_ui()
        except Exception as e:
            messagebox.showerror("خطا", str(e))

    def _load_model(self, path: Path):
        if self._model_loading:
            return
        self._model_loading = True
        # apply per-model sidecar settings if present
        try:
            from model_editor import load_meta, meta_path_for
            if meta_path_for(path).exists():
                m = load_meta(path)
                if hasattr(self, "ctx_var") and m.get("n_ctx"):
                    self.ctx_var.set(str(m.get("n_ctx")))
                if hasattr(self, "ngl_var") and m.get("n_gpu_layers") is not None:
                    self.ngl_var.set(str(m.get("n_gpu_layers")))
                if m.get("system_prompt"):
                    self.system_prompt = str(m.get("system_prompt"))
                for key, attr in (("temperature", "temperature"), ("max_tokens", "max_tokens"), ("top_p", "top_p")):
                    if m.get(key) is not None:
                        self.cfg[key] = str(m.get(key))
        except Exception:
            pass
        # auto-advise ctx/ngl if still defaults
        try:
            from load_advisor import suggest_load
            adv = suggest_load(path, prefer_gpu=False)
            if hasattr(self, "ctx_var") and str(self.ctx_var.get()) in ("", "4096"):
                self.ctx_var.set(str(adv.get("suggested_ctx") or 4096))
            if hasattr(self, "ngl_var") and str(self.ngl_var.get()) in ("", "0"):
                self.ngl_var.set(str(adv.get("suggested_ngl") if adv.get("suggested_ngl") is not None else 0))
            self._adv_threads = adv.get("suggested_threads")
            self._adv_batch = adv.get("suggested_batch")
            if adv.get("suggested_threads"):
                self.cfg["threads"] = str(adv.get("suggested_threads"))
            if adv.get("suggested_batch"):
                self.cfg["n_batch"] = str(adv.get("suggested_batch"))
            est = (adv.get("estimate") or {})
            self.load_status.configure(
                text=f"لود {path.name} | ctx={self.ctx_var.get()} ngl={self.ngl_var.get()} | RAM~{est.get('ram_gb_est')}GB"
            )
        except Exception:
            self.load_status.configure(text=f"در حال لود {path.name}…")
        self.set_status(f"لود مدل: {path.name}")
        self.update_idletasks()

        def work():
            err = self.engine.load(
                path,
                n_ctx=_safe_int(self.ctx_var.get(), 4096, 512, 131072),
                n_gpu_layers=_safe_int(self.ngl_var.get(), 0, -1, 999),
                n_threads=_safe_int(self.cfg.get("threads") or getattr(self, "_adv_threads", None) or 0, 0, 0, 64) or None,
                n_batch=_safe_int(self.cfg.get("n_batch") or getattr(self, "_adv_batch", None) or 0, 0, 0, 4096) or None,
                use_mmap=True,
            )
            self.after(0, lambda: self._after_load(path, err))

        threading.Thread(target=work, daemon=True).start()

    def _after_load(self, path: Path, err: str):
        self._model_loading = False
        self._last_send_ts = 0.0
        self.term_cwd = workspace_dir()
        self._restored_session = False
        if err:
            self.load_status.configure(text=f"خطا: {err}")
            tip = err
            if "حافظه" in err or "memory" in err.lower() or "cuda" in err.lower():
                tip = err + "\n\nپیشنهاد: GPU layers = 0 و ctx کوچکتر (مثلاً 2048)"
            messagebox.showerror("لود مدل", tip)
        else:
            ngl = getattr(self.engine, "n_gpu_layers", 0)
            gpu_txt = "CPU" if ngl == 0 else ("همه GPU" if ngl < 0 else f"{ngl} لایه GPU")
            self.load_status.configure(text=f"لود شد: {path.name} | {gpu_txt}")
            self.model_label.configure(text=f"مدل: {path.name}")
            self._refresh_topbar()
            try:
                self.set_status(self.engine.performance_summary())
            except Exception:
                pass
            cfg = load_ini()
            cfg["model"] = path.name
            try:
                if hasattr(self, "ctx_var"):
                    cfg["ctx_size"] = str(_safe_int(self.ctx_var.get(), 4096, 512, 131072))
                if hasattr(self, "ngl_var"):
                    cfg["n_gpu_layers"] = str(_safe_int(self.ngl_var.get(), 0, -1, 999))
                self.cfg = cfg
                save_ini(cfg)
            except Exception:
                pass
            self.show_page("chat")
            self.set_status(f"مدل فعال: {path.name}")
            self._log_chat("system", f"مدل لود شد: {path.name}")

    def _open_model_editor(self, path: Path):
        """ویرایش تنظیمات مدل (فایل جانبی JSON؛ وزن GGUF تغییر نمی‌کند)."""
        try:
            from model_editor import load_meta, save_meta, rename_model, meta_path_for
        except Exception as e:
            messagebox.showerror("ویرایش مدل", f"ماژول model_editor نیست:\n{e}")
            return
        path = Path(path)
        meta = load_meta(path)
        win = ctk.CTkToplevel(self)
        win.title(f"ویرایش مدل — {path.name}")
        win.geometry("720x640")
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(win, text=f"فایل: {path.name}", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=16, pady=(12, 4))
        gg = meta.get("gguf") or {}
        ctk.CTkLabel(
            win,
            text=f"معماری: {gg.get('architecture') or '—'} | حجم: {gg.get('size_gb') or '—'} GB | ctx: {gg.get('context_length') or '—'}",
            text_color="gray",
        ).pack(anchor="w", padx=16)
        form = ctk.CTkScrollableFrame(win)
        form.pack(fill="both", expand=True, padx=12, pady=8)

        def add_entry(label, value):
            ctk.CTkLabel(form, text=label).pack(anchor="w", padx=4, pady=(8, 0))
            var = ctk.StringVar(value=str(value if value is not None else ""))
            ctk.CTkEntry(form, textvariable=var).pack(fill="x", padx=4)
            return var

        def add_text(label, value, height=90):
            ctk.CTkLabel(form, text=label).pack(anchor="w", padx=4, pady=(8, 0))
            box = ctk.CTkTextbox(form, height=height)
            box.pack(fill="x", padx=4)
            box.insert("1.0", str(value or ""))
            return box

        v_name = add_entry("نام نمایشی", meta.get("display_name"))
        v_ctx = add_entry("n_ctx", meta.get("n_ctx"))
        v_ngl = add_entry("n_gpu_layers", meta.get("n_gpu_layers"))
        v_thr = add_entry("n_threads (0=خودکار)", meta.get("n_threads"))
        v_temp = add_entry("temperature", meta.get("temperature"))
        v_max = add_entry("max_tokens", meta.get("max_tokens"))
        v_top = add_entry("top_p", meta.get("top_p"))
        v_rep = add_entry("repeat_penalty", meta.get("repeat_penalty"))
        v_sys = add_text("System Prompt این مدل", meta.get("system_prompt"), 100)
        v_notes = add_text("یادداشت", meta.get("notes"), 70)
        v_rename = add_entry("تغییر نام فایل .gguf", path.name)
        fav_var = ctk.BooleanVar(value=bool(meta.get("favorite")))
        ctk.CTkCheckBox(form, text="علاقه‌مندی", variable=fav_var).pack(anchor="w", padx=4, pady=8)
        ctk.CTkLabel(
            form,
            text="وزن باینری GGUF تغییر نمی‌کند؛ تنظیمات در *.gguf.meta.json ذخیره می‌شود.",
            text_color="#e94560",
            wraplength=640,
        ).pack(anchor="w", padx=4, pady=6)

        def g(x):
            if isinstance(x, ctk.CTkTextbox):
                return x.get("1.0", "end").strip()
            return x.get()

        def on_save(close=True):
            data = {
                "display_name": g(v_name),
                "n_ctx": g(v_ctx),
                "n_gpu_layers": g(v_ngl),
                "n_threads": g(v_thr),
                "temperature": g(v_temp),
                "max_tokens": g(v_max),
                "top_p": g(v_top),
                "repeat_penalty": g(v_rep),
                "system_prompt": g(v_sys),
                "notes": g(v_notes),
                "favorite": bool(fav_var.get()),
            }
            res = save_meta(path, data)
            if not res.get("ok"):
                messagebox.showerror("ذخیره", res.get("error") or "خطا")
                return None
            new_name = (g(v_rename) or "").strip()
            final = path
            if new_name and new_name != path.name:
                r2 = rename_model(path, new_name)
                if not r2.get("ok"):
                    messagebox.showerror("تغییر نام", r2.get("error") or "خطا")
                    return None
                final = Path(r2.get("path") or path)
            try:
                self._refresh_models_ui()
            except Exception:
                pass
            if close:
                messagebox.showinfo("ذخیره", f"ذخیره شد\n{res.get('path')}")
                win.destroy()
            return final

        def on_save_load():
            final = on_save(close=False)
            if final is None:
                return
            win.destroy()
            self._load_model(Path(final))

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=12, pady=10)
        ctk.CTkButton(bar, text="ذخیره", fg_color="#22c55e", command=lambda: on_save(True)).pack(side="right", padx=4)
        ctk.CTkButton(bar, text="ذخیره و لود", fg_color="#6c5ce7", command=on_save_load).pack(side="right", padx=4)
        ctk.CTkButton(bar, text="انصراف", fg_color="#555", command=win.destroy).pack(side="right", padx=4)
        ctk.CTkLabel(bar, text=str(meta_path_for(path)), text_color="gray").pack(side="left")

    # ---------- AGENT ----------
    def _build_agent_page(self):

        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["agent"] = fr
        ctk.CTkLabel(fr, text="ایجنت و ابزارها", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=8)
        ctk.CTkLabel(
            fr,
            text="Agent را از صفحه چت روشن کنید. ابزارها با Approval Gate اجرا می‌شوند.\n"
            "لایه‌ها: کدنویسی، وب (allowlist)، حافظه/todo، سیستم (elevated).",
            justify="right",
            anchor="w",
        ).pack(anchor="w", pady=6)
        self.elev_var = ctk.BooleanVar(value=bool((load_approval_policy() or {}).get("elevated_mode")))
        ctk.CTkSwitch(fr, text="حالت مدیر سیستم (elevated_mode)", variable=self.elev_var, command=self._save_elevated).pack(
            anchor="w", pady=10
        )
        ctk.CTkButton(fr, text="لیست ابزارها در چت", command=lambda: (self.show_page("chat"), self._log_chat("system", tools_schema_for_prompt()[:3000]))).pack(
            anchor="w", pady=6
        )
        ctk.CTkButton(fr, text="ساخت اسکلت پروژه نمونه", command=self._scaffold_demo).pack(anchor="w", pady=6)

    def _save_elevated(self):
        path = ROOT / "profiles" / "approval_policy.json"
        try:
            pol = load_approval_policy() or {}
            pol["elevated_mode"] = bool(self.elev_var.get())
            path.write_text(json.dumps(pol, ensure_ascii=False, indent=2), encoding="utf-8")
            messagebox.showinfo("تنظیمات", f"elevated_mode = {pol['elevated_mode']}")
        except Exception as e:
            messagebox.showerror("خطا", str(e))

    def _scaffold_demo(self):
        r = execute_tool("build_scaffold", {"name": "demo-app", "template": "python-cli"})
        messagebox.showinfo("Scaffold", json.dumps(r, ensure_ascii=False, indent=2)[:1500])

    # ---------- OSINT ----------
    def _build_osint_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["osint"] = fr
        fr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(fr, text="OSINT سریع", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=8)
        ctk.CTkLabel(fr, text="هدف (دامنه / ایمیل / یوزرنیم)").grid(row=1, column=0, sticky="w")
        self.osint_target = ctk.CTkEntry(fr, width=360)
        self.osint_target.grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        row = ctk.CTkFrame(fr, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=2, sticky="w", pady=8)
        ctk.CTkButton(row, text="Domain graph", command=lambda: self._osint_run("osint_domain_graph")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Email DNS", command=lambda: self._osint_run("osint_email_dns")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="Username", command=lambda: self._osint_run("osint_username_parallel")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="crt.sh", command=lambda: self._osint_run("osint_crtsh")).pack(side="left", padx=4)
        ctk.CTkButton(row, text="ذخیره گزارش HTML", command=self._osint_save_report).pack(side="left", padx=12)
        self.osint_out = ctk.CTkTextbox(fr, height=360)
        self.osint_out.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=8)
        fr.grid_rowconfigure(3, weight=1)

    def _osint_run(self, tool: str):
        target = (self.osint_target.get() or "").strip()
        if not target:
            messagebox.showwarning("OSINT", "هدف را وارد کنید")
            return
        args = {}
        if tool == "osint_email_dns":
            args = {"email": target}
        elif tool in ("osint_domain_graph", "osint_crtsh"):
            args = {"domain": target}
        elif tool == "osint_username_parallel":
            args = {"username": target}
        self.osint_out.delete("1.0", "end")
        self.osint_out.insert("end", "در حال اجرا…\n")

        def work():
            res = execute_tool(tool, args)
            self.after(0, lambda: self._osint_done(res))

        threading.Thread(target=work, daemon=True).start()

    def _osint_save_report(self):
        target = (self.osint_target.get() or "target").strip()
        raw = self.osint_out.get("1.0", "end").strip()
        try:
            data = json.loads(raw) if raw.startswith("{") else {"raw": raw}
        except Exception:
            data = {"raw": raw}
        res = execute_tool(
            "generate_osint_report",
            {"name": f"ui_{int(datetime.now().timestamp())}", "target": target, "data_json": data},
        )
        messagebox.showinfo("گزارش", json.dumps(res, ensure_ascii=False, indent=2)[:1200])

    def _osint_done(self, res: dict):

        self.osint_out.delete("1.0", "end")
        self.osint_out.insert("end", json.dumps(res, ensure_ascii=False, indent=2))

    # ---------- SETTINGS ----------
    def _build_settings_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["settings"] = fr
        ctk.CTkLabel(fr, text="تنظیمات", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=8)
        ctk.CTkLabel(fr, text=f"مسیر نصب:\n{ROOT}", justify="left").pack(anchor="w", pady=4)
        ctk.CTkLabel(fr, text=f"Workspace:\n{workspace_dir()}", justify="left").pack(anchor="w", pady=4)
        ctk.CTkButton(fr, text="باز کردن workspace", command=lambda: self._open_path(workspace_dir())).pack(anchor="w", pady=6)
        ctk.CTkButton(fr, text="باز کردن models", command=lambda: self._open_path(ROOT / "models")).pack(anchor="w", pady=4)
        ctk.CTkButton(fr, text="باز کردن bin", command=lambda: self._open_path(ROOT / "bin")).pack(anchor="w", pady=4)
        ctk.CTkButton(fr, text="ویزارد اولین اجرا", command=self._launch_wizard).pack(anchor="w", pady=6)
        ctk.CTkButton(fr, text="پشتیبان سشن‌ها", command=self._backup_sessions).pack(anchor="w", pady=4)
        ctk.CTkButton(fr, text="بازیابی بکاپ سشن", command=self._restore_backup).pack(anchor="w", pady=4)
        ctk.CTkButton(fr, text="بررسی به‌روزرسانی", command=self._check_update).pack(anchor="w", pady=4)
        ctk.CTkButton(fr, text="دانلود Setup جدید", command=self._download_update).pack(anchor="w", pady=4)
        ctk.CTkButton(fr, text="لاگ Audit ابزارها", command=self._show_audit).pack(anchor="w", pady=4)
        ctk.CTkLabel(fr, text="Router دو‌مدله", font=ctk.CTkFont(weight="bold")).pack(anchor="e", pady=(12, 2))
        self.router_sw = ctk.CTkSwitch(fr, text="فعال", command=self._toggle_router)
        self.router_sw.pack(anchor="e", pady=2)
        self.router_tool_var = ctk.StringVar(value=str(load_router_config().get("tool_model") or ""))
        ctk.CTkEntry(fr, textvariable=self.router_tool_var, placeholder_text="نام فایل مدل ابزار در models", width=320).pack(anchor="e", pady=4)
        ctk.CTkButton(fr, text="ذخیره Router", command=self._save_router_ui).pack(anchor="e", pady=4)
        ctk.CTkLabel(fr, text="تم ظاهر (فارسی RTL در برچسب‌ها)").pack(anchor="e", pady=(12, 2))
        self.theme_var = ctk.StringVar(value="dark")
        ctk.CTkOptionMenu(fr, variable=self.theme_var, values=["dark", "light", "system"], command=lambda m: ctk.set_appearance_mode(m)).pack(anchor="w", pady=4)
        ctk.CTkLabel(fr, text="پروفایل چت (دکمه‌ای)").pack(anchor="e", pady=(12, 2))
        prow = ctk.CTkFrame(fr, fg_color="transparent")
        prow.pack(anchor="e", pady=4)
        for pn in ("default", "coder", "trader", "osint"):
            ctk.CTkButton(prow, text=pn, width=80, command=lambda n=pn: self._apply_profile(n)).pack(side="left", padx=3)
        profiles = ["default", "coder", "trader", "osint"]
        for pp in (ROOT / "profiles").glob("*.json"):
            if pp.stem not in profiles and pp.stem not in ("approval_policy", "models_map", "router_config", "identity"):
                profiles.append(pp.stem)
        self.profile_var = ctk.StringVar(value="default")
        ctk.CTkOptionMenu(fr, variable=self.profile_var, values=profiles, command=self._apply_profile).pack(anchor="w", pady=4)
        ctk.CTkLabel(
            fr,
            text="هویت پیش‌فرض: profiles/identity.json — «پدرت کیه» → مهدی",
            text_color="gray",
        ).pack(anchor="w", pady=12)

    def _open_path(self, p: Path):
        import os
        import subprocess
        p.mkdir(parents=True, exist_ok=True)
        try:
            if hasattr(os, "startfile"):
                os.startfile(str(p))
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            messagebox.showerror("خطا", str(e))

    def _apply_profile(self, name: str):
        try:
            prof = load_profile(name)
            self.system_prompt = prof.get("system") or self.system_prompt
            if prof.get("temperature") is not None:
                self.cfg["temperature"] = prof.get("temperature")
            if prof.get("max_tokens") is not None:
                self.cfg["max_tokens"] = prof.get("max_tokens")
            self.set_status(f"پروفایل: {name}")
            messagebox.showinfo("پروفایل", f"اعمال شد: {name}")
        except Exception as e:
            messagebox.showerror("پروفایل", str(e))

    def _launch_wizard(self):

        try:
            from first_run_wizard import FirstRunWizard
            w = FirstRunWizard(force=True)
            w.grab_set()
        except Exception as e:
            messagebox.showerror("ویزارد", str(e))

    # ---------- DOCTOR ----------
    def _build_doctor_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["doctor"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(fr, text="تشخیص محیط", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w")
        self.doctor_box = ctk.CTkTextbox(fr)
        self.doctor_box.grid(row=1, column=0, sticky="nsew", pady=8)
        ctk.CTkButton(fr, text="اجرای دوباره Doctor", command=self._run_doctor_ui).grid(row=2, column=0, sticky="w")

    def _run_doctor_ui(self):
        self.doctor_box.delete("1.0", "end")
        self.doctor_box.insert("end", "در حال بررسی…\n")

        def work():
            try:
                from doctor import run_doctor, model_wizard
                res = run_doctor()
                wiz = model_wizard()
            except Exception as e:
                res = {"summary_fa": str(e), "findings": []}
                wiz = {}
            self.after(0, lambda: self._fill_doctor(res, wiz))

        threading.Thread(target=work, daemon=True).start()

    def _fill_doctor(self, res: dict, wiz: dict):
        self.doctor_box.delete("1.0", "end")
        self.doctor_box.insert("end", (res.get("summary_fa") or "") + "\n\n")
        for f in res.get("findings") or []:
            self.doctor_box.insert("end", f"[{f.get('level')}] {f.get('msg')}\n")
            if f.get("fix"):
                self.doctor_box.insert("end", f"   → {f['fix']}\n")
        if wiz:
            self.doctor_box.insert("end", "\n--- ویزارد مدل ---\n")
            for s in wiz.get("steps_fa") or []:
                self.doctor_box.insert("end", s + "\n")

    def set_status(self, text: str):
        try:
            self.status.configure(text=f"  ●  {text}")
        except Exception:
            pass


    def _try_autoload_model(self):
        """Load last model from config.ini if present."""
        name = (self.cfg.get("model") or "").strip()
        if not name:
            self.set_status("مدلی انتخاب نشده — از کشو «مدل‌ها» اضافه کنید")
            return
        path = ROOT / "models" / name
        if not path.exists():
            models = list_models()
            if models:
                path = models[0]
            else:
                self.set_status("هیچ فایل .gguf در models نیست")
                return
        self.set_status(f"لود خودکار: {path.name}…")
        self._load_model(path)

    def _export_chat(self):
        if not self.messages:
            messagebox.showinfo("خروجی", "پیامی برای خروجی نیست")
            return
        dest = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("JSON", "*.json")],
            initialfile=f"chat_{self.session_id}.md",
        )
        if not dest:
            return
        path = Path(dest)
        try:
            if path.suffix.lower() == ".json":
                path.write_text(json.dumps(self.messages, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                lines = [f"# Chat {self.session_id}", ""]
                for m in self.messages:
                    lines.append(f"## {m.get('role')}\n\n{m.get('content', '')}\n")
                path.write_text("\n".join(lines), encoding="utf-8")
            messagebox.showinfo("خروجی", f"ذخیره شد:\n{path}")
        except Exception as e:
            messagebox.showerror("خطا", str(e))

    def _clear_chat_view(self):
        self.chat_box.configure(state="normal")
        self.chat_box.delete("1.0", "end")
        self.chat_box.configure(state="disabled")



    def _on_models_filter_key(self, event=None):
        if self._filter_after is not None:
            try:
                self.after_cancel(self._filter_after)
            except Exception:
                pass
        self._filter_after = self.after(180, self._refresh_models_ui)

    
    def _build_git_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["git"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_rowconfigure(4, weight=1)
        ctk.CTkLabel(fr, text="Git پیشرفته و فایل‌های بزرگ", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        # row actions
        r1 = ctk.CTkFrame(fr, fg_color="transparent")
        r1.grid(row=1, column=0, sticky="ew", pady=4)
        for label, cmd in [
            ("Status", lambda: self._git_tool("git_status")),
            ("Diff", lambda: self._git_tool("git_diff")),
            ("Log", lambda: self._git_tool("git_log")),
            ("Branches", lambda: self._git_tool("git_branch_list")),
            ("Remotes", lambda: self._git_tool("git_remote_list")),
            ("Init", lambda: self._git_tool("git_init")),
        ]:
            ctk.CTkButton(r1, text=label, width=90, command=cmd).pack(side="left", padx=3)
        r2 = ctk.CTkFrame(fr, fg_color="transparent")
        r2.grid(row=2, column=0, sticky="ew", pady=4)
        self.git_branch = ctk.CTkEntry(r2, placeholder_text="نام branch", width=160)
        self.git_branch.pack(side="left", padx=3)
        ctk.CTkButton(r2, text="Checkout", width=90, command=self._git_checkout).pack(side="left", padx=3)
        ctk.CTkButton(r2, text="Branch جدید", width=100, command=self._git_new_branch).pack(side="left", padx=3)
        self.git_msg = ctk.CTkEntry(r2, placeholder_text="پیام commit…", width=220)
        self.git_msg.pack(side="left", padx=8)
        ctk.CTkButton(r2, text="Commit", width=90, fg_color=UI["accent"], command=self._git_commit).pack(side="left", padx=3)
        r3 = ctk.CTkFrame(fr, fg_color="transparent")
        r3.grid(row=3, column=0, sticky="ew", pady=4)
        ctk.CTkButton(r3, text="ساخت/به‌روز .gitignore", command=lambda: self._git_tool("git_ensure_gitignore")).pack(side="left", padx=3)
        ctk.CTkButton(r3, text="اسکن فایل‌های بزرگ", fg_color="#6c5ce7", command=self._scan_large).pack(side="left", padx=3)
        ctk.CTkButton(r3, text="Git LFS وضعیت", command=lambda: self._git_tool("git_lfs_hint")).pack(side="left", padx=3)
        ctk.CTkButton(r3, text='LFS track *.gguf', command=lambda: self._git_tool("git_lfs_track", {"pattern": "*.gguf"})).pack(side="left", padx=3)
        ctk.CTkButton(r3, text="Stash", command=lambda: self._git_tool("git_stash", {"message": "hub"})).pack(side="left", padx=3)
        self.git_out = ctk.CTkTextbox(fr)
        self.git_out.grid(row=4, column=0, sticky="nsew", pady=8)

    def _refresh_git(self):
        self._git_tool("git_status")

    def _git_tool(self, name: str, args: dict | None = None):
        self.git_out.delete("1.0", "end")
        self.git_out.insert("end", f"→ {name}…\n")
        self.set_status(f"Git: {name}")
        args = args or {}

        def work():
            res = execute_tool(name, args)
            text = json.dumps(res, ensure_ascii=False, indent=2)
            if isinstance(res, dict):
                if res.get("stdout"):
                    text = str(res.get("stdout")) + "\n\n" + text
                if res.get("stderr"):
                    text += "\nSTDERR:\n" + str(res.get("stderr"))
                if res.get("files"):
                    lines = [f"{x.get('size_mb')} MB\t{x.get('path')}" for x in res["files"]]
                    text = "فایل‌های بزرگ:\n" + "\n".join(lines) + "\n\n" + text
            self.after(0, lambda: self._git_done(text))

        threading.Thread(target=work, daemon=True).start()

    def _git_done(self, text: str):
        self.git_out.delete("1.0", "end")
        self.git_out.insert("end", text[:50000])
        self.set_status("Git آماده")

    def _git_commit(self):
        msg = (self.git_msg.get() or "").strip()
        if not msg:
            messagebox.showwarning("Git", "پیام commit را بنویسید")
            return
        if not messagebox.askyesno("Git", f"Commit با پیام:\n{msg}"):
            return
        self._git_tool("git_commit", {"message": msg})

    def _git_checkout(self):
        name = (self.git_branch.get() or "").strip()
        if not name:
            messagebox.showwarning("Git", "نام branch را بنویسید")
            return
        self._git_tool("git_checkout", {"name": name})

    def _git_new_branch(self):
        name = (self.git_branch.get() or "").strip()
        if not name:
            messagebox.showwarning("Git", "نام branch جدید را بنویسید")
            return
        self._git_tool("git_branch_create", {"name": name, "checkout": True})

    def _scan_large(self):
        self._git_tool("large_files_scan", {"min_mb": 10.0, "limit": 50})



    def _build_rag_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["rag"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(fr, text="RAG محلی (کتاب‌ها / workspace)", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w")
        row = ctk.CTkFrame(fr, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", pady=8)
        ctk.CTkButton(row, text="ساخت ایندکس", fg_color=UI["accent"], command=self._rag_build).pack(side="left", padx=4)
        self.rag_q = ctk.CTkEntry(row, placeholder_text="جستجو در دانش محلی…", width=320)
        self.rag_q.pack(side="left", padx=8)
        ctk.CTkButton(row, text="جستجو", command=self._rag_search).pack(side="left", padx=4)
        self.rag_out = ctk.CTkTextbox(fr)
        self.rag_out.grid(row=2, column=0, sticky="nsew", pady=8)

    def _rag_build(self):
        self.rag_out.delete("1.0", "end")
        self.rag_out.insert("end", "در حال ایندکس…\n")
        self.set_status("RAG build…")

        def work():
            res = execute_tool("workspace_rag_rebuild", {"max_docs": 800})
            if not res.get("ok"):
                res = execute_tool("rag_build_index", {"max_docs": 800})
            self.after(0, lambda: (self.rag_out.delete("1.0", "end"), self.rag_out.insert("end", json.dumps(res, ensure_ascii=False, indent=2)[:12000]), self.set_status("RAG آماده")))

        threading.Thread(target=work, daemon=True).start()

    def _rag_search(self):
        q = (self.rag_q.get() or "").strip()
        if not q:
            return
        self.rag_out.delete("1.0", "end")
        self.rag_out.insert("end", "جستجو…\n")

        def work():
            res = execute_tool("workspace_rag_search", {"query": q, "top_k": 6})
            if not res.get("ok"):
                res = execute_tool("rag_search", {"query": q, "top_k": 6})
            self.after(0, lambda: (self.rag_out.delete("1.0", "end"), self.rag_out.insert("end", json.dumps(res, ensure_ascii=False, indent=2)[:12000])))

        threading.Thread(target=work, daemon=True).start()

    def _build_plugins_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["plugins"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(fr, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(top, text="پلاگین‌ها", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(top, text="تازه‌سازی", width=90, command=self._refresh_plugins).pack(side="right", padx=4)
        self.plugins_list = ctk.CTkScrollableFrame(fr)
        self.plugins_list.grid(row=1, column=0, sticky="nsew", pady=8)
        self.plugins_out = ctk.CTkTextbox(fr, height=160)
        self.plugins_out.grid(row=2, column=0, sticky="ew")

    def _refresh_plugins(self):
        for w in self.plugins_list.winfo_children():
            w.destroy()
        res = execute_tool("plugin_list", {})
        items = (res.get("plugins") or res.get("items") or []) if isinstance(res, dict) else []
        if not items:
            ctk.CTkLabel(self.plugins_list, text="پلاگینی یافت نشد (workspace/plugins).", text_color="gray").pack(anchor="w")
            return
        for it in items:
            name = it if isinstance(it, str) else (it.get("name") or it.get("id") or str(it))
            row = ctk.CTkFrame(self.plugins_list)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=str(name), anchor="w").pack(side="left", padx=8)
            ctk.CTkButton(row, text="اجرا", width=70, command=lambda n=name: self._run_plugin(n)).pack(side="right", padx=4)

    def _run_plugin(self, name: str):
        self.plugins_out.delete("1.0", "end")
        self.plugins_out.insert("end", f"Running {name}…\n")

        def work():
            res = execute_tool("plugin_run", {"name": name})
            self.after(0, lambda: (self.plugins_out.delete("1.0", "end"), self.plugins_out.insert("end", json.dumps(res, ensure_ascii=False, indent=2)[:8000])))

        threading.Thread(target=work, daemon=True).start()


    def _maybe_first_run(self):
        marker = ROOT / "workspace" / ".first_run_done.json"
        if marker.exists():
            return
        # non-blocking tip only — full wizard from settings
        try:
            if messagebox.askyesno(
                "اولین اجرا",
                "به نظر می‌رسد اولین اجراست.\nویزارد را باز کنیم؟\n(بعداً از تنظیمات هم می‌توانید)",
            ):
                self._launch_wizard()
        except Exception:
            pass

    def _server_status(self):
        import subprocess
        server = ROOT / "bin" / "llama-server.exe"
        lines = [
            f"llama-server.exe: {'موجود' if server.exists() else 'نیست'}",
            f"مسیر: {server}",
            "",
            "چت Hub از llama-cpp-python داخل‌پردازه‌ای استفاده می‌کند.",
            "llama-server برای API جدا / چند کلاینت اختیاری است.",
        ]
        if server.exists():
            try:
                lines.append(f"حجم: {server.stat().st_size / (1024*1024):.1f} MB")
            except OSError:
                pass
        # offer start if model loaded and server exists
        if not server.exists():
            messagebox.showinfo("Server", "\n".join(lines))
            return
        model = self.engine.model_path
        choice = messagebox.askyesnocancel(
            "llama-server",
            "\n".join(lines)
            + "\n\nYes = شروع سرور با مدل فعلی (اگر لود شده)"
            + "\nNo = فقط وضعیت"
            + "\nCancel = بستن",
        )
        if choice is True:
            if not model:
                messagebox.showwarning("Server", "اول یک مدل لود کنید.")
                return
            try:
                # start detached
                ngl = _safe_int(self.ngl_var.get() if hasattr(self, "ngl_var") else 0, 0, -1, 999)
                args = [str(server), "-m", str(model), "--port", "8080",
                        "-c", str(_safe_int(self.ctx_var.get() if hasattr(self, "ctx_var") else 4096, 4096, 512, 131072)),
                        "-ngl", str(ngl)]
                kwargs = {"cwd": str(ROOT)}
                if hasattr(subprocess, "CREATE_NEW_CONSOLE"):
                    kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
                subprocess.Popen(args, **kwargs)
                self.set_status("llama-server شروع شد روی :8080")
                messagebox.showinfo("Server", "شروع شد:\n" + " ".join(args) + "\n\nhttp://127.0.0.1:8080")
            except Exception as e:
                messagebox.showerror("Server", str(e))
        elif choice is False:
            messagebox.showinfo("Server", "\n".join(lines))


    def _build_about_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["about"] = fr
        ctk.CTkLabel(fr, text="درباره ما", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="e", pady=8)
        about = (
            "تیم توسعه نو\n\n"
            "مهدی طریری\n"
            "mahditariri@gmail.com\n\n"
            "محسن طریفی\n"
            "Masean.us@gmail.com\n"
        )
        box = ctk.CTkTextbox(fr, height=280)
        box.pack(fill="both", expand=True, pady=8)
        box.insert("1.0", about)
        box.configure(state="disabled")
        ctk.CTkButton(fr, text="باز کردن پوشه پروژه", command=lambda: self._open_path(ROOT)).pack(anchor="e", pady=6)

    def _unload_model(self):
        self.engine.unload()
        self.model_label.configure(text="مدل: —")
        self._refresh_topbar()
        self.set_status("مدل تخلیه شد")
        self.load_status.configure(text="مدل تخلیه شد")

    def _inspect_selected(self):
        from model_meta import read_gguf_metadata, estimate_ram_gb
        models = list_models()
        if not models:
            messagebox.showinfo("مدل", "مدلی نیست")
            return
        # prefer loaded
        path = self.engine.model_path or models[0]
        meta = read_gguf_metadata(path)
        ngl = _safe_int(self.ngl_var.get() if hasattr(self, "ngl_var") else 0, 0, -1, 999)
        ctx = _safe_int(self.ctx_var.get() if hasattr(self, "ctx_var") else 4096, 4096, 512, 131072)
        est = estimate_ram_gb(meta.get("size_bytes") or 0, ctx, ngl)
        msg = (
            f"فایل: {meta.get('name')}\n"
            f"حجم: {meta.get('size_gb')} GB\n"
            f"معماری: {meta.get('architecture') or '—'}\n"
            f"ctx متا: {meta.get('context_length') or '—'}\n"
            f"برآورد RAM: {est.get('ram_gb_est')} GB\n"
            f"برآورد VRAM: {est.get('vram_gb_est')} GB\n"
            f"{est.get('note_fa') or ''}"
        )
        messagebox.showinfo("اطلاعات مدل", msg)


    def _backup_sessions(self):
        res = execute_tool("backup_sessions", {})
        messagebox.showinfo("پشتیبان", json.dumps(res, ensure_ascii=False, indent=2)[:1500])

    def _check_update(self):
        res = execute_tool("update_check", {})
        messagebox.showinfo("به‌روزرسانی", json.dumps(res, ensure_ascii=False, indent=2)[:1500])

    def _show_audit(self):
        import time as _t
        res = execute_tool("audit_list", {"limit": 40})
        items = res.get("items") or []
        lines = []
        for it in items[:40]:
            ts = it.get("ts")
            try:
                tstr = _t.strftime("%H:%M:%S", _t.localtime(float(ts)))
            except Exception:
                tstr = "?"
            lines.append(f"{tstr}  {it.get('tool')}  ok={it.get('ok')}  err={it.get('error') or '-'}")
        messagebox.showinfo("Audit", ("\n".join(lines) if lines else "خالی")[:2500])


    def _toggle_router(self):
        cfg = load_router_config()
        cfg["enabled"] = bool(self.router_sw.get())
        cfg["tool_model"] = (self.router_tool_var.get() or "").strip()
        save_router_config(cfg)
        self.router.cfg = cfg
        if not cfg["enabled"]:
            self.router.unload()
        self.set_status("Router: " + ("ON" if cfg["enabled"] else "OFF"))

    def _save_router_ui(self):
        cfg = load_router_config()
        cfg["tool_model"] = (self.router_tool_var.get() or "").strip()
        cfg["enabled"] = bool(self.router_sw.get()) if hasattr(self, "router_sw") else cfg.get("enabled")
        save_router_config(cfg)
        self.router.cfg = cfg
        messagebox.showinfo("Router", "ذخیره شد. مدل ابزار باید در models\\ باشد.")

    def _sync_router_switch(self):
        try:
            cfg = load_router_config()
            if hasattr(self, "router_sw"):
                if cfg.get("enabled"):
                    self.router_sw.select()
                else:
                    self.router_sw.deselect()
            if hasattr(self, "router_tool_var"):
                self.router_tool_var.set(str(cfg.get("tool_model") or ""))
        except Exception:
            pass


    def _build_term_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="#0c0c0c")
        self.pages["term"] = fr
        fr.grid_columnconfigure(0, weight=1)
        fr.grid_rowconfigure(2, weight=1)
        top = ctk.CTkFrame(fr, fg_color="#0c0c0c")
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(top, text="AI Terminal  —  شبیه لینوکس + چت", text_color="#33ff33",
                     font=ctk.CTkFont(family="Consolas", size=14, weight="bold")).pack(side="left", padx=6)
        # quick command buttons (no typing)
        row1 = ctk.CTkFrame(top, fg_color="#0c0c0c")
        row1.pack(side="left", fill="x", expand=True)
        for lab, cmd in [
            ("help", "help"),
            ("ls", "ls"),
            ("pwd", "pwd"),
            ("cd ~", "cd ~"),
            ("models", "models"),
            ("clear", "clear"),
            ("status", "status"),
            ("doctor", "doctor"),
            ("whoami", "whoami"),
        ]:
            ctk.CTkButton(
                row1, text=lab, width=70, height=28,
                fg_color="#1a1a1a", text_color="#33ff33", border_width=1, border_color="#33ff33",
                command=lambda c=cmd: self._term_run_line(c),
            ).pack(side="left", padx=2, pady=1)
        self._term_btn_row2 = ctk.CTkFrame(fr, fg_color="#0c0c0c")
        self._term_btn_row2.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 4))
        for lab, cmd in [
            ("AI: سلام", "سلام، خودت را معرفی کن"),
            ("AI: کمک", "چه کارهایی می‌توانی انجام بدهی؟"),
            ("خلاصه", "گفتگوی تا اینجا را خلاصه کن"),
            ("کد Python", "یک hello world کوتاه پایتون بنویس"),
            ("OSINT؟", "ابزارهای OSINT دفاعی را فهرست کن"),
            ("Git؟", "دستورات git داخل این برنامه چیست؟"),
            ("Agent ON", "agent on"),
            ("سشن جدید", "/new"),
        ]:
            ctk.CTkButton(
                self._term_btn_row2, text=lab, width=100, height=28,
                fg_color="#102010", text_color="#66ff66", border_width=1, border_color="#2a5a2a",
                command=lambda c=cmd: self._term_run_line(c),
            ).pack(side="left", padx=2, pady=2)
        self.term_out = ctk.CTkTextbox(
            fr, fg_color="#0c0c0c", text_color="#33ff33",
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.term_out.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        self.term_out.insert("1.0", self._term_banner())
        self.term_out.configure(state="disabled")
        row = ctk.CTkFrame(fr, fg_color="#0c0c0c")
        row.grid(row=3, column=0, sticky="ew", padx=4, pady=4)
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text="user@gguf:~$", text_color="#33ff33",
                     font=ctk.CTkFont(family="Consolas", size=13, weight="bold")).grid(row=0, column=0, padx=(4, 6))
        self.term_in = ctk.CTkEntry(
            row, fg_color="#1a1a1a", text_color="#33ff33", border_color="#33ff33",
            font=ctk.CTkFont(family="Consolas", size=13),
        )
        self.term_in.grid(row=0, column=1, sticky="ew")
        self.term_in.bind("<Return>", lambda e: self._term_submit())
        ctk.CTkButton(row, text="Enter", width=70, fg_color="#1a1a1a", text_color="#33ff33",
                      border_width=1, border_color="#33ff33", command=self._term_submit).grid(row=0, column=2, padx=4)
        self.term_cwd = workspace_dir()
        self._restored_session = False
        self.term_history: list[str] = []
        self.term_hist_idx = -1
        self.term_in.bind("<Up>", self._term_hist_up)
        self.term_in.bind("<Down>", self._term_hist_down)

    def _term_banner(self) -> str:
        return (
            "GGUF Runner AI Terminal v" + APP_VERSION + "\n"
            "دکمه‌های بالا بدون تایپ‌اند. برای چت با AI هر متنی غیر از دستور را بنویسید.\n"
            "دستورات: help ls pwd cd models clear doctor status cat <file> whoami\n"
            "Agent/OSINT از صفحه چت؛ اینجا shell سبک روی workspace است.\n\n"
        )

    def _term_write(self, text: str):
        self.term_out.configure(state="normal")
        self.term_out.insert("end", text)
        self.term_out.see("end")
        self.term_out.configure(state="disabled")

    def _term_submit(self):
        line = (self.term_in.get() or "").strip()
        self.term_in.delete(0, "end")
        if not line:
            return
        self._term_run_line(line)

    def _term_run_line(self, line: str):
        self._term_write(f"user@gguf:{self._term_prompt_path()}$ {line}\n")
        self.term_history.append(line)
        low = line.strip().lower()
        parts = line.split()
        cmd = parts[0].lower() if parts else ""

        if cmd in ("help", "?"):
            self._term_write(
                "help  ls  pwd  cd <dir>  models  clear  doctor  status  whoami\n"
                "cat <file>  (نسبت به workspace)\n"
                "هر چیز دیگر → ارسال به مدل AI (اگر لود شده باشد)\n"
            )
            return
        if cmd == "clear":
            self.term_out.configure(state="normal")
            self.term_out.delete("1.0", "end")
            self.term_out.insert("1.0", self._term_banner())
            self.term_out.configure(state="disabled")
            return
        if cmd == "pwd":
            self._term_write(str(self.term_cwd) + "\n")
            return
        if cmd == "whoami":
            self._term_write("user (GGUF Hub)\n")
            return
        if cmd == "status":
            m = self.engine.model_path.name if self.engine.model_path else "none"
            self._term_write(f"model={m} agent={self.agent_mode} osint={self.osint_mode} cwd={self.term_cwd}\n")
            return
        if cmd == "models":
            ms = list_models()
            if not ms:
                self._term_write("(no .gguf in models/) — از صفحه مدل‌ها دکمه افزودن را بزنید\n")
            else:
                for i, m in enumerate(ms):
                    self._term_write(f"  [{i}] {m.name}\n")
            return
        if cmd == "ls":
            try:
                target = self.term_cwd
                if len(parts) > 1:
                    target = (self.term_cwd / parts[1]).resolve()
                    if not str(target).startswith(str(workspace_dir().resolve())):
                        self._term_write("permission denied (outside workspace)\n")
                        return
                for x in sorted(target.iterdir()):
                    mark = "/" if x.is_dir() else ""
                    self._term_write(f"  {x.name}{mark}\n")
            except Exception as e:
                self._term_write(f"error: {e}\n")
            return
        if cmd == "cd":
            if len(parts) < 2 or parts[1] == "~":
                self.term_cwd = workspace_dir()
                self._term_write(str(self.term_cwd) + "\n")
                return
            try:
                nxt = (self.term_cwd / parts[1]).resolve()
                if not str(nxt).startswith(str(workspace_dir().resolve())):
                    self._term_write("permission denied (outside workspace)\n")
                    return
                if nxt.is_dir():
                    self.term_cwd = nxt
                    self._term_write(str(self.term_cwd) + "\n")
                else:
                    self._term_write("not a directory\n")
            except Exception as e:
                self._term_write(f"error: {e}\n")
            return
        if cmd == "cat" and len(parts) > 1:
            try:
                fp = (self.term_cwd / parts[1]).resolve()
                if not str(fp).startswith(str(workspace_dir().resolve())):
                    self._term_write("permission denied\n")
                    return
                data = fp.read_text(encoding="utf-8", errors="replace")[:8000]
                self._term_write(data + ("\n" if not data.endswith("\n") else ""))
            except Exception as e:
                self._term_write(f"error: {e}\n")
            return
        if low in ("agent on", "agent"):
            self.agent_mode = True
            try:
                self.agent_sw.select()
            except Exception:
                pass
            self._term_write("agent=ON\n")
            return
        if low in ("agent off",):
            self.agent_mode = False
            try:
                self.agent_sw.deselect()
            except Exception:
                pass
            self._term_write("agent=OFF\n")
            return
        if low in ("/new", "new"):
            self.messages.clear()
            self._term_write("session cleared\n")
            return
        if cmd == "doctor":
            res = execute_tool("doctor", {})
            self._term_write((res.get("summary_fa") or "") + "\n")
            for f in (res.get("findings") or [])[:12]:
                self._term_write(f"  [{f.get('level')}] {f.get('msg')}\n")
            return

        # AI chat path
        if not self.engine.llm:
            self._term_write("no model loaded. صفحه «مدل‌ها» → لود (دکمه‌ای)\n")
            return
        self._term_write("AI thinking...\n")
        self.messages.append({"role": "user", "content": line})
        self.busy = True

        def work():
            try:
                stream = self.engine.chat(
                    self._build_messages(),
                    temperature=_safe_float(self.cfg.get("temperature", 0.7), 0.7, 0, 2),
                    max_tokens=_safe_int(self.cfg.get("max_tokens", 1024), 1024, 16, 8192),
                    top_p=_safe_float(self.cfg.get("top_p", 0.9), 0.9, 0, 1),
                    stream=True,
                )
                full = []
                for chunk in stream:
                    if not self.busy:
                        break
                    try:
                        delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                        tok = delta.get("content") or ""
                    except Exception:
                        tok = ""
                    if tok:
                        full.append(tok)
                        self.after(0, lambda t=tok: self._term_write(t))
                reply = "".join(full).strip()
                if reply:
                    self.messages.append({"role": "assistant", "content": reply})
                if len(self.messages) > 300:
                    self.messages = self.messages[-200:]
                self.after(0, lambda: self._term_write("\n"))
            except Exception as e:
                self.after(0, lambda: self._term_write(f"\nerror: {e}\n"))
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()

    def _term_prompt_path(self) -> str:
        try:
            rel = self.term_cwd.relative_to(workspace_dir())
            s = str(rel).replace("\\", "/")
            return "~" if s in (".", "") else "~/" + s
        except Exception:
            return "~"


    def _try_chat_command(self, text: str) -> bool:
        """If message is a terminal-like command, run it in chat (no model needed)."""
        line = (text or "").strip()
        if not line:
            return False
        low = line.lower()
        parts = line.split()
        cmd = parts[0].lower() if parts else ""
        builtins = {
            "help", "?", "ls", "pwd", "cd", "models", "clear", "status", "doctor",
            "whoami", "cat", "agent", "new",
        }
        # single-token or known multi
        if cmd not in builtins and not low.startswith("cd ") and not low.startswith("cat "):
            if low not in ("agent on", "agent off", "/new"):
                return False
        self._log_chat("user", line)
        # ensure term_cwd
        if not hasattr(self, "term_cwd") or self.term_cwd is None:
            self.term_cwd = workspace_dir()
        self._restored_session = False

        def out(msg: str):
            self._log_chat("system", msg)

        if cmd in ("help", "?"):
            out(
                "دستورات چت/ترمینال:\n"
                "help · ls · pwd · cd <dir> · models · clear · status · doctor · whoami\n"
                "cat <file> · agent on/off · new\n"
                "دکمه‌های میانبر در صفحه ترمینال هم همین‌ها را می‌زنند.\n"
                "بقیه پیام‌ها → مدل AI"
            )
            return True
        if cmd == "clear" or low == "new" or low == "/new":
            self.messages.clear()
            self._clear_chat_view()
            out("سشن پاک شد")
            return True
        if cmd == "pwd":
            out(str(getattr(self, "term_cwd", workspace_dir())))
            return True
        if cmd == "whoami":
            out("user (GGUF Hub)")
            return True
        if cmd == "status":
            m = self.engine.model_path.name if self.engine.model_path else "none"
            out(f"model={m} | agent={self.agent_mode} | osint={self.osint_mode}")
            return True
        if cmd == "models":
            ms = list_models()
            out("\n".join(f"• {m.name}" for m in ms) if ms else "مدلی در models/ نیست")
            return True
        if cmd == "ls":
            try:
                target = self.term_cwd
                if len(parts) > 1:
                    target = (self.term_cwd / parts[1]).resolve()
                    if not str(target).startswith(str(workspace_dir().resolve())):
                        out("permission denied")
                        return True
                lines = []
                for x in sorted(target.iterdir()):
                    lines.append(x.name + ("/" if x.is_dir() else ""))
                out("\n".join(lines) if lines else "(empty)")
            except Exception as e:
                out(str(e))
            return True
        if cmd == "cd":
            if len(parts) < 2 or parts[1] == "~":
                self.term_cwd = workspace_dir()
            else:
                try:
                    nxt = (self.term_cwd / parts[1]).resolve()
                    if not str(nxt).startswith(str(workspace_dir().resolve())):
                        out("permission denied")
                        return True
                    if nxt.is_dir():
                        self.term_cwd = nxt
                    else:
                        out("not a directory")
                        return True
                except Exception as e:
                    out(str(e))
                    return True
            out(str(self.term_cwd))
            return True
        if cmd == "cat" and len(parts) > 1:
            try:
                fp = (self.term_cwd / parts[1]).resolve()
                if not str(fp).startswith(str(workspace_dir().resolve())):
                    out("permission denied")
                    return True
                out(fp.read_text(encoding="utf-8", errors="replace")[:8000])
            except Exception as e:
                out(str(e))
            return True
        if cmd == "doctor":
            res = execute_tool("doctor", {})
            lines = [str(res.get("summary_fa") or "")]
            for f in (res.get("findings") or [])[:15]:
                lines.append(f"[{f.get('level')}] {f.get('msg')}")
            out("\n".join(lines))
            return True
        if low in ("agent on", "agent"):
            self.agent_mode = True
            try:
                self.agent_sw.select()
            except Exception:
                pass
            out("Agent ON")
            return True
        if low == "agent off":
            self.agent_mode = False
            try:
                self.agent_sw.deselect()
            except Exception:
                pass
            out("Agent OFF")
            return True
        return False

    def _chat_cmd_btn(self, cmd: str):
        if not self._try_chat_command(cmd):
            # treat as message
            self._clear_input()
            self.input.insert(0, cmd)
            self._on_send()


    def _autosave_session(self):
        """Silent save of current chat to sessions/."""
        try:
            if not self.messages:
                return
            sessions_dir().mkdir(parents=True, exist_ok=True)
            path = sessions_dir() / f"session_{self.session_id}.json"
            title = getattr(self, "session_title", None)
            if not title:
                # derive from first user message
                for m in self.messages:
                    if m.get("role") == "user" and m.get("content"):
                        title = str(m["content"]).strip().splitlines()[0][:48]
                        break
                title = title or f"Chat {self.session_id}"
                self.session_title = title
            payload = {
                "session_id": self.session_id,
                "title": title,
                "model": str(self.engine.model_path) if self.engine.model_path else "",
                "messages": self.messages,
                "autosave": True,
                "updated": datetime.now().isoformat(timespec="seconds"),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._session_cache = []
        except Exception:
            pass


    def _restore_last_session_soft(self):
        """Load last autosaved session messages if chat empty."""
        try:
            if self._restored_session or self.messages:
                return
            self._restored_session = True
            res = execute_tool("session_restore_last", {})
            if not res.get("ok"):
                return
            data = res.get("data") or {}
            msgs = data.get("messages") or []
            if not msgs:
                return
            self.messages = list(msgs)[-80:]
            self.session_id = data.get("id") or self.session_id
            for m in self.messages[-20:]:
                self._log_chat(m.get("role") or "user", str(m.get("content") or "")[:1500])
            self.set_status("آخرین سشن بازیابی شد")
        except Exception:
            pass


    def _restore_backup(self):
        res = execute_tool("backup_restore", {})
        messagebox.showinfo("بازیابی", json.dumps(res, ensure_ascii=False, indent=2)[:1500])
        try:
            self._refresh_sessions_ui()
        except Exception:
            pass

    def _download_update(self):
        if not messagebox.askyesno("به‌روزرسانی", "دانلود آخرین Setup/zip از GitHub Releases؟"):
            return
        try:
            from update_check import download_latest_asset
            res = download_latest_asset()
        except Exception as e:
            res = {"ok": False, "error": str(e)}
        messagebox.showinfo("دانلود", json.dumps(res, ensure_ascii=False, indent=2)[:1500])

    def _build_api_page(self):
        fr = ctk.CTkFrame(self.main, fg_color="transparent")
        self.pages["api"] = fr
        ctk.CTkLabel(fr, text="API محلی (OpenAI-compatible)", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="e", pady=8)
        ctk.CTkLabel(fr, text="scripts/start_openai_api.bat — پورت 8787", text_color="gray").pack(anchor="e")
        box = ctk.CTkTextbox(fr, height=360)
        box.pack(fill="both", expand=True, pady=8)
        sample = (
            "کلید ابری لازم نیست.\n\n"
            "1) scripts\\start_openai_api.bat\n"
            "2) GET  http://127.0.0.1:8787/v1/models\n"
            "3) POST http://127.0.0.1:8787/v1/chat/completions\n\n"
            "PowerShell:\n"
            "Invoke-RestMethod http://127.0.0.1:8787/v1/models\n"
        )
        box.insert("1.0", sample)
        box.configure(state="disabled")
        ctk.CTkButton(fr, text="باز کردن پوشه scripts", command=lambda: self._open_path(ROOT / "scripts")).pack(anchor="e", pady=4)


    def _term_hist_up(self, event=None):
        if not self.term_history:
            return "break"
        if self.term_hist_idx < 0:
            self.term_hist_idx = len(self.term_history) - 1
        else:
            self.term_hist_idx = max(0, self.term_hist_idx - 1)
        self.term_in.delete(0, "end")
        self.term_in.insert(0, self.term_history[self.term_hist_idx])
        return "break"

    def _term_hist_down(self, event=None):
        if not self.term_history or self.term_hist_idx < 0:
            return "break"
        self.term_hist_idx += 1
        self.term_in.delete(0, "end")
        if self.term_hist_idx >= len(self.term_history):
            self.term_hist_idx = -1
            return "break"
        self.term_in.insert(0, self.term_history[self.term_hist_idx])
        return "break"


    def _advise_load_btn(self):
        models = list_models()
        path = self.engine.model_path or (models[0] if models else None)
        if not path:
            messagebox.showinfo("پیشنهاد", "مدلی نیست")
            return
        res = execute_tool("load_advise", {"path": str(path), "prefer_gpu": False})
        if res.get("ok"):
            self.ctx_var.set(str(res.get("suggested_ctx") or 4096))
            self.ngl_var.set(str(res.get("suggested_ngl") if res.get("suggested_ngl") is not None else 0))
        messagebox.showinfo("پیشنهاد لود", json.dumps(res, ensure_ascii=False, indent=2)[:1800])


def main():
    if not _HAS_CTK:
        print("[ERROR] customtkinter is not installed.")
        print("Run SETUP.bat or: python -m pip install customtkinter")
        print("Or reinstall the full offline Setup EXE from GitHub Actions.")
        raise SystemExit(1)
    for d in ("models", "sessions", "workspace", "memory", "books"):
        (ROOT / d).mkdir(parents=True, exist_ok=True)
    app = AppHub()
    # Friendly status when no model is present — UI is still fully usable
    try:
        models = list_models()
        if not models:
            app.set_status("آماده | بدون مدل — می‌توانید از RAG (کتاب‌ها) و ابزار وب استفاده کنید")
            app.after(600, lambda: app._log_chat(
                "system",
                "مدل GGUF لود نشده. رابط باز است.\n"
                "• برای پاسخ از کتاب‌های محلی: صفحه RAG یا همین چت (حالت بدون مدل)\n"
                "• برای چت کامل: یک فایل .gguf در پوشه models بگذارید و از صفحه «مدل‌ها» لود کنید."
            ))
    except Exception:
        pass
    app.protocol("WM_DELETE_WINDOW", app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()

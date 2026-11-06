#!/usr/bin/env python3
"""Chrono - a desktop clock with alarms and a world clock.

Run:
    python clock_app.py

Data (alarms, settings, world cities) is stored in ``clock_data.json``
next to this file.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows platforms
    winsound = None


APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "clock_data.json"

BG = "#0b0f19"
PANEL = "#141b2b"
PANEL_2 = "#1d2639"
BORDER = "#2a3550"
TEXT = "#e6edf7"
MUTED = "#8b9bb4"
ACCENT = "#5eead4"
ON_ACCENT = "#06251f"
DANGER = "#f87171"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_ZONES = ["UTC", "Europe/London", "America/New_York", "Asia/Tokyo"]

# Used only when the IANA database is missing (Windows without `tzdata`).
# Offsets are standard time; DST follows only with the real database.
FALLBACK_OFFSETS = {
    "UTC": 0,
    "Europe/London": 0,
    "Europe/Paris": 1,
    "Europe/Berlin": 1,
    "Europe/Athens": 2,
    "Africa/Cairo": 2,
    "Africa/Lagos": 1,
    "Africa/Johannesburg": 2,
    "Asia/Dubai": 4,
    "Asia/Karachi": 5,
    "Asia/Kolkata": 5.5,
    "Asia/Dhaka": 6,
    "Asia/Bangkok": 7,
    "Asia/Shanghai": 8,
    "Asia/Singapore": 8,
    "Asia/Tokyo": 9,
    "Asia/Seoul": 9,
    "Australia/Perth": 8,
    "Australia/Sydney": 10,
    "Pacific/Auckland": 12,
    "America/Sao_Paulo": -3,
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
}

ALL_ZONES = sorted(available_timezones()) or sorted(FALLBACK_OFFSETS)


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without a display)
# ---------------------------------------------------------------------------


def city_name(zone: str) -> str:
    """Return a human-readable city name for an IANA timezone id."""
    if zone == "UTC":
        return "UTC"
    return zone.split("/")[-1].replace("_", " ")


def is_valid_zone(zone: str) -> bool:
    if zone in FALLBACK_OFFSETS:
        return True
    try:
        ZoneInfo(zone)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def zone_now(zone: str) -> datetime:
    """Current time in a zone; fixed-offset fallback when tzdata is missing."""
    try:
        return datetime.now(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        hours = FALLBACK_OFFSETS.get(zone, 0)
        return datetime.now(timezone(timedelta(hours=hours)))


def format_time(dt: datetime, use_24h: bool = True, seconds: bool = True) -> str:
    if use_24h:
        return dt.strftime("%H:%M:%S" if seconds else "%H:%M")
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    body = f"{hour}:{dt.minute:02d}"
    if seconds:
        body += f":{dt.second:02d}"
    return f"{body} {suffix}"


def offset_label(dt: datetime) -> str:
    off = dt.utcoffset()
    if off is None:
        return "UTC"
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    hours, minutes = divmod(abs(total) // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def next_occurrence(alarm: "Alarm", now: datetime) -> datetime | None:
    """Next datetime this alarm will fire, strictly after ``now``."""
    if not alarm.enabled:
        return None
    for offset in range(8):
        day = now + timedelta(days=offset)
        if alarm.days and day.weekday() not in alarm.days:
            continue
        candidate = day.replace(
            hour=alarm.hour, minute=alarm.minute, second=0, microsecond=0
        )
        if candidate > now:
            return candidate
    return None


def resolve_zone(text: str) -> str | None:
    """Resolve user input such as 'Tokyo', 'Asia/Tokyo' or 'Tokyo (Asia/Tokyo)'."""
    text = text.strip()
    if not text:
        return None
    if text.endswith(")") and "(" in text:
        candidate = text[text.rfind("(") + 1 : -1]
        if is_valid_zone(candidate):
            return candidate
    if is_valid_zone(text):
        return text
    low = text.lower()
    exact = [z for z in ALL_ZONES if city_name(z).lower() == low]
    if exact:
        return exact[0]
    likely = [z for z in ALL_ZONES if low in city_name(z).lower() or low in z.lower()]
    return likely[0] if len(likely) == 1 else None


@dataclass
class Alarm:
    hour: int = 7
    minute: int = 0
    label: str = "Alarm"
    days: list[int] = field(default_factory=list)  # 0 = Monday
    enabled: bool = True
    last_fired: str = ""  # "%Y-%m-%d %H:%M" of the last ring
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def matches(self, now: datetime) -> bool:
        if not self.enabled:
            return False
        if (now.hour, now.minute) != (self.hour, self.minute):
            return False
        if self.days and now.weekday() not in self.days:
            return False
        return self.last_fired != now.strftime("%Y-%m-%d %H:%M")

    def weekday_text(self) -> str:
        if not self.days:
            return "Once"
        days = set(self.days)
        if days == {0, 1, 2, 3, 4}:
            return "Weekdays"
        if days == {5, 6}:
            return "Weekends"
        if len(days) == 7:
            return "Every day"
        return " ".join(WEEKDAYS[d] for d in sorted(days))

    def time_text(self, use_24h: bool) -> str:
        return format_time(
            datetime(2000, 1, 1, self.hour, self.minute), use_24h, seconds=False
        )


@dataclass
class AppData:
    alarms: list[Alarm] = field(default_factory=list)
    use_24h: bool = True
    world_zones: list[str] = field(default_factory=lambda: list(DEFAULT_ZONES))


def load_data(path: Path = DATA_FILE) -> AppData:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppData()

    alarms: list[Alarm] = []
    for item in raw.get("alarms", []):
        if not isinstance(item, dict):
            continue
        try:
            hour, minute = int(item["hour"]), int(item["minute"])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue
            days = sorted({int(d) for d in item.get("days", []) if 0 <= int(d) <= 6})
            alarms.append(
                Alarm(
                    hour=hour,
                    minute=minute,
                    label=str(item.get("label", "Alarm"))[:60] or "Alarm",
                    days=days,
                    enabled=bool(item.get("enabled", True)),
                    last_fired=str(item.get("last_fired", "")),
                    id=str(item.get("id") or uuid.uuid4().hex[:8]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    zones_raw = raw.get("world_zones")
    if isinstance(zones_raw, list):
        zones = [z for z in zones_raw if isinstance(z, str) and is_valid_zone(z)]
        zones = list(dict.fromkeys(zones))
    else:
        zones = list(DEFAULT_ZONES)
    return AppData(
        alarms=alarms,
        use_24h=bool(raw.get("use_24h", True)),
        world_zones=zones,
    )


def save_data(data: AppData, path: Path = DATA_FILE) -> None:
    payload = {
        "alarms": [asdict(a) for a in data.alarms],
        "use_24h": data.use_24h,
        "world_zones": data.world_zones,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Alarm sound
# ---------------------------------------------------------------------------


class Ringer:
    """Plays a repeating alarm tone until stopped."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if winsound is not None:
                for freq in (988, 1175, 988, 1568):
                    if self._stop.is_set():
                        return
                    try:
                        winsound.Beep(freq, 200)
                    except RuntimeError:
                        time.sleep(0.2)
                    time.sleep(0.05)
                self._stop.wait(0.7)
            else:  # pragma: no cover - non-Windows fallback
                try:
                    self.root.bell()
                except tk.TclError:
                    return
                self._stop.wait(0.45)


# ---------------------------------------------------------------------------
# Scrollable container
# ---------------------------------------------------------------------------


class ScrollFrame(tk.Frame):
    def __init__(self, master: tk.Misc, bg: str = BG) -> None:
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_body(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class ClockApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.data = load_data()
        self.ringer = Ringer(root)
        self.ring_win: tk.Toplevel | None = None
        self.ring_alarm: Alarm | None = None
        self.pulse_job: str | None = None
        self.world_refs: list[tuple[str, dict[str, tk.Label]]] = []

        self.pages: dict[str, tk.Frame] = {}
        self.tabs: dict[str, tk.Button] = {}
        self.day_vars: list[tk.BooleanVar] = []

        self._build_ui()
        self._show_tab("Clock")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick()

    # -- layout ----------------------------------------------------------

    def _build_ui(self) -> None:
        self.root.configure(bg=BG)
        self.root.title("Chrono - Clock, Alarms & World Time")
        self.root.minsize(900, 620)
        self._center(980, 700)
        self._configure_styles()

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(18, 0))
        tk.Label(
            header, text="Chrono", bg=BG, fg=TEXT,
            font=("Segoe UI", 20, "bold"),
        ).pack(side="left")
        tk.Label(
            header, text="clock · alarms · world time", bg=BG, fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(12, 0), pady=(6, 0))

        tabbar = tk.Frame(self.root, bg=BG)
        tabbar.pack(fill="x", padx=24, pady=(14, 0))
        for name in ("Clock", "Alarms", "World Clock"):
            button = tk.Button(
                tabbar,
                text=name,
                command=lambda n=name: self._show_tab(n),
                bg=PANEL,
                fg=MUTED,
                activebackground=PANEL_2,
                activeforeground=TEXT,
                relief="flat",
                bd=0,
                padx=18,
                pady=8,
                cursor="hand2",
                font=("Segoe UI", 10, "bold"),
            )
            button.pack(side="left", padx=(0, 8))
            self.tabs[name] = button

        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(fill="both", expand=True, padx=24, pady=16)
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        self._build_clock_page()
        self._build_alarms_page()
        self._build_world_page()

    def _center(self, width: int, height: int) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=PANEL_2,
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
            darkcolor=PANEL_2,
            lightcolor=PANEL_2,
            width=10,
        )
        style.configure(
            "TCombobox",
            fieldbackground=PANEL_2,
            background=PANEL_2,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=PANEL_2,
            darkcolor=PANEL_2,
            padding=6,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL_2), ("focus", PANEL_2)],
            foreground=[("readonly", TEXT), ("focus", TEXT)],
            background=[("readonly", PANEL_2), ("focus", PANEL_2)],
            selectbackground=[("readonly", PANEL_2), ("focus", PANEL_2)],
            selectforeground=[("readonly", TEXT), ("focus", TEXT)],
            arrowcolor=[("readonly", TEXT), ("focus", TEXT)],
        )
        self.root.option_add("*TCombobox*Listbox.background", PANEL_2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", ON_ACCENT)
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

    def _show_tab(self, name: str) -> None:
        self.pages[name].tkraise()
        for tab_name, button in self.tabs.items():
            selected = tab_name == name
            button.configure(
                bg=ACCENT if selected else PANEL,
                fg=ON_ACCENT if selected else MUTED,
            )
        if name == "World Clock":
            self._update_world()

    # -- clock page ------------------------------------------------------

    def _build_clock_page(self) -> None:
        page = tk.Frame(self.content, bg=BG)
        page.grid(row=0, column=0, sticky="nsew")
        page.columnconfigure(1, weight=1)
        self.pages["Clock"] = page

        self.clock_canvas = tk.Canvas(
            page, width=420, height=420, bg=BG, highlightthickness=0
        )
        self.clock_canvas.grid(row=0, column=0, sticky="nw", pady=(6, 0))
        self._draw_dial()

        info = tk.Frame(page, bg=BG)
        info.grid(row=0, column=1, sticky="nsew", padx=(30, 0), pady=(34, 0))
        info.columnconfigure(0, weight=1)

        time_row = tk.Frame(info, bg=BG)
        time_row.grid(row=0, column=0, sticky="w")
        self.time_main = tk.Label(
            time_row, text="--:--", bg=BG, fg=TEXT,
            font=("Segoe UI", 68, "bold"),
        )
        self.time_main.pack(side="left")
        self.time_secs = tk.Label(
            time_row, text=":--", bg=BG, fg=ACCENT,
            font=("Segoe UI", 26, "bold"),
        )
        self.time_secs.pack(side="left", anchor="s", pady=(0, 16))

        self.date_label = tk.Label(
            info, text="", bg=BG, fg=TEXT, font=("Segoe UI", 15)
        )
        self.date_label.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.zone_label = tk.Label(
            info, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10)
        )
        self.zone_label.grid(row=2, column=0, sticky="w", pady=(2, 0))
        self.alarm_summary = tk.Label(
            info, text="", bg=BG, fg=ACCENT, font=("Segoe UI", 10, "bold")
        )
        self.alarm_summary.grid(row=3, column=0, sticky="w", pady=(16, 0))
        self.next_alarm_label = tk.Label(
            info, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10)
        )
        self.next_alarm_label.grid(row=4, column=0, sticky="w", pady=(2, 0))

        self.format_button = tk.Button(
            info,
            text="Show 12-hour",
            command=self._toggle_format,
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL_2,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            cursor="hand2",
            font=("Segoe UI", 10),
        )
        self.format_button.grid(row=5, column=0, sticky="w", pady=(24, 0))

    def _draw_dial(self) -> None:
        canvas = self.clock_canvas
        canvas.delete("all")
        cx = cy = 210
        radius = 192

        canvas.create_oval(
            cx - radius, cy - radius, cx + radius, cy + radius,
            outline=BORDER, width=2,
        )
        canvas.create_oval(
            cx - radius + 10, cy - radius + 10, cx + radius - 10, cy + radius - 10,
            outline="#1b2438",
        )
        for tick in range(60):
            angle = math.radians(tick * 6 - 90)
            outer = radius - 8
            inner = radius - (20 if tick % 5 == 0 else 14)
            canvas.create_line(
                cx + outer * math.cos(angle),
                cy + outer * math.sin(angle),
                cx + inner * math.cos(angle),
                cy + inner * math.sin(angle),
                fill=TEXT if tick % 5 == 0 else "#3a4766",
                width=3 if tick % 5 == 0 else 1,
                capstyle="round",
            )
        for hour in range(1, 13):
            angle = math.radians(hour * 30 - 90)
            canvas.create_text(
                cx + (radius - 44) * math.cos(angle),
                cy + (radius - 44) * math.sin(angle),
                text=str(hour),
                fill=MUTED,
                font=("Segoe UI", 14, "bold"),
            )

        self.hour_hand = canvas.create_line(
            cx, cy, cx, cy - 102, fill=TEXT, width=8, capstyle="round"
        )
        self.minute_hand = canvas.create_line(
            cx, cy, cx, cy - 146, fill=TEXT, width=5, capstyle="round"
        )
        self.second_hand = canvas.create_line(
            cx, cy, cx, cy - 166, fill=ACCENT, width=2, capstyle="round"
        )
        canvas.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=ACCENT, outline=BG)
        canvas.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=BG, outline="")
        self.clock_center = (cx, cy)

    def _update_hands(self, now: datetime) -> None:
        cx, cy = self.clock_center
        seconds = now.second + now.microsecond / 1_000_000
        minutes = now.minute + seconds / 60
        hours = (now.hour % 12) + minutes / 60
        for item, degrees, length in (
            (self.hour_hand, hours * 30, 102),
            (self.minute_hand, minutes * 6, 146),
            (self.second_hand, seconds * 6, 166),
        ):
            radians = math.radians(degrees - 90)
            self.clock_canvas.coords(
                item,
                cx,
                cy,
                cx + length * math.cos(radians),
                cy + length * math.sin(radians),
            )

    def _toggle_format(self) -> None:
        self.data.use_24h = not self.data.use_24h
        self._save()
        self._refresh_alarms()
        self._update_world()
        self._tick_now()

    # -- alarms page -----------------------------------------------------

    def _build_alarms_page(self) -> None:
        page = tk.Frame(self.content, bg=BG)
        page.grid(row=0, column=0, sticky="nsew")
        page.rowconfigure(2, weight=1)
        page.columnconfigure(0, weight=1)
        self.pages["Alarms"] = page

        form = tk.Frame(page, bg=PANEL, padx=18, pady=14)
        form.grid(row=0, column=0, sticky="ew")

        row = tk.Frame(form, bg=PANEL)
        row.pack(fill="x")
        tk.Label(
            row, text="New alarm", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=(0, 16))

        self.hour_var = tk.StringVar(value="07")
        self.minute_var = tk.StringVar(value="30")
        self.label_var = tk.StringVar()

        spin_style = dict(
            bg=PANEL_2,
            fg=TEXT,
            buttonbackground=PANEL_2,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Consolas", 14, "bold"),
            width=3,
            justify="center",
        )
        self.hour_spin = tk.Spinbox(
            row, from_=0, to=23, format="%02.0f",
            textvariable=self.hour_var, **spin_style,
        )
        self.hour_spin.pack(side="left")
        tk.Label(row, text=":", bg=PANEL, fg=TEXT, font=("Segoe UI", 14, "bold")).pack(
            side="left", padx=4
        )
        self.minute_spin = tk.Spinbox(
            row, from_=0, to=59, format="%02.0f",
            textvariable=self.minute_var, **spin_style,
        )
        self.minute_spin.pack(side="left")

        tk.Label(
            row, text="Label", bg=PANEL, fg=MUTED, font=("Segoe UI", 10)
        ).pack(side="left", padx=(18, 6))
        tk.Entry(
            row,
            textvariable=self.label_var,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Segoe UI", 11),
            width=18,
        ).pack(side="left", ipady=5)

        tk.Button(
            row,
            text="Add alarm",
            command=self._add_alarm,
            bg=ACCENT,
            fg=ON_ACCENT,
            activebackground="#7cf5e2",
            activeforeground=ON_ACCENT,
            relief="flat",
            bd=0,
            padx=16,
            pady=6,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="right")

        days_row = tk.Frame(form, bg=PANEL)
        days_row.pack(fill="x", pady=(12, 0))
        tk.Label(
            days_row, text="Repeat", bg=PANEL, fg=MUTED, font=("Segoe UI", 10)
        ).pack(side="left", padx=(0, 10))
        self.day_vars = []
        for index, day in enumerate(WEEKDAYS):
            var = tk.BooleanVar(value=False)
            self.day_vars.append(var)
            tk.Checkbutton(
                days_row,
                text=day,
                variable=var,
                bg=PANEL,
                fg=TEXT,
                activebackground=PANEL,
                activeforeground=TEXT,
                selectcolor=PANEL_2,
                relief="flat",
                bd=0,
                highlightthickness=0,
                font=("Segoe UI", 9),
            ).pack(side="left", padx=1)

        tk.Button(
            days_row,
            text="Test sound",
            command=self._test_sound,
            bg=PANEL_2,
            fg=MUTED,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=12,
            pady=4,
            cursor="hand2",
            font=("Segoe UI", 9),
        ).pack(side="right")

        self.form_error = tk.Label(
            form, text="", bg=PANEL, fg=DANGER, font=("Segoe UI", 9)
        )
        self.form_error.pack(fill="x", pady=(6, 0))

        list_header = tk.Frame(page, bg=BG)
        list_header.grid(row=1, column=0, sticky="ew", pady=(18, 6))
        tk.Label(
            list_header, text="Scheduled alarms", bg=BG, fg=TEXT,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        self.alarm_list = ScrollFrame(page, bg=BG)
        self.alarm_list.grid(row=2, column=0, sticky="nsew")
        self._refresh_alarms()

    def _add_alarm(self) -> None:
        try:
            hour = int(self.hour_var.get())
            minute = int(self.minute_var.get())
        except ValueError:
            self.form_error.config(text="Enter a valid hour and minute.")
            return
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            self.form_error.config(text="Hour must be 0-23 and minute 0-59.")
            return

        days = [i for i, var in enumerate(self.day_vars) if var.get()]
        label = self.label_var.get().strip()[:60] or "Alarm"
        self.data.alarms.append(Alarm(hour=hour, minute=minute, label=label, days=days))
        self._save()
        self._refresh_alarms()
        self.form_error.config(text="")
        self.label_var.set("")
        for var in self.day_vars:
            var.set(False)

    def _remove_alarm(self, alarm: Alarm) -> None:
        self.data.alarms = [a for a in self.data.alarms if a.id != alarm.id]
        self._save()
        self._refresh_alarms()

    def _toggle_alarm(self, alarm: Alarm) -> None:
        alarm.enabled = not alarm.enabled
        self._save()
        self._refresh_alarms()

    def _refresh_alarms(self) -> None:
        for widget in self.alarm_list.body.winfo_children():
            widget.destroy()

        if not self.data.alarms:
            tk.Label(
                self.alarm_list.body,
                text="No alarms yet - add one above.",
                bg=BG,
                fg=MUTED,
                font=("Segoe UI", 10),
            ).pack(pady=36)
            self._update_alarm_summary()
            return

        for alarm in sorted(self.data.alarms, key=lambda a: (a.hour, a.minute)):
            card = tk.Frame(self.alarm_list.body, bg=PANEL, padx=16, pady=10)
            card.pack(fill="x", pady=4)

            tk.Label(
                card,
                text=alarm.time_text(self.data.use_24h),
                bg=PANEL,
                fg=TEXT if alarm.enabled else MUTED,
                font=("Segoe UI", 22, "bold"),
                width=7,
                anchor="w",
            ).pack(side="left")

            text_block = tk.Frame(card, bg=PANEL)
            text_block.pack(side="left", fill="x", expand=True, padx=(8, 0))
            tk.Label(
                text_block,
                text=alarm.label,
                bg=PANEL,
                fg=TEXT if alarm.enabled else MUTED,
                font=("Segoe UI", 11),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                text_block,
                text=alarm.weekday_text(),
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 9),
                anchor="w",
            ).pack(fill="x")

            tk.Button(
                card,
                text="On" if alarm.enabled else "Off",
                command=lambda a=alarm: self._toggle_alarm(a),
                bg=ACCENT if alarm.enabled else PANEL_2,
                fg=ON_ACCENT if alarm.enabled else MUTED,
                activebackground="#7cf5e2",
                activeforeground=ON_ACCENT,
                relief="flat",
                bd=0,
                padx=12,
                pady=4,
                cursor="hand2",
                font=("Segoe UI", 9, "bold"),
            ).pack(side="right", padx=(10, 6))

            tk.Button(
                card,
                text="Delete",
                command=lambda a=alarm: self._remove_alarm(a),
                bg=PANEL,
                fg=DANGER,
                activebackground=PANEL_2,
                activeforeground=DANGER,
                relief="flat",
                bd=0,
                padx=8,
                pady=4,
                cursor="hand2",
                font=("Segoe UI", 9),
            ).pack(side="right")

        self._update_alarm_summary()

    def _update_alarm_summary(self) -> None:
        active = sum(1 for a in self.data.alarms if a.enabled)
        if active:
            text = f"{active} active alarm{'s' if active != 1 else ''}"
        else:
            text = "No active alarms"
        self.alarm_summary.config(text=text)

    def _update_next_alarm(self, now: datetime) -> None:
        upcoming = [
            (when, alarm)
            for alarm in self.data.alarms
            if (when := next_occurrence(alarm, now)) is not None
        ]
        if not upcoming:
            self.next_alarm_label.config(text="No upcoming alarms")
            return
        when, alarm = min(upcoming, key=lambda pair: pair[0])
        total_minutes = int((when - now).total_seconds() // 60)
        hours, minutes = divmod(total_minutes, 60)
        if hours >= 24:
            countdown = f"in {hours // 24}d {hours % 24}h"
        elif hours:
            countdown = f"in {hours}h {minutes}m"
        elif minutes:
            countdown = f"in {minutes}m"
        else:
            countdown = "in under a minute"
        days_ahead = (when.date() - now.date()).days
        if days_ahead == 0:
            day_part = "today"
        elif days_ahead == 1:
            day_part = "tomorrow"
        else:
            day_part = when.strftime("%A")
        self.next_alarm_label.config(
            text=(
                f"Next · {alarm.time_text(self.data.use_24h)} {alarm.label}"
                f" · {day_part}, {countdown}"
            )
        )

    def _test_sound(self) -> None:
        self.ringer.start()
        self.root.after(1600, self.ringer.stop)

    # -- world page ------------------------------------------------------

    def _build_world_page(self) -> None:
        page = tk.Frame(self.content, bg=BG)
        page.grid(row=0, column=0, sticky="nsew")
        page.rowconfigure(2, weight=1)
        page.columnconfigure(0, weight=1)
        self.pages["World Clock"] = page

        form = tk.Frame(page, bg=PANEL, padx=18, pady=14)
        form.grid(row=0, column=0, sticky="ew")
        tk.Label(
            form, text="Add a city", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=(0, 16))

        self._zone_choices = [f"{city_name(z)} ({z})" for z in ALL_ZONES]
        self.zone_var = tk.StringVar()
        self.zone_combo = ttk.Combobox(
            form, textvariable=self.zone_var, width=38, font=("Segoe UI", 10)
        )
        self.zone_combo["values"] = self._zone_choices
        self.zone_combo.pack(side="left", ipady=3)
        self.zone_combo.bind("<KeyRelease>", self._filter_zones)
        self.zone_combo.bind("<Return>", lambda _e: self._add_zone())

        tk.Button(
            form,
            text="Add",
            command=self._add_zone,
            bg=ACCENT,
            fg=ON_ACCENT,
            activebackground="#7cf5e2",
            activeforeground=ON_ACCENT,
            relief="flat",
            bd=0,
            padx=18,
            pady=6,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left", padx=(10, 0))

        self.world_error = tk.Label(
            form, text="", bg=PANEL, fg=DANGER, font=("Segoe UI", 9)
        )
        self.world_error.pack(side="left", padx=(12, 0))

        self.world_scroll = ScrollFrame(page, bg=BG)
        self.world_scroll.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        self.world_scroll.body.columnconfigure(0, weight=1)
        self.world_scroll.body.columnconfigure(1, weight=1)
        self._rebuild_world()

    def _filter_zones(self, event: tk.Event) -> None:
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return
        text = self.zone_var.get().strip().lower()
        if not text:
            values = self._zone_choices
        else:
            values = [choice for choice in self._zone_choices if text in choice.lower()][:80]
        self.zone_combo["values"] = values

    def _add_zone(self) -> None:
        zone = resolve_zone(self.zone_var.get())
        if zone is None:
            self.world_error.config(text="City not found - try another spelling.")
            return
        if zone in self.data.world_zones:
            self.world_error.config(text="That city is already shown.")
            return
        self.data.world_zones.append(zone)
        self._save()
        self.zone_var.set("")
        self.zone_combo["values"] = self._zone_choices
        self.world_error.config(text="")
        self._rebuild_world()

    def _remove_zone(self, zone: str) -> None:
        self.data.world_zones = [z for z in self.data.world_zones if z != zone]
        self._save()
        self._rebuild_world()

    def _rebuild_world(self) -> None:
        for widget in self.world_scroll.body.winfo_children():
            widget.destroy()
        self.world_refs = []

        for index, zone in enumerate(self.data.world_zones):
            card = tk.Frame(self.world_scroll.body, bg=PANEL, padx=16, pady=12)
            card.grid(
                row=index // 2,
                column=index % 2,
                sticky="nsew",
                padx=5,
                pady=5,
            )

            head = tk.Frame(card, bg=PANEL)
            head.pack(fill="x")
            tk.Label(
                head, text=city_name(zone), bg=PANEL, fg=TEXT,
                font=("Segoe UI", 12, "bold"),
            ).pack(side="left")
            tk.Button(
                head,
                text="Remove",
                command=lambda z=zone: self._remove_zone(z),
                bg=PANEL,
                fg=MUTED,
                activebackground=PANEL_2,
                activeforeground=DANGER,
                relief="flat",
                bd=0,
                padx=4,
                pady=2,
                cursor="hand2",
                font=("Segoe UI", 9),
            ).pack(side="right")

            tk.Label(
                card, text=zone, bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
                anchor="w",
            ).pack(fill="x")

            time_label = tk.Label(
                card, text="--:--", bg=PANEL, fg=TEXT,
                font=("Segoe UI", 26, "bold"), anchor="w",
            )
            time_label.pack(fill="x", pady=(4, 0))
            sub_label = tk.Label(
                card, text="", bg=PANEL, fg=MUTED,
                font=("Segoe UI", 9), anchor="w",
            )
            sub_label.pack(fill="x")

            self.world_refs.append((zone, {"time": time_label, "sub": sub_label}))

        self._update_world()

    def _update_world(self) -> None:
        for zone, refs in self.world_refs:
            dt = zone_now(zone)
            refs["time"].config(text=format_time(dt, self.data.use_24h))
            refs["sub"].config(
                text=f"{dt.strftime('%a, %d %b')} · {offset_label(dt)}"
            )

    # -- ticking ---------------------------------------------------------

    def _tick(self) -> None:
        now = datetime.now().astimezone()
        self._render(now)
        self._check_alarms()
        self.root.after(250, self._tick)

    def _tick_now(self) -> None:
        self._render(datetime.now().astimezone())

    def _render(self, now: datetime) -> None:
        if self.data.use_24h:
            main, secs = now.strftime("%H:%M"), now.strftime(":%S")
            self.format_button.config(text="Show 12-hour")
        else:
            hour = now.hour % 12 or 12
            main, secs = f"{hour}:{now.minute:02d}", f":{now.second:02d} {'AM' if now.hour < 12 else 'PM'}"
            self.format_button.config(text="Show 24-hour")
        self.time_main.config(text=main)
        self.time_secs.config(text=secs)
        self.date_label.config(text=now.strftime("%A, %d %B %Y"))
        self.zone_label.config(
            text=f"Local time · {now.tzname() or 'Local'} · {offset_label(now)}"
        )
        self._update_hands(now)
        self._update_alarm_summary()
        self._update_next_alarm(now)

    def _check_alarms(self) -> None:
        if self.ring_win is not None:
            return
        now = datetime.now()
        for alarm in self.data.alarms:
            if alarm.matches(now):
                alarm.last_fired = now.strftime("%Y-%m-%d %H:%M")
                if not alarm.days:
                    alarm.enabled = False
                self._save()
                self._refresh_alarms()
                self._fire(alarm)
                break

    # -- ringing ---------------------------------------------------------

    def _fire(self, alarm: Alarm) -> None:
        self.ring_alarm = alarm
        self.ringer.start()

        win = tk.Toplevel(self.root)
        win.title("Alarm")
        win.configure(bg=PANEL)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._dismiss)
        win.bind("<Escape>", lambda _event: self._dismiss())
        self.ring_win = win

        body = tk.Frame(win, bg=PANEL, padx=28, pady=22)
        body.pack(fill="both", expand=True)

        tk.Label(
            body, text="A L A R M", bg=PANEL, fg=MUTED,
            font=("Segoe UI", 9, "bold"),
        ).pack()
        self.ring_time_label = tk.Label(
            body, text=alarm.time_text(self.data.use_24h), bg=PANEL, fg=TEXT,
            font=("Segoe UI", 54, "bold"),
        )
        self.ring_time_label.pack(pady=(2, 0))
        tk.Label(
            body, text=alarm.label, bg=PANEL, fg=TEXT, font=("Segoe UI", 13),
        ).pack()

        buttons = tk.Frame(body, bg=PANEL)
        buttons.pack(pady=(20, 0))
        tk.Button(
            buttons,
            text="Snooze 5 min",
            command=self._snooze,
            bg=PANEL_2,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=16,
            pady=8,
            cursor="hand2",
            font=("Segoe UI", 10),
        ).pack(side="left", padx=(0, 10))
        tk.Button(
            buttons,
            text="Stop",
            command=self._dismiss,
            bg=ACCENT,
            fg=ON_ACCENT,
            activebackground="#7cf5e2",
            activeforeground=ON_ACCENT,
            relief="flat",
            bd=0,
            padx=24,
            pady=8,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        win.update_idletasks()
        width, height = 420, 300
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        win.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self._pulse()

    def _pulse(self) -> None:
        if self.ring_win is None or not self.ring_win.winfo_exists():
            self.pulse_job = None
            return
        accent_on = self.ring_time_label.cget("fg") != ACCENT
        self.ring_time_label.config(fg=ACCENT if accent_on else TEXT)
        self.pulse_job = self.root.after(600, self._pulse)

    def _close_ring(self) -> None:
        self.ringer.stop()
        if self.pulse_job is not None:
            self.root.after_cancel(self.pulse_job)
            self.pulse_job = None
        if self.ring_win is not None and self.ring_win.winfo_exists():
            self.ring_win.destroy()
        self.ring_win = None
        self.ring_alarm = None

    def _dismiss(self) -> None:
        self._close_ring()

    def _snooze(self) -> None:
        alarm = self.ring_alarm
        self._close_ring()
        if alarm is None:
            return
        when = datetime.now() + timedelta(minutes=5)
        self.data.alarms.append(
            Alarm(
                hour=when.hour,
                minute=when.minute,
                label=f"Snooze · {alarm.label}",
                days=[],
            )
        )
        self._save()
        self._refresh_alarms()

    # -- persistence / shutdown -----------------------------------------

    def _save(self) -> None:
        try:
            save_data(self.data)
        except OSError as exc:
            print(f"Could not save data: {exc}", file=sys.stderr)

    def _on_close(self) -> None:
        self._close_ring()
        self._save()
        self.root.destroy()


def main() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    ClockApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

"""
gym_macro_gui.py
=================
Tkinter GUI for the Roblox gym macro.
"""

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
import ctypes

from gym_macro_core import GymMacro, MacroConfig, __version__

APP_TITLE = f"Gym Macro Controller v{__version__} — by starlingz"
DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"
SETTINGS_PATH = Path(__file__).parent / "gym_macro_settings.json"

REQUIRED_TEMPLATES = [
    ("machine_prompt.png", "Interact prompt ('Press E' text/icon)"),
    ("low_stamina.png", "Low stamina container indicator"),
    ("maintaining.png", "'Maintaining' status indicator"),
    ("deficit.png", "Deficit status indicator (Emergency stop)"),
]
OPTIONAL_TEMPLATES = [
    ("full_stamina.png", "Full stamina indicator (optional: falls back to a timer)"),
    ("floor_trash.png", "Obstruction prompt - floor trash (optional: held-E cleared if present)"),
    ("waterspill.png", "Obstruction prompt - water spill (optional: held-E cleared if present)"),
    ("select_exercise_menu.png", "'Select an exercise' menu header (optional: retries click if still open)"),
    ("close_menu.png", "'X' close button on exercise menu (optional: clicks X to close menu if stuck open)"),
    ("type_word_prompt.png", "'Type a Word' prompt identifying header text frame indicator"),
]


class GymMacroGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("660x820")
        self.minsize(580, 680)

        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.template_dir = tk.StringVar(value=str(DEFAULT_TEMPLATE_DIR))
        self.workout_var = tk.StringVar(value="Any")
        self.last_seen_workouts = []

        self._build_widgets()
        self._refresh_template_status()
        self._load_settings(silent=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_log_queue)
        self._auto_update_loop()
        self._hotkey_poll()  # start checking for F6 toggle

    def _on_close(self):
        self._save_settings(silent=True)
        self.destroy()

    def _build_widgets(self):
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        settings_tab = ttk.Frame(notebook)
        templates_tab = ttk.Frame(notebook)
        debug_tab = ttk.Frame(notebook)
        run_tab = ttk.Frame(notebook)

        notebook.add(settings_tab, text="Settings")
        notebook.add(templates_tab, text="Templates")
        notebook.add(debug_tab, text="Debug")
        notebook.add(run_tab, text="Run")

        settings_scroll_area = self._make_scrollable(settings_tab)
        self._build_settings_tab(settings_scroll_area)
        self._build_templates_tab(templates_tab)
        self._build_debug_tab(debug_tab)
        self._build_run_tab(run_tab)

    def _make_scrollable(self, parent):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)

        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            delta = event.delta
            if delta == 0:
                return
            canvas.yview_scroll(int(-1 * (delta / 120)) if abs(delta) >= 120 else int(-1 * delta), "units")

        def _bind_wheel(_):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-3, "units"))
            canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(3, "units"))

        def _unbind_wheel(_):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

        return inner

    def _build_settings_tab(self, parent):
        pad = {"padx": 8, "pady": 6}

        # ═══════════════════════════════════════════════════════
        # KEYBINDS — what keys the macro presses in-game
        # ═══════════════════════════════════════════════════════
        keys_frame = ttk.LabelFrame(parent, text="Keybinds")
        keys_frame.pack(fill="x", **pad)

        ttk.Label(
            keys_frame,
            text="Set these to match your in-game keybinds. Use: e, space, f, lmb, rmb, mmb.\n"
                 "Hold (s) = how long to hold the key. 0 = quick tap, 0.5 = half-second hold.",
            foreground="#555",
            wraplength=550,
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=4, pady=(4, 8))

        ttk.Label(keys_frame, text="Key/Button").grid(row=1, column=1, padx=4)
        ttk.Label(keys_frame, text="Hold (s)").grid(row=1, column=2, padx=4)

        self.key_interact = tk.StringVar(value="e")
        self.key_interact_hold = tk.DoubleVar(value=0.5)
        self.key_workout = tk.StringVar(value="space")
        self.key_workout_hold = tk.DoubleVar(value=0.0)
        self.key_exit = tk.StringVar(value="e")
        self.key_exit_hold = tk.DoubleVar(value=0.5)

        self._keybind_row(keys_frame, "Interact (get on machine):", self.key_interact, self.key_interact_hold, 2)
        self._keybind_row(keys_frame, "Workout action (rep key):", self.key_workout, self.key_workout_hold, 3)
        self._keybind_row(keys_frame, "Exit machine (get off):", self.key_exit, self.key_exit_hold, 4)

        self.click_prompt = tk.BooleanVar(value=False)
        self.also_press_when_clicking = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            keys_frame,
            text="Click the machine prompt where it's detected on screen (fixes 'key works in chat but not in-game')",
            variable=self.click_prompt,
        ).grid(row=5, column=0, columnspan=4, sticky="w", padx=4, pady=(8, 0))
        ttk.Checkbutton(
            keys_frame,
            text="Also send the interact key right after the click",
            variable=self.also_press_when_clicking,
        ).grid(row=6, column=0, columnspan=4, sticky="w", padx=24, pady=(0, 6))

        # ═══════════════════════════════════════════════════════
        # WORKOUT — what exercise and mode to use
        # ═══════════════════════════════════════════════════════
        workout_select_frame = ttk.LabelFrame(parent, text="Workout")
        workout_select_frame.pack(fill="x", **pad)

        ttk.Label(
            workout_select_frame,
            text="Pick which exercise to do and what training mode. 'Any' = first machine found.",
            foreground="#555",
            wraplength=550,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))

        ttk.Label(workout_select_frame, text="Exercise:").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.workout_dropdown = ttk.Combobox(workout_select_frame, textvariable=self.workout_var, state="readonly", width=40)
        self.workout_dropdown.grid(row=1, column=1, sticky="w", padx=4, pady=4)
        self._refresh_workout_dropdown_list()

        ttk.Label(workout_select_frame, text="Mode:").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        self.workout_mode_var = tk.StringVar(value="Hypertrophy")
        self.workout_mode_dropdown = ttk.Combobox(workout_select_frame, textvariable=self.workout_mode_var, state="readonly", width=20, values=["Hypertrophy", "Strength", "Junk"])
        self.workout_mode_dropdown.grid(row=2, column=1, sticky="w", padx=4, pady=4)

        self.one_rep_off = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            workout_select_frame,
            text="One rep off (does 1 rep then immediately regens)",
            variable=self.one_rep_off,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))

        self.junk_no_food = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            workout_select_frame,
            text="Junk: skip eating entirely (never eats, just farms XP indefinitely)",
            variable=self.junk_no_food,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=2)

        self.keep_running = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            workout_select_frame,
            text="Keep running after 'maintaining' (normally the macro stops when you hit maintaining)",
            variable=self.keep_running,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=4, pady=(2, 6))

        # ═══════════════════════════════════════════════════════
        # SHAKERS & FOOD — auto creatine, pre workout, eating
        # ═══════════════════════════════════════════════════════
        shaker_frame = ttk.LabelFrame(parent, text="Shakers & Food")
        shaker_frame.pack(fill="x", **pad)

        ttk.Label(
            shaker_frame,
            text="Auto-drinks shakers on a timer. Creatine = 5 scoops every 4 min, Pre workout = 1 scoop every 10 min.",
            foreground="#555",
            wraplength=550,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))

        self.junk_use_shaker = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            shaker_frame,
            text="Use creatine shaker (auto-drinks 5 scoops on a timer)",
            variable=self.junk_use_shaker,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        self.junk_shaker_interval = tk.DoubleVar(value=5.0)
        self._labeled_entry(shaker_frame, "  Creatine interval (min):", self.junk_shaker_interval, 2)

        self.junk_use_preworkout = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            shaker_frame,
            text="Use pre workout shaker (auto-drinks 1 scoop on a timer, works in all modes)",
            variable=self.junk_use_preworkout,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        self.junk_preworkout_interval = tk.DoubleVar(value=10.0)
        self._labeled_entry(shaker_frame, "  Pre workout interval (min):", self.junk_preworkout_interval, 4)

        self.eat_limit = tk.IntVar(value=0)
        self.eat_stall_timeout = tk.DoubleVar(value=5.0)
        self._labeled_entry(shaker_frame, "Eat limit (0 = eat whole inventory):", self.eat_limit, 5)
        self._labeled_entry(shaker_frame, "Eat stall timeout (s, gives up if stuck):", self.eat_stall_timeout, 6)

        self.bulk_buy_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            shaker_frame,
            text="Bulk buy chicken before starting (buys from shop automatically)",
            variable=self.bulk_buy_enabled,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 2))
        self.bulk_buy_amount = tk.IntVar(value=100)
        self.bulk_buy_price = tk.IntVar(value=25)
        self._labeled_entry(shaker_frame, "  Amount to buy:", self.bulk_buy_amount, 8)
        self._labeled_entry(shaker_frame, "  Chicken price ($):", self.bulk_buy_price, 9)

        # ═══════════════════════════════════════════════════════
        # DISCORD — webhook notifications and progress reports
        # ═══════════════════════════════════════════════════════
        webhook_frame = ttk.LabelFrame(parent, text="Discord Webhook")
        webhook_frame.pack(fill="x", **pad)

        ttk.Label(
            webhook_frame,
            text="Sends embeds to your Discord channel: progress reports, disconnect alerts, status updates.",
            foreground="#555",
            wraplength=550,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))

        self.webhook_url = tk.StringVar(value="")
        self._labeled_entry(webhook_frame, "Webhook URL:", self.webhook_url, 1, width=50)
        ttk.Button(webhook_frame, text="Send test ping", command=self._send_test_ping)\
            .grid(row=2, column=1, sticky="w", padx=4, pady=4)

        self.progress_report_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            webhook_frame,
            text="Send progress reports (sets done, weight, XP gained) to Discord",
            variable=self.progress_report_enabled,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))
        self.progress_report_interval = tk.IntVar(value=10)
        self._labeled_entry(webhook_frame, "  Report every N sets:", self.progress_report_interval, 4)

        self.has_2x_crew_xp_gamepass = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            webhook_frame,
            text="Has 2x Crew XP gamepass (doubles XP in report calculations)",
            variable=self.has_2x_crew_xp_gamepass,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        self.has_server_boost = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            webhook_frame,
            text="Check for server boost (auto-detects on screen, adjusts XP when it expires)",
            variable=self.has_server_boost,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        self.starting_crew_xp = tk.IntVar(value=0)
        self._labeled_entry(webhook_frame, "  Starting Crew XP this session:", self.starting_crew_xp, 7)
        self.xp_per_rep = tk.IntVar(value=1)
        self._labeled_entry(webhook_frame, "  XP per rep (base, before multipliers):", self.xp_per_rep, 8)
        self.reps_per_set = tk.IntVar(value=3)
        self._labeled_entry(webhook_frame, "  Reps per set (used for XP math):", self.reps_per_set, 9)

        # ═══════════════════════════════════════════════════════
        # DETECTION — how the macro finds things on screen
        # ═══════════════════════════════════════════════════════
        detection_frame = ttk.LabelFrame(parent, text="Detection")
        detection_frame.pack(fill="x", **pad)

        ttk.Label(
            detection_frame,
            text="Controls how strictly templates must match and how often the screen is checked.",
            foreground="#555",
            wraplength=550,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))

        self.confidence = tk.DoubleVar(value=0.70)
        self.poll_interval = tk.DoubleVar(value=0.10)
        self.prompt_search_timeout = tk.DoubleVar(value=30.0)
        self.obstruction_confidence = tk.DoubleVar(value=0.7)

        self._labeled_scale(detection_frame, "Match confidence:", self.confidence, 1, 0.5, 1.0, 0)
        ttk.Label(
            detection_frame,
            text="How closely a template must match the screen. Lower = finds easier but may false-trigger.",
            foreground="#888",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 4))

        self._labeled_scale(detection_frame, "Poll interval (s):", self.poll_interval, 3, 0.05, 2.0, 1)
        ttk.Label(
            detection_frame,
            text="How often the macro checks the screen. 0.10 = 10 times/sec. Lower = faster but more CPU.",
            foreground="#888",
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 4))

        self._labeled_entry(detection_frame, "Machine prompt timeout (s):", self.prompt_search_timeout, 5)
        ttk.Label(
            detection_frame,
            text="How long to search for the 'Press E' prompt before giving up.",
            foreground="#888",
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 4))

        self._labeled_entry(detection_frame, "Obstruction match confidence:", self.obstruction_confidence, 7)
        ttk.Label(
            detection_frame,
            text="Sensitivity for detecting floor trash / water spills blocking the machine.",
            foreground="#888",
        ).grid(row=8, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 6))

        # ═══════════════════════════════════════════════════════
        # ADVANCED — timing, recovery, stuck detection
        # ═══════════════════════════════════════════════════════
        advanced_frame = ttk.LabelFrame(parent, text="Advanced (usually leave these alone)")
        advanced_frame.pack(fill="x", **pad)

        ttk.Label(
            advanced_frame,
            text="Only change these if you know what you're doing or were told to adjust them.",
            foreground="#555",
            wraplength=550,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 6))

        self.max_minutes = tk.DoubleVar(value=180)
        self.regen_fallback = tk.DoubleVar(value=30)
        self.stall_seconds = tk.DoubleVar(value=15.0)
        self.stall_tolerance = tk.DoubleVar(value=3.0)
        self.prompt_miss_unstick_after = tk.IntVar(value=2)
        self.obstruction_hold = tk.DoubleVar(value=1.0)
        self.workout_menu_retry_attempts = tk.IntVar(value=5)
        self.prompt_miss_ping_after = tk.IntVar(value=6)
        self.rest_mouse_after_actions = tk.BooleanVar(value=True)
        self.rest_mouse_y_fraction = tk.DoubleVar(value=0.9)
        self.close_menu_key = tk.StringVar(value="")
        self.key_inventory_toggle = tk.StringVar(value="`")

        self._labeled_entry(advanced_frame, "Max runtime (minutes, 0 = forever):", self.max_minutes, 1)
        self._labeled_entry(advanced_frame, "Regen fallback (s, max wait for stamina):", self.regen_fallback, 2)
        self._labeled_entry(advanced_frame, "Stall time (s, detects if stuck on machine):", self.stall_seconds, 3)
        self._labeled_entry(advanced_frame, "Stall tolerance (pixel change threshold):", self.stall_tolerance, 4)
        self._labeled_entry(advanced_frame, "Un-stick after N missed prompts:", self.prompt_miss_unstick_after, 5)
        ttk.Label(
            advanced_frame,
            text="Taps space if machine prompt not found N times in a row (in case character is stuck).",
            foreground="#888",
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=20, pady=(0, 4))

        self._labeled_entry(advanced_frame, "Obstruction hold (s, clears trash/spills):", self.obstruction_hold, 7)
        self._labeled_entry(advanced_frame, "Menu close retries:", self.workout_menu_retry_attempts, 8)
        self._labeled_entry(advanced_frame, "Ping Discord after N missed prompts:", self.prompt_miss_ping_after, 9)

        ttk.Checkbutton(
            advanced_frame,
            text="Rest mouse near bottom after clicks (keeps cursor off UI elements)",
            variable=self.rest_mouse_after_actions,
        ).grid(row=10, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 2))
        self._labeled_scale(advanced_frame, "  Rest position (0=top, 1=bottom):", self.rest_mouse_y_fraction, 11, 0.0, 1.0, 0)

        self._labeled_entry(advanced_frame, "Close-menu key (blank = use exit key):", self.close_menu_key, 12)
        self._labeled_entry(advanced_frame, "Inventory toggle key:", self.key_inventory_toggle, 13)

        # ═══════════════════════════════════════════════════════
        # SCREEN — which monitor to capture
        # ═══════════════════════════════════════════════════════
        monitor_frame = ttk.LabelFrame(parent, text="Screen Capture")
        monitor_frame.pack(fill="x", **pad)

        ttk.Label(monitor_frame, text="Which monitor has Roblox on it:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.monitor_choice = tk.StringVar()
        self.monitor_dropdown = ttk.Combobox(monitor_frame, textvariable=self.monitor_choice, state="readonly", width=40)
        self.monitor_dropdown.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Button(monitor_frame, text="Refresh", command=self._refresh_monitors).grid(row=0, column=2, padx=4)
        self._refresh_monitors()

        # ═══════════════════════════════════════════════════════
        save_frame = ttk.Frame(parent)
        save_frame.pack(fill="x", padx=8, pady=(8, 12))
        ttk.Button(save_frame, text="Save settings", command=self._save_settings).pack(side="left", padx=4)
        ttk.Button(save_frame, text="Load settings", command=self._load_settings).pack(side="left", padx=4)
        self.settings_status = ttk.Label(save_frame, text="Settings auto-save when you close the window.", foreground="#666")
        self.settings_status.pack(side="left", padx=8)

    def _refresh_workout_dropdown_list(self):
        path = Path(self.template_dir.get())
        workouts = ["Any", "Abs"]
        if path.exists():
            workout_files = sorted(path.glob("workout_*.png"))
            workouts.extend([f.name for f in workout_files])
        
        if workouts != self.last_seen_workouts:
            self.last_seen_workouts = workouts.copy()
            self.workout_dropdown["values"] = workouts
            if self.workout_var.get() not in workouts:
                self.workout_var.set("Any")

    def _auto_update_loop(self):
        try:
            self._refresh_workout_dropdown_list()
            self._refresh_template_status_silent()
        except Exception:
            pass
        self.after(2000, self._auto_update_loop)

    def _refresh_monitors(self):
        monitors = GymMacro.list_monitors()
        values = [f"{i}: {desc}" for i, desc in monitors]
        self.monitor_dropdown["values"] = values
        if values:
            current = self.monitor_choice.get()
            if not current or current not in values:
                default = next((v for v in values if v.startswith("1:")), values[0])
                self.monitor_choice.set(default)

    def _selected_monitor_index(self):
        val = self.monitor_choice.get()
        try:
            idx = int(val.split(":")[0].strip())
            return idx
        except (ValueError, IndexError, AttributeError):
            return 1

    def _load_settings(self, silent=False):
        if not SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            return

        vars_map = self._settings_vars()
        for name, value in data.items():
            if name in vars_map:
                var, _type = vars_map[name]
                try:
                    var.set(value)
                except Exception:
                    pass

        # Re-validate monitor choice against available monitors
        self._refresh_monitors()
        current = self.monitor_choice.get()
        values = list(self.monitor_dropdown["values"])
        if current not in values and values:
            # Try to match by index number from saved string
            try:
                saved_idx = int(current.split(":")[0].strip())
                match = next((v for v in values if v.startswith(f"{saved_idx}:")), None)
                if match:
                    self.monitor_choice.set(match)
                else:
                    self.monitor_choice.set(values[1] if len(values) > 1 else values[0])
            except (ValueError, IndexError):
                self.monitor_choice.set(values[1] if len(values) > 1 else values[0])

        self._refresh_template_status()
        self._refresh_workout_dropdown_list()
        if not silent:
            self.settings_status.config(text=f"Loaded from {SETTINGS_PATH.name}")

    def _settings_vars(self):
        return {
            "template_dir": (self.template_dir, str),
            "key_interact": (self.key_interact, str),
            "key_interact_hold": (self.key_interact_hold, float),
            "key_workout": (self.key_workout, str),
            "key_workout_hold": (self.key_workout_hold, float),
            "key_exit": (self.key_exit, str),
            "key_exit_hold": (self.key_exit_hold, float),
            "webhook_url": (self.webhook_url, str),
            "confidence": (self.confidence, float),
            "poll_interval": (self.poll_interval, float),
            "max_minutes": (self.max_minutes, float),
            "regen_fallback": (self.regen_fallback, float),
            "keep_running": (self.keep_running, bool),
            "monitor_choice": (self.monitor_choice, str),
            "click_prompt": (self.click_prompt, bool),
            "also_press_when_clicking": (self.also_press_when_clicking, bool),
            "chosen_workout": (self.workout_var, str),
            "workout_mode": (self.workout_mode_var, str),
            "junk_no_food": (self.junk_no_food, bool),
            "one_rep_off": (self.one_rep_off, bool),
            "junk_use_shaker": (self.junk_use_shaker, bool),
            "junk_shaker_interval": (self.junk_shaker_interval, float),
            "junk_use_preworkout": (self.junk_use_preworkout, bool),
            "junk_preworkout_interval": (self.junk_preworkout_interval, float),
            "stall_seconds": (self.stall_seconds, float),
            "stall_tolerance": (self.stall_tolerance, float),
            "prompt_search_timeout": (self.prompt_search_timeout, float),
            "prompt_miss_unstick_after": (self.prompt_miss_unstick_after, int),
            "obstruction_hold": (self.obstruction_hold, float),
            "obstruction_confidence": (self.obstruction_confidence, float),
            "workout_menu_retry_attempts": (self.workout_menu_retry_attempts, int),
            "prompt_miss_ping_after": (self.prompt_miss_ping_after, int),
            "rest_mouse_after_actions": (self.rest_mouse_after_actions, bool),
            "rest_mouse_y_fraction": (self.rest_mouse_y_fraction, float),
            "close_menu_key": (self.close_menu_key, str),
            "key_inventory_toggle": (self.key_inventory_toggle, str),
            "eat_limit": (self.eat_limit, int),
            "eat_stall_timeout": (self.eat_stall_timeout, float),
            "progress_report_enabled": (self.progress_report_enabled, bool),
            "progress_report_interval": (self.progress_report_interval, int),
            "has_2x_crew_xp_gamepass": (self.has_2x_crew_xp_gamepass, bool),
            "has_server_boost": (self.has_server_boost, bool),
            "starting_crew_xp": (self.starting_crew_xp, int),
            "xp_per_rep": (self.xp_per_rep, int),
            "reps_per_set": (self.reps_per_set, int),
            "bulk_buy_enabled": (self.bulk_buy_enabled, bool),
            "bulk_buy_amount": (self.bulk_buy_amount, int),
            "bulk_buy_price": (self.bulk_buy_price, int),
        }

    def _save_settings(self, silent=False):
        data = {name: var.get() for name, (var, _type) in self._settings_vars().items()}
        try:
            SETTINGS_PATH.write_text(json.dumps(data, indent=2))
            if not silent:
                self.settings_status.config(text=f"Saved to {SETTINGS_PATH.name}")
        except Exception as e:
            if not silent:
                messagebox.showerror("Save failed", str(e))

    def _labeled_entry(self, parent, label, var, row, width=20):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky="w", padx=4, pady=4)

    def _keybind_row(self, parent, label, key_var, hold_var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(parent, textvariable=key_var, width=10).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        ttk.Spinbox(
            parent, textvariable=hold_var, from_=0.0, to=10.0, increment=0.1, width=6, format="%.1f"
        ).grid(row=row, column=2, sticky="w", padx=4, pady=4)

    def _labeled_scale(self, parent, label, var, row, frm, to, col):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        scale = ttk.Scale(frame, from_=frm, to=to, orient="horizontal", variable=var, length=160)
        scale.pack(side="left")
        value_lbl = ttk.Label(frame, width=5)
        value_lbl.pack(side="left", padx=4)

        def update_label(*_):
            value_lbl.config(text=f"{var.get():.2f}")

        var.trace_add("write", update_label)
        update_label()

    def _build_templates_tab(self, parent):
        pad = {"padx": 8, "pady": 6}

        dir_frame = ttk.Frame(parent)
        dir_frame.pack(fill="x", **pad)
        ttk.Label(dir_frame, text="Templates folder:").pack(side="left")
        ttk.Entry(dir_frame, textvariable=self.template_dir, width=45).pack(side="left", padx=4)
        ttk.Button(dir_frame, text="Browse...", command=self._browse_template_dir).pack(side="left")
        ttk.Button(dir_frame, text="Refresh", command=self._refresh_template_status).pack(side="left", padx=4)

        self.status_frame = ttk.LabelFrame(parent, text="Template status")
        self.status_frame.pack(fill="both", expand=True, padx=8, pady=6)

        ttk.Button(parent, text="Open templates folder", command=self._open_template_folder)\
            .pack(padx=8, pady=6, anchor="w")

    def _build_debug_tab(self, parent):
        pad = {"padx": 8, "pady": 6}

        capture_frame = ttk.LabelFrame(parent, text="1. Capture what the macro currently sees")
        capture_frame.pack(fill="x", **pad)
        ttk.Button(capture_frame, text="Capture screen now", command=self._debug_capture).pack(
            side="left", padx=6, pady=6
        )
        self.debug_capture_status = ttk.Label(capture_frame, text="")
        self.debug_capture_status.pack(side="left", padx=6)

        test_frame = ttk.LabelFrame(parent, text="2. Test a specific template against the live screen")
        test_frame.pack(fill="x", **pad)

        ttk.Label(test_frame, text="Template file:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.debug_template_path = tk.StringVar()
        ttk.Entry(test_frame, textvariable=self.debug_template_path, width=40).grid(
            row=0, column=1, sticky="w", padx=4, pady=4
        )
        ttk.Button(test_frame, text="Browse...", command=self._browse_debug_template).grid(
            row=0, column=2, padx=4, pady=4
        )
        ttk.Button(test_frame, text="Test match", command=self._debug_test_template).grid(
            row=1, column=1, sticky="w", padx=4, pady=6
        )

        self.debug_result_text = tk.Text(test_frame, height=6, width=60, state="disabled", wrap="word")
        self.debug_result_text.grid(row=2, column=0, columnspan=3, sticky="we", padx=4, pady=6)

    def _browse_debug_template(self):
        chosen = filedialog.askopenfilename(
            initialdir=self.template_dir.get(),
            filetypes=[("PNG images", "*.png"), ("All files", "*.*")],
        )
        if chosen:
            self.debug_template_path.set(chosen)

    def _debug_capture(self):
        cfg = self._build_config()
        macro = GymMacro(cfg, log_fn=lambda m: None, stop_flag=None)
        out_dir = Path(self.template_dir.get()).parent / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "current_screen.png"
        try:
            w, h = macro.debug_capture(out_path)
            self.debug_capture_status.config(text=f"Saved {w}x{h} capture to {out_path}")
            self._open_path(out_path)
        except Exception as e:
            messagebox.showerror("Capture failed", str(e))

    def _debug_test_template(self):
        template_path = self.debug_template_path.get().strip()
        if not template_path or not Path(template_path).exists():
            messagebox.showwarning("No template", "Choose a template PNG file first.")
            return

        cfg = self._build_config()
        macro = GymMacro(cfg, log_fn=lambda m: None, stop_flag=None)
        out_dir = Path(self.template_dir.get()).parent / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"test_{Path(template_path).stem}.png"

        try:
            confidence, found, screen_size, template_size = macro.debug_test_template(
                Path(template_path), save_path=out_path
            )
        except Exception as e:
            messagebox.showerror("Test failed", str(e))
            return

        self.debug_result_text.configure(state="normal")
        self.debug_result_text.delete("1.0", "end")
        self.debug_result_text.insert(
            "end",
            f"Confidence: {confidence:.3f} (threshold: {cfg.confidence_threshold:.2f}) -> {'MATCH' if found else 'no match'}\n"
            f"Screen size captured: {screen_size[0]}x{screen_size[1]}\n"
            f"Template size: {template_size[0]}x{template_size[1]}\n"
            f"Annotated screenshot saved to: {out_path}\n"
        )
        self.debug_result_text.configure(state="disabled")
        self._open_path(out_path)

    def _open_path(self, path: Path):
        import subprocess
        import sys as _sys
        try:
            if _sys.platform == "win32":
                subprocess.Popen(["explorer", str(path)])
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    def _build_run_tab(self, parent):
        pad = {"padx": 8, "pady": 6}

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill="x", **pad)

        self.start_btn = ttk.Button(btn_frame, text="▶ Start", command=self._start)
        self.stop_btn = ttk.Button(btn_frame, text="■ Stop", command=self._stop, state="disabled")
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn.pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(btn_frame, textvariable=self.status_var, font=("", 10, "bold")).pack(side="left", padx=16)

        log_frame = ttk.LabelFrame(parent, text="Log")
        log_frame.pack(fill="both", expand=True, padx=8, pady=6)

        self.log_text = tk.Text(log_frame, wrap="word", state="disabled", height=20)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _browse_template_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.template_dir.get())
        if chosen:
            self.template_dir.set(chosen)
            self._refresh_template_status()
            self._refresh_workout_dropdown_list()

    def _open_template_folder(self):
        path = Path(self.template_dir.get())
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _refresh_template_status(self):
        for widget in self.status_frame.winfo_children():
            widget.destroy()

        path = Path(self.template_dir.get())
        row = 0
        for filename, description in REQUIRED_TEMPLATES:
            found = (path / filename).exists()
            self._status_row(row, filename, description, found, required=True)
            row += 1
        for filename, description in OPTIONAL_TEMPLATES:
            found = (path / filename).exists()
            self._status_row(row, filename, description, found, required=False)
            row += 1

        workout_files = sorted(path.glob("workout_*.png")) if path.exists() else []
        found = len(workout_files) > 0 or self.workout_var.get() == "Abs"
        label = f"workout_*.png ({len(workout_files)} found)"
        self._status_row(row, label, "Workout choice image context verification validation", found, required=True)

    def _refresh_template_status_silent(self):
        path = Path(self.template_dir.get())
        workout_files = sorted(path.glob("workout_*.png")) if path.exists() else []
        base_count = len(workout_files) + (1 if "Abs" in self.last_seen_workouts else 0) + (1 if "Any" in self.last_seen_workouts else 0)
        if base_count != len(self.last_seen_workouts):
            self._refresh_template_status()

    def _status_row(self, row, filename, description, found, required):
        mark = "✅" if found else ("❌" if required else "⚪")
        ttk.Label(self.status_frame, text=mark).grid(row=row, column=0, padx=4, pady=2)
        ttk.Label(self.status_frame, text=filename, width=20).grid(row=row, column=1, sticky="w", padx=4, pady=2)
        ttk.Label(self.status_frame, text=description, foreground="#666").grid(
            row=row, column=2, sticky="w", padx=4, pady=2
        )

    def _drain_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _hotkey_poll(self):
        """check if F6 is pressed to toggle macro on/off (works even when game is focused)"""
        VK_F6 = 0x75
        if ctypes.windll.user32.GetAsyncKeyState(VK_F6) & 0x8000:
            if self.worker_thread and self.worker_thread.is_alive():
                self._stop()
            else:
                self._start()
            time.sleep(0.3)  # debounce
        self.after(50, self._hotkey_poll)

    def _build_config(self) -> MacroConfig:
        return MacroConfig(
            template_dir=Path(self.template_dir.get()),
            monitor_index=self._selected_monitor_index(),
            key_interact=self.key_interact.get().strip() or "e",
            key_interact_hold=float(self.key_interact_hold.get()),
            key_workout_action=self.key_workout.get().strip() or "space",
            key_workout_hold=float(self.key_workout_hold.get()),
            key_exit_machine=self.key_exit.get().strip() or "e",
            key_exit_hold=float(self.key_exit_hold.get()),
            click_prompt_instead_of_key=bool(self.click_prompt.get()),
            also_press_key_when_clicking=bool(self.also_press_when_clicking.get()),
            webhook_url=self.webhook_url.get().strip(),
            confidence_threshold=float(self.confidence.get()),
            poll_interval=float(self.poll_interval.get()),
            max_loop_minutes=float(self.max_minutes.get()),
            regen_fallback_seconds=float(self.regen_fallback.get()),
            keep_running_after_ping=bool(self.keep_running.get()),
            chosen_workout=self.workout_var.get(),
            workout_mode=self.workout_mode_var.get(),
            junk_no_food=bool(self.junk_no_food.get()),
            one_rep_off=bool(self.one_rep_off.get()),
            junk_use_shaker=bool(self.junk_use_shaker.get()),
            junk_shaker_interval=float(self.junk_shaker_interval.get()),
            junk_use_preworkout=bool(self.junk_use_preworkout.get()),
            junk_preworkout_interval=float(self.junk_preworkout_interval.get()),
            stall_seconds=float(self.stall_seconds.get()),
            stall_fingerprint_tolerance=float(self.stall_tolerance.get()),
            prompt_search_timeout=float(self.prompt_search_timeout.get()),
            prompt_miss_unstick_after=int(self.prompt_miss_unstick_after.get()),
            obstruction_hold_seconds=float(self.obstruction_hold.get()),
            obstruction_confidence_threshold=float(self.obstruction_confidence.get()),
            workout_menu_retry_attempts=int(self.workout_menu_retry_attempts.get()),
            prompt_miss_ping_after=int(self.prompt_miss_ping_after.get()),
            rest_mouse_after_actions=bool(self.rest_mouse_after_actions.get()),
            rest_mouse_y_fraction=float(self.rest_mouse_y_fraction.get()),
            close_menu_key=self.close_menu_key.get().strip(),
            key_inventory_toggle=self.key_inventory_toggle.get().strip(),
            eat_limit=int(self.eat_limit.get()),
            eat_stall_timeout=float(self.eat_stall_timeout.get()),
            progress_report_enabled=bool(self.progress_report_enabled.get()),
            progress_report_interval=int(self.progress_report_interval.get()),
            has_2x_crew_xp_gamepass=bool(self.has_2x_crew_xp_gamepass.get()),
            has_server_boost=bool(self.has_server_boost.get()),
            starting_crew_xp=int(self.starting_crew_xp.get()),
            xp_per_rep=int(self.xp_per_rep.get()),
            reps_per_set=int(self.reps_per_set.get()),
            bulk_buy_enabled=bool(self.bulk_buy_enabled.get()),
            bulk_buy_amount=int(self.bulk_buy_amount.get()),
            bulk_buy_price=int(self.bulk_buy_price.get()),
        )

    def _start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        self._refresh_workout_dropdown_list()
        cfg = self._build_config()
        macro = GymMacro(cfg, log_fn=lambda m: self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] {m}"), stop_flag=self.stop_event)
        
        missing = macro.missing_required_templates()
        if missing:
            proceed = messagebox.askyesno(
                "Missing templates",
                "These required template images are missing:\n\n" + "\n".join(missing) + "\n\nStart anyway?",
            )
            if not proceed:
                return

        self.stop_event.clear()
        self.status_var.set("Running")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        def run_and_reset():
            try:
                macro.run()
            except Exception as e:
                self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] ERROR: {e}")
            finally:
                self.status_var.set("Idle")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")

        self.worker_thread = threading.Thread(target=run_and_reset, daemon=True)
        self.worker_thread.start()

    def _stop(self):
        self.status_var.set("Stopping...")
        self.stop_event.set()

    def _send_test_ping(self):
        url = self.webhook_url.get().strip()
        if not url:
            messagebox.showwarning("No webhook", "Enter a Discord webhook URL first.")
            return
        cfg = self._build_config()
        macro = GymMacro(cfg, log_fn=lambda m: None, stop_flag=None)
        threading.Thread(
            target=lambda: macro.send_discord_ping("🔔 Test ping from Gym Macro Controller."),
            daemon=True,
        ).start()


if __name__ == "__main__":
    app = GymMacroGUI()
    app.mainloop()
"""
gym_macro_core.py
==================
Core automation logic for the Roblox gym macro.
Made by starlingz
"""

__version__ = "1.1.8"

import time
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pyautogui
import pydirectinput
import requests
import mss

try:
    import pytesseract
    # Use local Tesseract if available, otherwise fall back to Program Files
    _local_tesseract = Path(__file__).parent / "Tesseract-OCR" / "tesseract.exe"
    if _local_tesseract.exists():
        pytesseract.pytesseract.tesseract_cmd = str(_local_tesseract)
    else:
        pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
except Exception as _e:
    print(f"pytesseract import failed: {_e}")
    pytesseract = None

pyautogui.FAILSAFE = True

pyautogui.PAUSE = 0.0

# --- Raw win32 mouse click for maximum speed (bypasses pydirectinput overhead) ---
import ctypes
import ctypes.wintypes

_SendInput = ctypes.windll.user32.SendInput

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.wintypes.LONG), ("dy", ctypes.wintypes.LONG),
                ("mouseData", ctypes.wintypes.DWORD), ("dwFlags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]
    _fields_ = [("type", ctypes.wintypes.DWORD), ("u", _U)]

def _raw_click():
    """Single left click using raw SendInput — fastest possible on Windows."""
    # Mouse down
    down = _INPUT()
    down.type = 0  # INPUT_MOUSE
    down.u.mi.dwFlags = 0x0002  # MOUSEEVENTF_LEFTDOWN
    # Mouse up
    up = _INPUT()
    up.type = 0
    up.u.mi.dwFlags = 0x0004  # MOUSEEVENTF_LEFTUP
    # Send both in one call
    inputs = (_INPUT * 2)(down, up)
    _SendInput(2, ctypes.pointer(inputs[0]), ctypes.sizeof(_INPUT))


@dataclass
class MacroConfig:
    template_dir: Path = Path(__file__).parent / "templates"
    monitor_index: int = 1  
    key_interact: str = "e"
    key_interact_hold: float = 1.0  
    key_workout_action: str = "lmb"
    key_workout_hold: float = 0.0
    key_exit_machine: str = "space"
    key_exit_hold: float = 0.0
    click_prompt_instead_of_key: bool = False  
    also_press_key_when_clicking: bool = True  
    webhook_url: str = ""
    confidence_threshold: float = 0.72
    poll_interval: float = 0.5
    max_loop_minutes: float = 180
    regen_fallback_seconds: float = 30
    ping_message: str = "💪 Gym macro: reached **maintaining** state."
    keep_running_after_ping: bool = False  
    chosen_workout: str = "Any"  
    stall_seconds: float = 1.0  
    stall_fingerprint_tolerance: float = 3.0  
    prompt_search_timeout: float = 30.0  
    prompt_miss_unstick_after: int = 2  
    obstruction_hold_seconds: float = 1.0  
    obstruction_confidence_threshold: float = 0.7  
    workout_menu_retry_attempts: int = 5  
    prompt_miss_ping_after: int = 6  
    rest_mouse_after_actions: bool = True  
    rest_mouse_y_fraction: float = 0.9  
    close_menu_key: str = ""  
    key_inventory_toggle: str = "`"  
    workout_mode: str = "Hypertrophy"  # Hypertrophy, Strength, Junk
    one_rep_off: bool = False  # do 1 rep then get off to regen immediately
    junk_no_food: bool = False  # skip eating entirely in junk mode
    junk_use_shaker: bool = False  # use creatine shaker periodically
    junk_shaker_interval: float = 4.0  # minutes between shakes
    junk_use_preworkout: bool = False  # use pre workout shaker every interval
    junk_preworkout_interval: float = 10.0  # minutes between pre workout
    shaker_circle_x: int = 0  # 0 = auto-detect, or absolute x of far right circle
    shaker_circle_y: int = 0  # 0 = auto-detect, or absolute y of far right circle
    # --- simple loop (timing based, no vision during workout) ---
    use_simple_loop: bool = False
    workout_duration: float = 3.75  # seconds of clicking per set
    click_interval: float = 0.275  # gap between clicks
    regen_pause: float = 2.25  # wait time for stamina refill
    menu_delay: float = 0.5  # pause after selecting workout
    # --- eating settings ---
    eat_limit: int = 0  # 0 = eat everything, set a number to cap how many items
    eat_stall_timeout: float = 5.0  # if calories stop going up for this long, stop eating
    # --- bulk buy ---
    bulk_buy_enabled: bool = False
    bulk_buy_amount: int = 100  # how many to buy
    bulk_buy_price: int = 25  # cost per item
    # --- progress reports ---
    progress_report_enabled: bool = False
    progress_report_interval: int = 10  # send a report every N sets
    # --- xp tracking ---
    has_2x_crew_xp_gamepass: bool = False  # permanent 2x from gamepass
    has_server_boost: bool = False  # temporary server boost (detected via template)
    starting_crew_xp: int = 0  # your crew xp at the start of session
    xp_per_rep: int = 1  # base xp per rep
    reps_per_set: int = 3  # how many reps the game does per set (for counting)


class StoppedException(Exception):
    """Raised internally to unwind the loop as soon as Stop is requested."""


class GymMacro:
    def __init__(self, config: MacroConfig, log_fn=print, stop_flag=None):
        self.cfg = config
        self.log_fn = log_fn
        self.stop_flag = stop_flag
        self._prompt_miss_count = 0
        self._prompt_stuck_pinged = False
        self._last_shaker_use = 0
        self._last_preworkout_use = 0  # Track shaker usage across regen cycles
        self._sets_done = 0
        self._last_report_set = 0
        self._last_weight_kg = None
        self._prev_report_weight = None
        self._first_weight_kg = None
        self._macro_start_time = None
        self._total_reps = 0
        # keep mss alive so we dont recreate it every frame
        self._sct = mss.mss()
        self._monitor = self._resolve_monitor()
        # cache templates so we dont read from disk every time
        self._tmpl_cache = {}

    def _resolve_monitor(self):
        monitors = self._sct.monitors
        idx = self.cfg.monitor_index
        if idx >= len(monitors):
            idx = 1
        return monitors[idx]

    def _load_template(self, path: Path):
        """load + cache a template image so we only hit disk once"""
        key = str(path)
        if key in self._tmpl_cache:
            return self._tmpl_cache[key]
        if not path.exists():
            self._tmpl_cache[key] = (None, None)
            return (None, None)
        bgr = cv2.imread(key, cv2.IMREAD_COLOR)
        if bgr is None:
            self._tmpl_cache[key] = (None, None)
            return (None, None)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        self._tmpl_cache[key] = (bgr, gray)
        return (bgr, gray)

    def log(self, msg):
        self.log_fn(msg)

    def _check_stop(self):
        if self.stop_flag is not None and self.stop_flag.is_set():
            raise StoppedException()

    # mouse button aliases
    _MOUSE_ALIASES = {
        "lmb": "left", "mouse1": "left", "leftclick": "left", "left_click": "left", "left click": "left",
        "rmb": "right", "mouse2": "right", "rightclick": "right", "right_click": "right", "right click": "right",
        "mmb": "middle", "mouse3": "middle", "middleclick": "middle", "middle_click": "middle", "middle click": "middle",
    }

    def _is_game_focused(self):
        """Check if the Roblox window is currently in the foreground."""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.lower()
            return "roblox" in title
        except Exception:
            return True  # assume focused if check fails

    def send_input(self, key_name: str, hold_seconds: float = 0.0):
        normalized = key_name.strip().lower()
        button = self._MOUSE_ALIASES.get(normalized)

        if hold_seconds and hold_seconds > 0:
            if button:
                pydirectinput.mouseDown(button=button)
            else:
                pydirectinput.keyDown(key_name)
            try:
                self._sleep(hold_seconds)
            finally:
                if button:
                    pydirectinput.mouseUp(button=button)
                else:
                    pydirectinput.keyUp(key_name)
        else:
            if button:
                pydirectinput.click(button=button)
            else:
                pydirectinput.press(key_name)

    def _sleep(self, seconds):
        if seconds <= 0:
            return
        end = time.time() + seconds
        while time.time() < end:
            self._check_stop()
            time.sleep(min(0.01, max(0, end - time.time())))

    def grab_screen(self):
        """grab the screen using the persistent mss handle"""
        shot = np.asarray(self._sct.grab(self._monitor))
        return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

    @staticmethod
    def list_monitors():
        with mss.mss() as sct:
            out = []
            for i, m in enumerate(sct.monitors):
                tag = " (all monitors combined)" if i == 0 else ""
                out.append((i, f"{m['width']}x{m['height']} at ({m['left']},{m['top']}){tag}"))
            return out

    def find_on_screen(self, template_path: Path, return_confidence=False, custom_threshold=None, screen=None):
        tmpl_bgr, tmpl_gray = self._load_template(template_path)
        if tmpl_gray is None:
            return (0.0, False) if return_confidence else None

        if screen is None:
            screen = self.grab_screen()
        threshold = custom_threshold if custom_threshold is not None else self.cfg.confidence_threshold

        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        th, tw = tmpl_gray.shape[:2]
        sh, sw = screen_gray.shape[:2]

        best_val = -1.0
        best_loc = None
        best_w, best_h = tw, th

        # multi-scale matching for game world stuff
        scales = [1.0, 1.1, 0.9, 1.2, 0.8, 0.7, 1.4]

        for scale in scales:
            nw = int(tw * scale)
            nh = int(th * scale)
            if nw < 10 or nh < 10 or nh > sh or nw > sw:
                continue

            if scale == 1.0:
                scaled = tmpl_gray
            else:
                interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                scaled = cv2.resize(tmpl_gray, (nw, nh), interpolation=interp)

            result = cv2.matchTemplate(screen_gray, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_w, best_h = nw, nh

            # Early exit if we already have a strong match
            if best_val >= threshold + 0.05:
                break

        found = best_val >= threshold

        if return_confidence:
            return (best_val, found)

        if found and best_loc is not None:
            return (best_loc[0] + best_w // 2, best_loc[1] + best_h // 2, best_val)
        return None

    def wait_for_any_template(self, template_paths, timeout=None, label=None):
        if not template_paths:
            return None
            
        label = label or " or ".join(t.name for t in template_paths)
        start = time.time()
        last_report = 0
        best_seen = 0.0
        
        while True:
            self._check_stop()
            # ONE screen grab shared across all templates
            screen = self.grab_screen()
            best_conf_this_loop = 0.0
            
            for t_path in template_paths:
                result = self.find_on_screen(t_path, screen=screen)
                if result:
                    self.log(f"Found '{t_path.name}' (confidence {result[2]:.2f})")
                    return result
                # Get confidence for logging
                conf, _ = self.find_on_screen(t_path, return_confidence=True, screen=screen)
                best_conf_this_loop = max(best_conf_this_loop, conf)

            best_seen = max(best_seen, best_conf_this_loop)
            
            if time.time() - last_report > 3:
                self.log(f"Still looking for {label}... best: {best_seen:.2f} (need {self.cfg.confidence_threshold:.2f})")
                last_report = time.time()

            if timeout and (time.time() - start) > timeout:
                self.log(f"Timed out waiting for {label} (best: {best_seen:.2f})")
                return None
                
            self._sleep(self.cfg.poll_interval)

    def wait_for_template(self, template_path, timeout=None, label=None):
        return self.wait_for_any_template([template_path], timeout=timeout, label=label)

    def debug_capture(self, save_path: Path):
        screen = self.grab_screen()
        cv2.imwrite(str(save_path), screen)
        return screen.shape[1], screen.shape[0]

    def debug_test_template(self, template_path: Path, save_path: Path = None):
        template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
        if template is None:
            return (0.0, False, None, None)

        screen = self.grab_screen()
        screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
        tmpl_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        th, tw = tmpl_gray.shape[:2]
        sh, sw = screen_gray.shape[:2]

        best_max_val = -1.0
        best_max_loc = None
        best_w, best_h = tw, th

        scales = [1.0, 1.1, 0.9, 1.2, 0.8, 1.4, 0.7, 1.6, 0.6]
        for scale in scales:
            nw = int(tw * scale)
            nh = int(th * scale)
            if nw < 10 or nh < 10 or nh > sh or nw > sw:
                continue
            if scale == 1.0:
                scaled = tmpl_gray
            else:
                interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                scaled = cv2.resize(tmpl_gray, (nw, nh), interpolation=interp)
            result = cv2.matchTemplate(screen_gray, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_max_val:
                best_max_val = max_val
                best_max_loc = max_loc
                best_w, best_h = nw, nh

        found = best_max_val >= self.cfg.confidence_threshold

        if save_path and best_max_loc is not None:
            annotated = screen.copy()
            color = (0, 255, 0) if found else (0, 0, 255)
            top_left = best_max_loc
            bottom_right = (top_left[0] + best_w, top_left[1] + best_h)
            cv2.rectangle(annotated, top_left, bottom_right, color, 3)
            cv2.putText(
                annotated, f"{best_max_val:.2f} @{best_w}x{best_h}",
                (top_left[0], max(0, top_left[1] - 10)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2,
            )
            cv2.imwrite(str(save_path), annotated)

        return (best_max_val, found, (screen.shape[1], screen.shape[0]), (best_w, best_h))

    def rest_mouse(self):
        if not self.cfg.rest_mouse_after_actions:
            return
        m = self._monitor
        x = m["left"] + m["width"] // 2
        y = m["top"] + int(m["height"] * self.cfg.rest_mouse_y_fraction)
        pydirectinput.moveTo(x, y)

    def move_and_click(self, x, y, double=False):
        pydirectinput.moveTo(int(x) + 2, int(y) + 2)
        time.sleep(0.02)
        pydirectinput.moveTo(int(x), int(y))
        time.sleep(0.06)
        if double:
            pydirectinput.click()
            time.sleep(0.05)
            pydirectinput.click()
        else:
            pydirectinput.click()

    def click_template(self, template_path, label=None):
        label = label or template_path.stem
        match = self.find_on_screen(template_path)
        if match:
            x, y, conf = match
            self.move_and_click(x, y, double=False)
            self.log(f"Clicked '{label}' at ({x},{y}) confidence {conf:.2f}")
            return True
        self.log(f"Could not find '{label}' to click")
        return False

    def stamina_fingerprint(self, screen=None):
        """fast stamina check — just sample the bar area directly (bottom left)"""
        if screen is None:
            screen = self.grab_screen()
        h, w = screen.shape[:2]
        # stamina bar is bottom-left, roughly 0-15% width, 88-95% height
        crop = screen[int(h*0.88):int(h*0.95), 0:int(w*0.15)]
        if crop.size == 0:
            return None
        return tuple(np.round(crop.reshape(-1, 3).mean(axis=0), 1))

    def check_low_stamina_by_color(self, screen=None):
        template_path = self.cfg.template_dir / "low_stamina.png"
        tmpl_bgr, _ = self._load_template(template_path)
        if tmpl_bgr is None:
            return False

        if screen is None:
            screen = self.grab_screen()
        if tmpl_bgr.shape[0] > screen.shape[0] or tmpl_bgr.shape[1] > screen.shape[1]:
            return False

        result = cv2.matchTemplate(screen, tmpl_bgr, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= 0.65:
            x, y = max_loc
            h, w = tmpl_bgr.shape[:2]
            crop = screen[y:y+h, x:x+w]
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            lower_red1 = np.array([0, 120, 70])
            upper_red1 = np.array([10, 255, 255])
            lower_red2 = np.array([170, 120, 70])
            upper_red2 = np.array([180, 255, 255])
            mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
            mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
            return np.sum((mask1 + mask2) > 0) > 15
        return False

    def send_discord_ping(self, message):
        if not self.cfg.webhook_url.strip():
            self.log("Discord webhook URL not set, skipping ping.")
            return
        try:
            resp = requests.post(self.cfg.webhook_url, json={"content": message}, timeout=10)
            if resp.status_code in (200, 204):
                self.log("Discord webhook sent.")
            else:
                self.log(f"Discord webhook failed: {resp.status_code} {resp.text}")
        except Exception as e:
            self.log(f"Discord webhook error: {e}")

    def send_discord_screenshot(self, message=""):
        """Capture the current screen and send it to Discord as an image attachment."""
        if not self.cfg.webhook_url.strip():
            return
        try:
            screen = self.grab_screen()
            # Encode to PNG in memory
            _, img_bytes = cv2.imencode('.png', screen)
            files = {"file": ("screenshot.png", img_bytes.tobytes(), "image/png")}
            payload = {}
            if message:
                payload["content"] = message
            resp = requests.post(
                self.cfg.webhook_url,
                data=payload,
                files=files,
                timeout=15,
            )
            if resp.status_code in (200, 204):
                self.log("Discord screenshot sent.")
            else:
                self.log(f"Discord screenshot failed: {resp.status_code}")
        except Exception as e:
            self.log(f"Discord screenshot error: {e}")

    def _match_status_template(self, template_path, threshold=0.70, screen=None):
        """Fast matching for fixed-size UI status text using cached templates."""
        tmpl_bgr, _ = self._load_template(template_path)
        if tmpl_bgr is None:
            return 0.0, False
        
        if screen is None:
            screen = self.grab_screen()
        th, tw = tmpl_bgr.shape[:2]
        sh, sw = screen.shape[:2]
        
        best_val = -1.0
        for scale in [1.0, 0.9, 1.1, 0.8, 1.2]:
            nw = int(tw * scale)
            nh = int(th * scale)
            if nw < 8 or nh < 8 or nh > sh or nw > sw:
                continue
            if scale == 1.0:
                scaled = tmpl_bgr
            else:
                interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
                scaled = cv2.resize(tmpl_bgr, (nw, nh), interpolation=interp)
            result = cv2.matchTemplate(screen, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > best_val:
                best_val = max_val
            if best_val >= threshold:
                break
        
        return best_val, best_val >= threshold

    def is_maintaining(self, screen=None):
        """Detect 'Maintaining' status with double confirmation."""
        tmpl_path = self.cfg.template_dir / "maintaining.png"
        conf, found = self._match_status_template(tmpl_path, threshold=0.75, screen=screen)
        if not found:
            return False
        # Confirm once more
        self._sleep(0.3)
        conf2, found2 = self._match_status_template(tmpl_path, threshold=0.75)
        if found2:
            self.log(f"Maintaining confirmed (conf: {conf:.2f}, {conf2:.2f})")
        return found2

    def is_deficit(self, screen=None):
        """Detect 'Deficit' status with double confirmation."""
        tmpl_path = self.cfg.template_dir / "deficit.png"
        conf, found = self._match_status_template(tmpl_path, threshold=0.65, screen=screen)
        if not found:
            return False
        self._sleep(0.3)
        conf2, found2 = self._match_status_template(tmpl_path, threshold=0.65)
        if found2:
            self.log(f"Deficit confirmed (conf: {conf:.2f}, {conf2:.2f})")
        return found2

    def is_cutting(self, screen=None):
        """Detect 'Cutting' status with double confirmation. Stops the macro."""
        tmpl_path = self.cfg.template_dir / "cutting.png"
        if not tmpl_path.exists():
            return False
        conf, found = self._match_status_template(tmpl_path, threshold=0.65, screen=screen)
        if not found:
            return False
        self._sleep(0.3)
        conf2, found2 = self._match_status_template(tmpl_path, threshold=0.65)
        if found2:
            self.log(f"Cutting confirmed (conf: {conf:.2f}, {conf2:.2f})")
        return found2

    def machine_prompt_templates(self):
        return sorted(self.cfg.template_dir.glob("machine_prompt*.png"))

    def obstruction_templates(self):
        patterns = ["floor_trash*.png", "waterspill*.png", "obstruction_*.png"]
        found = []
        for pattern in patterns:
            found.extend(self.cfg.template_dir.glob(pattern))
        return sorted(set(found))

    def select_exercise_menu_templates(self):
        patterns = ["select_exercise_menu*.png", "exercise_menu*.png"]
        found = []
        for pattern in patterns:
            found.extend(self.cfg.template_dir.glob(pattern))
        return sorted(set(found))

    def close_menu_templates(self):
        patterns = ["close_menu*.png", "menu_close*.png", "close_x*.png", "menu_x*.png", "close_button*.png"]
        found = []
        for pattern in patterns:
            found.extend(self.cfg.template_dir.glob(pattern))
        return sorted(set(found))

    def type_word_prompt_templates(self):
        patterns = ["type_word_prompt*.png", "word_prompt*.png", "type_prompt*.png"]
        found = []
        for pattern in patterns:
            found.extend(self.cfg.template_dir.glob(pattern))
        return sorted(set(found))

    def food_item_templates(self):
        """find food templates (excludes shaker/creatine since those arent food)"""
        patterns = ["food_*.png", "eat_*.png"]
        found = []
        for pattern in patterns:
            found.extend(self.cfg.template_dir.glob(pattern))
        # exclude shaker and creatine since they dont give calories
        exclude = {"food_shaker.png", "food_creatine.png"}
        return sorted(f for f in set(found) if f.name not in exclude)

    def close_exercise_menu(self):
        # find menu header first to know where the menu is
        menu_templates = self.select_exercise_menu_templates()
        menu_match = None
        for m_path in menu_templates:
            menu_match = self.find_on_screen(m_path)
            if menu_match:
                break
        
        if menu_match:
            x, y, conf = menu_match
            # try clicking X at multiple offsets from header (different menu sizes)
            for offset_x, offset_y in [(200, -5), (250, -5), (180, 0), (220, -10), (160, 5)]:
                self.move_and_click(x + offset_x, y + offset_y)
                self._sleep(0.3)
                if not self._menu_still_open():
                    self.rest_mouse()
                    return True
        
        # try X template directly
        close_templates = self.close_menu_templates()
        for c_path in close_templates:
            if self.click_template(c_path):
                self._sleep(0.3)
                self.rest_mouse()
                return True
        
        # last resort: try clicking where X typically is (top right of screen center area)
        m = self._monitor
        for attempt in range(3):
            # X is usually top-right of a centered popup
            click_x = m["left"] + int(m["width"] * 0.58) + (attempt * 20)
            click_y = m["top"] + int(m["height"] * 0.25)
            pydirectinput.moveTo(click_x + 2, click_y + 2)
            time.sleep(0.02)
            pydirectinput.moveTo(click_x, click_y)
            time.sleep(0.06)
            pydirectinput.click()
            self._sleep(0.3)
            if not self._menu_still_open():
                self.rest_mouse()
                return True
        
        self.log("⚠️ Could not close exercise menu!")
        return False

    def _menu_still_open(self, screen=None):
        menu_templates = self.select_exercise_menu_templates()
        if not menu_templates:
            return None
        return any(self.find_on_screen(m, screen=screen) for m in menu_templates)

    def workout_templates(self):
        return sorted(self.cfg.template_dir.glob("workout_*.png"))

    def missing_required_templates(self):
        required = ["maintaining.png"]
        missing = [name for name in required if not (self.cfg.template_dir / name).exists()]
        if self.cfg.chosen_workout != "Abs" and not self.workout_templates():
            missing.append("workout_*.png (at least one)")
        if not self.machine_prompt_templates():
            missing.append("machine_prompt*.png (at least one)")
        return missing

    def approach_and_interact(self):
        self.log("Looking for machine prompt...")
        prompts = self.machine_prompt_templates()
        if not prompts:
            self.log("No machine_prompt*.png templates found!")
            return False

        match = self._wait_for_prompt_clearing_obstructions(prompts, timeout=self.cfg.prompt_search_timeout)
        if not match:
            self._prompt_miss_count += 1
            self.log(f"Machine prompt not found this attempt ({self._prompt_miss_count} in a row).")

            # Send a screenshot to Discord so you can see what's wrong
            if self._prompt_miss_count == 1 or self._prompt_miss_count % 3 == 0:
                self.send_discord_screenshot(f"⚠️ Can't find machine prompt (attempt #{self._prompt_miss_count}):")

            if self._prompt_miss_count % self.cfg.prompt_miss_unstick_after == 0:
                # Don't press space if exercise menu is open (would make character jump)
                if self._menu_still_open():
                    self.log("Exercise menu is open, trying to click workout instead of space...")
                    self.choose_workout()
                else:
                    self.log(f"⚠️ Watchdog: no machine prompt found. Pressing space...")
                    self.send_input("space")

            # Try zooming in/out to find the machine prompt at different camera angles
            self._try_zoom_to_find_prompt()

            if self._prompt_miss_count >= self.cfg.prompt_miss_ping_after and not self._prompt_stuck_pinged:
                self.log(f"⚠️ Stuck: no machine prompt found. Pinging Discord.")
                self.send_discord_ping(f"⚠️ Gym macro: stuck looking for the machine prompt - might need attention.")
                self.send_discord_screenshot("⚠️ Current screen:")
                self._prompt_stuck_pinged = True

            # If stuck for way too long, assume disconnected
            if self._prompt_miss_count >= self.cfg.prompt_miss_ping_after * 3:
                self.log("🚨 Likely disconnected — too many missed prompts.")
                self.send_discord_ping("🚨 Gym macro: **Likely disconnected!** Stopping macro.")
                self.send_discord_screenshot("🚨 Screen at disconnect:")
                raise StoppedException()

            return False

        self._prompt_miss_count = 0  
        self._prompt_stuck_pinged = False

        x, y, conf = match
        if self.cfg.click_prompt_instead_of_key:
            self.move_and_click(x, y)
            self.log(f"Clicked machine prompt at ({x},{y})")
            if self.cfg.also_press_key_when_clicking:
                self._sleep(0.2)
                self.send_input(self.cfg.key_interact, self.cfg.key_interact_hold)
        else:
            self.send_input(self.cfg.key_interact, self.cfg.key_interact_hold)

        self.rest_mouse()
        self._sleep(0.1 if self.cfg.one_rep_off else 0.3)
        return True

    def _try_zoom_to_find_prompt(self):
        """Try zooming in/out to adjust camera and find the machine prompt."""
        m = self._monitor
        center_x = m["left"] + m["width"] // 2
        center_y = m["top"] + m["height"] // 2
        pydirectinput.moveTo(center_x, center_y)
        time.sleep(0.05)
        if self._prompt_miss_count % 2 == 1:
            self.log("Trying zoom in to find prompt...")
            pyautogui.scroll(3, x=center_x, y=center_y)
        else:
            self.log("Trying zoom out to find prompt...")
            pyautogui.scroll(-3, x=center_x, y=center_y)
        time.sleep(0.3)

    def _wait_for_prompt_clearing_obstructions(self, prompt_templates, timeout=None):
        start = time.time()
        last_report = 0
        best_seen = 0.0

        while True:
            self._check_stop()
            # ONE screen grab per loop iteration — shared across all template checks
            screen = self.grab_screen()

            obstructions = self.obstruction_templates()
            obstruction_cleared_this_loop = False
            for obstruction in obstructions:
                conf, found = self.find_on_screen(
                    obstruction, return_confidence=True,
                    custom_threshold=self.cfg.obstruction_confidence_threshold,
                    screen=screen,
                )
                if found:
                    self.log(f"Obstruction '{obstruction.name}' detected - clearing...")
                    self.send_input(self.cfg.key_interact, self.cfg.obstruction_hold_seconds)
                    self._sleep(0.2)  
                    obstruction_cleared_this_loop = True
                    break  

            if not obstruction_cleared_this_loop and self._menu_still_open(screen=screen):
                self.close_exercise_menu()
                self._sleep(0.3)
                continue  

            if not obstruction_cleared_this_loop:
                found_match = None
                best_conf_this_loop = 0.0
                for t_path in prompt_templates:
                    conf, found = self.find_on_screen(t_path, return_confidence=True, screen=screen)
                    best_conf_this_loop = max(best_conf_this_loop, conf)
                    if found:
                        found_match = self.find_on_screen(t_path, screen=screen)
                        break

                if found_match:
                    return found_match

                best_seen = max(best_seen, best_conf_this_loop)
                if time.time() - last_report > 3:
                    self.log(f"Still looking for machine prompt(s)... best: {best_seen:.2f}")
                    last_report = time.time()
                last_report = time.time()

            if timeout and (time.time() - start) > timeout:
                return None

            self._sleep(self.cfg.poll_interval)

    def choose_workout(self):
        if self.cfg.chosen_workout == "Abs":
            self.log("Abs selected. Skipping menu search step entirely.")
            return True

        if self.cfg.chosen_workout and self.cfg.chosen_workout != "Any":
            target = self.cfg.template_dir / self.cfg.chosen_workout
            if not target.exists():
                return False

            self.log(f"Looking specifically for '{self.cfg.chosen_workout}'...")
            match = self.wait_for_template(target, timeout=10)
            if not match:
                return False

            return self._click_and_confirm_menu_closed(lambda: self.click_template(target))

        templates = self.workout_templates()
        if not templates:
            return True

        random.shuffle(templates)

        def click_any():
            for template in templates:
                self._check_stop()
                if self.click_template(template):
                    return True
            return False

        return self._click_and_confirm_menu_closed(click_any)

    def _click_and_confirm_menu_closed(self, click_fn):
        for attempt in range(1, self.cfg.workout_menu_retry_attempts + 1):
            self._check_stop()
            clicked = click_fn()
            if not clicked:
                return False

            self._sleep(0.2 if self.cfg.one_rep_off else 0.5)
            still_open = self._menu_still_open()

            if still_open is None:
                self.rest_mouse()
                return True  
            if not still_open:
                self.rest_mouse()
                return True  

        self.log("Gave up: select exercise menu still open. Clicking 'X' to close menu...")
        self.close_exercise_menu()
        return False

    def handle_type_word_prompt(self, match_data):
        x, y, conf = match_data
        self.log(f"Processing type-a-word challenge found near ({x}, {y})...")
        
        screen = self.grab_screen()
        h_scr, w_scr = screen.shape[:2]
        
        roi_x1 = max(0, x - 150)
        roi_y1 = max(0, y - 50)
        roi_x2 = min(w_scr, x + 150)
        roi_y2 = min(h_scr, y + 100)
        
        crop = screen[roi_y1:roi_y2, roi_x1:roi_x2]
        
        detected_word = ""
        if pytesseract is not None:
            try:
                gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                resized = cv2.resize(gray, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                thresh = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
                
                raw_text = pytesseract.image_to_string(thresh, config='--psm 7')
                self.log(f"Raw OCR text read from popup: '{raw_text.strip()}'")
                
                cleaned_text = re.sub(r'(?i)type\s*:\s*', '', raw_text)
                detected_word = "".join([c for c in cleaned_text.strip() if c.isalnum()])
                self.log(f"Target word to type after stripping prefix: '{detected_word}'")
            except Exception as ocr_err:
                self.log(f"OCR Parsing engine exception encountered: {ocr_err}")
        else:
            self.log("Notice: Tesseract OCR modules unavailable. Defaulting to fallback focus.")
            
        click_target_x = x - 20
        click_target_y = y + 45
        
        self.log(f"Focusing input text box at coordinates ({click_target_x}, {click_target_y})")
        self.move_and_click(click_target_x, click_target_y)
        self._sleep(0.3)
        
        if detected_word:
            for char in detected_word:
                pydirectinput.press(char.lower())
                time.sleep(0.03)
        else:
            pydirectinput.press("space")
            
        self._sleep(0.1)
        pydirectinput.press("enter")
        self.log("Pressed Enter key to submit.")
        self._sleep(0.2)
        
        checkmark_x = x + 85
        checkmark_y = y + 45
        self.move_and_click(checkmark_x, checkmark_y)
        self.log("Clicked checkmark submit button as backup.")
        
        self._sleep(0.5)
        self.rest_mouse()

    def handle_unexpected_prompts(self):
        obstructions = self.obstruction_templates()
        for obstruction in obstructions:
            conf, found = self.find_on_screen(
                obstruction, return_confidence=True,
                custom_threshold=self.cfg.obstruction_confidence_threshold,
            )
            if found:
                self.log(f"Obstruction '{obstruction.name}' detected - holding key to close...")
                self.send_input(self.cfg.key_interact, self.cfg.obstruction_hold_seconds)
                self._sleep(0.3)
                return None

        word_prompts = self.type_word_prompt_templates()
        for wp in word_prompts:
            match = self.find_on_screen(wp, custom_threshold=0.88)
            if match:
                # Confirm to avoid false positive
                self._sleep(0.3)
                match2 = self.find_on_screen(wp, custom_threshold=0.88)
                if match2:
                    self.handle_type_word_prompt(match2)
                    return "word_prompt_handled"

        if self._menu_still_open():
            return "menu_reopened"

        return None

    def use_creatine_shaker(self):
        """
        Uses the creatine shaker:
        1. Press 1 to select shaker from hotbar (already equipped)
        2. Click to open shaker UI
        3. Click circle → click creatine × 5
        4. Click confirm to drink
        """
        self.log("🧪 Using creatine shaker...")
        
        # Dismiss tug of war / type word prompt if on screen before opening shaker
        word_prompts = self.type_word_prompt_templates()
        for wp in word_prompts:
            match = self.find_on_screen(wp, custom_threshold=0.88)
            if match:
                self.log("  Tug of war prompt detected, dismissing...")
                self.handle_type_word_prompt(match)
                self._sleep(1.0)
                break
        
        # Select shaker from hotbar slot 1 — UI opens automatically
        self.send_input("1")
        self._sleep(2.0)
        
        # Templates needed for the creatine fill process
        empty_circle_tmpl = self.cfg.template_dir / "empty_circle.png"
        creatine_tmpl = self.cfg.template_dir / "food_creatine.png"
        
        if not creatine_tmpl.exists():
            self.log("⚠️ food_creatine.png template not found!")
            self.rest_mouse()
            return
        
        # Get 7th circle position
        if self.cfg.shaker_circle_x > 0 and self.cfg.shaker_circle_y > 0:
            fifth_x = self.cfg.shaker_circle_x
            fifth_y = self.cfg.shaker_circle_y
        elif hasattr(self, '_saved_circle_pos') and self._saved_circle_pos:
            fifth_x, fifth_y = self._saved_circle_pos
            self.log(f"  Using saved circle position ({fifth_x}, {fifth_y})")
        else:
            # Auto-detect rightmost empty circle
            screen = self.grab_screen()
            screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            tmpl_bgr, tmpl_gray = self._load_template(empty_circle_tmpl)
            if tmpl_gray is None:
                self.log("⚠️ Could not load empty_circle template.")
                return
            
            th, tw = tmpl_gray.shape[:2]
            result = cv2.matchTemplate(screen_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
            locations = np.where(result >= 0.60)
            
            circles = []
            for pt_y, pt_x in zip(*locations):
                cx, cy = pt_x + tw//2, pt_y + th//2
                too_close = any(abs(cx - fx) < tw//3 and abs(cy - fy) < th//3 for fx, fy in circles)
                if not too_close:
                    circles.append((cx, cy))
            
            if not circles:
                self.log("⚠️ No circles found.")
                self.rest_mouse()
                return
            
            # Just pick the absolute rightmost circle. Period.
            circles.sort(key=lambda c: c[0])
            m = self._monitor
            fifth_x = m["left"] + circles[-1][0]
            fifth_y = m["top"] + circles[-1][1]
            
            # Save for future use so tug of war can't mess it up next time
            self._saved_circle_pos = (fifth_x, fifth_y)
            self.log(f"  Detected & saved circle position ({fifth_x}, {fifth_y})")
        
        self.log(f"  Clicking far-right circle at ({fifth_x}, {fifth_y})")
        
        # Click circle → click creatine → repeat 5 times to fill 5 circles
        match_c = None
        for i in range(5):
            self._check_stop()
            # Click the far-right circle
            ctypes.windll.user32.SetCursorPos(int(fifth_x), int(fifth_y))
            time.sleep(0.03)
            pydirectinput.click()
            self._sleep(0.5)
            
            # Find creatine (first time) or click same spot
            if match_c is None:
                match_c = self.find_on_screen(creatine_tmpl, custom_threshold=0.60)
                if not match_c:
                    self.log("⚠️ Creatine not found in popup.")
                    break
            cx, cy, cconf = match_c
            ctypes.windll.user32.SetCursorPos(int(cx), int(cy))
            time.sleep(0.03)
            pydirectinput.click()
            self._sleep(0.3)
            self.log(f"  Filled circle #{i+1}")
        
        self._sleep(0.3)
        
        # Click slightly above the circles to trigger the "Drink this shake?" popup
        pydirectinput.moveTo(fifth_x, fifth_y - 80)
        time.sleep(0.06)
        pydirectinput.click()
        self._sleep(1.0)
        
        # Step 3: Click the green "Confirm" button to drink the shake
        self._sleep(1.0)  # Wait for popup to fully appear
        confirm_tmpl = self.cfg.template_dir / "drink_button.png"
        confirmed = False
        
        # Simple native-scale-only search for the Confirm button (no multi-scale)
        for attempt in range(3):
            self._check_stop()
            tmpl_bgr, tmpl_gray = self._load_template(confirm_tmpl)
            if tmpl_bgr is None:
                self.log("⚠️ drink_button.png could not be loaded!")
                break
            
            screen = self.grab_screen()
            result = cv2.matchTemplate(screen, tmpl_bgr, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            self.log(f"  Confirm search attempt #{attempt+1}: conf={max_val:.2f} at {max_loc}")
            
            if max_val >= 0.50:
                h, w = tmpl_bgr.shape[:2]
                click_x = max_loc[0] + w // 2
                click_y = max_loc[1] + h // 2
                pydirectinput.moveTo(click_x + 2, click_y + 2)
                time.sleep(0.02)
                pydirectinput.moveTo(click_x, click_y)
                time.sleep(0.06)
                pydirectinput.click()
                confirmed = True
                self.log(f"  Clicked Confirm at ({click_x}, {click_y})")
                break
            self._sleep(0.5)
        
        if not confirmed:
            self.log("⚠️ Could not find Confirm button!")
        
        self._sleep(0.5)
        self.log("✅ Creatine shake done!")
        self.rest_mouse()

    def use_preworkout_shaker(self):
        """same as creatine shaker but only 1 scoop of pre workout"""
        self.log("⚡ Using pre workout shaker...")
        
        # make sure we're off the machine and away from prompts
        self.send_input(self.cfg.key_exit_machine, self.cfg.key_exit_hold)
        self._sleep(0.5)
        self.rest_mouse()
        self._sleep(0.3)
        
        # select shaker from hotbar
        self.send_input("1")
        self._sleep(1.5)
        
        # templates
        preworkout_tmpl = self.cfg.template_dir / "food_preworkout.png"
        if not preworkout_tmpl.exists():
            self.log("  ⚠️ food_preworkout.png template not found!")
            return
        
        # find circle position using template — pick rightmost to avoid tug of war
        empty_circle_tmpl = self.cfg.template_dir / "empty_circle.png"
        if self.cfg.shaker_circle_x > 0 and self.cfg.shaker_circle_y > 0:
            fifth_x = self.cfg.shaker_circle_x
            fifth_y = self.cfg.shaker_circle_y
        elif empty_circle_tmpl.exists():
            # find all circles and pick rightmost
            screen = self.grab_screen()
            screen_gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY)
            tmpl_bgr, tmpl_gray = self._load_template(empty_circle_tmpl)
            if tmpl_gray is not None:
                th, tw = tmpl_gray.shape[:2]
                result = cv2.matchTemplate(screen_gray, tmpl_gray, cv2.TM_CCOEFF_NORMED)
                locations = np.where(result >= 0.55)
                circles = []
                for pt_y, pt_x in zip(*locations):
                    cx, cy = pt_x + tw//2, pt_y + th//2
                    too_close = any(abs(cx - fx) < tw//2 for fx, _ in circles)
                    if not too_close:
                        circles.append((cx, cy))
                if circles:
                    circles.sort(key=lambda c: c[0])
                    m = self._monitor
                    # pick rightmost circle
                    fifth_x = m["left"] + circles[-1][0]
                    fifth_y = m["top"] + circles[-1][1]
                    self.log(f"  Found rightmost circle at ({fifth_x}, {fifth_y})")
                else:
                    m = self._monitor
                    fifth_x = m["left"] + int(m["width"] * 0.6)
                    fifth_y = m["top"] + int(m["height"] * 0.78)
            else:
                m = self._monitor
                fifth_x = m["left"] + int(m["width"] * 0.6)
                fifth_y = m["top"] + int(m["height"] * 0.78)
        else:
            m = self._monitor
            fifth_x = m["left"] + int(m["width"] * 0.6)
            fifth_y = m["top"] + int(m["height"] * 0.78)
        ctypes.windll.user32.SetCursorPos(int(fifth_x), int(fifth_y))
        time.sleep(0.03)
        pydirectinput.click()
        self._sleep(0.5)
        
        # click pre workout once (1 scoop)
        match_p = self.find_on_screen(preworkout_tmpl, custom_threshold=0.60)
        if match_p:
            px, py, pconf = match_p
            pydirectinput.moveTo(px + 2, py + 2)
            time.sleep(0.02)
            pydirectinput.moveTo(px, py)
            time.sleep(0.06)
            pydirectinput.click()
            self._sleep(0.4)
            self.log("  Added 1 scoop pre workout.")
        else:
            self.log("  ⚠️ Pre workout not found in popup.")
            return
        
        # click above to trigger confirm popup
        pydirectinput.moveTo(fifth_x, fifth_y - 80)
        time.sleep(0.06)
        pydirectinput.click()
        self._sleep(1.0)
        
        # click confirm
        confirm_tmpl = self.cfg.template_dir / "drink_button.png"
        confirmed = False
        for attempt in range(3):
            self._check_stop()
            try:
                import pytesseract as _pyt
            except ImportError:
                pass
            tmpl_bgr, tmpl_gray = self._load_template(confirm_tmpl)
            if tmpl_bgr is not None:
                screen = self.grab_screen()
                result = cv2.matchTemplate(screen, tmpl_bgr, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                if max_val >= 0.50:
                    h, w = tmpl_bgr.shape[:2]
                    click_x = max_loc[0] + w // 2
                    click_y = max_loc[1] + h // 2
                    pydirectinput.moveTo(click_x + 2, click_y + 2)
                    time.sleep(0.02)
                    pydirectinput.moveTo(click_x, click_y)
                    time.sleep(0.06)
                    pydirectinput.click()
                    confirmed = True
                    break
            self._sleep(0.5)
        
        if not confirmed:
            self.log("  ⚠️ Could not find Confirm button for pre workout!")
        
        self._sleep(0.5)
        self.log("✅ Pre workout consumed!")
        self.rest_mouse()
        
        self.rest_mouse()

    def work_out_until_low_stamina(self, skip_mode_wait=False):
        self.log("Working out...")
        start_time = time.time()
        last_fingerprint = None
        last_vision_check = 0
        stall_count = 0
        last_stam_number = -1
        stam_same_count = 0  # how many consecutive unchanged checks
        ocr_fail_count = 0

        # Check if this is a forearm workout (needs delay between clicks)
        is_forearm = "forearm" in self.cfg.chosen_workout.lower()
        is_strength = self.cfg.workout_mode.lower() == "strength"
        is_junk = self.cfg.workout_mode.lower() == "junk"
        click_delay = 0.2 if is_forearm else 0.0

        # Only wait for mode indicator on first entry, not after regen
        if not skip_mode_wait:
            if is_junk:
                mode_tmpl = self.cfg.template_dir / "junk.png"
                mode_label = "Junk"
            elif is_strength:
                mode_tmpl = self.cfg.template_dir / "strength.png"
                mode_label = "Strength"
            else:
                mode_tmpl = self.cfg.template_dir / "hypertrophy.png"
                mode_label = "Hypertrophy"

            if mode_tmpl.exists() and not self.cfg.one_rep_off:
                self.log(f"Waiting for {mode_label} indicator...")
                match = self.wait_for_template(mode_tmpl, timeout=1, label=mode_label)
                if match:
                    self.log(f"{mode_label} detected, starting workout.")
                    # Read weight — we're on the machine and panel is visible
                    if self.cfg.progress_report_enabled:
                        weight = self._read_weight_from_screen()
                        if weight:
                            self._last_weight_kg = weight
                else:
                    self.log(f"{mode_label} not seen after 8s, starting anyway.")
            else:
                self._sleep(2.0)

        # One rep off mode: do 1 click then immediately get off — no waiting
        if self.cfg.one_rep_off:
            if self.cfg.key_workout_action.strip().lower() in self._MOUSE_ALIASES:
                _raw_click()
            else:
                self.send_input(self.cfg.key_workout_action, self.cfg.key_workout_hold)
            time.sleep(0.1)
            self._total_reps += 1
            self._sets_done += 1
            if self.cfg.progress_report_enabled and self._sets_done % self.cfg.progress_report_interval == 0:
                weight = self._read_weight_from_screen()
                if weight:
                    self._last_weight_kg = weight
                self._send_progress_report()
            return "low_stamina"

        # Junk mode: click fast, only stops on maintaining (unless no-food) or stamina stall.
        if is_junk:
            self.log(f"Junk mode — farming crew XP. No food: {self.cfg.junk_no_food}")
            last_fingerprint_j = None
            last_vision_j = 0
            stall_count_j = 0
            last_stam_number = -1
            stam_same_count = 0
            ocr_fail_count = 0
            last_stam_frame = None
            stam_history = []
            lowest_stam_seen = 100

            # Check if shaker timer has elapsed (works across regen cycles)
            if self.cfg.junk_use_shaker and (time.time() - self._last_shaker_use) >= self.cfg.junk_shaker_interval * 60:
                self._last_shaker_use = time.time()
                # Make sure we're off the machine first
                self.send_input(self.cfg.key_exit_machine, self.cfg.key_exit_hold)
                self._sleep(0.5)
                self.use_creatine_shaker()
                # Re-approach machine after shaker
                if not self.approach_and_interact():
                    self._sleep(1)
                if not self.choose_workout():
                    self._sleep(1)

            # Check if pre workout timer has elapsed
            if self.cfg.junk_use_preworkout and (time.time() - self._last_preworkout_use) >= self.cfg.junk_preworkout_interval * 60:
                self._last_preworkout_use = time.time()
                self.send_input(self.cfg.key_exit_machine, self.cfg.key_exit_hold)
                self._sleep(0.5)
                self.use_preworkout_shaker()
                if not self.approach_and_interact():
                    self._sleep(1)
                if not self.choose_workout():
                    self._sleep(1)

            while True:
                self._check_stop()
                if self.cfg.key_workout_action.strip().lower() in self._MOUSE_ALIASES and click_delay <= 0:
                    for _ in range(10):
                        _raw_click()
                        time.sleep(0.12)
                    self._total_reps += 10
                else:
                    for _ in range(10):
                        self.send_input(self.cfg.key_workout_action, self.cfg.key_workout_hold)
                        if click_delay > 0:
                            time.sleep(click_delay)
                    self._total_reps += 10

                now = time.time()

                if now - last_vision_j >= 0.5:
                    last_vision_j = now
                    screen = self.grab_screen()
                    # skip maintaining check for first 10s of each set
                    if not self.cfg.junk_no_food and (time.time() - start_time) > 10 and self.is_maintaining(screen=screen):
                        return "maintaining"
                    
                    # Junk stall: read stamina number with OCR, get off when it hits 0-1
                    h, w = screen.shape[:2]
                    
                    # Use stamina_text.png template to locate the stamina label precisely
                    stam_tmpl = self.cfg.template_dir / "stamina_text.png"
                    if stam_tmpl.exists():
                        match = self.find_on_screen(stam_tmpl, custom_threshold=0.6, screen=screen)
                        if match:
                            sx, sy, _ = match
                            m = self._monitor
                            rel_x = sx - m["left"]
                            rel_y = sy - m["top"]
                            tmpl_bgr, tmpl_gray = self._load_template(stam_tmpl)
                            th, tw = tmpl_gray.shape[:2]
                            # The stamina number is right after the "Stamina" text
                            # Start crop right at the template center (overlaps label end)
                            # to make sure we capture "XX/100" not just "/100"
                            num_x1 = max(0, rel_x)
                            num_x2 = min(w, rel_x + tw + int(w*0.15))
                            num_y1 = max(0, rel_y - th)
                            num_y2 = min(h, rel_y + th)
                            stam_roi = screen[num_y1:num_y2, num_x1:num_x2]
                        else:
                            stam_roi = screen[int(h*0.92):h, 0:int(w*0.30)]
                    else:
                        stam_roi = screen[int(h*0.92):h, 0:int(w*0.30)]
                    try:
                        import pytesseract as _pyt
                        _local = Path(__file__).parent / "Tesseract-OCR" / "tesseract.exe"
                        if _local.exists():
                            _pyt.pytesseract.tesseract_cmd = str(_local)
                        else:
                            _pyt.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
                        gray = cv2.cvtColor(stam_roi, cv2.COLOR_BGR2GRAY)
                        # Scale up 3x for better OCR on small text
                        scaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                        _, thresh = cv2.threshold(scaled, 180, 255, cv2.THRESH_BINARY)
                        raw = _pyt.image_to_string(thresh, config='--psm 7 -c tessedit_char_whitelist=0123456789/')
                        self.log(f"  OCR raw: '{raw.strip()}'")
                        # Find all numbers in the raw text
                        all_nums = re.findall(r'\d+', raw)
                        current_stam = None
                        if len(all_nums) >= 2:
                            # Format is "XX 100" or "XX/100" — first number is current stamina
                            first = int(all_nums[0])
                            if 0 <= first <= 100:
                                current_stam = first
                        elif len(all_nums) == 1:
                            val = int(all_nums[0])
                            # Single number — only use if it's NOT 100 (that's the max)
                            if 0 <= val < 100:
                                current_stam = val
                        
                        if current_stam is not None:
                            self.log(f"  Junk stamina read: {current_stam}")
                            ocr_fail_count = 0
                            if current_stam <= 1:
                                stam_same_count += 1
                                if stam_same_count >= 3:
                                    self.log(f"Stamina at {current_stam} for 3 reads, getting off.")
                                    return "low_stamina"
                            else:
                                stam_same_count = 0
                            last_stam_number = current_stam
                        else:
                            # OCR returned nothing — if last read was low, stamina is probably at 0
                            ocr_fail_count += 1
                            if last_stam_number >= 0 and last_stam_number <= 10 and ocr_fail_count >= 3:
                                self.log(f"OCR empty after reading {last_stam_number}, stamina depleted. Getting off.")
                                return "low_stamina"
                    except Exception:
                        ocr_fail_count += 1
                        if last_stam_number >= 0 and last_stam_number <= 10 and ocr_fail_count >= 3:
                            self.log(f"OCR failed after reading {last_stam_number}, getting off.")
                            return "low_stamina"

        while True:
            self._check_stop()

            # click and check stamina after each click to count reps accurately
            if self.cfg.key_workout_action.strip().lower() in self._MOUSE_ALIASES and click_delay <= 0:
                _raw_click()
                time.sleep(0.12)
            else:
                self.send_input(self.cfg.key_workout_action, self.cfg.key_workout_hold)
                if click_delay > 0:
                    time.sleep(click_delay)

            # check stamina every click
            now = time.time()
            if now - last_vision_check >= 1.0:
                last_vision_check = now
                screen = self.grab_screen()

                # Use stamina_text.png template to locate stamina number
                h, w = screen.shape[:2]
                stam_tmpl = self.cfg.template_dir / "stamina_text.png"
                if stam_tmpl.exists():
                    match = self.find_on_screen(stam_tmpl, custom_threshold=0.6, screen=screen)
                    if match:
                        sx, sy, _ = match
                        m = self._monitor
                        rel_x = sx - m["left"]
                        rel_y = sy - m["top"]
                        tmpl_bgr, tmpl_gray = self._load_template(stam_tmpl)
                        th, tw = tmpl_gray.shape[:2]
                        num_x1 = max(0, rel_x)
                        num_x2 = min(w, rel_x + tw + int(w*0.15))
                        num_y1 = max(0, rel_y - th)
                        num_y2 = min(h, rel_y + th)
                        stam_roi = screen[num_y1:num_y2, num_x1:num_x2]
                    else:
                        stam_roi = screen[int(h*0.92):h, 0:int(w*0.30)]
                else:
                    stam_roi = screen[int(h*0.92):h, 0:int(w*0.30)]
                try:
                    import pytesseract as _pyt
                    _local = Path(__file__).parent / "Tesseract-OCR" / "tesseract.exe"
                    if _local.exists():
                        _pyt.pytesseract.tesseract_cmd = str(_local)
                    else:
                        _pyt.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
                    gray = cv2.cvtColor(stam_roi, cv2.COLOR_BGR2GRAY)
                    scaled = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
                    _, thresh = cv2.threshold(scaled, 180, 255, cv2.THRESH_BINARY)
                    raw = _pyt.image_to_string(thresh, config='--psm 7 -c tessedit_char_whitelist=0123456789/')
                    all_nums = re.findall(r'\d+', raw)
                    current_stam = None
                    if len(all_nums) >= 2:
                        first = int(all_nums[0])
                        if 0 <= first <= 100:
                            current_stam = first
                    elif len(all_nums) == 1:
                        val = int(all_nums[0])
                        if 0 <= val < 100:
                            current_stam = val
                    if current_stam is not None:
                        ocr_fail_count = 0
                        if current_stam == last_stam_number:
                            stam_same_count += 1
                        else:
                            stam_same_count = 0
                            last_stam_number = current_stam
                            self._total_reps += 1  # stamina dropped = rep done
                        if stam_same_count >= 3:
                            # stamina stuck for 3 checks — done with set
                            if self.cfg.progress_report_enabled:
                                weight = self._read_weight_from_screen()
                                if weight:
                                    self._last_weight_kg = weight
                                self._sets_done += 1
                                min_reps = self._sets_done * self.cfg.reps_per_set
                                if self._total_reps < min_reps:
                                    self._total_reps = min_reps
                                if self._sets_done % self.cfg.progress_report_interval == 0:
                                    self._send_progress_report()
                            self.log(f"Stamina stuck at {current_stam} for 3 reads, getting off to regen.")
                            return "low_stamina"
                    else:
                        ocr_fail_count += 1
                        if last_stam_number >= 0 and last_stam_number <= 10 and ocr_fail_count >= 3:
                            self.log(f"OCR empty after reading {last_stam_number}, getting off.")
                            return "low_stamina"
                        elif ocr_fail_count >= 10:
                            self.log("OCR failed 10 times, getting off to regen.")
                            return "low_stamina"
                except Exception:
                    ocr_fail_count += 1
                    if ocr_fail_count >= 10:
                        self.log("OCR exception 10 times, getting off to regen.")
                        return "low_stamina"

                # Still check captcha since it blocks input
                word_prompts = self.type_word_prompt_templates()
                for wp in word_prompts:
                    match = self.find_on_screen(wp, custom_threshold=0.88, screen=screen)
                    if match:
                        self._sleep(0.3)
                        match2 = self.find_on_screen(wp, custom_threshold=0.88)
                        if match2:
                            self.handle_type_word_prompt(match2)
                        break

            if (time.time() - start_time) > 35:
                return "low_stamina"

    def bulk_buy_food(self):
        """
        Bulk buy chicken from the shop. Looks for the chicken buy button template
        and clicks it repeatedly for the configured amount. Pings Discord when done
        so user can set up the macro position.
        """
        if not self.cfg.bulk_buy_enabled or self.cfg.bulk_buy_amount <= 0:
            return
        
        buy_template = self.cfg.template_dir / "buy_chicken.png"
        if not buy_template.exists():
            self.log("⚠️ buy_chicken.png template not found! Cannot bulk buy.")
            return
        
        total_cost = self.cfg.bulk_buy_amount * self.cfg.bulk_buy_price
        self.log(f"🛒 Bulk buying {self.cfg.bulk_buy_amount} chicken (${total_cost} total)...")
        
        # First, find the chicken item in the store (may need to scroll)
        match = None
        for scroll_attempt in range(5):
            match = self.find_on_screen(buy_template, custom_threshold=0.70)
            if match:
                break
            # Scroll down in the store menu to find chicken
            self.log(f"Scrolling store to find chicken (attempt {scroll_attempt + 1})...")
            pyautogui.scroll(-3, x=pyautogui.position()[0], y=pyautogui.position()[1])
            self._sleep(0.5)
        
        if not match:
            self.log("⚠️ Could not find chicken in store after scrolling. Stopping.")
            return
        
        # Found it — now click it repeatedly to buy
        x, y, conf = match
        self.log(f"Found chicken at ({x}, {y}), clicking to buy...")
        
        bought = 0
        for i in range(self.cfg.bulk_buy_amount):
            self._check_stop()
            # Click the chicken row — menu stays open, just keep clicking same spot
            pydirectinput.moveTo(x + 2, y + 2)
            time.sleep(0.02)
            pydirectinput.moveTo(x, y)
            time.sleep(0.06)
            pydirectinput.click()
            self._sleep(0.3)
            bought += 1
            if bought % 25 == 0:
                self.log(f"  Bought {bought}/{self.cfg.bulk_buy_amount}...")
        
        self.log(f"✅ Bulk buy complete: {bought} chicken purchased (${bought * self.cfg.bulk_buy_price}).")
        
        # Close the store menu — click outside it (top-right area away from menu)
        self.log("Closing store...")
        m = self._monitor
        # Click far right of screen, away from the store popup
        close_x = m["left"] + int(m["width"] * 0.9)
        close_y = m["top"] + int(m["height"] * 0.5)
        pydirectinput.moveTo(close_x, close_y)
        time.sleep(0.06)
        pydirectinput.click()
        self._sleep(0.5)
        # Try clicking the X template if available
        close_templates = self.close_menu_templates()
        for c_path in close_templates:
            if self.click_template(c_path):
                break
        self._sleep(1.0)
        
        # Now eat all the chicken we just bought
        self.log("🍗 Eating all purchased chicken...")
        calories, items = self.scan_and_eat_inventory()
        
        item_summary = ", ".join([f"{count}x {name}" for name, count in items.items()]) if items else "Nothing eaten"
        self.send_discord_ping(
            f"🛒 **Bulk Buy + Eat Complete**\n"
            f"Purchased: **{bought}x Chicken** for **${bought * self.cfg.bulk_buy_price}**\n"
            f"Eaten: `{item_summary}` ({calories} kcal)\n"
            f"Please set up macro position and start!"
        )
        self.send_discord_screenshot("📸 Screen after buying and eating:")

    def scan_and_eat_inventory(self):
        """
        Toggles the backpack, scrolls through the entire inventory looking for food,
        eats any found items. Stops when:
        - eat_limit reached (if set > 0)
        - no more food found in inventory
        - calories stop increasing for eat_stall_timeout seconds (hit max)
        """
        self.log("📋 Opening inventory to scan for food...")
        self.send_input(self.cfg.key_inventory_toggle)
        self._sleep(1.0)
        
        food_options = self.food_item_templates()
        if not food_options:
            self.log("No food asset image templates loaded (food_*.png). Skipping inventory processing.")
            self.send_input(self.cfg.key_inventory_toggle)
            return 0, {}

        consumed_log = {}
        total_calories = 0
        total_items_eaten = 0
        last_calorie_change = time.time()
        last_calorie_count = 0
        eat_limit = self.cfg.eat_limit  # 0 = unlimited
        
        calorie_values = {
            "apple": 95,
            "shake": 350,
            "protein": 280,
            "banana": 105,
            "bar": 210,
            "chicken": 300,
        }

        max_scroll_pages = 20  # Scroll through entire inventory
        max_eat_per_page = 50  # No practical limit per page

        for scroll_page in range(max_scroll_pages):
            self._check_stop()
            items_this_page = 0
            
            while items_this_page < max_eat_per_page:
                self._check_stop()

                # Check eat limit
                if eat_limit > 0 and total_items_eaten >= eat_limit:
                    self.log(f"Eat limit reached ({eat_limit} items). Stopping.")
                    break

                # Check calorie stall — if calories haven't changed, assume full
                if total_items_eaten > 0 and total_calories == last_calorie_count:
                    if (time.time() - last_calorie_change) > self.cfg.eat_stall_timeout:
                        self.log(f"Calories stalled at {total_calories} kcal for {self.cfg.eat_stall_timeout}s. Assuming full.")
                        break
                else:
                    last_calorie_change = time.time()
                    last_calorie_count = total_calories

                found_any = False
                
                for food_t in food_options:
                    match = self.find_on_screen(food_t, custom_threshold=0.78)
                    if match:
                        x, y, conf = match
                        item_label = food_t.stem.replace("food_", "")
                        item_calories = calorie_values.get(item_label, 150)
                        
                        self.log(f"Found '{item_label}' (conf: {conf:.2f}). Equipping...")
                        self.move_and_click(x, y)
                        self._sleep(0.3)
                        
                        # Close inventory
                        self.send_input(self.cfg.key_inventory_toggle)
                        self._sleep(0.3)
                        
                        # Click away from any prompts first (dismiss Use Machine etc)
                        m = self._monitor
                        eat_x = m["left"] + m["width"] // 2
                        eat_y = m["top"] + int(m["height"] * 0.4)
                        pydirectinput.moveTo(eat_x, eat_y)
                        time.sleep(0.06)
                        pydirectinput.click()
                        self._sleep(0.2)
                        
                        # Now click to eat
                        self.log(f"Clicking to eat '{item_label}'...")
                        pydirectinput.click()
                        self._sleep(0.5)
                        
                        # Re-open inventory to look for more
                        self.send_input(self.cfg.key_inventory_toggle)
                        self._sleep(0.5)
                        
                        consumed_log[item_label] = consumed_log.get(item_label, 0) + 1
                        total_calories += item_calories
                        total_items_eaten += 1
                        items_this_page += 1
                        found_any = True
                        break
                
                if not found_any:
                    break

            # Check if we've hit the eat limit before scrolling
            if eat_limit > 0 and total_items_eaten >= eat_limit:
                break
            # Check calorie stall before scrolling
            if total_items_eaten > 0 and total_calories == last_calorie_count:
                if (time.time() - last_calorie_change) > self.cfg.eat_stall_timeout:
                    break
            
            # Scroll down in the inventory to reveal more items
            if scroll_page < max_scroll_pages - 1:
                self.log(f"Scrolling inventory down (page {scroll_page + 1}/{max_scroll_pages})...")
                pyautogui.scroll(-3, x=pyautogui.position()[0], y=pyautogui.position()[1])
                self._sleep(0.5)
        
        self.log(f"Inventory scan complete. Consumed {total_items_eaten} items for {total_calories} kcal.")
        self.send_input(self.cfg.key_inventory_toggle)
        self._sleep(0.5)
        self.rest_mouse()
        return total_calories, consumed_log

    def get_off_and_regen(self):
        self.log("Getting off machine to regen stamina.")
        self.send_input(self.cfg.key_exit_machine, self.cfg.key_exit_hold)
        self.rest_mouse()
        
        # One rep mode: don't wait for full stamina, just get off briefly
        if self.cfg.one_rep_off:
            self._sleep(0.3)
            return "regenerated"
        
        self._sleep(0.3)

        full_template = self.cfg.template_dir / "full_stamina.png"
        start = time.time()

        while True:
            self._check_stop()

            # Just check for full stamina — skip expensive deficit/maintaining checks during regen
            if full_template.exists() and self.find_on_screen(full_template):
                return "regenerated"

            # Fallback timeout
            if (time.time() - start) > self.cfg.regen_fallback_seconds:
                return "regenerated"

            # Tight poll — stamina regens fast
            self._sleep(0.1)

    def run_simple_loop(self):
        """
        Timing-based workout loop with vision-based machine interaction and workout selection.
        Rapid clicks for a fixed duration, fixed regen pause, then uses image recognition
        to find the machine and choose the workout.
        """
        self.log("Starting SIMPLE timing-based loop.")
        self.log(f"  Workout duration: {self.cfg.workout_duration}s @ {self.cfg.click_interval}s interval")
        self.log(f"  Regen pause: {self.cfg.regen_pause}s")
        start_time = time.time()

        # Focus game window
        m = self._monitor
        center_x = m["left"] + m["width"] // 2
        center_y = m["top"] + int(m["height"] * 0.8)
        pydirectinput.moveTo(center_x, center_y)
        pydirectinput.click()
        self._sleep(0.3)

        try:
            # First time: find machine and choose workout via vision
            if not self.approach_and_interact():
                self.log("Could not find machine prompt on first attempt.")
                return
            if not self.choose_workout():
                self.log("Could not choose workout on first attempt.")
                return

            while True:
                self._check_stop()
                if (time.time() - start_time) / 60 > self.cfg.max_loop_minutes:
                    break

                # Step 1: Rapid workout clicking for workout_duration seconds
                self.log("Clicking workout...")
                click_end = time.time() + self.cfg.workout_duration
                while time.time() < click_end:
                    self._check_stop()
                    pydirectinput.click(button="left")
                    jitter = random.uniform(-0.02, 0.02)
                    time.sleep(self.cfg.click_interval + jitter)

                # Step 2: Stamina regeneration pause
                self.log(f"Regen pause ({self.cfg.regen_pause}s)...")
                self._sleep(self.cfg.regen_pause)

                # Check statuses during regen
                if self.is_maintaining():
                    self._handle_maintaining_flow()
                    self.log("Restarting after eating...")
                    self._sleep(3.0)
                if self.is_deficit() or self.is_cutting():
                    self._on_deficit()
                    return

                # Step 3: Use vision to find machine and interact
                if not self.approach_and_interact():
                    self.log("Could not find machine, retrying...")
                    self._sleep(1)
                    continue

                # Step 4: Use vision to choose workout from menu
                if not self.choose_workout():
                    self.log("Could not choose workout, retrying...")
                    continue

                # Step 5: Small delay for workout to start loading
                self._sleep(self.cfg.menu_delay)

        except StoppedException:
            self.log("Stopped by user.")
        finally:
            self.log("Simple loop finished.")

    def _read_weight_from_screen(self):
        """find the 'current weight' template, crop below it, ocr the number"""
        try:
            import pytesseract as _pyt
            _pyt.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
            _local = Path(__file__).parent / "Tesseract-OCR" / "tesseract.exe"
            if _local.exists():
                _pyt.pytesseract.tesseract_cmd = str(_local)
        except ImportError:
            return None
        try:
            # Find "Current weight" template on screen
            cw_tmpl = self.cfg.template_dir / "current_weight.png"
            if not cw_tmpl.exists():
                self.log("  OCR: current_weight.png template not found")
                return None
            
            match = self.find_on_screen(cw_tmpl, custom_threshold=0.6)
            if not match:
                self.log("  OCR: 'Current weight' not found on screen")
                return None
            
            x, y, conf = match
            # Crop below the template — the bold number
            screen = self.grab_screen()
            h, w = screen.shape[:2]
            # number is bigger text, need wider crop
            roi_y1 = min(y + 5, h - 5)
            roi_y2 = min(y + 80, h)
            roi_x1 = max(x - 150, 0)
            roi_x2 = min(x + 150, w)
            roi = screen[roi_y1:roi_y2, roi_x1:roi_x2]
            
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
            upscaled = cv2.resize(thresh, (0, 0), fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
            raw = _pyt.image_to_string(upscaled, config='--psm 7')
            self.log(f"  OCR raw text: '{raw.strip()}'")
            
            # Extract number
            numbers = re.findall(r'(\d+)', raw)
            if numbers:
                kg = max(int(n) for n in numbers)
                self.log(f"  OCR read weight: {kg} kg")
                return kg
            self.log("  OCR: no number found below 'Current weight'")
        except Exception as e:
            self.log(f"  OCR error: {e}")
        return None

    def _send_progress_report(self):
        """send a discord embed with sets, weight, time etc"""
        if not self.cfg.webhook_url.strip():
            return
        
        sets_since_last = self._sets_done - self._last_report_set
        self._last_report_set = self._sets_done
        
        weight_kg = self._last_weight_kg
        if weight_kg is not None:
            if self._first_weight_kg is None:
                self._first_weight_kg = weight_kg
            total_increase = weight_kg - self._first_weight_kg
            increase_str = f" (+{total_increase} kg)" if total_increase > 0 else ""
            weight_str = f"**{weight_kg} kg**{increase_str}"
        else:
            weight_str = "Not read yet"
        
        workout_name = self.cfg.chosen_workout.replace(".png", "").replace("workout_", "").title()
        
        # Calculate elapsed time
        elapsed_seconds = int(time.time() - self._macro_start_time) if self._macro_start_time else 0
        hours = elapsed_seconds // 3600
        minutes = (elapsed_seconds % 3600) // 60
        time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        
        # calculate xp multiplier
        # gamepass = permanent 2x, server boost = temporary 2x (check if on screen)
        # both together = 4x
        multiplier = 1
        if self.cfg.has_2x_crew_xp_gamepass:
            multiplier *= 2
        # check if server boost is currently active (template on screen)
        server_boost_tmpl = self.cfg.template_dir / "server_boost.png"
        server_boost_active = False
        if self.cfg.has_server_boost and server_boost_tmpl.exists():
            match_sb = self.find_on_screen(server_boost_tmpl, custom_threshold=0.6)
            if match_sb:
                server_boost_active = True
                multiplier *= 2
        
        estimated_xp = self._total_reps * self.cfg.xp_per_rep * multiplier
        xp_str = f"**{estimated_xp:,}** XP"
        boost_parts = []
        if self.cfg.has_2x_crew_xp_gamepass:
            boost_parts.append("2x gamepass")
        if server_boost_active:
            boost_parts.append("2x server")
        if boost_parts:
            xp_str += f" ({' + '.join(boost_parts)} = {multiplier}x)"
        
        embed = {
            "embeds": [{
                "title": "\U0001f4aa Workout Progress Report",
                "color": 0x00ff88,
                "fields": [
                    {"name": "Total Sets", "value": str(self._sets_done), "inline": True},
                    {"name": "Total Reps", "value": f"{self._total_reps:,}", "inline": True},
                    {"name": "Current Weight", "value": weight_str, "inline": True},
                    {"name": "Crew XP Earned", "value": xp_str, "inline": True},
                    {"name": "Exercise", "value": workout_name, "inline": True},
                    {"name": "Time Running", "value": time_str, "inline": True},
                ],
                "footer": {"text": f"Made by starlingz | Mode: {self.cfg.workout_mode} | Starting XP: {self.cfg.starting_crew_xp:,}"}
            }]
        }
        
        try:
            # Send embed with screenshot attached
            screen = self.grab_screen()
            _, img_bytes = cv2.imencode('.png', screen)
            files = {"file": ("progress.png", img_bytes.tobytes(), "image/png")}
            payload = {"payload_json": str(embed).replace("'", '"').replace("True", "true").replace("False", "false")}
            
            # Try embed with image first
            import json
            resp = requests.post(
                self.cfg.webhook_url,
                data={"payload_json": json.dumps(embed)},
                files=files,
                timeout=15,
            )
            if resp.status_code in (200, 204):
                self.log(f"\U0001f4ca Progress report sent ({self._sets_done} sets, {weight_str})")
            else:
                # Fallback: just send embed without image
                requests.post(self.cfg.webhook_url, json=embed, timeout=10)
                self.log(f"\U0001f4ca Progress report sent (no image)")
        except Exception as e:
            self.log(f"Progress report failed: {e}")

    def run(self):
        # Bulk buy food if enabled
        if self.cfg.bulk_buy_enabled:
            self.bulk_buy_food()
            return  # Stop after buying — user needs to position for macro

        # Use simple timing-based loop if configured
        if self.cfg.use_simple_loop:
            return self.run_simple_loop()

        self.log(f"Starting gym macro. Chosen workout: {self.cfg.chosen_workout}")
        start_time = time.time()
        self._macro_start_time = start_time

        # Move mouse to Roblox window so inputs go to the game
        m = self._monitor
        center_x = m["left"] + m["width"] // 2
        center_y = m["top"] + int(m["height"] * 0.8)
        pydirectinput.moveTo(center_x, center_y)
        pydirectinput.click()
        self.log(f"Mouse clicked at game window ({center_x}, {center_y})")

        # Use shakers at startup
        if self.cfg.workout_mode.lower() == "junk" and self.cfg.junk_use_shaker:
            self.use_creatine_shaker()
            self._last_shaker_use = time.time()
        if self.cfg.junk_use_preworkout:
            self.use_preworkout_shaker()
            self._last_preworkout_use = time.time()

        try:
            while True:
                self._check_stop()

                if (time.time() - start_time) / 60 > self.cfg.max_loop_minutes:
                    break

                # Skip status checks in Junk mode (except maintaining)
                is_junk_mode = self.cfg.workout_mode.lower() == "junk"

                # Skip deficit/cutting check for the first 10 seconds to avoid false positives on startup
                if not is_junk_mode and (time.time() - start_time) > 10 and self.is_deficit():
                    self._on_deficit()
                    break

                if not is_junk_mode and (time.time() - start_time) > 10 and self.is_cutting():
                    self.log("🚨 CUTTING STATUS REACHED! Stopping.")
                    self.send_discord_ping("🚨 Gym macro stopped: **Cutting** status detected!")
                    break

                if not (is_junk_mode and self.cfg.junk_no_food) and (time.time() - start_time) > 10 and self.is_maintaining():
                    self._handle_maintaining_flow()
                    self.log("Restarting macro after eating...")
                    self._sleep(3.0)  # Wait for game status to update after eating
                    continue

                if not self.approach_and_interact():
                    self._sleep(1)
                    continue

                cycle_done = False
                first_set = True
                while not cycle_done:
                    self._check_stop()

                    if not self.choose_workout():
                        self._sleep(1)
                        break  

                    result = self.work_out_until_low_stamina(skip_mode_wait=not first_set)
                    first_set = False
                    if result == "deficit":
                        self._on_deficit()
                        return
                    if result == "maintaining":
                        self._handle_maintaining_flow()
                        self.log("Restarting macro after eating...")
                        cycle_done = True
                        continue
                    if result == "menu_reopened":
                        continue  

                    result = self.get_off_and_regen()
                    if result == "deficit":
                        self._on_deficit()
                        return
                    if result == "maintaining":
                        self._handle_maintaining_flow()
                        self.log("Restarting macro after eating...")
                        cycle_done = True
                        continue
                    if result == "menu_reopened":
                        continue  

                    # Regen done — check statuses before getting back on (skip deficit/cutting in Junk mode)
                    if not (is_junk_mode and self.cfg.junk_no_food) and self.is_maintaining():
                        self._handle_maintaining_flow()
                        self.log("Restarting macro after eating...")
                        cycle_done = True
                        continue
                    if not is_junk_mode:
                        if self.is_deficit() or self.is_cutting():
                            self._on_deficit()
                            return

                    # Re-interact and start next set immediately
                    # check if pre workout timer elapsed
                    if self.cfg.junk_use_preworkout and (time.time() - self._last_preworkout_use) >= self.cfg.junk_preworkout_interval * 60:
                        self._last_preworkout_use = time.time()
                        self.use_preworkout_shaker()
                    self.send_input(self.cfg.key_interact, self.cfg.key_interact_hold)
                    self._sleep(0.3)

        except StoppedException:
            self.log("Stopped by user.")
        finally:
            self.log("Macro finished.")

    def _handle_maintaining_flow(self):
        self.log("Maintaining state confirmed! Exiting station assembly to ingest foods...")
        self.send_input(self.cfg.key_exit_machine, self.cfg.key_exit_hold)
        self._sleep(0.5)
        
        # click away from any prompts before eating
        m = self._monitor
        pydirectinput.moveTo(m["left"] + m["width"] // 2, m["top"] + int(m["height"] * 0.4))
        time.sleep(0.06)
        pydirectinput.click()
        self._sleep(0.3)
        
        calories, items = self.scan_and_eat_inventory()
        
        if not items:
            # No food found in entire inventory - stop the macro
            self.log("⚠️ No food found in inventory! Stopping macro.")
            self.send_discord_ping("⚠️ Gym macro stopped: **No food left in inventory!**")
            self._sleep(0.5)
            self.send_discord_screenshot("📸 Screen when food ran out:")
            raise StoppedException()
        
        item_summary = ", ".join([f"{count}x {name}" for name, count in items.items()])
        webhook_content = (
            f"🍖 **Inventory Feeding Report**\n"
            f"Status: Reached **Maintaining** State!\n"
            f"Items Ingested: `{item_summary}`\n"
            f"Total Calorie Count: **{calories} kcal**\n"
            f"Action: Restarting macro loop..."
        )
        self.send_discord_ping(webhook_content)
        # Send a screenshot showing current game state after eating
        self._sleep(0.5)
        self.send_discord_screenshot("📸 Current screen after eating:")

    def _on_deficit(self):
        self.log("🚨 DEFICIT STATE REACHED! Stopping immediately.")
        self.send_discord_ping("🚨 Gym macro stopped: **Deficit** status detected!")


if __name__ == "__main__":
    import threading
    stop_event = threading.Event()
    macro = GymMacro(MacroConfig(), log_fn=print, stop_flag=stop_event)
    try:
        macro.run()
    except KeyboardInterrupt:
        stop_event.set()
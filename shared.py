#!/usr/bin/env python3
"""
Shared utilities for Ragnarok X bots.

Provides window detection, clicking, OCR, and config management
used by both garden_bot.py and boss_bot.py.
"""

import pyautogui
import time
import subprocess
import tempfile
import os
import random
import json

# --- Config ---

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
BOSS_CONFIG_FILE = os.path.join(SCRIPT_DIR, "boss_config.json")
OCR_HELPER = os.path.join(SCRIPT_DIR, "ocr_helper")


# ═══════════════════════════════════════
#  Window Detection
# ═══════════════════════════════════════

def find_game_window():
    """Auto-detect the Ragnarok X window using CoreGraphics (no permissions needed)."""
    try:
        import Quartz
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID
        )
        for w in windows:
            owner = w.get("kCGWindowOwnerName", "")
            if "Ragnarok" in owner or "ragnarok" in owner.lower():
                bounds = w.get("kCGWindowBounds", {})
                layer = w.get("kCGWindowLayer", 0)
                if layer != 0:
                    continue
                return {
                    "x": int(bounds.get("X", 0)),
                    "y": int(bounds.get("Y", 0)),
                    "w": int(bounds.get("Width", 0)),
                    "h": int(bounds.get("Height", 0)),
                }
    except ImportError:
        pass

    # Fallback: AppleScript
    for name_pattern in ["Ragnarok X", "Ragnarok"]:
        script = f'''
        tell application "System Events"
            set ragProc to first process whose name contains "{name_pattern}"
            set winPos to position of window 1 of ragProc
            set winSize to size of window 1 of ragProc
            set x to item 1 of winPos
            set y to item 2 of winPos
            set w to item 1 of winSize
            set h to item 2 of winSize
            return (x as text) & "," & (y as text) & "," & (w as text) & "," & (h as text)
        end tell
        '''
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",")
                return {
                    "x": int(parts[0]),
                    "y": int(parts[1]),
                    "w": int(parts[2]),
                    "h": int(parts[3]),
                }
        except Exception:
            continue
    return None


def get_window_or_exit():
    """Find game window or exit with error."""
    import sys
    win = find_game_window()
    if not win:
        print("Could not find Ragnarok X window!")
        print("Make sure the game is running and visible.")
        sys.exit(1)
    return win


# ═══════════════════════════════════════
#  Config Management
# ═══════════════════════════════════════

def load_layout():
    """Load calibrated pixel offsets from config.json."""
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_layout(layout):
    """Save calibrated pixel offsets to config.json."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(layout, f, indent=2)


def load_boss_config():
    """Load boss selection and timer data from boss_config.json."""
    if not os.path.exists(BOSS_CONFIG_FILE):
        return {"selected_mvps": [], "selected_minis": [], "timers": {}}
    with open(BOSS_CONFIG_FILE) as f:
        return json.load(f)


def save_boss_config(config):
    """Save boss selection and timer data."""
    with open(BOSS_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


# ═══════════════════════════════════════
#  Mouse Actions
# ═══════════════════════════════════════

def click_at(x, y, jitter=6):
    """Click with explicit press-hold-release for reliable registration."""
    dx = random.randint(-jitter, jitter)
    dy = random.randint(-jitter, jitter)
    target_x = int(x + dx)
    target_y = int(y + dy)

    move_duration = 0.1 + random.random() * 0.25
    pyautogui.moveTo(target_x, target_y, duration=move_duration)

    time.sleep(0.05 + random.random() * 0.08)
    pyautogui.mouseDown()
    time.sleep(0.04 + random.random() * 0.06)
    pyautogui.mouseUp()
    time.sleep(0.03 + random.random() * 0.05)


def type_on_numpad(number, numpad):
    """Click numpad digits to enter a number."""
    for ch in str(abs(int(number))):
        if ch in numpad:
            click_at(*numpad[ch], jitter=4)
            time.sleep(0.2 + random.random() * 0.25)


# ═══════════════════════════════════════
#  OCR via macOS Vision
# ═══════════════════════════════════════

def ocr_vision(image_path):
    """Run macOS Vision OCR via the compiled Swift helper.

    Returns the recognized text, or empty string on failure.
    """
    try:
        result = subprocess.run(
            [OCR_HELPER, image_path],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def ocr_region(x, y, w, h):
    """Screenshot a screen region and run OCR on it.

    Returns the recognized text.
    """
    shot = pyautogui.screenshot(region=(int(x), int(y), int(w), int(h)))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        shot.save(f.name)
        text = ocr_vision(f.name)
        os.unlink(f.name)
    return text


def screenshot_region(x, y, w, h):
    """Take a screenshot of a screen region. Returns PIL Image."""
    return pyautogui.screenshot(region=(int(x), int(y), int(w), int(h)))


def check_brightness(x, y, w, h, threshold=160):
    """Check if a screen region is brighter than threshold (dialog detection)."""
    shot = pyautogui.screenshot(region=(int(x), int(y), int(w), int(h)))
    gray = shot.convert("L")
    avg = sum(gray.getdata()) / len(list(gray.getdata()))
    return avg > threshold


# ═══════════════════════════════════════
#  Boss Calibration
# ═══════════════════════════════════════

BOSS_CALIBRATION_STEPS = [
    ("mvp_panel_button", "MVP panel button (top bar icon)"),
    ("mvp_tab", "MVP tab at top of panel"),
    ("mini_tab", "Mini tab at top of panel"),
    ("first_boss_row", "Top-left of FIRST boss entry in the list"),
    ("last_visible_boss_row", "Top-left of LAST visible boss entry (4th row)"),
    ("go_button", "Go button on a boss entry"),
    ("panel_close", "Panel close (X) button"),
    ("auto_attack_toggle", "Auto-attack toggle (above chat)"),
    ("monster_list_first", "First monster in auto-attack dropdown"),
    ("resurrect_button", "Resurrect button (shown on death)"),
    ("channel_button", "Channel button (top-right)"),
    ("ch1_button", "Channel 1 button in popup"),
]


def calibrate_boss():
    """Record 11 boss-related positions.

    Interactive: hover mouse, press Enter for each.
    Saves into the existing config.json alongside garden calibration.
    """
    win = get_window_or_exit()
    layout = load_layout() or {}

    print()
    print("=" * 50)
    print("  Boss Calibration (11 positions)")
    print("=" * 50)
    print()
    print(f"  Window: ({win['x']}, {win['y']}) {win['w']}x{win['h']}")
    print()
    print("  For each step: hover mouse over the element, press ENTER.")
    print()

    boss_layout = {}
    total = len(BOSS_CALIBRATION_STEPS)

    for i, (key, description) in enumerate(BOSS_CALIBRATION_STEPS, 1):
        input(f"  [{i:>2d}/{total}] Hover over: {description} -> Enter: ")
        p = pyautogui.position()
        offset = (p.x - win["x"], p.y - win["y"])
        boss_layout[key] = list(offset)
        print(f"           Offset: {offset}")
        print()

    # Compute scroll parameters from first and last visible rows
    if "first_boss_row" in boss_layout and "last_visible_boss_row" in boss_layout:
        first = boss_layout["first_boss_row"]
        last = boss_layout["last_visible_boss_row"]
        # Row height = distance between first and last row / 3 (4 visible = 3 gaps)
        row_height = (last[1] - first[1]) / 3.0
        # Visible height = all 4 rows
        visible_height = last[1] - first[1] + row_height
        # Panel width: extend to panel close button X for full coverage
        panel_width = 300
        if "panel_close" in boss_layout:
            panel_width = boss_layout["panel_close"][0] - first[0]
        elif "go_button" in boss_layout:
            panel_width = boss_layout["go_button"][0] - first[0] + 100

        boss_layout["panel_scroll_region"] = [
            first[0], first[1], panel_width, int(visible_height)
        ]
        boss_layout["row_height"] = int(row_height)
        boss_layout["visible_rows"] = 4
        boss_layout["scroll_distance"] = int(visible_height)

        print(f"  Calculated: row_height={int(row_height)}px, "
              f"scroll_distance={int(visible_height)}px")

    # Merge into main config
    layout["boss"] = boss_layout
    save_layout(layout)

    print(f"  Saved {total} positions to {CONFIG_FILE}")
    print()
    print("  Boss calibration complete!")
    print("  You can now use the boss farming bot.")
    print()


def get_boss_positions(win, layout):
    """Calculate absolute screen positions for boss-related elements."""
    if "boss" not in layout:
        return None

    boss = layout["boss"]
    wx, wy = win["x"], win["y"]

    # Scalar config values (not screen coordinates)
    SCALAR_KEYS = {"row_height", "visible_rows", "scroll_distance"}

    positions = {}
    for key, offset in boss.items():
        if key in SCALAR_KEYS:
            positions[key] = offset  # pass through as-is
        elif key == "panel_scroll_region":
            positions[key] = (wx + offset[0], wy + offset[1], offset[2], offset[3])
        else:
            positions[key] = (wx + offset[0], wy + offset[1])

    return positions

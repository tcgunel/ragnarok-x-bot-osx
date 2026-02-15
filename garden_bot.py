#!/usr/bin/env python3
"""
Ragnarok X Garden Bot

Automates gardening clicks and solves math CAPTCHAs.
Zero calibration - auto-detects game window position.

Usage:
    python garden_bot.py calibrate          One-time setup (4 clicks)
    python garden_bot.py run [interval]     Start bot (default: 3s)
    python garden_bot.py run --debug        Enable debug screenshots
    python garden_bot.py test               Test CAPTCHA solving once
    python garden_bot.py window             Show detected window info

Safety:
    Move mouse to any screen corner to abort (pyautogui failsafe).
    Press Ctrl+C to stop gracefully.
"""

import pyautogui
from PIL import Image, ImageEnhance
import time
import re
import subprocess
import tempfile
import sys
import os
import random

# --- Config ---

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


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
                # Skip non-standard windows (menus, overlays)
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

    # Fallback: try AppleScript (needs Accessibility permission)
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


# ═══════════════════════════════════════
#  Layout - pixel offsets from window origin
# ═══════════════════════════════════════

import json

CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
GARDEN_REF_FILE = os.path.join(SCRIPT_DIR, "garden_ref.png")

# Garden button detection
GARDEN_PATCH_SIZE = 40    # pixels around garden button to compare
GARDEN_MATCH_THRESHOLD = 0.85  # similarity threshold (0-1)

# Math expression region: this is auto-detected from the calibrated
# input_field position (math is always above the input field)
MATH_ABOVE_INPUT = 25   # pixels above input field (center of search area)
MATH_WIDTH = 120        # width of OCR capture region
MATH_HEIGHT = 36        # height of OCR capture region

# OK button is always below the input field, same X
OK_BELOW_INPUT = 85     # pixels below input field


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


def calibrate():
    """Quick 4-click calibration while CAPTCHA is visible.

    Records pixel offsets from window origin for:
    1. Garden button (the plot you click repeatedly)
    2. Input field (answer box in CAPTCHA dialog)
    3. Numpad button "1"
    4. Numpad button "0"

    Everything else is calculated from these 4 points.
    """
    win = get_window_or_exit()

    print()
    print("=" * 44)
    print("  Quick Calibration (4 clicks)")
    print("=" * 44)
    print()
    print(f"  Window: ({win['x']}, {win['y']}) {win['w']}x{win['h']}")
    print()
    print("  Make sure a CAPTCHA dialog is visible!")
    print("  For each step: hover mouse, press ENTER.")
    print()

    # 1. Garden button
    input("  [1/4] Hover over the GARDEN BUTTON -> Enter: ")
    p = pyautogui.position()
    garden_offset = (p.x - win["x"], p.y - win["y"])
    print(f"         Offset: {garden_offset}")

    # Save reference screenshot of garden button area
    half = GARDEN_PATCH_SIZE // 2
    ref_region = (p.x - half, p.y - half, GARDEN_PATCH_SIZE, GARDEN_PATCH_SIZE)
    ref_shot = pyautogui.screenshot(region=ref_region)
    ref_shot.save(GARDEN_REF_FILE)
    print(f"         Reference saved: {GARDEN_REF_FILE}")
    print()

    # 2. Input field
    input("  [2/4] Hover over the INPUT FIELD (answer box) -> Enter: ")
    p = pyautogui.position()
    input_offset = (p.x - win["x"], p.y - win["y"])
    print(f"         Offset: {input_offset}")
    print()

    # 3. Numpad "1"
    input("  [3/4] Hover over numpad button '1' -> Enter: ")
    p = pyautogui.position()
    numpad1_offset = (p.x - win["x"], p.y - win["y"])
    print(f"         Offset: {numpad1_offset}")
    print()

    # 4. Numpad "0"
    input("  [4/4] Hover over numpad button '0' -> Enter: ")
    p = pyautogui.position()
    numpad0_offset = (p.x - win["x"], p.y - win["y"])
    print(f"         Offset: {numpad0_offset}")
    print()

    layout = {
        "garden_button": list(garden_offset),
        "input_field": list(input_offset),
        "numpad_1": list(numpad1_offset),
        "numpad_0": list(numpad0_offset),
    }
    save_layout(layout)

    print(f"  Saved to {CONFIG_FILE}")
    print()
    print("  Calculated positions:")
    _show_calculated(win, layout)
    print()
    print("  Next: python garden_bot.py test")
    print()


def _show_calculated(win, layout):
    """Show all calculated positions for verification."""
    gb = layout["garden_button"]
    inp = layout["input_field"]
    n1 = layout["numpad_1"]
    n0 = layout["numpad_0"]

    print(f"    Garden button: offset {tuple(gb)}")

    # Math region
    mx = inp[0] - MATH_WIDTH // 2
    my = inp[1] - MATH_ABOVE_INPUT - MATH_HEIGHT
    print(f"    Math region  : offset ({mx}, {my}) size {MATH_WIDTH}x{MATH_HEIGHT}")
    print(f"    Input field  : offset {tuple(inp)}")

    # OK button
    ok = (inp[0], inp[1] + OK_BELOW_INPUT)
    print(f"    OK button    : offset {ok}")

    # Numpad
    cs = (n0[0] - n1[0]) / 3.0
    rs = (n0[1] - n1[1]) / 1.0
    print(f"    Numpad '1'   : offset {tuple(n1)}")
    print(f"    Numpad '0'   : offset {tuple(n0)}")
    print(f"    Grid spacing : col={cs:.0f}px row={rs:.0f}px")


def get_positions(win, layout):
    """Calculate all absolute screen positions from window + calibrated offsets."""
    wx, wy = win["x"], win["y"]
    gb = layout["garden_button"]
    inp = layout["input_field"]
    n1 = layout["numpad_1"]
    n0 = layout["numpad_0"]

    # Garden button
    garden_pos = (wx + gb[0], wy + gb[1])

    # Math region (above input field, centered on same X)
    math_x = wx + inp[0] - MATH_WIDTH // 2
    math_y = wy + inp[1] - MATH_ABOVE_INPUT - MATH_HEIGHT
    math_region = (math_x, math_y, MATH_WIDTH, MATH_HEIGHT)

    # Input field
    input_pos = (wx + inp[0], wy + inp[1])

    # OK button (same X as input, below it)
    ok_pos = (wx + inp[0], wy + inp[1] + OK_BELOW_INPUT)

    # Numpad grid
    x1 = wx + n1[0]
    y1 = wy + n1[1]
    x0 = wx + n0[0]
    y0 = wy + n0[1]
    cs = (x0 - x1) / 3.0
    rs = (y0 - y1) / 1.0

    numpad = {
        "1": (x1,          y1),
        "2": (x1 + cs,     y1),
        "3": (x1 + 2*cs,   y1),
        "4": (x1,          y1 + rs),
        "5": (x1 + cs,     y1 + rs),
        "6": (x1 + 2*cs,   y1 + rs),
        "7": (x1,          y1 + 2*rs),
        "8": (x1 + cs,     y1 + 2*rs),
        "9": (x1 + 2*cs,   y1 + 2*rs),
        "0": (x0,          y0),
        "clear": (x1 + 3*cs, y1),
        "confirm": (x1 + 3*cs, y1 + 2*rs),
    }

    return {
        "garden": garden_pos,
        "math_region": math_region,
        "input": input_pos,
        "ok": ok_pos,
        "numpad": numpad,
    }


# ═══════════════════════════════════════
#  CAPTCHA Detection & OCR
# ═══════════════════════════════════════

def is_garden_visible(positions):
    """Check if the garden button is visible by comparing to the calibration reference."""
    if not os.path.exists(GARDEN_REF_FILE):
        return True  # No reference saved, assume visible

    ref = Image.open(GARDEN_REF_FILE)
    gx, gy = positions["garden"]
    half = GARDEN_PATCH_SIZE // 2
    current = pyautogui.screenshot(region=(int(gx - half), int(gy - half),
                                           GARDEN_PATCH_SIZE, GARDEN_PATCH_SIZE))

    # Compare using normalized pixel similarity
    ref_pixels = list(ref.convert("RGB").getdata())
    cur_pixels = list(current.convert("RGB").getdata())

    if len(ref_pixels) != len(cur_pixels):
        return True  # Size mismatch, assume visible

    total = len(ref_pixels) * 3  # R, G, B per pixel
    diff_sum = sum(
        abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
        for (r1, g1, b1), (r2, g2, b2) in zip(ref_pixels, cur_pixels)
    )
    similarity = 1.0 - (diff_sum / (total * 255))

    return similarity >= GARDEN_MATCH_THRESHOLD


def is_dialog_visible(positions):
    """Quick brightness check on the math region to detect the CAPTCHA dialog."""
    x, y, w, h = positions["math_region"]
    if w <= 0 or h <= 0:
        return False

    shot = pyautogui.screenshot(region=(x, y, w, h))
    gray = shot.convert("L")
    avg_brightness = sum(gray.getdata()) / len(list(gray.getdata()))

    return avg_brightness > 160


def _capture_math_region(positions):
    """Screenshot the math expression region."""
    x, y, w, h = positions["math_region"]
    return pyautogui.screenshot(region=(x, y, w, h)), w, h


OCR_HELPER = os.path.join(SCRIPT_DIR, "ocr_helper")


def _ocr_vision(image_path):
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


def _normalize_expression(raw):
    """Normalize OCR output into a clean math expression.

    Handles common misreads: x/X -> *, t/T -> +, etc.
    """
    expr = raw.replace(" ", "")

    for ch in ["×", "x", "X"]:
        expr = expr.replace(ch, "*")
    expr = expr.replace("÷", "/")

    # Common OCR misreads for operators between digits
    expr = re.sub(r"(\d)[tT](\d)", r"\1+\2", expr)
    expr = re.sub(r"(\d)[lI|](\d)", r"\1-\2", expr)

    expr = re.sub(r"[^0-9+\-*/]", "", expr)
    return expr


def _extract_expression(raw):
    """Extract a valid 'digit(s) operator digit(s)' pattern from noisy OCR."""
    normalized = _normalize_expression(raw)

    # Direct match: "4+3", "12-5", etc.
    m = re.match(r"^(\d+)([+\-*/])(\d+)$", normalized)
    if m:
        return normalized

    # Pattern inside garbage: find first valid expression
    m = re.search(r"(\d+)([+\-*/])(\d+)", normalized)
    if m:
        return m.group(0)

    return None


# All valid single-digit expression results for brute-force recovery
_VALID_EXPRESSIONS = {}
for _a in range(1, 10):
    for _b in range(1, 10):
        for _op in ["+", "-", "*"]:
            _result = eval(f"{_a}{_op}{_b}")
            _key = f"{_a}{_b}"
            if _key not in _VALID_EXPRESSIONS:
                _VALID_EXPRESSIONS[_key] = []
            _VALID_EXPRESSIONS[_key].append((f"{_a}{_op}{_b}", _result))


def _brute_force_from_digits(all_candidates):
    """Last resort: if OCR only reads digits (e.g. '17' from '1+7'),
    try to find the expression by matching against known single-digit math.

    Only works for single-digit operands (which is what this game uses).
    We look at ALL candidates to find a consensus answer.
    """
    digit_candidates = []
    for source, raw in all_candidates:
        cleaned = re.sub(r"[^0-9]", "", raw)
        if len(cleaned) == 2:
            digit_candidates.append(cleaned)

    if not digit_candidates:
        return None

    # Find the most common 2-digit reading
    from collections import Counter
    most_common = Counter(digit_candidates).most_common(1)[0][0]

    matches = _VALID_EXPRESSIONS.get(most_common, [])
    if len(matches) == 1:
        # Unambiguous: only one expression produces these two digits
        expr, result = matches[0]
        return expr

    # Ambiguous (e.g., "24" could be "2+4" or "2*4" but not "2-4"=negative)
    # Prefer +, then *, then -
    for preferred_op in ["+", "*", "-"]:
        for expr, result in matches:
            if preferred_op in expr and result >= 0:
                return expr

    return None


def _ocr_single_shot(shot, w, h):
    """Run Vision OCR on a screenshot with multiple preprocessing strategies.
    Returns list of (source, text) candidates.
    """
    candidates = []

    # Try raw screenshot
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        shot.save(f.name)
        text = _ocr_vision(f.name)
        os.unlink(f.name)
    if text:
        candidates.append(("raw", text))

    # Try 4x scaled up (helps with small text)
    big = shot.resize((w * 4, h * 4), Image.LANCZOS)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        big.save(f.name)
        text = _ocr_vision(f.name)
        os.unlink(f.name)
    if text:
        candidates.append(("scaled", text))

    # Try high-contrast version
    gray = big.convert("L")
    enhanced = ImageEnhance.Contrast(gray).enhance(3.0)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        enhanced.save(f.name)
        text = _ocr_vision(f.name)
        os.unlink(f.name)
    if text:
        candidates.append(("contrast", text))

    return candidates


def read_math_expression(positions, debug=False):
    """OCR the math expression using macOS Vision framework.

    1. Capture math region screenshot
    2. Run Vision OCR on raw + scaled-up versions
    3. If no digits found, shift region up/down and retry
    4. Return first result that parses as valid math
    """
    shot, w, h = _capture_math_region(positions)

    if debug:
        shot.save(os.path.join(SCRIPT_DIR, "debug_math_raw.png"))

    all_candidates = _ocr_single_shot(shot, w, h)

    # Check if any candidate has digits (i.e. we captured the right area)
    has_digits = any(re.search(r"\d", text) for _, text in all_candidates)

    # If no digits found, the capture region missed the math expression.
    # Try shifting the region up and down by small amounts.
    if not has_digits:
        x, y, rw, rh = positions["math_region"]
        for dy in [-12, -20, 8, 15]:
            shifted_region = (x, y + dy, rw, rh)
            shifted_shot = pyautogui.screenshot(region=shifted_region)
            shifted_candidates = _ocr_single_shot(shifted_shot, rw, rh)
            if any(re.search(r"\d", text) for _, text in shifted_candidates):
                all_candidates = shifted_candidates
                if debug:
                    print(f"\n    Region shifted by dy={dy} to find digits")
                break

    if debug:
        print(f"\n    OCR candidates: {all_candidates}")

    # Pick the first candidate that yields a valid expression
    for source, raw in all_candidates:
        expr = _extract_expression(raw)
        if expr:
            if debug:
                print(f"    Winner: '{raw}' -> '{expr}' (from {source})")
            return expr

    # Fallback: brute-force from digits if operator was lost
    brute = _brute_force_from_digits(all_candidates)
    if brute:
        if debug:
            print(f"    Brute-force recovery: '{brute}'")
        return brute

    if all_candidates:
        return all_candidates[0][1]
    return ""


def solve_expression(raw):
    """Parse and evaluate the math expression."""
    expr = _extract_expression(raw) if raw else None
    if not expr:
        return None

    if not re.match(r"^\d+[+\-*/]\d+$", expr):
        return None

    try:
        return int(eval(expr))
    except Exception:
        return None


# ═══════════════════════════════════════
#  Mouse Actions (randomized)
# ═══════════════════════════════════════

def click_at(x, y, jitter=6):
    """Click with explicit press-hold-release for reliable registration."""
    dx = random.randint(-jitter, jitter)
    dy = random.randint(-jitter, jitter)
    target_x = int(x + dx)
    target_y = int(y + dy)

    move_duration = 0.1 + random.random() * 0.25
    pyautogui.moveTo(target_x, target_y, duration=move_duration)

    # Settle before pressing
    time.sleep(0.05 + random.random() * 0.08)
    # Explicit press-hold-release so the game registers the click
    pyautogui.mouseDown()
    time.sleep(0.04 + random.random() * 0.06)  # hold 40-100ms
    pyautogui.mouseUp()
    # Brief pause after release
    time.sleep(0.03 + random.random() * 0.05)


def type_on_numpad(number, numpad):
    """Click numpad digits to enter a number."""
    for ch in str(abs(int(number))):
        if ch in numpad:
            click_at(*numpad[ch], jitter=4)
            time.sleep(0.2 + random.random() * 0.25)


# ═══════════════════════════════════════
#  CAPTCHA Handler
# ═══════════════════════════════════════

def handle_captcha(positions, debug=False):
    """Detect, read, solve, and submit the CAPTCHA."""
    if not is_dialog_visible(positions):
        return False

    time.sleep(0.5)

    raw_text = read_math_expression(positions, debug)
    ts = time.strftime("%H:%M:%S")
    print(f"\n    [{ts}] CAPTCHA detected! Expression: '{raw_text}'", end="")

    answer = solve_expression(raw_text)
    if answer is None:
        print(" -> FAILED, retrying...")
        time.sleep(0.8)
        raw_text = read_math_expression(positions, debug)
        print(f"    Retry: '{raw_text}'", end="")
        answer = solve_expression(raw_text)
        if answer is None:
            print(" -> FAILED again")
            return False

    print(f" = {answer}")

    numpad = positions["numpad"]

    # Tap the input field to open the numpad
    click_at(*positions["input"], jitter=5)
    time.sleep(0.4 + random.random() * 0.3)

    # Clear any existing input
    click_at(*numpad["clear"], jitter=4)
    time.sleep(0.2 + random.random() * 0.2)

    # Type answer on numpad
    type_on_numpad(answer, numpad)
    time.sleep(0.3 + random.random() * 0.3)

    # Click numpad checkmark to confirm input
    click_at(*numpad["confirm"], jitter=4)
    time.sleep(0.4 + random.random() * 0.3)

    # Click OK button to submit the dialog
    click_at(*positions["ok"], jitter=6)
    time.sleep(0.8 + random.random() * 0.5)

    return True


# ═══════════════════════════════════════
#  Main Loop
# ═══════════════════════════════════════

def get_window_or_exit():
    """Find game window or exit with error."""
    win = find_game_window()
    if not win:
        print("Could not find Ragnarok X window!")
        print("Make sure the game is running and visible.")
        print()
        print("If auto-detection fails, you can set the window manually:")
        print('  python garden_bot.py run --window "x,y,w,h"')
        print('  Example: python garden_bot.py run --window "0,25,1036,772"')
        sys.exit(1)
    return win



def run(interval=3.0, debug=False):
    """Main automation loop: click garden + solve CAPTCHAs."""
    layout = load_layout()
    if not layout:
        print("No calibration found! Run first:")
        print("  python garden_bot.py calibrate")
        sys.exit(1)

    win = get_window_or_exit()
    positions = get_positions(win, layout)

    print()
    print("=" * 44)
    print("  Ragnarok X Garden Bot  -  Running")
    print("=" * 44)
    print(f"  Window       : ({win['x']}, {win['y']}) {win['w']}x{win['h']}")
    print(f"  Garden btn   : ({int(positions['garden'][0])}, {int(positions['garden'][1])})")
    print(f"  Interval     : {interval}s")
    print(f"  Debug        : {'ON' if debug else 'OFF'}")
    print(f"  Failsafe     : move mouse to any corner")
    print(f"  Stop         : Ctrl+C")
    print()

    for i in range(5, 0, -1):
        print(f"  Starting in {i}...", end="\r")
        time.sleep(1)
    print("  Running!            ")
    print()

    cycle = 0
    captchas_solved = 0

    try:
        while True:
            cycle += 1
            ts = time.strftime("%H:%M:%S")

            # Re-detect window each cycle (user may have moved it)
            new_win = find_game_window()
            if new_win:
                win = new_win
                positions = get_positions(win, layout)

            if is_garden_visible(positions):
                click_at(*positions["garden"], jitter=8)
                print(f"[{ts}] #{cycle:>4d} | garden click", end="", flush=True)
            else:
                print(f"[{ts}] #{cycle:>4d} | garden not visible, skipping", end="", flush=True)

            jitter_range = interval * 0.3
            wait = interval + random.uniform(-jitter_range, jitter_range)
            wait = max(1.0, wait)
            time.sleep(wait)

            if handle_captcha(positions, debug):
                captchas_solved += 1
                print(f"    Total CAPTCHAs solved: {captchas_solved}")
            else:
                print()

            time.sleep(0.3 + random.random() * 0.7)

    except KeyboardInterrupt:
        print()
        print()
        print("=" * 44)
        print(f"  Stopped after {cycle} cycles")
        print(f"  CAPTCHAs solved: {captchas_solved}")
        print("=" * 44)


# ═══════════════════════════════════════
#  Test & Info Commands
# ═══════════════════════════════════════

def show_window_info():
    """Show detected window position and calculated element positions."""
    win = get_window_or_exit()
    layout = load_layout()

    print()
    print(f"  Window: ({win['x']}, {win['y']}) size {win['w']}x{win['h']}")
    print()

    if not layout:
        print("  No calibration found.")
        print("  Run: python garden_bot.py calibrate")
        print()
        return

    positions = get_positions(win, layout)

    print(f"  Garden button : ({int(positions['garden'][0])}, {int(positions['garden'][1])})")
    print(f"  Math region   : {positions['math_region']}")
    print(f"  Input field   : ({int(positions['input'][0])}, {int(positions['input'][1])})")
    print(f"  OK button     : ({int(positions['ok'][0])}, {int(positions['ok'][1])})")
    print()
    print("  Numpad:")
    for key in ["1","2","3","4","5","6","7","8","9","0","clear","confirm"]:
        x, y = positions["numpad"][key]
        print(f"    [{key:>7s}] -> ({int(x)}, {int(y)})")
    print()


def test_captcha():
    """Test CAPTCHA detection + solving once."""
    layout = load_layout()
    if not layout:
        print("No calibration found! Run first:")
        print("  python garden_bot.py calibrate")
        sys.exit(1)

    win = get_window_or_exit()
    positions = get_positions(win, layout)

    print()
    print("CAPTCHA test mode")
    print("Make sure the CAPTCHA dialog is visible on screen!")
    print(f"Window: ({win['x']}, {win['y']}) {win['w']}x{win['h']}")
    print("Checking in 3 seconds...")
    time.sleep(3)

    if handle_captcha(positions, debug=True):
        print("\n  -> Success! CAPTCHA was solved.")
    else:
        print("\n  -> Could not detect or solve CAPTCHA.")
        debug_files = [f for f in os.listdir(SCRIPT_DIR) if f.startswith("debug_math")]
        if debug_files:
            print(f"     Check debug images in: {SCRIPT_DIR}/")
            for f in debug_files:
                print(f"       {f}")
        else:
            print("     Dialog might not have been detected (brightness too low).")
    print()


# ═══════════════════════════════════════
#  CLI
# ═══════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "calibrate":
        calibrate()

    elif cmd == "run":
        interval = 3.0
        debug = "--debug" in sys.argv
        for arg in sys.argv[2:]:
            if arg not in ("--debug",) and not arg.startswith("--"):
                try:
                    interval = float(arg)
                except ValueError:
                    pass
        run(interval=interval, debug=debug)

    elif cmd == "test":
        test_captcha()

    elif cmd == "window":
        show_window_info()

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
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
import tempfile
import sys
import os
import random

from shared import (
    find_game_window,
    get_window_or_exit,
    load_layout,
    save_layout,
    click_at,
    type_on_numpad,
    ocr_vision,
    SCRIPT_DIR,
    CONFIG_FILE,
)

# --- Config ---

GARDEN_REF_FILE = os.path.join(SCRIPT_DIR, "garden_ref.png")

# Garden button detection
GARDEN_PATCH_SIZE = 40    # pixels around garden button to compare
GARDEN_MATCH_THRESHOLD = 0.85  # similarity threshold (0-1)

# Math expression region: auto-detected from calibrated input_field position
MATH_ABOVE_INPUT = 25   # pixels above input field
MATH_WIDTH = 120        # width of OCR capture region
MATH_HEIGHT = 36        # height of OCR capture region

# OK button is always below the input field, same X
OK_BELOW_INPUT = 85     # pixels below input field


# ═══════════════════════════════════════
#  Calibration
# ═══════════════════════════════════════

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

    layout = load_layout() or {}
    layout.update({
        "garden_button": list(garden_offset),
        "input_field": list(input_offset),
        "numpad_1": list(numpad1_offset),
        "numpad_0": list(numpad0_offset),
    })
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

    mx = inp[0] - MATH_WIDTH // 2
    my = inp[1] - MATH_ABOVE_INPUT - MATH_HEIGHT
    print(f"    Math region  : offset ({mx}, {my}) size {MATH_WIDTH}x{MATH_HEIGHT}")
    print(f"    Input field  : offset {tuple(inp)}")

    ok = (inp[0], inp[1] + OK_BELOW_INPUT)
    print(f"    OK button    : offset {ok}")

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

    garden_pos = (wx + gb[0], wy + gb[1])

    math_x = wx + inp[0] - MATH_WIDTH // 2
    math_y = wy + inp[1] - MATH_ABOVE_INPUT - MATH_HEIGHT
    math_region = (math_x, math_y, MATH_WIDTH, MATH_HEIGHT)

    input_pos = (wx + inp[0], wy + inp[1])
    ok_pos = (wx + inp[0], wy + inp[1] + OK_BELOW_INPUT)

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
        return True

    ref = Image.open(GARDEN_REF_FILE)
    gx, gy = positions["garden"]
    half = GARDEN_PATCH_SIZE // 2
    current = pyautogui.screenshot(region=(int(gx - half), int(gy - half),
                                           GARDEN_PATCH_SIZE, GARDEN_PATCH_SIZE))

    ref_pixels = list(ref.convert("RGB").getdata())
    cur_pixels = list(current.convert("RGB").getdata())

    if len(ref_pixels) != len(cur_pixels):
        return True

    total = len(ref_pixels) * 3
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


def _normalize_expression(raw):
    """Normalize OCR output into a clean math expression."""
    expr = raw.replace(" ", "")

    for ch in ["×", "x", "X"]:
        expr = expr.replace(ch, "*")
    expr = expr.replace("÷", "/")

    expr = re.sub(r"(\d)[tT](\d)", r"\1+\2", expr)
    expr = re.sub(r"(\d)[lI|](\d)", r"\1-\2", expr)

    expr = re.sub(r"[^0-9+\-*/]", "", expr)
    return expr


def _extract_expression(raw):
    """Extract a valid 'digit(s) operator digit(s)' pattern from noisy OCR."""
    normalized = _normalize_expression(raw)

    m = re.match(r"^(\d+)([+\-*/])(\d+)$", normalized)
    if m:
        return normalized

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
    """Last resort: try to find the expression by matching against known single-digit math."""
    digit_candidates = []
    for source, raw in all_candidates:
        cleaned = re.sub(r"[^0-9]", "", raw)
        if len(cleaned) == 2:
            digit_candidates.append(cleaned)

    if not digit_candidates:
        return None

    from collections import Counter
    most_common = Counter(digit_candidates).most_common(1)[0][0]

    matches = _VALID_EXPRESSIONS.get(most_common, [])
    if len(matches) == 1:
        expr, result = matches[0]
        return expr

    for preferred_op in ["+", "*", "-"]:
        for expr, result in matches:
            if preferred_op in expr and result >= 0:
                return expr

    return None


def _ocr_single_shot(shot, w, h):
    """Run Vision OCR on a screenshot with multiple preprocessing strategies."""
    candidates = []

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        shot.save(f.name)
        text = ocr_vision(f.name)
        os.unlink(f.name)
    if text:
        candidates.append(("raw", text))

    big = shot.resize((w * 4, h * 4), Image.LANCZOS)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        big.save(f.name)
        text = ocr_vision(f.name)
        os.unlink(f.name)
    if text:
        candidates.append(("scaled", text))

    gray = big.convert("L")
    enhanced = ImageEnhance.Contrast(gray).enhance(3.0)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        enhanced.save(f.name)
        text = ocr_vision(f.name)
        os.unlink(f.name)
    if text:
        candidates.append(("contrast", text))

    return candidates


def read_math_expression(positions, debug=False):
    """OCR the math expression using macOS Vision framework."""
    shot, w, h = _capture_math_region(positions)

    if debug:
        shot.save(os.path.join(SCRIPT_DIR, "debug_math_raw.png"))

    all_candidates = _ocr_single_shot(shot, w, h)

    has_digits = any(re.search(r"\d", text) for _, text in all_candidates)

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

    for source, raw in all_candidates:
        expr = _extract_expression(raw)
        if expr:
            if debug:
                print(f"    Winner: '{raw}' -> '{expr}' (from {source})")
            return expr

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

    click_at(*positions["input"], jitter=5)
    time.sleep(0.4 + random.random() * 0.3)

    click_at(*numpad["clear"], jitter=4)
    time.sleep(0.2 + random.random() * 0.2)

    type_on_numpad(answer, numpad)
    time.sleep(0.3 + random.random() * 0.3)

    click_at(*numpad["confirm"], jitter=4)
    time.sleep(0.4 + random.random() * 0.3)

    click_at(*positions["ok"], jitter=6)
    time.sleep(0.8 + random.random() * 0.5)

    return True


# ═══════════════════════════════════════
#  Main Loop
# ═══════════════════════════════════════

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
#  Garden Bot as Background Thread (for TUI)
# ═══════════════════════════════════════

class GardenBotThread:
    """Runs garden automation in a background thread with callback-based logging."""

    def __init__(self, interval=3.0, debug=False, log_callback=None):
        self.interval = interval
        self.debug = debug
        self.log = log_callback or (lambda msg: None)
        self.running = False
        self.captchas_solved = 0
        self.cycle = 0
        self._thread = None

    def start(self):
        if self.running:
            return
        import threading
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log("Garden bot started")

    def stop(self):
        self.running = False
        self.log("Garden bot stopped")

    def _loop(self):
        layout = load_layout()
        if not layout or "garden_button" not in layout:
            self.log("Garden: No calibration found!")
            self.running = False
            return

        win = find_game_window()
        if not win:
            self.log("Garden: Game window not found!")
            self.running = False
            return

        positions = get_positions(win, layout)

        while self.running:
            try:
                self.cycle += 1

                new_win = find_game_window()
                if new_win:
                    win = new_win
                    positions = get_positions(win, layout)

                if is_garden_visible(positions):
                    click_at(*positions["garden"], jitter=8)

                jitter_range = self.interval * 0.3
                wait = self.interval + random.uniform(-jitter_range, jitter_range)
                time.sleep(max(1.0, wait))

                if handle_captcha_quiet(positions, self.debug):
                    self.captchas_solved += 1
                    self.log(f"Garden CAPTCHA solved (total: {self.captchas_solved})")

                time.sleep(0.3 + random.random() * 0.7)

            except Exception as e:
                self.log(f"Garden error: {e}")
                time.sleep(2)


def handle_captcha_quiet(positions, debug=False):
    """CAPTCHA handler that doesn't print to stdout (for TUI mode)."""
    if not is_dialog_visible(positions):
        return False

    time.sleep(0.5)

    raw_text = read_math_expression(positions, debug)
    answer = solve_expression(raw_text)
    if answer is None:
        time.sleep(0.8)
        raw_text = read_math_expression(positions, debug)
        answer = solve_expression(raw_text)
        if answer is None:
            return False

    numpad = positions["numpad"]

    click_at(*positions["input"], jitter=5)
    time.sleep(0.4 + random.random() * 0.3)

    click_at(*numpad["clear"], jitter=4)
    time.sleep(0.2 + random.random() * 0.2)

    type_on_numpad(answer, numpad)
    time.sleep(0.3 + random.random() * 0.3)

    click_at(*numpad["confirm"], jitter=4)
    time.sleep(0.4 + random.random() * 0.3)

    click_at(*positions["ok"], jitter=6)
    time.sleep(0.8 + random.random() * 0.5)

    return True


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

    elif cmd == "calibrate-boss":
        from shared import calibrate_boss
        calibrate_boss()

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

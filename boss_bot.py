#!/usr/bin/env python3
"""
Ragnarok X Boss Farming Bot

Automates MVP/Mini boss hunting:
- Opens MVP/Mini panel, reads boss status via OCR
- Navigates to spawned bosses (clicks "Go")
- Toggles auto-attack, selects boss monster
- Detects death → resurrects → re-navigates
- Switches to Channel 1 for boss spawns

State machine:
    IDLE → SWITCH_CH1 → OPEN_PANEL → CHECK_STATUS → CLICK_GO
    → START_ATTACK → FIGHTING → DEAD → RESURRECT → (loop)
"""

import time
import re
import random
import threading
from enum import Enum

import pyautogui

from shared import (
    find_game_window,
    load_layout,
    load_boss_config,
    save_boss_config,
    click_at,
    ocr_region,
    screenshot_region,
    check_brightness,
    get_boss_positions,
)


# ═══════════════════════════════════════
#  Boss Lists
# ═══════════════════════════════════════

MVP_BOSSES = [
    "Phreeoni", "Mistress", "Kraken", "Eddga",
    "Maya", "Orc Hero", "Pharaoh", "Orc Lord",
]

MINI_BOSSES = [
    "Dragon Fly", "Eclipse", "Mastering", "Ghostring",
    "Toad", "King Dramoh", "Deviling", "Angeling",
]

ALL_BOSSES = MVP_BOSSES + MINI_BOSSES


# ═══════════════════════════════════════
#  State Machine
# ═══════════════════════════════════════

class BossState(Enum):
    IDLE = "IDLE"
    SWITCH_CH1 = "SWITCH_CH1"
    OPEN_PANEL = "OPEN_PANEL"
    CHECK_STATUS = "CHECK_STATUS"
    CLICK_GO = "CLICK_GO"
    START_ATTACK = "START_ATTACK"
    FIGHTING = "FIGHTING"
    DEAD = "DEAD"
    RESURRECT = "RESURRECT"
    RE_NAVIGATE = "RE_NAVIGATE"


class BossFarmingBot:
    """Boss farming automation engine.

    Runs as a background thread, controlled via start()/stop().
    Communicates status via log_callback and exposes state for the TUI.
    """

    def __init__(self, log_callback=None):
        self.log = log_callback or (lambda msg: None)
        self.state = BossState.IDLE
        self.running = False
        self._thread = None

        # Current target
        self.target_boss = None
        self.target_is_mvp = True

        # Stats
        self.deaths = 0
        self.bosses_killed = 0
        self.current_channel = "?"

        # Config
        self.selected_mvps = []
        self.selected_minis = []
        self.check_interval = 30  # seconds between panel checks when idle

        # Positions (loaded on start)
        self._win = None
        self._layout = None
        self._boss_pos = None

        # Timers: boss_name -> last_seen_timer_text
        self.boss_timers = {}

    # ─── Public API ───

    def start(self):
        if self.running:
            return
        config = load_boss_config()
        self.selected_mvps = config.get("selected_mvps", [])
        self.selected_minis = config.get("selected_minis", [])

        if not self.selected_mvps and not self.selected_minis:
            self.log("No bosses selected! Select bosses in the panel first.")
            return

        self._layout = load_layout()
        if not self._layout or "boss" not in self._layout:
            self.log("Boss calibration not found! Press C to calibrate.")
            return

        self._win = find_game_window()
        if not self._win:
            self.log("Game window not found!")
            return

        self._boss_pos = get_boss_positions(self._win, self._layout)

        self.running = True
        self.state = BossState.IDLE
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        self.log("Boss bot started")

    def stop(self):
        self.running = False
        self.state = BossState.IDLE
        self.target_boss = None
        self.log("Boss bot stopped")

    def update_selection(self, mvps, minis):
        """Update boss selection (called from TUI)."""
        self.selected_mvps = mvps
        self.selected_minis = minis
        save_boss_config({
            "selected_mvps": mvps,
            "selected_minis": minis,
            "timers": self.boss_timers,
        })

    # ─── Main Loop ───

    def _main_loop(self):
        while self.running:
            try:
                # Refresh window position
                new_win = find_game_window()
                if new_win:
                    self._win = new_win
                    self._boss_pos = get_boss_positions(self._win, self._layout)

                if not self._boss_pos:
                    self.log("Boss positions unavailable")
                    time.sleep(5)
                    continue

                # Check for loading screen before every state tick.
                # Loading screens mean a map/channel change — after it
                # finishes we must re-verify we're on CH 1.
                if self._detect_loading_screen():
                    self._wait_for_loading_screen()
                    # After loading, force channel check
                    self.state = BossState.SWITCH_CH1
                    continue

                if self.state == BossState.IDLE:
                    self._handle_idle()
                elif self.state == BossState.SWITCH_CH1:
                    self._handle_switch_ch1()
                elif self.state == BossState.OPEN_PANEL:
                    self._handle_open_panel()
                elif self.state == BossState.CHECK_STATUS:
                    self._handle_check_status()
                elif self.state == BossState.CLICK_GO:
                    self._handle_click_go()
                elif self.state == BossState.START_ATTACK:
                    self._handle_start_attack()
                elif self.state == BossState.FIGHTING:
                    self._handle_fighting()
                elif self.state == BossState.DEAD:
                    self._handle_dead()
                elif self.state == BossState.RESURRECT:
                    self._handle_resurrect()
                elif self.state == BossState.RE_NAVIGATE:
                    self._handle_re_navigate()

            except Exception as e:
                self.log(f"Error in state {self.state.value}: {e}")
                time.sleep(3)

    # ─── State Handlers ───

    def _handle_idle(self):
        """Wait and periodically check the boss panel for spawns."""
        self.log("Waiting for boss spawn...")

        # Check every N seconds
        for _ in range(self.check_interval):
            if not self.running:
                return
            time.sleep(1)

        # Time to check - switch to CH1 first if needed
        self.state = BossState.SWITCH_CH1

    def _handle_switch_ch1(self):
        """Switch to Channel 1 where bosses spawn.

        First OCRs the channel button to check current channel.
        If already on CH 1, skips opening the modal entirely.
        """
        pos = self._boss_pos

        # OCR a wider area around the channel button to read "CH 1" etc.
        # Note: the arrow icon next to the number gets misread as "1" by OCR,
        # e.g. "CH 2" → "CH 21", "CH 10" → "CH 101".
        # The real number is all digits with the trailing arrow-"1" stripped.
        ch_x, ch_y = pos["channel_button"]
        ch_text = ocr_region(ch_x - 60, ch_y - 15, 130, 35)
        self.log(f"Current channel OCR: '{ch_text.strip()}'")

        ch_match = re.search(r'ch\s*(\d+)', ch_text, re.IGNORECASE)
        if ch_match:
            raw_digits = ch_match.group(1)
            # Strip trailing "1" from arrow misread (only if >1 digit)
            if len(raw_digits) > 1 and raw_digits.endswith("1"):
                channel_num = raw_digits[:-1]
            else:
                channel_num = raw_digits
        else:
            channel_num = None

        self.log(f"Detected channel: {channel_num}")

        if channel_num == "1":
            self.current_channel = "CH 1"
            self.log("Already on CH 1, skipping channel switch.")
            self.state = BossState.OPEN_PANEL
            return

        # Click channel button and verify the modal opened.
        # Retry up to 3 times — sometimes the click doesn't register.
        ch1_x, ch1_y = pos["ch1_button"]
        modal_opened = False

        for attempt in range(3):
            self.log(f"Opening channel selector (attempt {attempt + 1})...")
            click_at(*pos["channel_button"], jitter=0)
            time.sleep(2.0 + random.random() * 0.5)

            # Check if the channel modal opened — the modal is a bright popup
            # in the center area around where CH 1 button is
            modal_opened = check_brightness(
                ch1_x - 60, ch1_y - 40, 120, 80, threshold=150
            )
            if modal_opened:
                break
            self.log("Channel modal didn't open, retrying...")
            time.sleep(1.0 + random.random() * 0.5)

        if not modal_opened:
            self.log("Failed to open channel modal. Proceeding to panel anyway.")
            self.state = BossState.OPEN_PANEL
            return

        # Click CH 1 in the popup
        self.log("Selecting CH 1...")
        click_at(*pos["ch1_button"], jitter=2)
        time.sleep(2.0 + random.random() * 1.0)

        self.current_channel = "CH 1"
        self.log("Switched to CH 1")
        self.state = BossState.OPEN_PANEL

    def _handle_open_panel(self):
        """Open the MVP/Mini panel and scroll to top to normalize."""
        pos = self._boss_pos

        # Click the MVP panel button on top bar, verify it opened
        MAX_RETRIES = 3
        panel_open = False
        for attempt in range(MAX_RETRIES):
            click_at(*pos["mvp_panel_button"], jitter=3)
            time.sleep(1.2 + random.random() * 0.5)

            # Check if the panel opened by looking for brightness around the
            # panel close (X) button — the panel header is bright when open
            close_x, close_y = pos["panel_close"]
            panel_open = check_brightness(
                close_x - 40, close_y - 15, 80, 30, threshold=160
            )
            if panel_open:
                break
            self.log(f"Panel didn't open (attempt {attempt + 1}/{MAX_RETRIES}), retrying...")
            time.sleep(0.5 + random.random() * 0.3)

        if not panel_open:
            self.log("Failed to open MVP panel after retries. Back to IDLE.")
            self.state = BossState.IDLE
            return

        # Determine which tab to check first (MVPs have priority)
        if self.selected_mvps:
            click_at(*pos["mvp_tab"], jitter=3)
            self._checking_mvp_tab = True
        elif self.selected_minis:
            click_at(*pos["mini_tab"], jitter=3)
            self._checking_mvp_tab = False
        else:
            self._close_panel()
            self.state = BossState.IDLE
            return

        time.sleep(0.8 + random.random() * 0.3)

        # Scroll to top to normalize position before reading
        self._scroll_to_top()

        self.state = BossState.CHECK_STATUS

    def _handle_check_status(self):
        """OCR each boss row individually to find one that has appeared.

        Per-row OCR prevents mismatching boss names with statuses.
        The panel shows 4 rows at a time; we check page 1, scroll, check page 2.
        """
        pos = self._boss_pos

        scroll_region = pos.get("panel_scroll_region")
        if not scroll_region:
            fr_x, fr_y = pos["first_boss_row"]
            scroll_region = (fr_x, fr_y, 831, 405)

        sx, sy, sw, sh = scroll_region
        scroll_dist = pos.get("scroll_distance", sh)
        row_h = pos.get("row_height", sh // 4)

        if self._checking_mvp_tab:
            targets = self.selected_mvps
        else:
            targets = self.selected_minis

        # Scan 2 pages (page 1 = rows 1-4, page 2 = rows 5-8)
        found_boss = None
        found_on_page = 0

        for page in range(2):
            # OCR each of the 4 visible rows individually
            for row_idx in range(4):
                row_y = sy + row_idx * row_h
                row_text = ocr_region(sx, row_y, sw, row_h)
                row_lower = row_text.lower()

                # Check if THIS row has a target boss AND "appeared"/"battle"
                for boss in targets:
                    if boss.lower() in row_lower:
                        if ("appeared" in row_lower
                                or "inthebattle" in row_lower
                                or "battle" in row_lower):
                            found_boss = boss
                            self._found_row_idx = row_idx
                            break
                        # Record timer for this boss
                        timer_match = re.search(r"(\d{1,2}:\d{2}:\d{2})", row_text)
                        if timer_match:
                            self.boss_timers[boss] = timer_match.group(1)

                if found_boss:
                    break

            if found_boss:
                found_on_page = page
                break

            # Scroll down for page 2 (only after page 1)
            if page == 0:
                self._scroll_panel_down(sx + sw // 2, sy + sh // 2, scroll_dist)
                time.sleep(0.6 + random.random() * 0.3)

        if found_boss:
            self.target_boss = found_boss
            self.target_is_mvp = found_boss in MVP_BOSSES
            self.log(f"{found_boss} appeared! (row {self._found_row_idx + 1}, page {found_on_page + 1})")
            self.state = BossState.CLICK_GO
            return

        # Scroll back to top before switching tabs or closing
        self._scroll_to_top()
        time.sleep(0.3)

        # If we checked MVPs, now try Minis
        if self._checking_mvp_tab and self.selected_minis:
            click_at(*pos["mini_tab"], jitter=3)
            self._checking_mvp_tab = False
            time.sleep(0.8 + random.random() * 0.3)
            self._scroll_to_top()
            return  # re-enters CHECK_STATUS for mini tab

        # No bosses spawned on either tab
        self._close_panel()
        self.log("No bosses spawned. Checking again later.")
        self.state = BossState.IDLE

    def _handle_click_go(self):
        """Click the Go button on the correct row for the found boss."""
        pos = self._boss_pos

        # The Go button is calibrated for one row. Adjust Y by the row offset.
        go_x, go_y = pos["go_button"]
        row_h = pos.get("row_height", 101)
        first_y = pos["first_boss_row"][1]
        # Calibrated go_button might be on any row — calculate which row it was on
        calib_row = round((go_y - first_y) / row_h) if row_h > 0 else 0
        # Offset to the found row
        row_offset = (getattr(self, '_found_row_idx', 0) - calib_row) * row_h
        target_go_y = go_y + row_offset

        self.log(f"Clicking Go for {self.target_boss} (row {getattr(self, '_found_row_idx', 0) + 1})...")
        click_at(go_x, target_go_y, jitter=2)
        time.sleep(2.0 + random.random() * 0.5)

        # Verify the panel actually closed (Go closes it automatically).
        # Sometimes scroll jumps and Go isn't clicked — panel stays open.
        close_x, close_y = pos["panel_close"]
        panel_still_open = check_brightness(
            close_x - 40, close_y - 15, 80, 30, threshold=160
        )
        if panel_still_open:
            self.log("Panel still open after Go click — closing manually...")
            self._close_panel()
            time.sleep(1.0 + random.random() * 0.3)

        # Wait for loading screen (map change) to appear and finish
        self.log("Waiting for loading screen...")
        # Give the game a moment to start the loading screen
        time.sleep(2.0)
        loading_detected = False
        for _ in range(10):  # check for up to 10s for loading to start
            if self._detect_loading_screen():
                loading_detected = True
                break
            time.sleep(1.0)

        if loading_detected:
            self._wait_for_loading_screen()
        else:
            self.log("No loading screen detected, may be same map.")
            time.sleep(3.0)

        if not self.running:
            return

        # After loading, ensure we're on CH 1 before tracking movement
        self._ensure_ch1()

        if not self.running:
            return

        # Open minimap and watch for arrival (character walking to boss)
        self._wait_for_arrival()

        if not self.running:
            return

        self._fighting_start = time.time()
        self.state = BossState.START_ATTACK

    def _wait_for_arrival(self):
        """Open minimap, compare screenshots to detect when character stops moving.

        Takes periodic screenshots of the minimap area and compares pixel
        differences. When the image is stable for 5 seconds, the character
        has arrived. Much more reliable than OCR coordinate parsing.
        Max wait of 120 seconds to avoid infinite loop.
        """
        pos = self._boss_pos
        ch_x, ch_y = pos["channel_button"]

        # Open minimap — the button is near the channel button, top-right
        minimap_btn_x = ch_x
        minimap_btn_y = ch_y + 50
        self.log("Opening minimap to track movement...")

        # Check if minimap is already open; if not, click to open
        minimap_check_x = ch_x - 120
        minimap_check_y = ch_y + 40
        minimap_check_w = 150
        minimap_check_h = 150
        is_open = check_brightness(
            minimap_check_x, minimap_check_y,
            minimap_check_w, minimap_check_h, threshold=140
        )
        if not is_open:
            click_at(minimap_btn_x, minimap_btn_y, jitter=1)
            time.sleep(1.0 + random.random() * 0.3)

        # Region to compare: the minimap content area
        snap_x = minimap_check_x
        snap_y = minimap_check_y
        snap_w = minimap_check_w
        snap_h = minimap_check_h

        STABLE_DURATION = 5.0   # seconds of no change = arrived
        MAX_WAIT = 120.0
        POLL_INTERVAL = 1.5
        # Pixel difference threshold: below this % means "same image"
        DIFF_THRESHOLD = 2.0

        last_snapshot = None
        stable_since = None
        start_time = time.time()
        self.log(f"Walking to {self.target_boss}... watching minimap")

        while self.running and (time.time() - start_time) < MAX_WAIT:
            current_snapshot = screenshot_region(snap_x, snap_y, snap_w, snap_h)

            if last_snapshot is not None:
                diff_pct = self._image_diff_percent(last_snapshot, current_snapshot)

                if diff_pct > DIFF_THRESHOLD:
                    # Image changed — still moving
                    elapsed = time.time() - start_time
                    self.log(f"Moving... ({elapsed:.0f}s, diff={diff_pct:.1f}%)")
                    stable_since = time.time()
                else:
                    # Image stable
                    if stable_since is None:
                        stable_since = time.time()
                    elapsed_stable = time.time() - stable_since
                    if elapsed_stable >= STABLE_DURATION:
                        self.log(f"Arrived at {self.target_boss}! (minimap stable for {STABLE_DURATION}s)")
                        break
            else:
                stable_since = time.time()

            last_snapshot = current_snapshot
            time.sleep(POLL_INTERVAL)
        else:
            if self.running:
                self.log("Navigation timeout (120s). Proceeding anyway...")

        # Close the minimap
        self.log("Closing minimap...")
        click_at(minimap_btn_x, minimap_btn_y, jitter=1)
        time.sleep(0.8 + random.random() * 0.3)

        # Verify minimap closed
        still_open = check_brightness(
            minimap_check_x, minimap_check_y,
            minimap_check_w, minimap_check_h, threshold=140
        )
        if still_open:
            click_at(minimap_btn_x, minimap_btn_y, jitter=0)
            time.sleep(0.5)

    @staticmethod
    def _image_diff_percent(img1, img2):
        """Calculate the percentage of pixels that differ between two PIL images.

        Converts to grayscale, computes per-pixel absolute difference,
        and returns the percentage of pixels that differ by more than 10 levels.
        """
        g1 = img1.convert("L")
        g2 = img2.convert("L")
        pixels1 = list(g1.getdata())
        pixels2 = list(g2.getdata())
        if len(pixels1) != len(pixels2):
            return 100.0
        total = len(pixels1)
        changed = sum(1 for a, b in zip(pixels1, pixels2) if abs(a - b) > 10)
        return (changed / total) * 100.0

    def _handle_start_attack(self):
        """Close minimap if open, then toggle auto-attack and select boss monster."""
        pos = self._boss_pos

        # Detect and close minimap if open — it blocks auto-attack.
        self._close_minimap_if_open()

        # Click auto-attack toggle to open the monster dropdown
        self.log("Enabling auto-attack...")
        click_at(*pos["auto_attack_toggle"], jitter=3)
        time.sleep(1.0 + random.random() * 0.5)

        # OCR the monster dropdown to find the boss name
        self._select_boss_from_monster_list()

        self.log(f"Attacking {self.target_boss}!")
        self._fighting_start = time.time()
        self.state = BossState.FIGHTING

    def _handle_fighting(self):
        """Monitor for death while fighting the boss.

        Has a timeout: if fighting for >90s with no death/kill detected,
        assume the boss is already dead and go back to check the panel.
        """
        FIGHTING_TIMEOUT = 90  # seconds

        # Check for death
        if self._detect_death():
            self.deaths += 1
            self.log(f"Died! (death #{self.deaths})")
            self.state = BossState.DEAD
            return

        # Check fighting timeout — boss may already be dead or gone
        elapsed = time.time() - getattr(self, '_fighting_start', time.time())
        if elapsed > FIGHTING_TIMEOUT:
            self.log(f"Fighting timeout ({FIGHTING_TIMEOUT}s). Boss may be dead. Re-checking panel...")
            self.target_boss = None
            self.state = BossState.IDLE
            return

        time.sleep(2.0 + random.random() * 1.0)

    def _handle_dead(self):
        """Player died - prepare to resurrect."""
        # Brief pause before resurrect (game has a short delay)
        time.sleep(2.0 + random.random() * 1.0)
        self.state = BossState.RESURRECT

    def _handle_resurrect(self):
        """Click the resurrect button."""
        pos = self._boss_pos

        self.log("Clicking resurrect...")
        click_at(*pos["resurrect_button"], jitter=4)
        time.sleep(3.0 + random.random() * 1.0)

        # Verify we're alive (check for resurrect button gone)
        if self._detect_death():
            # Still dead, try again
            self.log("Resurrect failed, retrying...")
            click_at(*pos["resurrect_button"], jitter=4)
            time.sleep(3.0 + random.random() * 1.0)

        self.log("Resurrected! Re-navigating to boss...")
        self.state = BossState.RE_NAVIGATE

    def _handle_re_navigate(self):
        """After resurrection, navigate back to the boss."""
        if not self.target_boss:
            self.state = BossState.IDLE
            return

        # Open panel again to click Go
        self.state = BossState.OPEN_PANEL

    # ─── Channel Helpers ───

    def _ensure_ch1(self):
        """Check current channel and switch to CH 1 if needed.

        Inline version of _handle_switch_ch1 for use within other methods
        (e.g. after loading screens during navigation).
        """
        pos = self._boss_pos

        # OCR the channel button
        ch_x, ch_y = pos["channel_button"]
        ch_text = ocr_region(ch_x - 60, ch_y - 15, 130, 35)
        self.log(f"Post-load channel OCR: '{ch_text.strip()}'")

        ch_match = re.search(r'ch\s*(\d+)', ch_text, re.IGNORECASE)
        if ch_match:
            raw_digits = ch_match.group(1)
            if len(raw_digits) > 1 and raw_digits.endswith("1"):
                channel_num = raw_digits[:-1]
            else:
                channel_num = raw_digits
        else:
            channel_num = None

        if channel_num == "1":
            self.current_channel = "CH 1"
            self.log("Already on CH 1.")
            return

        self.log(f"On CH {channel_num or '?'}, switching to CH 1...")

        # Open channel modal with retries
        ch1_x, ch1_y = pos["ch1_button"]
        modal_opened = False
        for attempt in range(3):
            click_at(*pos["channel_button"], jitter=0)
            time.sleep(2.0 + random.random() * 0.5)

            modal_opened = check_brightness(
                ch1_x - 60, ch1_y - 40, 120, 80, threshold=150
            )
            if modal_opened:
                break
            self.log(f"Channel modal didn't open (attempt {attempt + 1}), retrying...")
            time.sleep(1.0 + random.random() * 0.5)

        if modal_opened:
            self.log("Selecting CH 1...")
            click_at(*pos["ch1_button"], jitter=2)
            time.sleep(2.0 + random.random() * 1.0)

            # Wait for possible loading screen from channel switch
            time.sleep(2.0)
            if self._detect_loading_screen():
                self._wait_for_loading_screen()

            self.current_channel = "CH 1"
            self.log("Switched to CH 1.")
        else:
            self.log("Failed to open channel modal. Continuing anyway.")

    # ─── Detection Helpers ───

    def _detect_loading_screen(self):
        """Detect if a loading screen is showing.

        Loading screens are very dark and cover the entire game window.
        Check the center of the screen for low brightness.
        """
        if not self._win:
            return False
        wx, wy = self._win["x"], self._win["y"]
        ww, wh = self._win["w"], self._win["h"]
        # Sample a large center area
        is_dark = not check_brightness(
            wx + ww // 4, wy + wh // 4, ww // 2, wh // 2,
            threshold=40  # Loading screens are very dark
        )
        return is_dark

    def _wait_for_loading_screen(self):
        """Wait for a loading screen to finish (screen goes bright again).

        Polls every 1 second, max 30 seconds.
        """
        self.log("Loading screen detected. Waiting...")
        MAX_WAIT = 30.0
        start = time.time()
        while self.running and (time.time() - start) < MAX_WAIT:
            time.sleep(1.0)
            if not self._detect_loading_screen():
                # Screen is back — wait a bit more for UI to settle
                time.sleep(2.0 + random.random() * 1.0)
                self.log("Loading complete. Re-checking channel...")
                return
        self.log("Loading screen timeout (30s). Proceeding...")

    def _detect_death(self):
        """Detect if the player has died.

        Looks for the resurrect button (bright element on dark death screen).
        Uses OCR to find "Resurrect" text.
        """
        pos = self._boss_pos
        rx, ry = pos["resurrect_button"]

        # Check area around resurrect button for "Resurrect" text
        text = ocr_region(rx - 60, ry - 15, 120, 30)
        if "resurrect" in text.lower() or "revive" in text.lower():
            return True

        # Also check screen brightness (death screen is darker)
        if self._win:
            wx, wy = self._win["x"], self._win["y"]
            ww, wh = self._win["w"], self._win["h"]
            # Check center of screen brightness
            center_bright = check_brightness(
                wx + ww // 4, wy + wh // 4, ww // 2, wh // 2,
                threshold=80  # Death screen is very dark
            )
            if not center_bright:
                # Screen is dark - likely dead
                return True

        return False

    def _check_boss_defeated(self):
        """Quick check if the current target boss has been defeated.

        Opens panel briefly to check status.
        """
        # Don't check too aggressively - this interrupts combat
        return False  # TODO: implement periodic panel check

    def _close_panel(self):
        """Close the MVP/Mini panel."""
        pos = self._boss_pos
        if "panel_close" in pos:
            click_at(*pos["panel_close"], jitter=3)
            time.sleep(0.5 + random.random() * 0.3)

    def _get_card_drag_point(self, row_idx=1):
        """Get the center of a boss card for drag scrolling.

        The boss list is on the LEFT side of the panel.
        Must start the drag ON a card, not between cards.
        row_idx: which visible row to use as drag start (0-3).
        """
        pos = self._boss_pos
        first_x, first_y = pos["first_boss_row"]
        go_x = pos["go_button"][0]
        row_h = pos.get("row_height", 101)

        # Card center X: midpoint between left edge and Go button
        card_cx = (first_x + go_x) // 2
        # Card center Y: center of the specified row
        card_cy = first_y + row_idx * row_h + row_h // 2

        return card_cx, card_cy

    def _drag_scroll(self, start_x, start_y, dy):
        """Scroll by dragging: click-hold on a card, move vertically, release.

        dy > 0 means drag downward (scroll content up / toward top).
        dy < 0 means drag upward (scroll content down / toward bottom).
        """
        jx = random.randint(-4, 4)
        jy = random.randint(-4, 4)
        pyautogui.moveTo(int(start_x + jx), int(start_y + jy), duration=0.1)
        time.sleep(0.05)
        pyautogui.mouseDown()
        time.sleep(0.05)
        pyautogui.move(0, int(dy), duration=0.5)
        time.sleep(0.05)
        pyautogui.mouseUp()
        time.sleep(0.2)

    def _scroll_to_top(self):
        """Scroll boss list to top: drag down on a card twice."""
        pos = self._boss_pos
        row_h = pos.get("row_height", 101)
        scroll_dist = pos.get("scroll_distance", 405)

        # Start drag on the 2nd card (row 1) — safe middle position
        cx, cy = self._get_card_drag_point(row_idx=1)

        # Drag down generously twice to guarantee top
        self._drag_scroll(cx, cy, scroll_dist)
        time.sleep(0.2)
        self._drag_scroll(cx, cy, scroll_dist)
        time.sleep(0.3)

    def _scroll_panel_down(self, cx_unused, cy_unused, distance):
        """Scroll list down: drag UP on a card to reveal lower bosses."""
        # Start on the 3rd card (row 2) — room to drag upward
        cx, cy = self._get_card_drag_point(row_idx=2)
        self._drag_scroll(cx, cy, -distance)

    def _scroll_panel_up(self, cx_unused, cy_unused, distance):
        """Scroll list up: drag DOWN on a card to reveal upper bosses."""
        # Start on the 2nd card (row 1) — room to drag downward
        cx, cy = self._get_card_drag_point(row_idx=1)
        self._drag_scroll(cx, cy, distance)

    def _close_minimap_if_open(self):
        """Detect if the minimap is open and close it. Don't toggle blindly.

        The minimap appears in the top-right corner below the channel button.
        When open, that area is bright (map content). When closed, the area
        shows the darker game world. We check brightness to decide.
        """
        pos = self._boss_pos
        ch_x, ch_y = pos["channel_button"]

        # The minimap region sits below the channel button, top-right of screen.
        # Check a ~150x150 area below the channel button for brightness.
        minimap_check_x = ch_x - 120
        minimap_check_y = ch_y + 40
        minimap_check_w = 150
        minimap_check_h = 150

        is_bright = check_brightness(
            minimap_check_x, minimap_check_y,
            minimap_check_w, minimap_check_h,
            threshold=140  # minimap has bright map content
        )

        if is_bright:
            self.log("Minimap detected (bright), closing it...")
            # Click the minimap toggle button to close it
            minimap_btn_x = ch_x
            minimap_btn_y = ch_y + 50
            click_at(minimap_btn_x, minimap_btn_y, jitter=1)
            time.sleep(0.8 + random.random() * 0.3)

            # Verify it closed
            still_bright = check_brightness(
                minimap_check_x, minimap_check_y,
                minimap_check_w, minimap_check_h,
                threshold=140
            )
            if still_bright:
                self.log("Minimap still open, trying again...")
                click_at(minimap_btn_x, minimap_btn_y, jitter=0)
                time.sleep(0.8 + random.random() * 0.3)
        else:
            self.log("Minimap not detected, proceeding to auto-attack.")

    def _select_boss_from_monster_list(self):
        """OCR the monster dropdown list and click on the boss name.

        The auto-attack dropdown shows nearby monsters. We scan each row
        via OCR to find the target boss, then click on that specific entry.
        Falls back to the first entry if the boss name isn't found.
        """
        pos = self._boss_pos
        first_x, first_y = pos["monster_list_first"]

        # Monster dropdown entries are stacked vertically.
        # Each entry is roughly 35-40px tall. Scan up to 6 entries.
        ENTRY_HEIGHT = 38
        MAX_ENTRIES = 6
        ENTRY_WIDTH = 200  # width of the monster name area

        # OCR the entire dropdown area at once for efficiency
        region_x = first_x - 100
        region_y = first_y - 10
        region_w = ENTRY_WIDTH
        region_h = ENTRY_HEIGHT * MAX_ENTRIES

        full_text = ocr_region(region_x, region_y, region_w, region_h)
        self.log(f"Monster list OCR: {full_text[:80]}")

        # Per-row OCR to find the exact row with the boss name
        boss_lower = self.target_boss.lower() if self.target_boss else ""
        # Also try partial matches (e.g. "Dragon Fly" might show as "Dragon fly")
        boss_words = boss_lower.split()

        # Blacklist: never click these entries
        SKIP_ENTRIES = {"all monsters", "all monster", "tüm canavarlar"}

        found_row = None
        for row_idx in range(MAX_ENTRIES):
            row_y = first_y + row_idx * ENTRY_HEIGHT - 5
            row_text = ocr_region(region_x, row_y, ENTRY_WIDTH, ENTRY_HEIGHT)
            row_lower = row_text.lower().strip()

            if not row_lower:
                continue

            # Never select "All Monsters"
            if any(skip in row_lower for skip in SKIP_ENTRIES):
                self.log(f"Skipping '{row_text.strip()}' (row {row_idx + 1})")
                continue

            # Exact match
            if boss_lower in row_lower:
                found_row = row_idx
                self.log(f"Found {self.target_boss} at monster list row {row_idx + 1}")
                break

            # Partial match: all words of boss name appear in row
            if boss_words and all(w in row_lower for w in boss_words):
                found_row = row_idx
                self.log(f"Found {self.target_boss} (partial) at row {row_idx + 1}: '{row_text.strip()}'")
                break

        if found_row is not None:
            click_y = first_y + found_row * ENTRY_HEIGHT + ENTRY_HEIGHT // 2
            click_at(first_x, click_y, jitter=3)
        else:
            # Boss not found — do NOT click anything, especially not "All Monsters"
            self.log(f"[yellow]Boss '{self.target_boss}' not found in monster list. Not attacking.[/]")

        time.sleep(0.5 + random.random() * 0.3)

    # ─── Status for TUI ───

    @property
    def status_text(self):
        """Current state as display text."""
        return self.state.value

    @property
    def target_text(self):
        """Current target boss name."""
        return self.target_boss or "None"

    def get_boss_timer(self, boss_name):
        """Get last known timer for a boss."""
        return self.boss_timers.get(boss_name, "--:--:--")

    def get_boss_status(self, boss_name):
        """Get display status for a boss in the TUI."""
        if boss_name == self.target_boss:
            if self.state == BossState.FIGHTING:
                return "Fighting!"
            elif self.state in (BossState.CLICK_GO, BossState.START_ATTACK):
                return "Navigating..."
            elif self.state in (BossState.DEAD, BossState.RESURRECT, BossState.RE_NAVIGATE):
                return "Died - respawning"
            else:
                return "Appeared!"
        timer = self.boss_timers.get(boss_name)
        if timer:
            return f"⏱ {timer}"
        return "⏱ --:--:--"

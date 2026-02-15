#!/usr/bin/env python3
"""
Ragnarok X Bot - Terminal UI

Launch with: python gui.py

Controls:
    S  Start/Stop boss farming
    G  Toggle garden bot on/off
    C  Run boss calibration (suspends TUI)
    Q  Quit
"""

import time
import sys
import os

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Header,
    Footer,
    Static,
    Label,
    RichLog,
    Checkbox,
)
from textual.binding import Binding
from textual.reactive import reactive

from shared import (
    load_layout,
    load_boss_config,
    save_boss_config,
    find_game_window,
    calibrate_boss,
    SCRIPT_DIR,
)
from boss_bot import BossFarmingBot, MVP_BOSSES, MINI_BOSSES
from garden_bot import GardenBotThread


# ═══════════════════════════════════════
#  Widgets
# ═══════════════════════════════════════

class BossCheckbox(Checkbox):
    """A checkbox for a boss with timer display."""

    def __init__(self, boss_name: str, is_mvp: bool, checked: bool = False):
        super().__init__(
            boss_name,
            value=checked,
            id=f"boss-{boss_name.lower().replace(' ', '-')}",
        )
        self.boss_name = boss_name
        self.is_mvp = is_mvp


class StatusPanel(Static):
    """Live status display for bot state."""

    bot_state = reactive("IDLE")
    target_boss = reactive("None")
    death_count = reactive(0)
    channel = reactive("?")
    garden_status = reactive("Stopped")
    garden_interval = reactive(3.0)
    captchas_solved = reactive(0)

    def render(self) -> str:
        return (
            f"[bold]State:[/]    {self.bot_state}\n"
            f"[bold]Target:[/]   {self.target_boss}\n"
            f"[bold]Deaths:[/]   {self.death_count}\n"
            f"[bold]Channel:[/]  {self.channel}\n"
            f"\n"
            f"[bold]Garden:[/]   {self.garden_status}\n"
            f"[bold]Interval:[/] {self.garden_interval}s\n"
            f"[bold]CAPTCHAs:[/] {self.captchas_solved}"
        )


class ControlsPanel(Static):
    """Keyboard controls reference."""

    def render(self) -> str:
        return (
            "[bold cyan]S[/] Start/Stop Boss\n"
            "[bold cyan]G[/] Garden On/Off\n"
            "[bold cyan]C[/] Calibrate Boss\n"
            "[bold cyan]Q[/] Quit"
        )


# ═══════════════════════════════════════
#  Main App
# ═══════════════════════════════════════

class RagnarokBotApp(App):
    """Ragnarok X Bot Terminal UI."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2 2;
        grid-columns: 1fr 1fr;
        grid-rows: 3fr 1fr;
        grid-gutter: 1;
    }

    #left-panel {
        row-span: 1;
        border: solid $accent;
        padding: 1;
        overflow-y: auto;
    }

    #right-panel {
        layout: vertical;
        row-span: 1;
    }

    #status-box {
        border: solid $accent;
        padding: 1;
        height: 1fr;
    }

    #controls-box {
        border: solid $accent;
        padding: 1;
        height: auto;
    }

    #log-panel {
        column-span: 2;
        border: solid $accent;
        padding: 0 1;
    }

    .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    .boss-section-title {
        text-style: bold;
        color: $warning;
        margin-top: 1;
        margin-bottom: 0;
    }

    BossCheckbox {
        height: auto;
        min-height: 1;
        margin: 0;
        padding: 0;
    }

    StatusPanel {
        height: auto;
    }

    ControlsPanel {
        height: auto;
    }

    RichLog {
        height: 100%;
    }
    """

    TITLE = "Ragnarok X Bot"

    BINDINGS = [
        Binding("s", "toggle_boss", "Start/Stop Boss", show=True),
        Binding("g", "toggle_garden", "Garden On/Off", show=True),
        Binding("c", "calibrate", "Calibrate", show=True),
        Binding("q", "quit_app", "Quit", show=True),
    ]

    def __init__(self):
        super().__init__()
        self._boss_bot = BossFarmingBot(log_callback=self._bot_log)
        self._garden_bot = GardenBotThread(
            interval=3.0,
            log_callback=self._bot_log,
        )
        self._boss_running = False
        self._garden_running = False

    def compose(self) -> ComposeResult:
        yield Header()

        # Left: Boss selection
        with VerticalScroll(id="left-panel"):
            yield Label("Boss Selection", classes="section-title")
            config = load_boss_config()
            selected_mvps = config.get("selected_mvps", [])
            selected_minis = config.get("selected_minis", [])

            yield Label("MVPs:", classes="boss-section-title")
            for boss in MVP_BOSSES:
                yield BossCheckbox(boss, is_mvp=True, checked=boss in selected_mvps)

            yield Label("Minis:", classes="boss-section-title")
            for boss in MINI_BOSSES:
                yield BossCheckbox(boss, is_mvp=False, checked=boss in selected_minis)

        # Right: Status + Controls
        with Vertical(id="right-panel"):
            with Vertical(id="status-box"):
                yield Label("Status", classes="section-title")
                yield StatusPanel(id="status-display")

            with Vertical(id="controls-box"):
                yield Label("Controls", classes="section-title")
                yield ControlsPanel()

        # Bottom: Activity Log
        with Vertical(id="log-panel"):
            yield Label("Activity Log", classes="section-title")
            yield RichLog(id="activity-log", highlight=True, markup=True)

        yield Footer()

    def on_mount(self) -> None:
        self._log("Ragnarok X Bot ready. Press S to start boss farming, G for garden.")

        layout = load_layout()
        if not layout:
            self._log("[yellow]Warning: No calibration found. Run garden calibration first.[/]")
        elif "boss" not in layout:
            self._log("[yellow]Warning: Boss calibration not found. Press C to calibrate.[/]")
        else:
            self._log("Boss calibration loaded.")

        win = find_game_window()
        if win:
            self._log(f"Game window found: ({win['x']}, {win['y']}) {win['w']}x{win['h']}")
        else:
            self._log("[red]Game window not detected. Make sure the game is running.[/]")

        self.set_interval(1.0, self._update_status)

    # ─── Boss checkbox changes ───

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Save boss selection when checkboxes change."""
        if not isinstance(event.checkbox, BossCheckbox):
            return

        selected_mvps = []
        selected_minis = []

        for cb in self.query(BossCheckbox):
            if cb.value:
                if cb.is_mvp:
                    selected_mvps.append(cb.boss_name)
                else:
                    selected_minis.append(cb.boss_name)

        save_boss_config({
            "selected_mvps": selected_mvps,
            "selected_minis": selected_minis,
            "timers": self._boss_bot.boss_timers,
        })

        self._boss_bot.update_selection(selected_mvps, selected_minis)
        self._log(f"Selection: {len(selected_mvps)} MVPs, {len(selected_minis)} Minis")

    # ─── Actions ───

    def action_toggle_boss(self) -> None:
        """Start or stop boss farming."""
        if self._boss_running:
            self._boss_bot.stop()
            self._boss_running = False
            self._log("[red]Boss farming stopped.[/]")
        else:
            self._boss_bot.start()
            self._boss_running = self._boss_bot.running
            if self._boss_running:
                self._log("[green]Boss farming started![/]")

    def action_toggle_garden(self) -> None:
        """Toggle garden bot."""
        if self._garden_running:
            self._garden_bot.stop()
            self._garden_running = False
            self._log("[red]Garden bot stopped.[/]")
        else:
            self._garden_bot.start()
            self._garden_running = self._garden_bot.running
            if self._garden_running:
                self._log("[green]Garden bot started![/]")

    def action_calibrate(self) -> None:
        """Suspend TUI and run interactive boss calibration."""
        self._log("Suspending TUI for boss calibration...")

        # Use app.suspend() to give the terminal back for interactive input()
        with self.suspend():
            print()
            print("=" * 50)
            print("  Boss Calibration")
            print("  TUI is suspended. Complete calibration below.")
            print("=" * 50)
            print()
            try:
                calibrate_boss()
                print()
                print("Calibration complete! Returning to TUI...")
                time.sleep(1)
            except KeyboardInterrupt:
                print()
                print("Calibration cancelled. Returning to TUI...")
                time.sleep(1)
            except Exception as e:
                print()
                print(f"Calibration error: {e}")
                print("Returning to TUI...")
                time.sleep(2)

        # Back in TUI - refresh state
        self._log("[green]Boss calibration complete! Positions saved.[/]")

    def action_quit_app(self) -> None:
        """Clean shutdown."""
        if self._boss_running:
            self._boss_bot.stop()
        if self._garden_running:
            self._garden_bot.stop()
        self.exit()

    # ─── Logging ───

    def _log(self, message: str) -> None:
        """Add a timestamped message to the activity log."""
        ts = time.strftime("%H:%M:%S")
        try:
            log_widget = self.query_one("#activity-log", RichLog)
            log_widget.write(f"[dim]{ts}[/] {message}")
        except Exception:
            pass

    def _bot_log(self, message: str) -> None:
        """Log callback that works from both the main thread and background threads."""
        try:
            self.call_from_thread(self._log, message)
        except RuntimeError:
            # Already on the main/app thread — call directly
            self._log(message)

    # ─── Status Updates ───

    def _update_status(self) -> None:
        """Update the status panel with current bot state."""
        try:
            status = self.query_one("#status-display", StatusPanel)
        except Exception:
            return

        # Boss bot status
        if self._boss_running and self._boss_bot.running:
            status.bot_state = f"[green]{self._boss_bot.status_text}[/]"
        elif self._boss_running:
            status.bot_state = "[red]Error[/]"
            self._boss_running = False
        else:
            status.bot_state = "[dim]Stopped[/]"

        status.target_boss = self._boss_bot.target_text
        status.death_count = self._boss_bot.deaths
        status.channel = self._boss_bot.current_channel

        # Garden bot status
        if self._garden_running and self._garden_bot.running:
            status.garden_status = f"[green]Running[/] (cycle {self._garden_bot.cycle})"
        elif self._garden_running:
            status.garden_status = "[red]Error[/]"
            self._garden_running = False
        else:
            status.garden_status = "[dim]Stopped[/]"

        status.garden_interval = self._garden_bot.interval
        status.captchas_solved = self._garden_bot.captchas_solved

        # Update boss timer tooltips
        for cb in self.query(BossCheckbox):
            timer_text = self._boss_bot.get_boss_status(cb.boss_name)
            cb.tooltip = timer_text


# ═══════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════

def main():
    app = RagnarokBotApp()
    app.run()


if __name__ == "__main__":
    main()

"""Journal Listener and Temporal Correlator.

Watches application journal and command log streams (e.g. vivado.jou, novas.cmd),
correlating physical GUI action execution spans with underlying echoed text commands.
"""

import logging
import os
import time

from dobby.types import CorrelatedStep

logger = logging.getLogger(__name__)


class JournalCorrelator:
    """Correlates GUI actions with underlying application journal/command file byte offsets."""

    NOISE_PREFIXES = (
        "#",
        "gui_",
        "wvZoom",
        "select_area",
        "get_gui_resources",
    )

    def __init__(self, journal_path: str | None = None) -> None:
        self.journal_path: str | None = None
        self._fd = None
        self._mark_offset: int = 0
        self.is_recording: bool = False
        self.recorded_steps: list[CorrelatedStep] = []

        if journal_path:
            self.set_journal_path(journal_path)

    def set_journal_path(self, path: str) -> bool:
        """Sets the active journal file path and opens the stream."""
        self.close()
        self.journal_path = os.path.expanduser(path)

        if os.path.exists(self.journal_path):
            try:
                self._fd = open(self.journal_path, encoding="utf-8", errors="replace")
                self._fd.seek(0, os.SEEK_END)
                self._mark_offset = self._fd.tell()
                logger.info(
                    f"JournalCorrelator attached to '{self.journal_path}' at byte offset {self._mark_offset}"
                )
                return True
            except Exception as e:
                logger.warning(f"Failed to open journal file '{self.journal_path}': {e}")
                self._fd = None
                return False
        else:
            logger.info(
                f"Journal file '{self.journal_path}' does not exist yet; will attach upon creation"
            )
            return False

    def start_recording(self) -> None:
        """Starts capturing a multi-step GUI trajectory."""
        self.is_recording = True
        self.recorded_steps.clear()
        self.mark_pre_action()

    def stop_recording(self) -> list[CorrelatedStep]:
        """Stops recording and returns all captured correlated steps."""
        self.is_recording = False
        return list(self.recorded_steps)

    def clear_recorded_steps(self) -> None:
        """Clears accumulated trajectory history."""
        self.recorded_steps.clear()

    def mark_pre_action(self) -> int:
        """Records the current end-of-file byte offset immediately prior to physical action injection."""
        if not self._fd and self.journal_path and os.path.exists(self.journal_path):
            try:
                self._fd = open(self.journal_path, encoding="utf-8", errors="replace")
            except Exception:
                self._fd = None

        if self._fd:
            try:
                self._fd.seek(0, os.SEEK_END)
                self._mark_offset = self._fd.tell()
            except Exception:
                self._mark_offset = 0
        return self._mark_offset

    def harvest_post_quiescence(
        self,
        action_id: str,
        gui_intent: str,
        ui_target_text: str = "",
        t_start: float | None = None,
    ) -> CorrelatedStep:
        """Harvests new commands written to the journal file since the last pre-action mark.

        Filters out comments, empty lines, and pure viewport zoom commands.
        """
        now = time.monotonic()
        t_start_val = t_start if t_start is not None else now
        valid_lines: list[str] = []

        if self._fd:
            try:
                self._fd.seek(self._mark_offset, os.SEEK_SET)
                raw_chunk = self._fd.read()
                self._mark_offset = self._fd.tell()

                for line in raw_chunk.splitlines():
                    cleaned = line.strip()
                    if not cleaned:
                        continue
                    if cleaned.startswith(self.NOISE_PREFIXES):
                        continue
                    valid_lines.append(cleaned)
            except Exception as e:
                logger.debug(f"Error harvesting journal lines: {e}")

        step = CorrelatedStep(
            action_id=action_id,
            gui_intent=gui_intent,
            ui_target_text=ui_target_text,
            timestamp_start=t_start_val,
            timestamp_end=now,
            journal_lines=valid_lines,
        )

        if self.is_recording:
            self.recorded_steps.append(step)

        return step

    def close(self) -> None:
        """Closes file descriptor."""
        if self._fd:
            try:
                self._fd.close()
            except Exception:
                pass
            self._fd = None

    def __del__(self) -> None:
        self.close()

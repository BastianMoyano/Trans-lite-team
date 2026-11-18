"""Astro's state machine: what the mascot shows, driven only by real application events.

It holds no timers and draws nothing, so recording never depends on an animation: the app reports
events (recording started, capture finished...) and the mascot asks for `state` and `message`
whenever it paints. Short reactions (capture, success, error) expire after a few seconds and the
mascot falls back to what is really going on (recording, processing or idle).
"""
from __future__ import annotations

from enum import Enum
import time
from typing import Callable


class AstroState(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    RECORDING = "recording"
    STOPPING = "stopping"
    CAPTURING = "capturing"
    PROCESSING = "processing"
    SUCCESS = "success"
    ERROR = "error"


MESSAGES = {
    AstroState.IDLE: "Listo para comenzar",
    AstroState.MOVING: "",
    AstroState.RECORDING: "Grabando reunión",
    AstroState.STOPPING: "Deteniendo la grabación…",
    AstroState.CAPTURING: "Captura realizada",
    AstroState.PROCESSING: "Procesando grabación",
    AstroState.SUCCESS: "¡Reunión guardada!",
}
REACTION_SECONDS = {AstroState.CAPTURING: 2.0, AstroState.SUCCESS: 3.5, AstroState.ERROR: 6.0}
GENERIC_ERROR = "Algo salió mal."


class AstroMachine:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, on_change: Callable[[], None] | None = None):
        self._clock = clock
        self._on_change = on_change
        self._recording = False
        self._stopping = False
        self._processing = False
        self._progress: float | None = None
        self._moving = False
        self._reaction: AstroState | None = None
        self._reaction_until = 0.0
        self._error_message = ""

    # What is really going on, without the short reactions on top.
    def _base(self) -> AstroState:
        if self._recording:
            return AstroState.RECORDING
        if self._stopping:
            return AstroState.STOPPING
        if self._processing:
            return AstroState.PROCESSING
        return AstroState.IDLE

    def _active_reaction(self) -> AstroState | None:
        if self._reaction is not None and self._clock() >= self._reaction_until:
            self._reaction = None
        return self._reaction

    @property
    def state(self) -> AstroState:
        if self._moving:
            return AstroState.MOVING
        return self._active_reaction() or self._base()

    @property
    def message(self) -> str:
        state = self.state
        if state is AstroState.ERROR:
            return self._error_message
        if state is AstroState.PROCESSING and self._progress is not None:
            return f"{MESSAGES[state]} · {round(self._progress * 100)} %"
        return MESSAGES[state]

    def _react(self, state: AstroState) -> None:
        self._reaction = state
        self._reaction_until = self._clock() + REACTION_SECONDS[state]

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    # Events reported by the application.
    def recording_started(self) -> None:
        self._recording, self._stopping = True, False
        self._reaction = None
        self._changed()

    def stop_requested(self) -> None:
        self._recording, self._stopping = False, True
        self._changed()

    def recording_stopped(self, *, processing: bool) -> None:
        """The audio is finalized and stored; transcription may follow."""
        self._stopping = False
        if processing:
            self._processing, self._progress = True, None
        else:
            self._react(AstroState.SUCCESS)
        self._changed()

    def processing_progress(self, fraction: float) -> None:
        self._processing, self._progress = True, max(0.0, min(1.0, fraction))
        self._changed()

    def processing_finished(self, *, error: str | None = None) -> None:
        self._processing, self._progress = False, None
        if error is None:
            self._react(AstroState.SUCCESS)
        else:
            self._error(error)
        self._changed()

    def capture_finished(self, ok: bool, message: str | None = None) -> None:
        if ok:
            self._react(AstroState.CAPTURING)
        else:
            self._error(message)
        self._changed()

    def error(self, message: str | None) -> None:
        self._error(message)
        self._changed()

    def _error(self, message: str | None) -> None:
        self._error_message = (message or "").strip() or GENERIC_ERROR
        self._react(AstroState.ERROR)

    def move_started(self) -> None:
        self._moving = True
        self._changed()

    def move_finished(self) -> None:
        self._moving = False
        self._changed()

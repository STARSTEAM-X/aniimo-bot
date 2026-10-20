"""Deterministic lock input -> configured delay -> one shot cycle."""

from dataclasses import dataclass
from enum import Enum


class CyclePhase(str, Enum):
    NEED_LOCK = "need_lock"
    WAIT_DELAY = "wait_delay"
    READY_TO_SHOOT = "ready_to_shoot"
    COOLDOWN = "cooldown"


@dataclass
class CaptureCycle:
    phase: CyclePhase = CyclePhase.NEED_LOCK
    completed_cycles: int = 0

    def reset(self):
        self.phase = CyclePhase.NEED_LOCK

    def lock_sent(self):
        if self.phase == CyclePhase.COOLDOWN:
            self.phase = CyclePhase.NEED_LOCK
        if self.phase != CyclePhase.NEED_LOCK:
            raise RuntimeError(f"cannot send lock input during {self.phase.value}")
        self.phase = CyclePhase.WAIT_DELAY

    def delay_elapsed(self):
        if self.phase != CyclePhase.WAIT_DELAY:
            raise RuntimeError(f"cannot finish delay during {self.phase.value}")
        self.phase = CyclePhase.READY_TO_SHOOT

    def shot_sent(self):
        if self.phase != CyclePhase.READY_TO_SHOOT:
            raise RuntimeError(f"cannot shoot during {self.phase.value}")
        self.completed_cycles += 1
        self.phase = CyclePhase.COOLDOWN

"""GUI-to-Script Skill Crystallization Engine.

Translates slow, high-token physical GUI demonstrations into deterministic,
sub-millisecond parameterized Tcl/CLI scripts that execute with 0 visual tokens.
"""

from pinlet.crystallization.compiler import TclTrajectoryCompiler
from pinlet.crystallization.correlator import JournalCorrelator
from pinlet.crystallization.registry import DynamicSkillRegistry
from pinlet.crystallization.trace_compiler import TraceCompiler, replay_macro
from pinlet.types import (
    CorrelatedStep,
    CrystallizedMacro,
    CrystallizedSkillSpec,
    SkillExecutionResult,
)

__all__ = [
    "CorrelatedStep",
    "CrystallizedMacro",
    "CrystallizedSkillSpec",
    "DynamicSkillRegistry",
    "JournalCorrelator",
    "SkillExecutionResult",
    "TclTrajectoryCompiler",
    "TraceCompiler",
    "replay_macro",
]

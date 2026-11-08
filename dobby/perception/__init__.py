"""Perception and visual/semantic grounding components."""

from dobby.perception.atspi import ATSPIInspector
from dobby.perception.capture import CapturePipeline
from dobby.perception.cv_detector import CVDetector
from dobby.perception.som import SetOfMarksGenerator

__all__ = ["ATSPIInspector", "CVDetector", "CapturePipeline", "SetOfMarksGenerator"]

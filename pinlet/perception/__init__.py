"""Perception and visual/semantic grounding components."""

from pinlet.perception.atspi import ATSPIInspector
from pinlet.perception.capture import CapturePipeline
from pinlet.perception.cv_detector import CVDetector
from pinlet.perception.som import SetOfMarksGenerator

__all__ = ["ATSPIInspector", "CVDetector", "CapturePipeline", "SetOfMarksGenerator"]

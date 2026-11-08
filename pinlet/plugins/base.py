"""Base abstractions for UI Application Plugins in Vision MCP."""

from abc import ABC, abstractmethod
from typing import Any

from pinlet.types import (
    BoundingBox,
    FeatureDescriptor,
    PluginSummary,
    SubPaneLayoutSpec,
    SubPaneROI,
)


class BaseUIAppPlugin(ABC):
    """Abstract Base Class for high-feature application plugins.

    Enables specialized sub-pane routing, declarative feature catalogs, coordinate-free hotkey
    dispatching, and workflow macros.
    """

    app_id: str = "base"
    name: str = "Base Application Plugin"
    description: str = "Base plugin specification"

    def __init__(self) -> None:
        self.sub_panes: dict[str, SubPaneROI] = {}
        self.subpane_layouts: dict[str, SubPaneLayoutSpec] = {}
        self.feature_map: dict[str, FeatureDescriptor] = {}
        self._register_panes()
        self._register_features()

    @abstractmethod
    def _register_panes(self) -> None:
        """Subclasses define available functional sub-panes."""

    @abstractmethod
    def _register_features(self) -> None:
        """Subclasses declare available features, hotkeys, and menus."""

    @abstractmethod
    def detect(self, window_title: str, app_name: str | None = None) -> bool:
        """Determines whether a desktop window corresponds to this application plugin.

        Args:
            window_title: Window title text from window manager.
            app_name: Process or WM_CLASS name if available.
        """

    def get_sub_pane(self, pane_name: str, window_bbox: BoundingBox) -> BoundingBox | None:
        """Resolves the absolute physical screen bounding box for a named sub-pane.

        Args:
            pane_name: Identifier of the requested pane (e.g. 'address_bar', 'waveform_canvas').
            window_bbox: Physical bounding box of the host application window.

        Returns:
            Computed BoundingBox or None if pane not recognized.
        """
        pane = self.sub_panes.get(pane_name.lower())
        if not pane:
            return None
        return pane.compute_bounds(window_bbox)

    def get_subpane_layout_spec(self, pane_name: str) -> SubPaneLayoutSpec | None:
        """Returns dynamic anchor-relative layout spec for sub-pane if defined.

        Args:
            pane_name: Identifier of requested sub-pane.
        """
        return self.subpane_layouts.get(pane_name.lower())

    def list_features(self) -> list[FeatureDescriptor]:
        """Returns all declarative features supported by this plugin."""
        return list(self.feature_map.values())

    def get_feature(self, feature_id: str) -> FeatureDescriptor | None:
        """Looks up a feature descriptor by feature_id."""
        return self.feature_map.get(feature_id)

    def summarize(self, is_active: bool = False) -> PluginSummary:
        """Returns a metadata summary of this plugin."""
        return PluginSummary(
            app_id=self.app_id,
            name=self.name,
            description=self.description,
            is_active=is_active,
            supported_panes=list(self.sub_panes.keys()),
            features_count=len(self.feature_map),
        )

    @abstractmethod
    def execute_feature(
        self,
        feature_id: str,
        controller: Any,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Executes a named feature with closed-loop verification.

        Args:
            feature_id: Unique feature identifier.
            controller: FeedbackLoopController instance for input injection and observation.
            args: Optional argument payload for parameterized features.

        Returns:
            Dictionary with execution status, delta verification, and messages.
        """

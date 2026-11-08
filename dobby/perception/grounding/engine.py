"""Grounding Engine Coordinator providing hotswappable perception strategies."""

from PIL import Image

from dobby.config import CONFIG
from dobby.perception.grounding.base import BaseGroundingProvider
from dobby.perception.grounding.cv import CVGroundingProvider
from dobby.perception.grounding.ensemble import EnsembleGroundingProvider
from dobby.perception.grounding.magnitude import ground_magnitude_icon
from dobby.perception.grounding.semantic import SemanticGroundingProvider
from dobby.perception.grounding.template import TemplateGroundingProvider
from dobby.types import RelationalAnchor, UIElement


class GroundingEngine:
    """Coordinates and hotswaps perception grounding providers at runtime.
    Supports 'ensemble', 'semantic', 'cv', and 'template' modes.
    """

    def __init__(self, default_strategy: str = CONFIG.grounding_strategy) -> None:
        self._semantic = SemanticGroundingProvider()
        self._cv = CVGroundingProvider()
        self._template = TemplateGroundingProvider()
        self._ensemble = EnsembleGroundingProvider(
            semantic_provider=self._semantic,
            cv_provider=self._cv,
            template_provider=self._template,
        )

        self._providers: dict[str, BaseGroundingProvider] = {
            "semantic": self._semantic,
            "cv": self._cv,
            "template": self._template,
            "ensemble": self._ensemble,
        }

        self.active_strategy = (
            default_strategy if default_strategy in self._providers else "ensemble"
        )

    @property
    def current_provider(self) -> BaseGroundingProvider:
        """Returns the currently active grounding provider."""
        return self._providers.get(self.active_strategy, self._ensemble)

    def set_strategy(self, strategy: str) -> bool:
        """Hotswaps the active grounding strategy.

        Args:
            strategy: 'ensemble', 'semantic', 'cv', or 'template'.

        Returns:
            True if strategy was successfully switched, False otherwise.
        """
        low = strategy.lower().strip()
        if low in self._providers:
            self.active_strategy = low
            CONFIG.grounding_strategy = low
            return True
        return False

    def list_strategies(self) -> list[str]:
        """Returns list of registered grounding strategy names."""
        return list(self._providers.keys())

    def get_provider(self, name: str) -> BaseGroundingProvider | None:
        """Gets a specific provider by name."""
        return self._providers.get(name.lower().strip())

    def detect_elements(
        self,
        image: Image.Image,
        scale_factor: float = 1.0,
        strategy_override: str | None = None,
        app_filter: str | None = None,
        region: tuple[int, int, int, int] | None = None,
    ) -> list[UIElement]:
        """Runs detection using the active or overridden grounding provider."""
        provider = (
            self.get_provider(strategy_override) if strategy_override else self.current_provider
        )
        if not provider:
            provider = self._ensemble
        return provider.detect(
            image=image,
            scale_factor=scale_factor,
            app_filter=app_filter,
            region=region,
        )

    def find_element(
        self,
        query: str,
        image: Image.Image | None = None,
        scale_factor: float = 1.0,
        fuzzy: bool = True,
        strategy_override: str | None = None,
    ) -> list[UIElement]:
        """Searches for elements matching query text across the chosen grounding backend."""
        provider = (
            self.get_provider(strategy_override) if strategy_override else self.current_provider
        )
        if not provider:
            provider = self._ensemble
        return provider.find_by_text(
            query=query,
            image=image,
            scale_factor=scale_factor,
            fuzzy=fuzzy,
        )

    def ground_icon(
        self,
        image: Image.Image,
        template: Image.Image,
        anchor: RelationalAnchor | None = None,
        ocr_lines: list | None = None,
        scale_factor: float = 1.0,
        cache: object | None = None,
        app_key: str = "desktop",
    ) -> UIElement | None:
        """Magnitude match, optionally inside an OCR offset window, with cache write-back."""
        element = ground_magnitude_icon(
            image=image,
            template=template,
            anchor=anchor,
            ocr_lines=ocr_lines,
            scale_factor=scale_factor,
        )
        if (
            element is not None
            and anchor is not None
            and cache is not None
            and hasattr(cache, "remember_anchor")
        ):
            cache.remember_anchor(app_key, anchor.label, anchor)
        return element

"""Plugin Registry for managing UI Application Plugins in Vision MCP."""

from dobby.plugins.base import BaseUIAppPlugin
from dobby.plugins.chrome import ChromeBrowserPlugin
from dobby.plugins.vivado import VivadoPlugin
from dobby.types import PluginSummary


class PluginRegistry:
    """Central registry for discovering, registering, and routing actions
    to high-feature application plugins.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, BaseUIAppPlugin] = {}
        self._register_default_plugins()

    def _register_default_plugins(self) -> None:
        """Registers standard out-of-the-box application plugins."""
        self.register(ChromeBrowserPlugin())
        self.register(VivadoPlugin())

    def register(self, plugin: BaseUIAppPlugin) -> None:
        """Registers a new application plugin."""
        self._plugins[plugin.app_id.lower()] = plugin

    def get(self, app_id: str) -> BaseUIAppPlugin | None:
        """Retrieves a registered plugin by app_id."""
        return self._plugins.get(app_id.lower())

    def detect(self, window_title: str, app_name: str | None = None) -> BaseUIAppPlugin | None:
        """Matches an application plugin against window title or process name.

        Args:
            window_title: Title bar string of desktop window.
            app_name: Optional process binary name.

        Returns:
            Matching BaseUIAppPlugin instance or None.
        """
        for plugin in self._plugins.values():
            if plugin.detect(window_title, app_name):
                return plugin
        return None

    def list_all(self, active_window_title: str | None = None) -> list[PluginSummary]:
        """Returns summaries of all registered plugins, marking which is currently active."""
        summaries = []
        for plugin in self._plugins.values():
            is_active = False
            if active_window_title and plugin.detect(active_window_title):
                is_active = True
            summaries.append(plugin.summarize(is_active=is_active))
        return summaries


# Default global registry singleton
PLUGIN_REGISTRY = PluginRegistry()

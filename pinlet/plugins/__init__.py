"""UI Application Plugins Package for Vision MCP."""

from pinlet.plugins.base import BaseUIAppPlugin
from pinlet.plugins.chrome import ChromeBrowserPlugin
from pinlet.plugins.registry import PluginRegistry

# Default global registry singleton
PLUGIN_REGISTRY = PluginRegistry()

__all__ = [
    "PLUGIN_REGISTRY",
    "BaseUIAppPlugin",
    "ChromeBrowserPlugin",
    "PluginRegistry",
]

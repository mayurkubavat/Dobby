"""Pinlet's colours, shared by the top-bar view and its icons."""

from __future__ import annotations

# Palette "Frost" (chosen 2026-11-08, replacing "Elf Violet, tuned").
PALETTE: dict[str, str] = {
    "accent": "#0EA5C6",  # glow and icon on a light desktop
    "accent_dark": "#67E8F9",  # the same on a dark desktop
    "fill": "#0E7490",  # filled buttons and tags, white text
    "on_fill": "#FFFFFF",
    "ok": "#22A365",
    "caution": "#F5A524",  # paused, needs you
    "danger": "#F04438",  # stopped
    "idle": "#98A1AC",  # sleeping / paused grey
    "surface_light": "#F7FAFC",
    "surface_dark": "#141A22",
    "text_secondary": "#8A94A0",
}

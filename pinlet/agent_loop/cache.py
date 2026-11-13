"""Self-Healing Action & Locator Cache for Zero-Token Replay.

Inspired by Stagehand's locator caching and enterprise RPA multi-anchoring.
Caches element bounding boxes, roles, and centroids so repeat interactions
execute in <10ms with 0 multimodal LLM tokens.

Features:
- Dynamic window movement re-projection (compensates for window drag and resize)
- Pre-flight visual dHash verification (prevents blind clicks on drifted/closed views)
- Localized neighborhood template auto-reanchoring (drift recovery without re-grounding)
- Persistent JSON storage with compact base64 visual templates
- Comprehensive cache telemetry and proactive verification
"""

import base64
import io
import json
import logging
import os
import time
from typing import Any

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from pinlet.imagehash import compute_dhash, hamming_distance
from pinlet.types import BoundingBox, WindowInfo

logger = logging.getLogger(__name__)


def image_to_base64_thumbnail(image: Image.Image, max_dim: int = 256) -> str:
    """Downscales if exceeding max_dim and encodes an element ROI as a compact base64 PNG string."""
    try:
        w, h = image.size
        scale = min(max_dim / max(1, w), max_dim / max(1, h), 1.0)
        if scale < 1.0:
            thumb = image.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.BILINEAR
            )
        else:
            thumb = image.copy()
        buf = io.BytesIO()
        thumb.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        logger.debug(f"Error encoding thumbnail: {e}")
        return ""


def base64_to_image(b64_str: str) -> Image.Image | None:
    """Decodes a base64 encoded PNG string into a PIL Image."""
    if not b64_str:
        return None
    try:
        raw_bytes = base64.b64decode(b64_str)
        return Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception as e:
        logger.debug(f"Error decoding thumbnail: {e}")
        return None


def template_match_neighborhood(
    full_image: Image.Image,
    template_img: Image.Image,
    expected_bbox: BoundingBox,
    search_radius: int = 60,
    min_score: float = 0.78,
) -> tuple[BoundingBox, tuple[int, int], float] | None:
    """Searches within a localized bounding neighborhood around expected_bbox using normalized cross-correlation.

    Returns:
        (new_bbox, new_center, match_score) if a match meeting min_score is found, else None.
    """
    try:
        import cv2

        full_w, full_h = full_image.size
        tpl_w, tpl_h = template_img.size

        # Define search neighborhood
        sx = max(0, expected_bbox.x - search_radius)
        sy = max(0, expected_bbox.y - search_radius)
        ex = min(full_w, expected_bbox.right + search_radius)
        ey = min(full_h, expected_bbox.bottom + search_radius)

        search_w = ex - sx
        search_h = ey - sy

        # If search area is smaller than template, cannot match
        if search_w < tpl_w or search_h < tpl_h:
            return None

        search_crop = full_image.crop((sx, sy, ex, ey))
        search_arr = cv2.cvtColor(np.array(search_crop), cv2.COLOR_RGB2BGR)
        tpl_arr = cv2.cvtColor(np.array(template_img), cv2.COLOR_RGB2BGR)

        res = cv2.matchTemplate(search_arr, tpl_arr, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)

        if max_val >= min_score:
            matched_x = sx + max_loc[0]
            matched_y = sy + max_loc[1]
            new_bbox = BoundingBox(
                x=matched_x,
                y=matched_y,
                width=expected_bbox.width,
                height=expected_bbox.height,
            )
            return new_bbox, new_bbox.center, float(max_val)

    except Exception as e:
        logger.debug(f"Error in template_match_neighborhood: {e}")

    return None


class CachedLocator(BaseModel):
    """Cached element signature and coordinates with dynamic self-healing telemetry."""

    app_key: str = Field(
        description="Application or window identifier (e.g. 'chrome', 'gedit', 'vivado')"
    )
    query: str = Field(description="Target search query or label")
    bbox: BoundingBox = Field(description="Physical desktop bounding box")
    center_coords: tuple[int, int] = Field(description="Physical (x, y) coordinates for clicking")
    role: str = Field(default="unknown", description="Semantic role (button, text_entry, etc.)")
    hit_count: int = Field(default=1, description="Number of successful replayed clicks")
    heal_count: int = Field(
        default=0, description="Number of times auto-reanchored without re-grounding"
    )
    created_at: float = Field(default_factory=time.time)
    last_verified_at: float = Field(default_factory=time.time)

    # Dynamic Self-Healing Geometry
    window_title: str | None = Field(default=None, description="Title of host window when recorded")
    window_bbox: BoundingBox | None = Field(
        default=None, description="Host window bounding box when recorded"
    )
    window_offset: tuple[int, int] | None = Field(
        default=None, description="(offset_x, offset_y) from host window top-left"
    )
    window_relative_bbox: tuple[float, float, float, float] | None = Field(
        default=None, description="(rel_x, rel_y, rel_w, rel_h) fractional coordinates"
    )
    subpane_id: str | None = Field(default=None, description="Sub-pane ID if localized")
    vdom_selector: str | None = Field(default=None, description="vDOM CSS spatial selector")

    # Perceptual Signatures
    visual_dhash: int | None = Field(default=None, description="64-bit dHash of element crop")
    template_b64: str | None = Field(default=None, description="Base64 encoded thumbnail template")
    last_drift: tuple[int, int] | None = Field(
        default=None, description="Last recorded (dx, dy) healing drift"
    )


class ActionCache:
    """Manages persistent storage and self-healing lifecycle for element locators.

    Supports window-relative coordinate re-projection, pre-flight visual verification, and
    localized neighborhood template auto-reanchoring.
    """

    def __init__(self, cache_file_path: str | None = None) -> None:
        if cache_file_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_dir = os.path.join(base_dir, ".cache")
            os.makedirs(cache_dir, exist_ok=True)
            self.cache_file = os.path.join(cache_dir, "locator_cache.json")
        else:
            self.cache_file = cache_file_path
            os.makedirs(os.path.dirname(os.path.abspath(self.cache_file)), exist_ok=True)

        self._cache: dict[str, CachedLocator] = {}
        self.relational_anchors: dict[str, Any] = {}
        self._load()

    def remember_anchor(self, app_key: str, query: str, anchor: Any) -> None:
        """Writes a relational OCR anchor after a Tier 2 or Tier 3 hit."""
        self.relational_anchors[self._make_key(app_key, query)] = anchor

    def _make_key(self, app_key: str, query: str) -> str:
        """Generates normalized lookup key."""
        return f"{app_key.lower().strip()}::{query.lower().strip()}"

    def _load(self) -> None:
        """Loads cached entries from disk if present."""
        if not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                raw_data = json.load(f)
            for k, v in raw_data.items():
                self._cache[k] = CachedLocator(**v)
        except Exception as e:
            logger.debug(f"Could not load action cache from '{self.cache_file}': {e}")
            self._cache = {}

    def _save(self) -> None:
        """Persists cache to disk."""
        try:
            raw_data = {k: v.model_dump() for k, v in self._cache.items()}
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(raw_data, f, indent=2)
        except Exception as e:
            logger.debug(f"Could not save action cache to '{self.cache_file}': {e}")

    def get(self, app_key: str, query: str) -> CachedLocator | None:
        """Retrieves cached locator if exists."""
        key = self._make_key(app_key, query)
        return self._cache.get(key)

    def record(
        self,
        app_key: str,
        query: str,
        bbox: BoundingBox,
        center_coords: tuple[int, int],
        role: str = "unknown",
        window_info: WindowInfo | None = None,
        visual_crop: Image.Image | None = None,
        subpane_id: str | None = None,
        vdom_selector: str | None = None,
    ) -> CachedLocator:
        """Records or updates a verified locator with geometry and perceptual fingerprints."""
        key = self._make_key(app_key, query)

        # Compute window relative offsets if window_info is provided
        win_offset = None
        win_rel = None
        win_title = None
        win_box = None

        if window_info is not None:
            win_title = window_info.title
            win_box = window_info.bbox
            win_offset = (bbox.x - win_box.x, bbox.y - win_box.y)
            if win_box.width > 0 and win_box.height > 0:
                win_rel = (
                    round((bbox.x - win_box.x) / win_box.width, 4),
                    round((bbox.y - win_box.y) / win_box.height, 4),
                    round(bbox.width / win_box.width, 4),
                    round(bbox.height / win_box.height, 4),
                )

        # Compute perceptual signatures if visual_crop is provided
        dhash = None
        tpl_b64 = None
        if visual_crop is not None:
            dhash = compute_dhash(visual_crop)
            tpl_b64 = image_to_base64_thumbnail(visual_crop)

        if key in self._cache:
            entry = self._cache[key]
            entry.bbox = bbox
            entry.center_coords = center_coords
            entry.role = role
            entry.hit_count += 1
            entry.last_verified_at = time.time()
            if win_offset is not None:
                entry.window_offset = win_offset
                entry.window_relative_bbox = win_rel
                entry.window_title = win_title
                entry.window_bbox = win_box
            if dhash is not None:
                entry.visual_dhash = dhash
                entry.template_b64 = tpl_b64
            if subpane_id is not None:
                entry.subpane_id = subpane_id
            if vdom_selector is not None:
                entry.vdom_selector = vdom_selector
        else:
            entry = CachedLocator(
                app_key=app_key.lower().strip(),
                query=query.lower().strip(),
                bbox=bbox,
                center_coords=center_coords,
                role=role,
                hit_count=1,
                window_title=win_title,
                window_bbox=win_box,
                window_offset=win_offset,
                window_relative_bbox=win_rel,
                subpane_id=subpane_id,
                vdom_selector=vdom_selector,
                visual_dhash=dhash,
                template_b64=tpl_b64,
            )
            self._cache[key] = entry

        self._save()
        return entry

    def resolve_locator(
        self,
        app_key: str,
        query: str,
        live_screen: Image.Image | None = None,
        current_window: WindowInfo | None = None,
        search_radius: int = 60,
        min_match_score: float = 0.78,
        dhash_threshold: int = 6,
        auto_heal: bool = True,
    ) -> tuple[str, CachedLocator | None]:
        """Resolves a cached locator against the current screen.

        Re-projects it into the window's current position, verifies it visually before use,
        and re-anchors it within a small neighbourhood if the element has moved.

        Returns:
            (status, locator) where status is:
            - 'VERIFIED': Visual dHash matches exactly at target coordinates.
            - 'WINDOW_REPROJECTED': Window moved; coordinates adjusted to new window position.
            - 'HEALED': Drift detected and auto-reanchored via localized template match.
            - 'CACHED': Returned cached entry (visual verification skipped or unassisted).
            - 'STALE': Verification failed and auto-reanchoring could not locate the element.
            - 'MISS': Element query not found in cache.
        """
        key = self._make_key(app_key, query)
        entry = self._cache.get(key)
        if entry is None:
            return "MISS", None

        # 1. Window Movement Re-projection
        candidate_bbox = entry.bbox
        candidate_center = entry.center_coords
        window_reprojected = False

        if (
            current_window is not None
            and entry.window_bbox is not None
            and entry.window_offset is not None
        ):
            # Check if window moved
            cur_wbox = current_window.bbox
            orig_wbox = entry.window_bbox

            if cur_wbox.x != orig_wbox.x or cur_wbox.y != orig_wbox.y:
                # Window moved: calculate new position from window offset
                new_x = cur_wbox.x + entry.window_offset[0]
                new_y = cur_wbox.y + entry.window_offset[1]
                candidate_bbox = BoundingBox(
                    x=new_x,
                    y=new_y,
                    width=entry.bbox.width,
                    height=entry.bbox.height,
                )
                candidate_center = candidate_bbox.center
                window_reprojected = True

        # 2. Visual Pre-Flight Verification
        if live_screen is not None and entry.visual_dhash is not None:
            # Crop candidate ROI from live screen
            cx, cy = candidate_bbox.x, candidate_bbox.y
            cw, ch = candidate_bbox.width, candidate_bbox.height
            full_w, full_h = live_screen.size

            if cx >= 0 and cy >= 0 and cx + cw <= full_w and cy + ch <= full_h:
                roi_crop = live_screen.crop((cx, cy, cx + cw, cy + ch))
                live_dhash = compute_dhash(roi_crop)
                dist = hamming_distance(live_dhash, entry.visual_dhash)

                if dist <= dhash_threshold:
                    # Verified match!
                    if window_reprojected:
                        # Update entry with new window position
                        self.record_heal(
                            app_key=app_key,
                            query=query,
                            new_bbox=candidate_bbox,
                            new_center=candidate_center,
                            drift=(
                                candidate_bbox.x - entry.bbox.x,
                                candidate_bbox.y - entry.bbox.y,
                            ),
                            window_info=current_window,
                        )
                        return "WINDOW_REPROJECTED", entry
                    return "VERIFIED", entry

            # 3. Drift Recovery via Localized Neighborhood Auto-Reanchoring
            if auto_heal and entry.template_b64:
                template_img = base64_to_image(entry.template_b64)
                if template_img is not None:
                    matched = template_match_neighborhood(
                        full_image=live_screen,
                        template_img=template_img,
                        expected_bbox=candidate_bbox,
                        search_radius=search_radius,
                        min_score=min_match_score,
                    )
                    if matched is not None:
                        healed_bbox, healed_center, match_score = matched
                        dx = healed_bbox.x - entry.bbox.x
                        dy = healed_bbox.y - entry.bbox.y
                        logger.info(
                            f"Auto-reanchored cached locator '{query}' (score={match_score:.2f}, drift dx={dx}, dy={dy})"
                        )
                        healed_entry = self.record_heal(
                            app_key=app_key,
                            query=query,
                            new_bbox=healed_bbox,
                            new_center=healed_center,
                            drift=(dx, dy),
                            window_info=current_window,
                            new_crop=live_screen.crop(
                                (
                                    healed_bbox.x,
                                    healed_bbox.y,
                                    healed_bbox.right,
                                    healed_bbox.bottom,
                                )
                            ),
                        )
                        return "HEALED", healed_entry

            # Visual check failed and neighborhood search did not match -> STALE
            return "STALE", entry

        # Fallback if live_screen was not provided
        if window_reprojected:
            entry.bbox = candidate_bbox
            entry.center_coords = candidate_center
            return "WINDOW_REPROJECTED", entry

        return "CACHED", entry

    def record_hit(self, app_key: str, query: str) -> None:
        """Increments hit count on successful cache replay."""
        key = self._make_key(app_key, query)
        if key in self._cache:
            self._cache[key].hit_count += 1
            self._cache[key].last_verified_at = time.time()
            self._save()

    def record_heal(
        self,
        app_key: str,
        query: str,
        new_bbox: BoundingBox,
        new_center: tuple[int, int],
        drift: tuple[int, int] = (0, 0),
        window_info: WindowInfo | None = None,
        new_crop: Image.Image | None = None,
    ) -> CachedLocator | None:
        """Updates locator with healed coordinates and drift telemetry."""
        key = self._make_key(app_key, query)
        if key in self._cache:
            entry = self._cache[key]
            entry.bbox = new_bbox
            entry.center_coords = new_center
            entry.heal_count += 1
            entry.last_drift = drift
            entry.last_verified_at = time.time()

            if window_info is not None:
                entry.window_title = window_info.title
                entry.window_bbox = window_info.bbox
                entry.window_offset = (
                    new_bbox.x - window_info.bbox.x,
                    new_bbox.y - window_info.bbox.y,
                )

            if new_crop is not None:
                entry.visual_dhash = compute_dhash(new_crop)
                entry.template_b64 = image_to_base64_thumbnail(new_crop)

            self._save()
            return entry
        return None

    def invalidate(self, app_key: str, query: str) -> bool:
        """Invalidates a broken cache entry when layout shift is detected."""
        key = self._make_key(app_key, query)
        if key in self._cache:
            del self._cache[key]
            self._save()
            return True
        return False

    def clear(self) -> None:
        """Clears all cached locators."""
        self._cache.clear()
        if os.path.exists(self.cache_file):
            try:
                os.remove(self.cache_file)
            except Exception:
                pass

    def proactive_verify_all(
        self,
        app_key: str,
        live_screen: Image.Image,
        current_window: WindowInfo | None = None,
    ) -> dict[str, Any]:
        """Proactively evaluates the health of all cached locators for an application."""
        verified = []
        healed = []
        stale = []

        norm_app = app_key.lower().strip()
        matching_entries = [e for e in self._cache.values() if e.app_key == norm_app]

        for entry in matching_entries:
            status, _ = self.resolve_locator(
                app_key=entry.app_key,
                query=entry.query,
                live_screen=live_screen,
                current_window=current_window,
                auto_heal=True,
            )
            if status in ("VERIFIED", "WINDOW_REPROJECTED"):
                verified.append(entry.query)
            elif status == "HEALED":
                healed.append(entry.query)
            elif status == "STALE":
                stale.append(entry.query)

        return {
            "app_key": app_key,
            "total_checked": len(matching_entries),
            "verified_count": len(verified),
            "healed_count": len(healed),
            "stale_count": len(stale),
            "verified": verified,
            "healed": healed,
            "stale": stale,
        }

    def get_stats(self) -> dict[str, Any]:
        """Returns comprehensive cache telemetry and health metrics."""
        total_hits = sum(e.hit_count for e in self._cache.values())
        total_heals = sum(e.heal_count for e in self._cache.values())
        total_ops = total_hits + total_heals
        healed_ratio = round(total_heals / total_ops, 3) if total_ops > 0 else 0.0

        entries_summary = {}
        for k, v in self._cache.items():
            entries_summary[k] = {
                "role": v.role,
                "hits": v.hit_count,
                "heals": v.heal_count,
                "last_drift": v.last_drift,
                "has_template": v.template_b64 is not None,
                "has_dhash": v.visual_dhash is not None,
                "window": v.window_title,
            }

        return {
            "entries_count": len(self._cache),
            "total_hits": total_hits,
            "total_heals": total_heals,
            "healed_ratio": healed_ratio,
            "cache_file": self.cache_file,
            "cached_keys": list(self._cache.keys()),
            "entries": entries_summary,
        }

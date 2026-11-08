"""Zero-Token Spatial & Semantic Query Engine for Virtual DOM (vDOM).

Allows the agent to address UI elements without visual context via CSS-like
selectors and topological spatial constraints (below, above, right_of, left_of, inside, near).
"""

import math
import re

from dobby.types import (
    BoundingBox,
    UIRole,
    VDOMNode,
    VirtualDOMSnapshot,
)


class VDOMQueryEngine:
    """Evaluates spatial and semantic queries against an in-memory VirtualDOMSnapshot with 0 visual tokens."""

    def __init__(self, snapshot: VirtualDOMSnapshot) -> None:
        self.snapshot = snapshot
        self.nodes = list(snapshot.nodes.values())

    def query_selector(self, selector: str, limit: int = 10) -> list[VDOMNode]:
        """Finds nodes that match a CSS-like spatial selector.

        Examples:
        - 'push_button[name*="Run Synthesis"]:enabled'
        - 'text_input:right_of("Period (ns):")'
        - 'waveform_signal[name="clk_div2"]:inside("Waveform")'
        - 'push_button:below("Summary"):enabled'
        - '#vdom_0142'
        """
        clean_sel = selector.strip()

        # Direct node ID lookup: '#vdom_1234'
        if clean_sel.startswith("#"):
            target_id = clean_sel[1:]
            node = self.snapshot.nodes.get(target_id)
            return [node] if node else []

        # Role extract (leading identifier, e.g. 'push_button' or ':sash_divider')
        role_match = re.match(r"^:?([a-z_]+)", clean_sel)
        role: UIRole | None = None
        if role_match:
            cand = role_match.group(1).lower()
            try:
                role = UIRole(cand)
            except ValueError:
                role = None

        # Name extract: [name*="..."] or [name="..."]
        name_match = re.search(r'\[name(\*?)=["\']([^"\']+)["\']\]', clean_sel)
        text_regex: str | None = None
        exact_name: bool = False
        if name_match:
            exact_name = name_match.group(1) != "*"
            val = name_match.group(2)
            text_regex = f"^{re.escape(val)}$" if exact_name else re.escape(val)

        # State extract: :enabled, :focused, :checked, :expanded
        state_match = re.search(
            r":(enabled|focused|checked|expanded|visible)\b", clean_sel, re.IGNORECASE
        )
        state = state_match.group(1).upper() if state_match else None

        # Spatial constraints
        below_match = re.search(r':below\(["\']([^"\']+)["\']\)', clean_sel)
        above_match = re.search(r':above\(["\']([^"\']+)["\']\)', clean_sel)
        right_match = re.search(r':right_of\(["\']([^"\']+)["\']\)', clean_sel)
        left_match = re.search(r':left_of\(["\']([^"\']+)["\']\)', clean_sel)
        inside_match = re.search(r':inside\(["\']([^"\']+)["\']\)', clean_sel)
        near_match = re.search(r':near\(["\']([^"\']+)["\'](?:,\s*(\d+))?\)', clean_sel)

        return self.find_elements(
            role=role,
            text_regex=text_regex,
            state=state,
            below_text=below_match.group(1) if below_match else None,
            above_text=above_match.group(1) if above_match else None,
            right_of_text=right_match.group(1) if right_match else None,
            left_of_text=left_match.group(1) if left_match else None,
            inside_text=inside_match.group(1) if inside_match else None,
            near_text=near_match.group(1) if near_match else None,
            near_max_dist=int(near_match.group(2)) if near_match and near_match.group(2) else 150,
            limit=limit,
        )

    def find_elements(
        self,
        role: UIRole | None = None,
        text_regex: str | None = None,
        state: str | None = None,
        below_text: str | None = None,
        above_text: str | None = None,
        right_of_text: str | None = None,
        left_of_text: str | None = None,
        inside_text: str | None = None,
        near_text: str | None = None,
        near_max_dist: int = 150,
        limit: int = 10,
    ) -> list[VDOMNode]:
        """Multi-constraint filtering and spatial sorting over vDOM nodes."""
        candidates = list(self.nodes)

        if role:
            candidates = [n for n in candidates if n.role == role]

        if state:
            candidates = [n for n in candidates if state.upper() in [s.upper() for s in n.states]]

        if text_regex:
            pat = re.compile(text_regex, re.IGNORECASE)
            candidates = [
                n for n in candidates if pat.search(n.name) or (n.value and pat.search(n.value))
            ]

        # Spatial: inside container
        if inside_text:
            anchor = self._find_best_anchor(inside_text)
            if anchor:
                candidates = [
                    n
                    for n in candidates
                    if anchor.bbox.containment_ratio(n.bbox) >= 0.70 and n.node_id != anchor.node_id
                ]

        # Spatial: below("anchor")
        if below_text:
            anchor = self._find_best_anchor(below_text)
            if anchor:
                anchor_bottom = anchor.bbox.bottom
                candidates = [
                    n
                    for n in candidates
                    if n.bbox.y >= (anchor_bottom - 4)
                    and self._horizontal_overlap(anchor.bbox, n.bbox) > 0
                ]
                candidates.sort(key=lambda n: n.bbox.y - anchor_bottom)

        # Spatial: above("anchor")
        if above_text:
            anchor = self._find_best_anchor(above_text)
            if anchor:
                anchor_top = anchor.bbox.y
                candidates = [
                    n
                    for n in candidates
                    if n.bbox.bottom <= (anchor_top + 4)
                    and self._horizontal_overlap(anchor.bbox, n.bbox) > 0
                ]
                candidates.sort(key=lambda n: anchor_top - n.bbox.bottom)

        # Spatial: right_of("anchor")
        if right_of_text:
            anchor = self._find_best_anchor(right_of_text)
            if anchor:
                anchor_right = anchor.bbox.right
                candidates = [
                    n
                    for n in candidates
                    if n.bbox.x >= (anchor_right - 4)
                    and self._vertical_overlap(anchor.bbox, n.bbox) > 0
                ]
                candidates.sort(key=lambda n: n.bbox.x - anchor_right)

        # Spatial: left_of("anchor")
        if left_of_text:
            anchor = self._find_best_anchor(left_of_text)
            if anchor:
                anchor_left = anchor.bbox.x
                candidates = [
                    n
                    for n in candidates
                    if n.bbox.right <= (anchor_left + 4)
                    and self._vertical_overlap(anchor.bbox, n.bbox) > 0
                ]
                candidates.sort(key=lambda n: anchor_left - n.bbox.right)

        # Spatial: near("anchor", max_dist)
        if near_text:
            anchor = self._find_best_anchor(near_text)
            if anchor:
                ax, ay = anchor.click_point
                filtered = []
                for n in candidates:
                    if n.node_id == anchor.node_id:
                        continue
                    nx, ny = n.click_point
                    dist = math.hypot(nx - ax, ny - ay)
                    if dist <= near_max_dist:
                        filtered.append((dist, n))
                filtered.sort(key=lambda item: item[0])
                candidates = [item[1] for item in filtered]

        return candidates[:limit]

    def _find_best_anchor(self, text: str) -> VDOMNode | None:
        """Finds closest node matching text anchor for relative spatial queries."""
        # Check node_id first
        if text in self.snapshot.nodes:
            return self.snapshot.nodes[text]

        # Case-insensitive substring match
        pat = re.compile(re.escape(text), re.IGNORECASE)
        matches = [n for n in self.nodes if pat.search(n.name)]
        if matches:
            # Sort by shortest name (exact matches prioritized)
            matches.sort(key=lambda n: len(n.name))
            return matches[0]
        return None

    @staticmethod
    def _horizontal_overlap(a: BoundingBox, b: BoundingBox) -> int:
        return max(0, min(a.right, b.right) - max(a.x, b.x))

    @staticmethod
    def _vertical_overlap(a: BoundingBox, b: BoundingBox) -> int:
        return max(0, min(a.bottom, b.bottom) - max(a.y, b.y))

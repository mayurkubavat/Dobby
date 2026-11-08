"""Set-of-Marks (SoM) visual badge and bounding box overlay generator."""

from PIL import Image, ImageDraw, ImageFont

from pinlet.config import CONFIG
from pinlet.types import UIElement

# Vibrant badge color palette for high contrast across dark and light themes
PALETTE = [
    ("#E60049", "#FFFFFF"),  # Crimson / White
    ("#0BB4FF", "#FFFFFF"),  # Electric Blue / White
    ("#50E991", "#000000"),  # Mint Green / Black
    ("#FFA300", "#000000"),  # Amber / Black
    ("#B33DC6", "#FFFFFF"),  # Purple / White
    ("#EA5545", "#FFFFFF"),  # Coral / White
    ("#BDCF32", "#000000"),  # Lime / Black
    ("#27AE60", "#FFFFFF"),  # Emerald / White
]


class SetOfMarksGenerator:
    """Overlays numbered badges onto screenshots so VLMs can reference elements by numeric ID."""

    def __init__(self, font_size: int = CONFIG.som_badge_font_size) -> None:
        self.font_size = font_size
        self._font = None
        try:
            # Try to load standard TrueType fonts on Linux
            for font_path in [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
            ]:
                try:
                    self._font = ImageFont.truetype(font_path, font_size)
                    break
                except OSError:
                    continue
        except Exception:
            self._font = None

        if self._font is None:
            self._font = ImageFont.load_default()

    def annotate(
        self,
        image: Image.Image,
        elements: list[UIElement],
        scale_factor: float = 1.0,
    ) -> tuple[Image.Image, dict[int, UIElement]]:
        """Draws bounding boxes and numeric ID badges on the image.

        Args:
            image: PIL image (may be scaled down).
            elements: List of UIElement models with bounding boxes in original screen coordinates.
            scale_factor: Scale ratio of the provided image compared to the original desktop screen.

        Returns:
            Tuple of (annotated_image, mapping_dict_by_mark_id)
        """
        annotated = image.copy().convert("RGBA")
        overlay = Image.new("RGBA", annotated.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        mark_mapping: dict[int, UIElement] = {}

        for idx, elem in enumerate(elements):
            mark_id = idx + 1
            mark_mapping[mark_id] = elem

            # Scale bounding box to match current image dimensions
            orig_bbox = elem.bbox
            x = int(orig_bbox.x * scale_factor)
            y = int(orig_bbox.y * scale_factor)
            w = int(orig_bbox.width * scale_factor)
            h = int(orig_bbox.height * scale_factor)

            if w <= 0 or h <= 0:
                continue

            bg_color, text_color = PALETTE[idx % len(PALETTE)]

            # Draw semi-transparent bounding box outline
            draw.rectangle(
                [x, y, x + w, y + h],
                outline=bg_color,
                width=2,
            )

            # Draw numeric badge at top-left of element
            label = str(mark_id)
            badge_padding = 3
            try:
                bbox_text = draw.textbbox((0, 0), label, font=self._font)
                text_w = bbox_text[2] - bbox_text[0]
                text_h = bbox_text[3] - bbox_text[1]
            except Exception:
                text_w, text_h = (len(label) * 8, 12)

            badge_w = text_w + badge_padding * 2 + 4
            badge_h = text_h + badge_padding * 2 + 2

            badge_x0 = max(0, x)
            badge_y0 = max(0, y - badge_h) if y >= badge_h else y
            badge_x1 = badge_x0 + badge_w
            badge_y1 = badge_y0 + badge_h

            # Badge background with outline
            draw.rounded_rectangle(
                [badge_x0, badge_y0, badge_x1, badge_y1],
                radius=3,
                fill=bg_color,
                outline="#000000",
                width=1,
            )

            # Badge text
            draw.text(
                (badge_x0 + badge_padding + 2, badge_y0 + badge_padding),
                label,
                fill=text_color,
                font=self._font,
            )

        combined = Image.alpha_composite(annotated, overlay).convert("RGB")
        return combined, mark_mapping

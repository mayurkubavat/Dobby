"""Screen capture, compression, scaling, and cursor overlay pipeline."""

import base64
import io

from PIL import Image, ImageChops, ImageDraw

from dobby.config import CONFIG
from dobby.hal.base import IDesktopHAL


class CapturePipeline:
    """Manages screenshot acquisition, optimization, scaling, and cursor rendering."""

    def __init__(self, hal: IDesktopHAL) -> None:
        self.hal = hal

    def draw_cursor(self, image: Image.Image, cursor_pos: tuple[int, int]) -> Image.Image:
        """Draws a high-visibility cursor indicator at the current pointer location."""
        img = image.copy()
        draw = ImageDraw.Draw(img)
        cx, cy = cursor_pos

        # Distinctive red/white crosshair + pointer arrow
        radius = 8
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline="red",
            width=2,
        )
        draw.line([cx - radius - 4, cy, cx + radius + 4, cy], fill="red", width=2)
        draw.line([cx, cy - radius - 4, cx, cy + radius + 4], fill="red", width=2)
        draw.point((cx, cy), fill="white")
        return img

    def scale_image(
        self, image: Image.Image, max_dim: int = CONFIG.max_image_dimension
    ) -> tuple[Image.Image, float]:
        """Scales down image if dimensions exceed max_dim.

        Returns:
            Tuple of (scaled_image, scale_factor).
            scale_factor < 1.0 means the image was reduced in size.
        """
        orig_w, orig_h = image.size
        max_current = max(orig_w, orig_h)

        if max_current <= max_dim:
            return image, 1.0

        scale = max_dim / float(max_current)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)

        scaled = image.resize((new_w, new_h), Image.Resampling.BILINEAR)
        return scaled, scale

    def encode_image(
        self, image: Image.Image, format: str = "JPEG", quality: int = CONFIG.image_quality
    ) -> str:
        """Encodes PIL image to Base64 string."""
        buffer = io.BytesIO()
        if format.upper() == "JPEG" and image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
        image.save(buffer, format=format, quality=quality, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def capture(
        self,
        region: tuple[int, int, int, int] | None = None,
        draw_cursor: bool = CONFIG.draw_cursor,
        scale_down: bool = True,
    ) -> tuple[Image.Image, float, tuple[int, int], tuple[int, int]]:
        """Captures screenshot and returns processed image and metadata.

        Returns:
            Tuple of:
                (processed_image, scale_factor, original_resolution, cursor_pos)
        """
        raw_img = self.hal.capture_screen(region=region)
        orig_w, orig_h = raw_img.size
        cursor_pos = self.hal.get_cursor_position()

        if draw_cursor:
            # Only draw if cursor falls within capture region
            if region:
                rx, ry, rw, rh = region
                if rx <= cursor_pos[0] <= rx + rw and ry <= cursor_pos[1] <= ry + rh:
                    local_pos = (cursor_pos[0] - rx, cursor_pos[1] - ry)
                    raw_img = self.draw_cursor(raw_img, local_pos)
            else:
                raw_img = self.draw_cursor(raw_img, cursor_pos)

        if scale_down:
            scaled_img, scale_factor = self.scale_image(raw_img)
        else:
            scaled_img, scale_factor = raw_img, 1.0

        return scaled_img, scale_factor, (orig_w, orig_h), cursor_pos

    @staticmethod
    def compute_image_delta(img1: Image.Image, img2: Image.Image) -> float:
        """Calculates fraction of pixels changed between two images of same dimensions.
        Returns float between 0.0 (identical) and 1.0 (completely different).
        """
        if img1.size != img2.size:
            img2 = img2.resize(img1.size)
        diff = ImageChops.difference(img1.convert("RGB"), img2.convert("RGB"))
        bbox = diff.getbbox()
        if not bbox:
            return 0.0

        # Calculate bounding box area ratio
        total_pixels = img1.size[0] * img1.size[1]
        data = getattr(diff, "get_flattened_data", diff.getdata)()
        # Handle RGB (3 bytes per pixel) or tuples
        if isinstance(data, (bytes, bytearray, memoryview)):
            changed_pixels = sum(
                1 for i in range(0, len(data), 3) if any(data[i + j] > 15 for j in range(3))
            )
        else:
            changed_pixels = sum(1 for pixel in data if any(c > 15 for c in pixel))
        return changed_pixels / float(total_pixels)

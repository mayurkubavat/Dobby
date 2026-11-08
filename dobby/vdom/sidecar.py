"""Local Neural Grounding Sidecar & Shared Memory IPC Pipeline.

Facilitates sub-60ms element proposals via POSIX shared memory (/dev/shm)
and local neural/CV inference without cloud token overhead.
"""

import logging
import os
from typing import Any

import numpy as np
from PIL import Image

from dobby.config import CONFIG
from dobby.vdom.fast_detector import FastONNXUIDetector
from dobby.vdom.gemma3_sidecar import Gemma3Grounder

logger = logging.getLogger(__name__)


class LocalNeuralGrounder:
    """Local grounding sidecar interfacing with POSIX shared memory (/dev/shm)
    and running sub-20ms neural/CV visual proposals and local Gemma 3 VLM grounding.
    """

    def __init__(
        self,
        shm_path: str = CONFIG.vdom_sidecar_shm_path,
        onnx_model_path: str | None = None,
        enable_fast_onnx: bool = CONFIG.enable_fast_onnx_detector,
        enable_gemma3: bool = CONFIG.enable_gemma3_sidecar,
    ) -> None:
        self.shm_path = shm_path
        self.onnx_model_path = onnx_model_path or CONFIG.fast_onnx_model_path
        self.enable_fast_onnx = enable_fast_onnx
        self.fast_detector: FastONNXUIDetector | None = None
        self._onnx_session = None
        self.enable_gemma3 = enable_gemma3
        self.gemma3_grounder = Gemma3Grounder() if enable_gemma3 else None

        if self.enable_fast_onnx:
            self._init_fast_detector()
        elif self.onnx_model_path and os.path.exists(self.onnx_model_path):
            self._init_onnx()

    def _init_fast_detector(self) -> None:
        """Initializes high-speed ONNX UI detector."""
        try:
            self.fast_detector = FastONNXUIDetector(
                model_path=self.onnx_model_path,
                conf_thresh=CONFIG.fast_onnx_conf_threshold,
                iou_thresh=CONFIG.fast_onnx_iou_threshold,
                num_threads=CONFIG.fast_onnx_num_threads,
            )
            if self.fast_detector.is_available():
                self._onnx_session = self.fast_detector._session
                logger.info("Initialized Fast ONNX UI detector in LocalNeuralGrounder")
            else:
                self.fast_detector = None
        except Exception as e:
            logger.warning(f"Could not initialize Fast ONNX detector in LocalNeuralGrounder: {e}")
            self.fast_detector = None

    def _init_onnx(self) -> None:
        try:
            import onnxruntime as ort

            sess_opts = ort.SessionOptions()
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            available = ort.get_available_providers()
            providers = [
                p for p in ["CUDAExecutionProvider", "CPUExecutionProvider"] if p in available
            ] or ["CPUExecutionProvider"]
            self._onnx_session = ort.InferenceSession(
                self.onnx_model_path,
                sess_options=sess_opts,
                providers=providers,
            )
            logger.info(
                f"Loaded ONNX model from '{self.onnx_model_path}' with providers {self._onnx_session.get_providers()}"
            )
        except Exception as e:
            logger.warning(f"Could not initialize ONNX runtime: {e}")
            self._onnx_session = None

    def write_frame_to_shm(self, image: Image.Image) -> bool:
        """Writes raw RGB/RGBA frame to POSIX shared memory buffer for zero-copy DMA IPC."""
        try:
            arr = np.array(image)
            raw_bytes = arr.tobytes()
            # Ensure shm directory exists
            os.makedirs(os.path.dirname(self.shm_path), exist_ok=True)
            with open(self.shm_path, "wb") as f:
                f.write(raw_bytes)
            return True
        except Exception as e:
            logger.debug(f"Failed to write frame to shared memory '{self.shm_path}': {e}")
            return False

    def generate_proposals(
        self,
        image: Image.Image,
        offset: tuple[int, int] = (0, 0),
    ) -> list[dict[str, Any]]:
        """Generates fast UI element bounding box proposals (< 20ms) from the given image frame.
        Uses neural inference via FastONNXUIDetector if available, with graceful fallback to OpenCV contouring.
        """
        if self.fast_detector and self.fast_detector.is_available():
            try:
                proposals = self.fast_detector.detect(image, offset=offset)
                if proposals:
                    return proposals
            except Exception as e:
                logger.warning(f"Error during Fast ONNX proposal generation: {e}")

        # Backward compatibility if raw onnx session was manually set
        if self._onnx_session and not self.fast_detector:
            return self._run_onnx_inference(image, offset)

        # High-speed CV proposal generator (fallback)
        return self._generate_cv_proposals(image, offset)

    def ground_subpane(
        self,
        image: Image.Image,
        offset: tuple[int, int] = (0, 0),
        anchor_id: str = "default",
        task_intent: str = "ground_ui",
    ) -> list[dict[str, Any]]:
        """Detector boxes for a sub-pane ROI, named by local Gemma where possible.

        Geometry always comes from the ONNX/CV detector. Gemma only labels the
        numbered boxes (Set-of-Marks), so a Gemma failure just leaves names empty.
        ``anchor_id`` scopes the label cache (use the app or pane name).
        """
        proposals = self.generate_proposals(image, offset=(0, 0))
        if self.enable_gemma3 and self.gemma3_grounder and proposals:
            boxes = [
                (p["bbox"]["x"], p["bbox"]["y"], p["bbox"]["width"], p["bbox"]["height"])
                for p in proposals
            ]
            labels = self.gemma3_grounder.marks.label_marks(image, boxes, app_key=anchor_id)
            for i, info in labels.items():
                proposals[i]["text"] = info["label"]
                proposals[i]["class_name"] = info["role"]
                proposals[i]["named_by"] = "gemma"
        ox, oy = offset
        for p in proposals:
            p["bbox"] = {**p["bbox"], "x": p["bbox"]["x"] + ox, "y": p["bbox"]["y"] + oy}
        return proposals

    def _run_onnx_inference(
        self, image: Image.Image, offset: tuple[int, int]
    ) -> list[dict[str, Any]]:
        """Runs batched ONNX tensor inference on the frame."""
        if self.fast_detector and self.fast_detector.is_available():
            proposals = self.fast_detector.detect(image, offset=offset)
            if proposals:
                return proposals
        return self._generate_cv_proposals(image, offset)

    def _generate_cv_proposals(
        self,
        image: Image.Image,
        offset: tuple[int, int] = (0, 0),
    ) -> list[dict[str, Any]]:
        """Extracts candidate button, icon, and input regions using OpenCV edge and gradient morphology."""
        ox, oy = offset
        proposals: list[dict[str, Any]] = []

        try:
            import cv2

            img_np = np.array(image.convert("RGB"))
            gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
            h, w = gray.shape

            # Edge detection + morphological dilation to bridge character glyphs into words/boxes
            edges = cv2.Canny(gray, 50, 150)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 3))
            dilated = cv2.dilate(edges, kernel, iterations=1)

            contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            for cnt in contours:
                x, y, bw, bh = cv2.boundingRect(cnt)
                # Filter out microscopic noise and full screen frames
                if bw < 14 or bh < 12 or (bw >= w - 10 and bh >= h - 10):
                    continue
                if bw * bh > (w * h * 0.9):
                    continue

                # Classify rough type based on aspect ratio
                class_name = (
                    "icon_button"
                    if (bw <= 36 and bh <= 36)
                    else "text_input"
                    if bw > bh * 3
                    else "push_button"
                )

                proposals.append(
                    {
                        "bbox": {
                            "x": ox + x,
                            "y": oy + y,
                            "width": bw,
                            "height": bh,
                        },
                        "confidence": 0.82,
                        "class_name": class_name,
                        "text": "",
                    }
                )

        except Exception as e:
            logger.debug(f"Error in CV proposal generator: {e}")

        return proposals

"""Local OCR services - surya-ocr, docTR, PaddleOCR"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    text: str
    confidence: float
    bboxes: list
    raw_data: dict


class LocalOCRService:
    """Multi-engine OCR: surya-ocr (default), docTR, PaddleOCR"""

    def __init__(self, engine: str = "surya", device: str = "cpu"):
        self.engine = engine
        self.device = device
        self._det_model = None
        self._rec_model = None
        self._det_processor = None
        self._rec_processor = None
        self._doctr_model = None
        self._paddle = None

    def _load_surya(self):
        try:
            from surya.model.detection import segformer as det_segformer
            from surya.model.recognition import processor as rec_processor
            from surya.ocr import run_ocr

            self._run_ocr = run_ocr
            self._det_processor, self._det_model = (
                det_segformer.load_processor(),
                det_segformer.load_model(),
            )
            self._rec_processor, self._rec_model = (
                rec_processor.load_processor(),
                rec_processor.load_model(),
            )
            logger.info("surya-ocr loaded")
        except ImportError:
            logger.warning("surya-ocr not installed, falling back to docTR")
            self._load_doctr()

    def _load_doctr(self):
        try:
            from doctr.io import DocumentFile
            from doctr.models import ocr_predictor

            self._doctr_model = ocr_predictor(pretrained=True)
            self._DocumentFile = DocumentFile
            logger.info("docTR loaded")
        except ImportError:
            logger.warning("docTR not installed, falling back to paddleocr")
            self._load_paddleocr()

    def _load_paddleocr(self):
        try:
            from paddleocr import PaddleOCR

            self._paddle = PaddleOCR(
                use_angle_cls=True, lang="es", use_gpu=self.device == "cuda"
            )
            logger.info("PaddleOCR loaded")
        except ImportError:
            raise RuntimeError(
                "No OCR engine available. Install surya-ocr, docTR, or paddleocr"
            )

    def _ensure_loaded(self):
        if self.engine == "surya" and self._det_model is None:
            self._load_surya()
        elif self.engine == "doctr" and self._doctr_model is None:
            self._load_doctr()
        elif self.engine == "paddle" and self._paddle is None:
            self._load_paddleocr()

    async def extract_receipt(self, image_path: str) -> OCRResult:
        self._ensure_loaded()
        loop = asyncio.get_event_loop()

        if self.engine == "surya":
            return await loop.run_in_executor(None, self._extract_surya, image_path)
        elif self.engine == "doctr":
            return await loop.run_in_executor(None, self._extract_doctr, image_path)
        else:
            return await loop.run_in_executor(None, self._extract_paddle, image_path)

    def _extract_surya(self, image_path: str) -> OCRResult:
        from PIL import Image

        img = Image.open(image_path)
        results = self._run_ocr(
            [img],
            [self._det_processor],
            self._det_model,
            self._rec_processor,
            self._rec_model,
        )
        result = results[0]
        text_lines = [line.text for line in result.text_lines]
        full_text = "\n".join(text_lines)
        avg_conf = sum(line.confidence for line in result.text_lines) / max(
            len(result.text_lines), 1
        )
        bboxes = [
            {"bbox": line.bbox, "text": line.text, "confidence": line.confidence}
            for line in result.text_lines
        ]
        return OCRResult(
            text=full_text,
            confidence=avg_conf,
            bboxes=bboxes,
            raw_data={"engine": "surya"},
        )

    def _extract_doctr(self, image_path: str) -> OCRResult:
        doc = self._DocumentFile.from_images(image_path)
        result = self._doctr_model(doc)
        text_lines = []
        confidences = []
        bboxes = []
        for page in result.pages:
            for block in page.blocks:
                for line in block.lines:
                    line_text = " ".join(word.value for word in line.words)
                    line_conf = sum(word.confidence for word in line.words) / max(
                        len(line.words), 1
                    )
                    text_lines.append(line_text)
                    confidences.append(line_conf)
                    bboxes.append(
                        {
                            "bbox": [
                                line.geometry[0][0],
                                line.geometry[0][1],
                                line.geometry[1][0],
                                line.geometry[1][1],
                            ],
                            "text": line_text,
                            "confidence": line_conf,
                        }
                    )
        full_text = "\n".join(text_lines)
        avg_conf = sum(confidences) / max(len(confidences), 1)
        return OCRResult(
            text=full_text,
            confidence=avg_conf,
            bboxes=bboxes,
            raw_data={"engine": "doctr"},
        )

    def _extract_paddle(self, image_path: str) -> OCRResult:
        result = self._paddle.ocr(image_path, cls=True)
        text_lines = []
        confidences = []
        bboxes = []
        for line in result[0]:
            bbox, (text, conf) = line
            text_lines.append(text)
            confidences.append(conf)
            bboxes.append({"bbox": bbox, "text": text, "confidence": conf})
        full_text = "\n".join(text_lines)
        avg_conf = sum(confidences) / max(len(confidences), 1)
        return OCRResult(
            text=full_text,
            confidence=avg_conf,
            bboxes=bboxes,
            raw_data={"engine": "paddle"},
        )

    async def extract_batch(self, image_paths: list[str]) -> list[OCRResult]:
        semaphore = asyncio.Semaphore(4)

        async def extract_one(path: str):
            async with semaphore:
                return await self.extract_receipt(path)

        tasks = [extract_one(p) for p in image_paths]
        return await asyncio.gather(*tasks, return_exceptions=True)


def scan_images(folder_path: str, recursive: bool = True) -> list[str]:
    """Scan folder for image files"""
    path = Path(folder_path)
    if not path.exists():
        raise ValueError(f"Folder not found: {folder_path}")

    extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".pdf",
        ".heic",
        ".tiff",
        ".tif",
        ".bmp",
        ".webp",
    }
    pattern = "**/*" if recursive else "*"
    images = [
        str(f)
        for f in path.glob(pattern)
        if f.is_file() and f.suffix.lower() in extensions
    ]
    logger.info(f"Found {len(images)} images in {folder_path}")
    return images

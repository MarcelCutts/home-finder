"""SSCD (Self-Supervised Copy Detection) image embeddings.

Uses Meta's SSCD model (ResNet50, trained with contrastive learning) to produce
512-dim L2-normalized embeddings that are robust to crops, overlays, compression,
and color jitter — exactly the augmentations property platforms apply.

Cosine similarity > 0.75 gives ~90% precision for copy detection.

Model: sscd_disc_mixup.torchscript.pt (~100MB, downloaded once to data/models/).

Usage:
    from sscd_embeddings import SSCDEncoder
    encoder = SSCDEncoder()  # Downloads model on first use
    embedding = encoder.encode_file(Path("image.jpg"))  # 512-dim numpy array
    embeddings = encoder.encode_batch([Path("a.jpg"), Path("b.jpg")])
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from home_finder.logging import get_logger

logger = get_logger(__name__)

MODEL_DIR = Path(__file__).parent / "data" / "models"
MODEL_FILENAME = "sscd_disc_mixup.torchscript.pt"
MODEL_URL = "https://dl.fbaipublicfiles.com/sscd-copy-detection/sscd_disc_mixup.torchscript.pt"

# SSCD input: 288x288 (as per the paper), ImageNet normalization
SSCD_INPUT_SIZE = 288
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

# Lazy-loaded globals
_model = None
_torch = None
_transforms = None


def _ensure_torch():
    """Import torch lazily — allows the rest of the codebase to work without it."""
    global _torch, _transforms
    if _torch is not None:
        return
    try:
        import torch
        import torchvision.transforms as transforms

        _torch = torch
        _transforms = transforms
    except ImportError as e:
        raise ImportError(
            "SSCD embeddings require torch and torchvision. Install with: uv sync --extra sscd"
        ) from e


def _download_model() -> Path:
    """Download SSCD model if not already present."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / MODEL_FILENAME

    if model_path.exists():
        return model_path

    logger.info("downloading_sscd_model", url=MODEL_URL, dest=str(model_path))
    print(f"Downloading SSCD model (~100MB) to {model_path} ...")

    urllib.request.urlretrieve(MODEL_URL, model_path)

    logger.info("sscd_model_downloaded", size_mb=model_path.stat().st_size / 1e6)
    print("Download complete.")
    return model_path


def _get_model():
    """Load the SSCD TorchScript model (singleton)."""
    global _model
    if _model is not None:
        return _model

    _ensure_torch()
    model_path = _download_model()

    logger.info("loading_sscd_model", path=str(model_path))
    _model = _torch.jit.load(model_path, map_location="cpu")
    _model.eval()
    return _model


def _get_transform():
    """Build the SSCD preprocessing transform."""
    _ensure_torch()
    return _transforms.Compose(
        [
            _transforms.Resize((SSCD_INPUT_SIZE, SSCD_INPUT_SIZE)),
            _transforms.ToTensor(),
            _transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
        ]
    )


def _preprocess_image(image: Image.Image):
    """Convert PIL image to SSCD input tensor."""
    if image.mode != "RGB":
        image = image.convert("RGB")
    transform = _get_transform()
    return transform(image)


class SSCDEncoder:
    """SSCD copy-detection encoder.

    Produces 512-dim L2-normalized embeddings from images.
    Thread-safe for inference (model is loaded once, used read-only).
    """

    def __init__(self) -> None:
        # Defer model loading to first use
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            self._model = _get_model()

    def encode_pil(self, image: Image.Image) -> np.ndarray:
        """Encode a PIL Image to a 512-dim L2-normalized embedding."""
        self._ensure_model()
        _ensure_torch()

        tensor = _preprocess_image(image).unsqueeze(0)  # Add batch dim

        with _torch.no_grad():
            embedding = self._model(tensor)

        # L2 normalize
        embedding = _torch.nn.functional.normalize(embedding, dim=1)
        return embedding.squeeze(0).numpy()

    def encode_bytes(self, data: bytes) -> np.ndarray:
        """Encode image bytes to a 512-dim embedding."""
        image = Image.open(io.BytesIO(data))
        return self.encode_pil(image)

    def encode_file(self, path: Path) -> np.ndarray:
        """Encode an image file to a 512-dim embedding."""
        image = Image.open(path)
        return self.encode_pil(image)

    def encode_batch(self, paths: list[Path], *, batch_size: int = 16) -> list[np.ndarray]:
        """Encode multiple image files in batches for efficiency.

        Returns list of 512-dim embeddings (one per path).
        Failed images return zero vectors.
        """
        self._ensure_model()
        _ensure_torch()

        results: list[np.ndarray] = []

        for batch_start in range(0, len(paths), batch_size):
            batch_paths = paths[batch_start : batch_start + batch_size]
            tensors = []
            valid_indices = []

            for i, path in enumerate(batch_paths):
                try:
                    image = Image.open(path)
                    tensor = _preprocess_image(image)
                    tensors.append(tensor)
                    valid_indices.append(i)
                except Exception as e:
                    logger.debug("sscd_preprocess_failed", path=str(path), error=str(e))

            # Process batch
            batch_embeddings: dict[int, np.ndarray] = {}
            if tensors:
                batch_tensor = _torch.stack(tensors)
                with _torch.no_grad():
                    embeddings = self._model(batch_tensor)
                embeddings = _torch.nn.functional.normalize(embeddings, dim=1)

                for j, idx in enumerate(valid_indices):
                    batch_embeddings[idx] = embeddings[j].numpy()

            # Fill results (zero vector for failed images)
            for i in range(len(batch_paths)):
                if i in batch_embeddings:
                    results.append(batch_embeddings[i])
                else:
                    results.append(np.zeros(512, dtype=np.float32))

        return results


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two L2-normalized embeddings.

    Since SSCD embeddings are already L2-normalized, this is just the dot product.
    """
    return float(np.dot(a, b))


def count_embedding_matches(
    gallery_a: list[np.ndarray],
    gallery_b: list[np.ndarray],
    *,
    threshold: float = 0.75,
) -> tuple[int, list[tuple[int, int, float]]]:
    """Count matching images between two galleries using SSCD embeddings.

    Uses greedy 1:1 matching (same as hash-based count_gallery_matches).

    Returns:
        (match_count, list of (idx_a, idx_b, similarity) matched triples)
    """
    if not gallery_a or not gallery_b:
        return 0, []

    matched_b: set[int] = set()
    matches: list[tuple[int, int, float]] = []

    for i, emb_a in enumerate(gallery_a):
        best_j = -1
        best_sim = -1.0

        for j, emb_b in enumerate(gallery_b):
            if j in matched_b:
                continue
            sim = cosine_similarity(emb_a, emb_b)
            if sim >= threshold and sim > best_sim:
                best_sim = sim
                best_j = j

        if best_j >= 0:
            matched_b.add(best_j)
            matches.append((i, best_j, best_sim))

    return len(matches), matches

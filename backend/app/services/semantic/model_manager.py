"""Manager for local sentence-embedding model lifecycle, storage, and fallback."""

import asyncio
import logging
from pathlib import Path

import httpx

from app.core.config import settings
from app.services.semantic.embedding_service import (
    BaseEmbeddingService,
    FallbackEmbeddingService,
    OnnxEmbeddingService,
)
from app.services.semantic.models import ModelLifecycleStatus, ModelStatusInfo

logger = logging.getLogger(__name__)

# Hugging Face CDN endpoints for all-MiniLM-L6-v2 ONNX assets
DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
TOKENIZER_URL = (
    "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/tokenizer.json"
)
TOKENIZER_FALLBACK_URL = (
    "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/tokenizer.json"
)
# Verified 22.9 MB quantized INT8 ONNX model
ONNX_MODEL_URL = (
    "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/onnx/model_quantized.onnx"
)
# Secondary fallback full float32 model URL (90.4 MB)
ONNX_MODEL_FALLBACK_URL = (
    "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx"
)

# Minimum valid asset sizes for integrity checks
MIN_MODEL_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB minimum
MIN_TOKENIZER_SIZE_BYTES = 100 * 1024  # 100 KB minimum


class SemanticModelManager:
    """Manages local embedding model assets, download lifecycle, and active service instance."""

    _instance: "SemanticModelManager | None" = None

    def __init__(self, models_dir: Path | None = None) -> None:
        self.models_dir = models_dir or (settings.ensure_data_dir() / "models" / "all-MiniLM-L6-v2")
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.model_path = self.models_dir / "model.onnx"
        self.tokenizer_path = self.models_dir / "tokenizer.json"

        self._active_service: BaseEmbeddingService | None = None
        self._fallback_service = FallbackEmbeddingService()

        self._status = ModelLifecycleStatus.NOT_DOWNLOADED
        self._download_progress = 0.0
        self._error_message: str | None = None
        self._is_enabled = True
        self._is_downloading = False
        self._lock = asyncio.Lock()

        # Check existing disk files on startup
        self._inspect_disk_state()

    @classmethod
    def get_instance(cls) -> "SemanticModelManager":
        """Singleton accessor for the model manager."""
        if cls._instance is None:
            cls._instance = SemanticModelManager()
        return cls._instance

    def _inspect_disk_state(self) -> None:
        """Inspect and verify integrity of model assets existing on disk."""
        if not self._is_enabled:
            self._status = ModelLifecycleStatus.DISABLED
            return

        if self.model_path.exists() and self.tokenizer_path.exists():
            try:
                # 1. Size integrity check
                model_size = self.model_path.stat().st_size
                tokenizer_size = self.tokenizer_path.stat().st_size

                if model_size < MIN_MODEL_SIZE_BYTES or tokenizer_size < MIN_TOKENIZER_SIZE_BYTES:
                    logger.warning(
                        "Corrupted/truncated model assets detected on disk "
                        "(model: %d B, tokenizer: %d B). Cleaning up...",
                        model_size,
                        tokenizer_size,
                    )
                    self.model_path.unlink(missing_ok=True)
                    self.tokenizer_path.unlink(missing_ok=True)
                    self._status = ModelLifecycleStatus.NOT_DOWNLOADED
                    self._active_service = None
                    return

                # 2. Instantiate and verify with a test forward-pass
                service = OnnxEmbeddingService(self.model_path, self.tokenizer_path)
                test_vec = service.embed_text("warmup")
                if len(test_vec) != 384:
                    raise ValueError(f"Unexpected embedding dimension: {len(test_vec)}")

                self._active_service = service
                self._status = ModelLifecycleStatus.READY
                self._download_progress = 100.0
                self._error_message = None
                logger.info(
                    "Verified and initialized OnnxEmbeddingService successfully from %s",
                    self.models_dir,
                )
            except Exception as exc:
                logger.warning(
                    "Existing model files failed integrity check/startup: %s. "
                    "Reverting to NOT_DOWNLOADED.",
                    exc,
                )
                self.model_path.unlink(missing_ok=True)
                self.tokenizer_path.unlink(missing_ok=True)
                self._status = ModelLifecycleStatus.NOT_DOWNLOADED
                self._error_message = str(exc)
                self._active_service = None
        else:
            self._status = ModelLifecycleStatus.NOT_DOWNLOADED
            self._active_service = None

    def get_status(self) -> ModelStatusInfo:
        """Return the current snapshot of model lifecycle status."""
        is_ready = self._status == ModelLifecycleStatus.READY and self._active_service is not None
        return ModelStatusInfo(
            status=self._status,
            model_name="all-MiniLM-L6-v2",
            model_path=str(self.models_dir),
            dimension=384,
            is_ready=is_ready,
            is_fallback=not is_ready,
            download_progress=self._download_progress,
            error_message=self._error_message,
        )

    def get_embedding_service(self) -> BaseEmbeddingService:
        """Return active neural embedding service if ready, or deterministic fallback."""
        if (
            self._status == ModelLifecycleStatus.READY
            and self._active_service is not None
            and self._is_enabled
        ):
            return self._active_service
        return self._fallback_service

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable semantic neural matching."""
        self._is_enabled = enabled
        if not enabled:
            self._status = ModelLifecycleStatus.DISABLED
        else:
            self._inspect_disk_state()

    async def download_model(self) -> bool:
        """Download model weights and tokenizer with integrity checks."""
        if self._is_downloading or self._status == ModelLifecycleStatus.DOWNLOADING:
            logger.info("Download already in progress; skipping duplicate trigger.")
            return True

        if self._status == ModelLifecycleStatus.READY and self._active_service is not None:
            return True

        async with self._lock:
            if self._is_downloading:
                return True
            self._is_downloading = True

            self._status = ModelLifecycleStatus.DOWNLOADING
            self._download_progress = 0.0
            self._error_message = None

            temp_tokenizer = self.models_dir / "tokenizer.json.tmp"
            temp_model = self.models_dir / "model.onnx.tmp"

            try:
                async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
                    # 1. Download tokenizer with fallback
                    logger.info("Downloading tokenizer...")
                    tok_downloaded = False
                    for tok_url in (TOKENIZER_URL, TOKENIZER_FALLBACK_URL):
                        try:
                            resp = await client.get(tok_url)
                            resp.raise_for_status()
                            temp_tokenizer.write_bytes(resp.content)
                            if temp_tokenizer.stat().st_size >= MIN_TOKENIZER_SIZE_BYTES:
                                tok_downloaded = True
                                break
                        except Exception as tok_err:
                            logger.warning(
                                "Tokenizer download from %s failed: %s", tok_url, tok_err
                            )

                    if not tok_downloaded:
                        raise RuntimeError(
                            "Failed to download a valid tokenizer from all endpoints."
                        )

                    self._download_progress = 10.0

                    # 2. Download ONNX model with stream and fallback
                    logger.info("Downloading ONNX model...")

                    async def _stream_to_file(url: str) -> None:
                        async with client.stream("GET", url) as stream_resp:
                            stream_resp.raise_for_status()
                            total_bytes = int(
                                stream_resp.headers.get("Content-Length", 25 * 1024 * 1024)
                            )
                            downloaded_bytes = 0
                            with open(temp_model, "wb") as f:
                                async for chunk in stream_resp.aiter_bytes(chunk_size=128 * 1024):
                                    f.write(chunk)
                                    downloaded_bytes += len(chunk)
                                    self._download_progress = min(
                                        90.0, 10.0 + (downloaded_bytes / total_bytes) * 80.0
                                    )

                    model_downloaded = False
                    for model_url in (ONNX_MODEL_URL, ONNX_MODEL_FALLBACK_URL):
                        try:
                            logger.info("Attempting model download from: %s", model_url)
                            await _stream_to_file(model_url)
                            if temp_model.stat().st_size >= MIN_MODEL_SIZE_BYTES:
                                model_downloaded = True
                                break
                        except Exception as m_err:
                            logger.warning("Model download from %s failed: %s", model_url, m_err)
                            temp_model.unlink(missing_ok=True)

                    if not model_downloaded:
                        raise RuntimeError(
                            "Failed to download a valid ONNX model from all endpoints."
                        )

                    # 3. Verify integrity of temp files prior to promoting
                    logger.info("Verifying downloaded model integrity with test inference...")
                    test_service = OnnxEmbeddingService(temp_model, temp_tokenizer)
                    test_vec = test_service.embed_text("integrity check")
                    if len(test_vec) != 384:
                        raise ValueError(
                            f"Integrity check failed: invalid dimension {len(test_vec)}"
                        )

                    # 4. Atomic promotion
                    temp_tokenizer.replace(self.tokenizer_path)
                    temp_model.replace(self.model_path)

                    self._download_progress = 100.0
                    self._active_service = test_service
                    self._status = ModelLifecycleStatus.READY
                    logger.info("Semantic model verified, downloaded, and activated successfully!")
                    return True

            except Exception as exc:
                logger.error("Failed to download or verify semantic model: %s", exc)
                self._status = ModelLifecycleStatus.ERROR
                self._error_message = f"Download failed: {exc}"
                temp_tokenizer.unlink(missing_ok=True)
                temp_model.unlink(missing_ok=True)
                return False
            finally:
                self._is_downloading = False

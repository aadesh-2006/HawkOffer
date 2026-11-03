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
ONNX_MODEL_URL = "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model_quantized.onnx"
# Alternative fallback full float32 model URL if quantized is unavailable
ONNX_MODEL_FALLBACK_URL = (
    "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx"
)


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
        """Inspect if valid model assets already exist on disk."""
        if not self._is_enabled:
            self._status = ModelLifecycleStatus.DISABLED
            return

        if self.model_path.exists() and self.tokenizer_path.exists():
            try:
                # Instantiate ONNX service to verify loadability
                self._active_service = OnnxEmbeddingService(self.model_path, self.tokenizer_path)
                self._status = ModelLifecycleStatus.READY
                self._download_progress = 100.0
                self._error_message = None
                logger.info(
                    "Initialized OnnxEmbeddingService successfully from %s", self.models_dir
                )
            except Exception as exc:
                logger.warning(
                    "Existing model files failed to initialize: %s. Reverting to NOT_DOWNLOADED.",
                    exc,
                )
                self._status = ModelLifecycleStatus.ERROR
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
        """Download model weights and tokenizer from Hugging Face asynchronously."""
        async with self._lock:
            if self._status == ModelLifecycleStatus.DOWNLOADING:
                return True
            if self._status == ModelLifecycleStatus.READY and self._active_service is not None:
                return True

            self._status = ModelLifecycleStatus.DOWNLOADING
            self._download_progress = 0.0
            self._error_message = None

            temp_tokenizer = self.models_dir / "tokenizer.json.tmp"
            temp_model = self.models_dir / "model.onnx.tmp"

            try:
                async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
                    # 1. Download tokenizer (~1 MB)
                    logger.info("Downloading tokenizer from %s", TOKENIZER_URL)
                    resp = await client.get(TOKENIZER_URL)
                    resp.raise_for_status()
                    temp_tokenizer.write_bytes(resp.content)
                    self._download_progress = 10.0

                    # 2. Download ONNX model (~22 MB quantized)
                    logger.info("Downloading ONNX model from %s", ONNX_MODEL_URL)

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
                                    # Progress from 10% to 95%
                                    self._download_progress = min(
                                        95.0, 10.0 + (downloaded_bytes / total_bytes) * 85.0
                                    )

                    try:
                        await _stream_to_file(ONNX_MODEL_URL)
                    except Exception:
                        await _stream_to_file(ONNX_MODEL_FALLBACK_URL)

                    # 3. Rename atomic
                    if temp_tokenizer.exists():
                        temp_tokenizer.replace(self.tokenizer_path)
                    if temp_model.exists():
                        temp_model.replace(self.model_path)

                    self._download_progress = 100.0

                    # 4. Initialize engine
                    self._active_service = OnnxEmbeddingService(
                        self.model_path, self.tokenizer_path
                    )
                    self._status = ModelLifecycleStatus.READY
                    logger.info("Semantic model downloaded and initialized successfully!")
                    return True

            except Exception as exc:
                logger.error("Failed to download or initialize semantic model: %s", exc)
                self._status = ModelLifecycleStatus.ERROR
                self._error_message = f"Download failed: {exc}"
                # Clean up temporary files
                temp_tokenizer.unlink(missing_ok=True)
                temp_model.unlink(missing_ok=True)
                return False

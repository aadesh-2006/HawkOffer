"""Semantic job matching and embedding package."""

from app.services.semantic.embedding_service import (
    FALLBACK_MODEL_VERSION,
    ONNX_MODEL_VERSION,
    BaseEmbeddingService,
    FallbackEmbeddingService,
    OnnxEmbeddingService,
)
from app.services.semantic.matcher import SemanticJobMatcher
from app.services.semantic.model_manager import SemanticModelManager
from app.services.semantic.models import (
    ModelLifecycleStatus,
    ModelStatusInfo,
    SemanticMatchResult,
)

__all__ = [
    "FALLBACK_MODEL_VERSION",
    "ONNX_MODEL_VERSION",
    "BaseEmbeddingService",
    "FallbackEmbeddingService",
    "ModelLifecycleStatus",
    "ModelStatusInfo",
    "OnnxEmbeddingService",
    "SemanticJobMatcher",
    "SemanticMatchResult",
    "SemanticModelManager",
]

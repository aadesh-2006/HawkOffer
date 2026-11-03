"""Embedding service providing local neural and deterministic fallback embeddings."""

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Model version constant for all-MiniLM-L6-v2 ONNX
ONNX_MODEL_VERSION = "all-MiniLM-L6-v2-onnx-v1"
FALLBACK_MODEL_VERSION = "deterministic-hash-384-v1"


class BaseEmbeddingService(ABC):
    """Abstract base class for local embedding generation."""

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Unique identifier representing the model architecture and version."""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimension of the generated embedding vectors."""
        ...

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate normalized embedding vectors for a batch of text strings."""
        ...

    def embed_text(self, text: str) -> list[float]:
        """Generate a single normalized embedding vector."""
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else [0.0] * self.dimension

    def similarity(self, text1: str, text2: str) -> float:
        """Compute cosine similarity between two texts (0.0 to 1.0)."""
        v1 = self.embed_text(text1)
        v2 = self.embed_text(text2)
        dot = sum(a * b for a, b in zip(v1, v2, strict=False))
        return float(max(0.0, min(1.0, dot)))


class FallbackEmbeddingService(BaseEmbeddingService):
    """Deterministic, CPU-only feature hashing embedding service.

    Used when the ONNX neural model has not yet been downloaded, during offline mode,
    or in lightweight unit test environments. Produces normalized 384-dimensional
    vectors using subword n-grams, word tokens, and Murmur/MD5 feature hashing.
    """

    def __init__(self, dimension: int = 384) -> None:
        self._dimension = dimension

    @property
    def model_version(self) -> str:
        return FALLBACK_MODEL_VERSION

    @property
    def dimension(self) -> int:
        return self._dimension

    def _hash_token(self, token: str, seed: int = 0) -> tuple[int, float]:
        """Hash token into a dimension bucket and non-negative feature weight."""
        key = f"{seed}:{token}".encode()
        h = int(hashlib.md5(key, usedforsecurity=False).hexdigest(), 16)
        bucket = h % self._dimension
        return bucket, 1.0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []

        for text in texts:
            if not text or not text.strip():
                # Zero vector for empty text
                results.append([0.0] * self._dimension)
                continue

            vec = np.zeros(self._dimension, dtype=np.float32)
            cleaned = text.lower()
            words = re.findall(r"\b[a-z0-9+#.-]+\b", cleaned)

            if not words:
                results.append([0.0] * self._dimension)
                continue

            # 1. Unigram word hashing with log-frequency weighting
            word_counts: dict[str, int] = {}
            for w in words:
                word_counts[w] = word_counts.get(w, 0) + 1

            for w, count in word_counts.items():
                weight = 1.0 + math.log(count)
                # Primary bucket
                b1, s1 = self._hash_token(w, seed=42)
                vec[b1] += s1 * weight * 1.5

                # Secondary bucket for dispersion
                b2, s2 = self._hash_token(w, seed=1337)
                vec[b2] += s2 * weight * 0.75

                # Character 3-grams for subword / spelling similarity
                if len(w) >= 3:
                    for i in range(len(w) - 2):
                        tri = w[i : i + 3]
                        b_tri, s_tri = self._hash_token(tri, seed=7)
                        vec[b_tri] += s_tri * 0.4

            # 2. Bigrams for phrase capture (e.g. "software engineer", "machine learning")
            for i in range(len(words) - 1):
                bi = f"{words[i]}_{words[i + 1]}"
                b_bi, s_bi = self._hash_token(bi, seed=999)
                vec[b_bi] += s_bi * 2.0

            # 3. L2 Normalization to unit length
            norm = float(np.linalg.norm(vec))
            vec = vec / norm if norm > 1e-12 else np.zeros(self._dimension, dtype=np.float32)

            results.append(vec.tolist())

        return results


class OnnxEmbeddingService(BaseEmbeddingService):
    """Production local neural embedding engine using ONNX Runtime and Tokenizers.

    Runs sentence-transformers/all-MiniLM-L6-v2 entirely on CPU with zero PyTorch overhead.
    """

    def __init__(self, model_path: Path, tokenizer_path: Path) -> None:
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self._session: Any = None
        self._tokenizer: Any = None
        self._dimension = 384
        self._load_engine()

    def _load_engine(self) -> None:
        """Initialize the ONNX Runtime session and Tokenizer."""
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            if not self.model_path.exists():
                raise FileNotFoundError(f"ONNX model file not found at: {self.model_path}")
            if not self.tokenizer_path.exists():
                raise FileNotFoundError(f"Tokenizer file not found at: {self.tokenizer_path}")

            # Configure thread pool for efficient CPU inference without choking the machine
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

            self._session = ort.InferenceSession(
                str(self.model_path),
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )

            self._tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
            self._tokenizer.enable_truncation(max_length=512)
            self._tokenizer.enable_padding()

            logger.info("Loaded ONNX embedding engine from %s", self.model_path)
        except Exception as exc:
            logger.error("Failed to initialize ONNX embedding engine: %s", exc)
            raise

    @property
    def model_version(self) -> str:
        return ONNX_MODEL_VERSION

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Encode texts, run ONNX inference, apply mean pooling and L2 normalization."""
        if not texts:
            return []

        # Identify indices of non-empty texts to infer
        non_empty_indices = [i for i, t in enumerate(texts) if t and t.strip()]
        if not non_empty_indices:
            return [[0.0] * self._dimension for _ in texts]

        # Extract only non-empty texts for tokenizer and inference
        active_texts = [texts[i] for i in non_empty_indices]

        # 1. Tokenize batch dynamically (pads to batch max sequence length <= 512)
        encoded = self._tokenizer.encode_batch(active_texts)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        # 2. Build ONNX inputs
        feed_dict: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        # Some ONNX models require token_type_ids, check input names
        session_input_names = [inp.name for inp in self._session.get_inputs()]
        if "token_type_ids" in session_input_names:
            feed_dict["token_type_ids"] = token_type_ids

        # 3. Execute inference
        outputs = self._session.run(None, feed_dict)
        token_embeddings = outputs[0]  # Shape: (batch_size, seq_len, 384)

        # 4. Mean Pooling with attention mask
        # Expand attention_mask to (batch_size, seq_len, 384)
        mask_expanded = np.expand_dims(attention_mask, -1).astype(np.float32)
        sum_embeddings = np.sum(token_embeddings * mask_expanded, axis=1)
        sum_mask = np.clip(np.sum(mask_expanded, axis=1), a_min=1e-9, a_max=None)
        pooled = sum_embeddings / sum_mask

        # 5. L2 Normalization
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms = np.clip(norms, a_min=1e-12, a_max=None)
        normalized = pooled / norms
        computed_vectors: list[list[float]] = normalized.astype(float).tolist()

        # Reconstruct output array mapping computed vectors to non-empty positions
        final_vectors: list[list[float]] = [[0.0] * self._dimension for _ in texts]
        for original_idx, computed_vec in zip(non_empty_indices, computed_vectors, strict=False):
            final_vectors[original_idx] = computed_vec

        return final_vectors

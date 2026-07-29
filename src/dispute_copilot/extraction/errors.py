"""Fail-closed errors raised while loading evidence bundles."""

from pathlib import Path
from uuid import UUID


class EvidenceBundleError(Exception):
    """Base error for deterministic evidence-bundle loading."""


class PolicySelectionNotFoundError(EvidenceBundleError):
    """Raised when a requested policy selection does not exist."""

    def __init__(self, selection_id: UUID) -> None:
        self.selection_id = selection_id
        super().__init__(f"Policy selection '{selection_id}' was not found")


class SelectionNotReadyForExtractionError(EvidenceBundleError):
    """Raised when a selection has not emitted extraction readiness."""


class SelectionCaseRunNotFoundError(EvidenceBundleError):
    """Raised when a selection references a missing immutable case run."""

    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(
            f"Case run '{run_id}' referenced by selection was not found"
        )


class EvidenceFileMissingError(EvidenceBundleError):
    """Raised when an intake-admitted evidence path is no longer a file."""

    def __init__(self, filename: str, path: Path) -> None:
        self.filename = filename
        self.path = path
        super().__init__(f"Evidence file is missing: {filename} ({path})")


class EvidenceHashMismatchError(EvidenceBundleError):
    """Raised when current evidence bytes differ from the intake snapshot."""

    def __init__(
        self,
        filename: str,
        expected_sha256: str,
        actual_sha256: str,
    ) -> None:
        self.filename = filename
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256
        super().__init__(
            f"Evidence hash mismatch for {filename}: expected "
            f"{expected_sha256}, got {actual_sha256}"
        )


class EvidenceSizeMismatchError(EvidenceBundleError):
    """Raised when current evidence size differs from intake metadata."""

    def __init__(
        self,
        filename: str,
        expected_size: int,
        actual_size: int,
    ) -> None:
        self.filename = filename
        self.expected_size = expected_size
        self.actual_size = actual_size
        super().__init__(
            f"Evidence size mismatch for {filename}: expected "
            f"{expected_size}, got {actual_size}"
        )


class DocumentConversionError(Exception):
    """Base error for source-preserving document conversion."""


class UnsupportedDocumentMediaTypeError(DocumentConversionError):
    """Raised when no deterministic conversion route exists."""

    def __init__(self, media_type: str) -> None:
        self.media_type = media_type
        super().__init__(
            f"Unsupported document media type: {media_type}"
        )


class OCRProviderRequiredError(DocumentConversionError):
    """Raised when a PDF page has no native text and OCR is absent."""

    def __init__(self, filename: str, page_number: int) -> None:
        self.filename = filename
        self.page_number = page_number
        super().__init__(
            f"OCR provider required for {filename}, page {page_number}"
        )


class VisionProviderRequiredError(DocumentConversionError):
    """Raised when visual evidence has no configured vision provider."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        super().__init__(
            f"Vision provider required for visual evidence: {filename}"
        )


class EmptyConversionOutputError(DocumentConversionError):
    """Raised when a configured converter returns no source description."""


class EvidenceDocumentAlreadyExistsError(Exception):
    """Raised when a source/version cache key is already persisted."""

    def __init__(
        self,
        source_sha256: str,
        converter_version: str,
    ) -> None:
        self.source_sha256 = source_sha256
        self.converter_version = converter_version
        super().__init__(
            "Evidence document already exists for "
            f"{source_sha256}/{converter_version}"
        )


class EvidenceExtractionError(Exception):
    """Base error for lean evidence-fact extraction."""


class ProviderError(EvidenceExtractionError):
    """Base error for provider configuration and live inference."""


class ProviderConfigurationError(ProviderError, ValueError):
    """Raised before evidence access when provider settings are invalid."""


class UnsupportedProviderError(ProviderConfigurationError):
    """Raised when no runtime factory is registered for a provider."""


class ProviderTransportUnavailableError(ProviderError):
    """Raised when an adapter transport cannot be constructed."""


class ProviderEndpointError(ProviderError):
    """Raised when the configured provider endpoint cannot be reached."""


class ProviderRejectionError(ProviderError):
    """Raised when the configured provider/model rejects a request."""


class StructuredOutputUnsupportedError(ProviderRejectionError):
    """Raised when required structured output is not supported."""


class InvalidProviderResponseError(ProviderError):
    """Raised when provider output fails the untrusted response schema."""


class EvidenceCandidateValidationError(EvidenceExtractionError, ValueError):
    """Raised when candidates do not exactly cover the requested facts."""


class EvidenceCandidateValueError(
    EvidenceCandidateValidationError,
):
    """Raised when an untrusted value violates its catalogue definition."""


class EvidenceCandidateProvenanceError(
    EvidenceCandidateValidationError,
):
    """Raised when candidate provenance is not an admitted source."""


class FactResolutionServiceError(EvidenceExtractionError):
    """Raised when trusted workflow inputs cannot be resolved."""

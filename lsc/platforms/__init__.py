"""Platform adapter public exports."""
from .base import (
    ERROR_OFFLINE,
    ERROR_PARSE_FAILED,
    ERROR_RESTRICTED,
    ERROR_UNSUPPORTED_URL,
    StreamInfo,
    headers_to_ffmpeg_input_args,
)
from .candidate_health import (
    CandidateHealthSnapshot,
    CandidateHealthStore,
    get_default_candidate_health_store,
)
from .capabilities import all_platform_capabilities, get_platform_capabilities
from .credentials import (
    CredentialContext,
    CredentialProvider,
    CredentialStatus,
    LegacyCredentialProvider,
    get_default_credential_provider,
)
from .failure import FailureKind, classify_failure, is_recoverable_failure
from .models import (
    PlatformCapabilities,
    PlatformError,
    ProbeRequest,
    ProbeResult,
    ResolveRequest,
    ResolveResult,
    StreamCandidate,
    StreamLease,
    resolve_result_to_stream_info,
)
from .probe import ProbeService, score_candidate, select_best_candidate
from .registry import detect_platform, parse_stream, select_quality
from .resolver import (
    probe_candidates,
    resolve_candidates,
    resolve_playable_lease,
    resolve_stream_v2,
    select_ingest_lease,
    select_stream_lease,
)
from .url_policy import validate_public_url, validate_redirect_chain

__all__ = [
    "ERROR_OFFLINE",
    "ERROR_PARSE_FAILED",
    "ERROR_RESTRICTED",
    "ERROR_UNSUPPORTED_URL",
    "StreamInfo",
    "CredentialContext",
    "CredentialProvider",
    "CredentialStatus",
    "LegacyCredentialProvider",
    "get_default_credential_provider",
    "FailureKind",
    "classify_failure",
    "is_recoverable_failure",
    "all_platform_capabilities",
    "get_platform_capabilities",
    "CandidateHealthSnapshot",
    "CandidateHealthStore",
    "get_default_candidate_health_store",
    "PlatformCapabilities",
    "PlatformError",
    "ProbeRequest",
    "ProbeResult",
    "ResolveRequest",
    "ResolveResult",
    "StreamCandidate",
    "StreamLease",
    "resolve_result_to_stream_info",
    "detect_platform",
    "headers_to_ffmpeg_input_args",
    "parse_stream",
    "select_quality",
    "ProbeService",
    "score_candidate",
    "select_best_candidate",
    "resolve_candidates",
    "resolve_playable_lease",
    "resolve_stream_v2",
    "probe_candidates",
    "select_ingest_lease",
    "select_stream_lease",
    "validate_public_url",
    "validate_redirect_chain",
]

"""bomscout: an MCP server for OWASP Dependency-Track.

Ask an AI assistant about your software portfolio: which projects a CVE affects and how far
remediation got, the riskiest projects, findings by severity, licenses, policy violations,
and where a package is used.
"""

__version__ = "0.1.0"

from .client import DependencyTrack  # noqa: E402
from .errors import DTError  # noqa: E402
from .scout import Scout  # noqa: E402

__all__ = ["DependencyTrack", "DTError", "Scout", "__version__"]

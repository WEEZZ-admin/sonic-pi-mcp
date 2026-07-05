class SonicPiMcpError(RuntimeError):
    """Base exception for this package."""


class SonicPiNotFoundError(SonicPiMcpError):
    """Raised when a usable Sonic Pi root cannot be found."""


class SonicPiBootError(SonicPiMcpError):
    """Raised when Sonic Pi cannot be started or made ready."""


class SonicPiProtocolError(SonicPiMcpError):
    """Raised when OSC or daemon output is malformed."""


class SonicPiStateError(SonicPiMcpError):
    """Raised when a requested action is invalid for the current session state."""


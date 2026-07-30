import datetime
import sys
from pathlib import Path

_PYTHON_CMD = Path(sys.executable).stem


class AlexaSessionExpiredException(Exception):
    """Raised when the Alexa session cookies have expired."""

    def __init__(self, expired_at: datetime.datetime) -> None:
        super().__init__(
            f"Alexa session expired on {expired_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            f" ({expired_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}) — "
            f"re-run {_PYTHON_CMD} -m pyalexalist.get_alexa_cookies to refresh"
        )
        self.expired_at = expired_at

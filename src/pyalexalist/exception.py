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


class DuplicateListNameException(Exception):
    """Raised when creating a list whose name already exists (Alexa disallows duplicate list names)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"A list named '{name}' already exists")
        self.name = name


class DefaultListModificationException(Exception):
    """Raised when attempting to delete, rename, or archive one of Alexa's built-in SHOP/TODO lists."""

    def __init__(self, name: str, action: str) -> None:
        super().__init__(f"Cannot {action} '{name}' — SHOP and TODO are built-in lists and cannot be modified this way")
        self.name = name
        self.action = action

"""Shared HTTP error type for API handlers.

Handlers raise HttpError; the router converts it to a JSON response.
Avoids sentinel-dict patterns that the router would need to introspect.
"""

from __future__ import annotations

from typing import Any


class HttpError(Exception):
    """Raise to short-circuit a handler with a specific HTTP status."""

    def __init__(self, status: int, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or {}

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.message}
        if self.details:
            body["details"] = self.details
        return body

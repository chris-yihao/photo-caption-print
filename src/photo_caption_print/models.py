from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PhotoMetadata:
    source: Path
    captured_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    device: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class ProcessResult:
    source: Path
    output: Path | None
    status: str
    warning: str = ""
    error: str = ""

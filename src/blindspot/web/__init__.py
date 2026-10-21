"""The web UI (#47): static HTML, CSS and JS served by the service, no build step (D48)."""

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

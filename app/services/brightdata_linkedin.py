from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

BRIGHTDATA_LINKEDIN_JOBS_DATASET_ID = "gd_lpfll7v5hcqtkxl6l"
BRIGHTDATA_SCRAPE_URL = "https://api.brightdata.com/datasets/v3/scrape"
DEFAULT_TIMEOUT_SECONDS = 45


class BrightDataError(RuntimeError):
    """Raised when the Bright Data LinkedIn jobs integration fails."""


def validate_linkedin_job_url(url: str) -> str:
    """Validate that the provided URL looks like a LinkedIn job posting."""
    normalized = (url or "").strip()
    if not normalized:
        raise BrightDataError("LinkedIn job URL is required.")

    parsed = urlparse(normalized)
    hostname = (parsed.netloc or "").lower()
    path = parsed.path or ""
    if parsed.scheme not in {"http", "https"}:
        raise BrightDataError("LinkedIn job URL must start with http:// or https://.")
    if "linkedin.com" not in hostname:
        raise BrightDataError("Only LinkedIn job posting URLs are supported.")
    if "/jobs/view/" not in path:
        raise BrightDataError("URL must point to a LinkedIn job posting.")
    return normalized


def fetch_linkedin_job_posting(
    linkedin_job_url: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """
    Fetch one LinkedIn job posting via Bright Data's synchronous scrape API.

    Bright Data returns a list of result objects for `/datasets/v3/scrape`,
    even when a single input URL is provided.
    """
    normalized_url = validate_linkedin_job_url(linkedin_job_url)
    api_key = os.getenv("BRIGHT_DATA_API_KEY")
    if not api_key:
        raise BrightDataError("BRIGHT_DATA_API_KEY is not configured.")

    payload = json.dumps([{"url": normalized_url}]).encode("utf-8")
    request = Request(
        url=f"{BRIGHTDATA_SCRAPE_URL}?dataset_id={BRIGHTDATA_LINKEDIN_JOBS_DATASET_ID}&format=json",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except Exception as exc:
        raise BrightDataError(f"Bright Data request failed: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrightDataError("Bright Data returned invalid JSON.") from exc

    if isinstance(parsed, dict) and parsed.get("error"):
        raise BrightDataError(str(parsed["error"]))
    if not isinstance(parsed, list) or not parsed:
        raise BrightDataError("Bright Data returned no LinkedIn job data.")
    if not isinstance(parsed[0], dict):
        raise BrightDataError("Bright Data returned an unexpected response shape.")

    return parsed[0]

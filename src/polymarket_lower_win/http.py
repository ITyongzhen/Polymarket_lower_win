from __future__ import annotations

import json
import subprocess
import time
from typing import Any, Dict
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Codex Polymarket Lower Win)",
    "Accept": "application/json, text/plain;q=0.9, text/html;q=0.8, */*;q=0.7",
}


def build_url(url: str, params: Dict[str, Any] | None = None) -> str:
    clean = {key: value for key, value in (params or {}).items() if value is not None}
    if not clean:
        return url
    return f"{url}?{urlencode(clean, doseq=True)}"


def http_get_text(
    url: str,
    *,
    params: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
    timeout_s: int = 20,
) -> str:
    final_url = build_url(url, params=params)
    request_headers = dict(DEFAULT_HEADERS)
    request_headers.update(headers or {})
    last_error: str = ""
    for attempt in range(4):
        try:
            curl = subprocess.run(
                [
                    "curl",
                    "-sSL",
                    "--http1.1",
                    "--retry",
                    "2",
                    "--retry-delay",
                    "1",
                    final_url,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            if curl.returncode == 0 and curl.stdout.strip():
                return curl.stdout
            last_error = f"curl_rc={curl.returncode}:{curl.stderr.strip()}"
        except Exception as exc:  # pragma: no cover - runtime dependent
            last_error = f"curl_failed:{type(exc).__name__}:{exc}"

        try:
            request = Request(final_url, headers=request_headers)
            with urlopen(request, timeout=timeout_s) as response:
                payload = response.read().decode("utf-8")
            if payload.strip():
                return payload
            last_error = "urlopen_empty_response"
        except Exception as exc:  # pragma: no cover - runtime dependent
            last_error = f"urlopen_failed:{type(exc).__name__}:{exc}"

        if attempt < 3:
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"http_get_text_failed:{last_error}:{final_url}")


def http_get_json(
    url: str,
    *,
    params: Dict[str, Any] | None = None,
    headers: Dict[str, str] | None = None,
    timeout_s: int = 20,
) -> Any:
    payload = http_get_text(url, params=params, headers=headers, timeout_s=timeout_s)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"http_get_json_invalid:{exc}:{build_url(url, params=params)}") from exc


def http_post_json(
    url: str,
    *,
    payload: Any,
    headers: Dict[str, str] | None = None,
    timeout_s: int = 20,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False)
    request_headers = dict(DEFAULT_HEADERS)
    request_headers.update({"Content-Type": "application/json"})
    request_headers.update(headers or {})
    last_error: str = ""
    for attempt in range(4):
        try:
            curl = subprocess.run(
                [
                    "curl",
                    "-sSL",
                    "--http1.1",
                    "--retry",
                    "2",
                    "--retry-delay",
                    "1",
                    "-X",
                    "POST",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    body,
                    url,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            if curl.returncode == 0 and curl.stdout.strip():
                return json.loads(curl.stdout)
            last_error = f"curl_rc={curl.returncode}:{curl.stderr.strip()}"
        except Exception as exc:  # pragma: no cover - runtime dependent
            last_error = f"curl_failed:{type(exc).__name__}:{exc}"

        try:
            request = Request(
                url,
                data=body.encode("utf-8"),
                headers=request_headers,
                method="POST",
            )
            with urlopen(request, timeout=timeout_s) as response:
                raw = response.read().decode("utf-8")
            if raw.strip():
                return json.loads(raw)
            last_error = "urlopen_empty_response"
        except Exception as exc:  # pragma: no cover - runtime dependent
            last_error = f"urlopen_failed:{type(exc).__name__}:{exc}"

        if attempt < 3:
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"http_post_json_failed:{last_error}:{url}")

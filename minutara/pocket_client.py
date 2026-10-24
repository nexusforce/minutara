"""
pocket_client.py — thin client for the Pocket Public API.

Two directions, both useful:

  PULL: your Pocket device recorded an in-person meeting or a phone call.
        list_recordings() / get_recording() / download_audio() bring it into
        the same local pipeline as your PC-captured calls.

  PUSH: you captured a Zoom/Teams call on your PC. upload_recording() injects
        that audio into Pocket so it lands in the app alongside your device
        recordings, gets Pocket's summary treatment, and becomes searchable
        via Ask Pocket / the Pocket MCP server.

Confirmed from the API docs:
  base URL   https://public.heypocketai.com/api/v1
  auth       Authorization: Bearer pk_xxx
  upload     POST /public/recordings/upload-url  -> pre-signed S3 URL
             body: {title, duration, file_name, recording_at}
             NOTE: personal user API keys only. Organization API keys are
             explicitly not allowed on the upload endpoint.
  list       GET  /public/recordings  (start_date, end_date, tag_ids, page, limit<=100)

UNVERIFIED — check these against a live key before you trust them:
  * exact shape of the `data` object in responses (docs render it as null)
  * whether the pre-signed PUT needs a specific Content-Type
  * whether uploaded audio is auto-transcribed or needs a nudge
  * whether their ToS permits non-device audio. Ask support first.

Requires: pip install requests
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://public.heypocketai.com/api/v1"


class PocketError(RuntimeError):
    pass


@dataclass
class PocketRecording:
    id: str
    title: str
    recorded_at: str
    raw: dict


class PocketClient:
    def __init__(self, api_key: str | None = None, timeout: int = 60,
                 base_url: str | None = None):
        self.api_key = api_key or os.environ.get("POCKET_API_KEY")
        if not self.api_key:
            raise PocketError(
                "No API key. Set POCKET_API_KEY (a personal pk_... key, not an org key)."
            )
        self.timeout = timeout
        # Configurable (Settings → Pocket) in case Pocket moves its API.
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
        )

    def _get(self, path: str, **params) -> dict:
        r = self.s.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        return self._unwrap(r)

    def _post(self, path: str, payload: dict) -> dict:
        r = self.s.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout)
        return self._unwrap(r)

    @staticmethod
    def _unwrap(r: requests.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            raise PocketError(f"HTTP {r.status_code}: non-JSON response: {r.text[:200]}")
        if not r.ok or not body.get("success", False):
            raise PocketError(f"HTTP {r.status_code}: {body.get('error') or body}")
        return body

    # ---------- PULL: device recordings in ----------

    def list_recordings(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
        page: int = 1,
    ) -> list[PocketRecording]:
        """Dates are YYYY-MM-DD and interpreted as UTC."""
        params = {"limit": min(limit, 100), "page": page}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date

        body = self._get("/public/recordings", **params)
        items = body.get("data") or []
        out = []
        for it in items:
            out.append(
                PocketRecording(
                    id=str(it.get("id", "")),
                    title=it.get("title") or "(untitled)",
                    recorded_at=it.get("recording_at") or it.get("created_at") or "",
                    raw=it,
                )
            )
        return out

    def get_recording(self, recording_id: str) -> dict:
        """Full details — transcript and summary live here if Pocket has processed it."""
        return self._get(f"/public/recordings/{recording_id}").get("data") or {}

    def download_audio(self, recording_id: str, dest: Path) -> Path:
        """Fetch the device's original audio so you can run it through local Whisper."""
        body = self._get(f"/public/recordings/{recording_id}/audio-download-url")
        data = body.get("data") or {}
        url = data.get("url") or data.get("download_url")
        if not url:
            raise PocketError(f"No download URL in response: {data}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=self.timeout) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        return dest

    # ---------- PUSH: PC-captured audio out ----------

    def upload_recording(
        self,
        audio_path: Path,
        title: str,
        duration_s: float,
        recorded_at: datetime | None = None,
    ) -> dict:
        """
        Two-step: ask Pocket for a pre-signed S3 URL, then PUT the bytes there.
        """
        recorded_at = recorded_at or datetime.now(timezone.utc)

        body = self._post(
            "/public/recordings/upload-url",
            {
                "title": title,
                "duration": int(duration_s),
                "file_name": audio_path.name,
                "recording_at": recorded_at.astimezone(timezone.utc).isoformat(),
            },
        )
        data = body.get("data") or {}
        upload_url = data.get("upload_url") or data.get("url")
        if not upload_url:
            raise PocketError(
                f"No upload URL returned — inspect the response shape: {data}"
            )

        with open(audio_path, "rb") as f:
            put = requests.put(
                upload_url,
                data=f,
                headers={"Content-Type": _content_type(audio_path)},
                timeout=None,
            )
        if not put.ok:
            raise PocketError(f"S3 PUT failed: HTTP {put.status_code} {put.text[:200]}")

        return data


def _content_type(p: Path) -> str:
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
    }.get(p.suffix.lower(), "application/octet-stream")

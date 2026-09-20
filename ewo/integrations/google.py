"""Google integration: OAuth (token stored in DB, auto-refreshed), calendar
read, and email sending via the Gmail API.

Design notes:
- Dedicated Google account; the OAuth app is published "In production" so the
  refresh token is long-lived. One-time consent via ``ewo google auth``.
- Tokens live in the ``oauth_tokens`` table — no file writes, keeping Docker
  overlay writes at zero.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ewo.config import GoogleConfig
from ewo.db.models import OAuthToken

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleAuthError(Exception):
    pass


def save_token(session: Session, token: dict[str, Any]) -> None:
    row = session.scalars(select(OAuthToken).where(OAuthToken.provider == "google")).first()
    if row is None:
        row = OAuthToken(provider="google", token_json=token)
        session.add(row)
    else:
        row.token_json = token
    session.commit()


def load_token(session: Session) -> dict[str, Any] | None:
    row = session.scalars(select(OAuthToken).where(OAuthToken.provider == "google")).first()
    return dict(row.token_json) if row is not None else None


def run_consent_flow(config: GoogleConfig) -> dict[str, Any]:  # pragma: no cover - interactive
    """Interactive one-time consent flow (opens a local browser)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": _TOKEN_URL,
            }
        },
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0)
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "expires_at": creds.expiry.isoformat() if creds.expiry else None,
    }


def _refresh(config: GoogleConfig, refresh_token: str) -> dict[str, Any]:
    response = httpx.post(
        _TOKEN_URL,
        data={
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise GoogleAuthError(f"token refresh failed: {response.text}")
    payload = response.json()
    expires_at = datetime.now(UTC) + timedelta(seconds=payload.get("expires_in", 3600))
    return {
        "access_token": payload["access_token"],
        "refresh_token": refresh_token,
        "expires_at": expires_at.isoformat(),
    }


def get_access_token(session: Session, config: GoogleConfig) -> str:
    """Return a valid access token, auto-refreshing (and persisting) if needed."""
    token = load_token(session)
    if token is None or not token.get("refresh_token"):
        raise GoogleAuthError("no google token stored — run `ewo google auth` first")

    expires_at = token.get("expires_at")
    if expires_at:
        expiry = datetime.fromisoformat(str(expires_at))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if expiry - timedelta(minutes=5) > datetime.now(UTC):
            return str(token["access_token"])

    refreshed = _refresh(config, str(token["refresh_token"]))
    save_token(session, refreshed)
    return str(refreshed["access_token"])


@dataclass
class CalendarEvent:
    summary: str
    start: str
    end: str


class GoogleClient(Protocol):
    def list_events_today(self) -> list[CalendarEvent]: ...

    def send_email(self, to: str, subject: str, body: str) -> None: ...


class HttpGoogleClient:
    """Calendar read + Gmail send over the REST APIs."""

    def __init__(self, session: Session, config: GoogleConfig) -> None:
        self._session = session
        self._config = config

    def _headers(self) -> dict[str, str]:
        token = get_access_token(self._session, self._config)
        return {"Authorization": f"Bearer {token}"}

    def list_events_today(self) -> list[CalendarEvent]:
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        response = httpx.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{self._config.calendar_id}/events",
            params={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
            },
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        events: list[CalendarEvent] = []
        for item in response.json().get("items", []):
            events.append(
                CalendarEvent(
                    summary=item.get("summary", ""),
                    start=(item.get("start") or {}).get("dateTime")
                    or (item.get("start") or {}).get("date", ""),
                    end=(item.get("end") or {}).get("dateTime")
                    or (item.get("end") or {}).get("date", ""),
                )
            )
        return events

    def send_email(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["To"] = to
        message["From"] = self._config.email_from
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        response = httpx.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": raw},
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()

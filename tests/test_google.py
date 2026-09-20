"""Google integration: token store/refresh, calendar, gmail (respx-mocked)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
import respx
from httpx import Response
from sqlalchemy.orm import Session

from ewo.config import GoogleConfig
from ewo.integrations.google import (
    GoogleAuthError,
    HttpGoogleClient,
    get_access_token,
    load_token,
    save_token,
)


def _config() -> GoogleConfig:
    return GoogleConfig(
        enabled=True,
        client_id="cid",
        client_secret="sec",
        email_from="ewo@x.com",
        email_to="me@x.com",
    )


def test_save_and_load_token(session: Session) -> None:
    assert load_token(session) is None
    save_token(session, {"refresh_token": "r1", "access_token": "a1"})
    token = load_token(session)
    assert token is not None and token["refresh_token"] == "r1"

    save_token(session, {"refresh_token": "r2", "access_token": "a2"})
    token = load_token(session)
    assert token is not None and token["refresh_token"] == "r2"


def test_get_access_token_missing(session: Session) -> None:
    with pytest.raises(GoogleAuthError, match="ewo google auth"):
        get_access_token(session, _config())


def test_get_access_token_valid_not_refreshed(session: Session) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    save_token(session, {"refresh_token": "r", "access_token": "still-good", "expires_at": future})
    assert get_access_token(session, _config()) == "still-good"


@respx.mock
def test_get_access_token_refreshes_expired(session: Session) -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    save_token(session, {"refresh_token": "r", "access_token": "old", "expires_at": past})
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=Response(200, json={"access_token": "fresh", "expires_in": 3600})
    )
    assert get_access_token(session, _config()) == "fresh"
    stored = load_token(session)
    assert stored is not None and stored["access_token"] == "fresh"
    assert stored["refresh_token"] == "r"  # preserved


@respx.mock
def test_refresh_failure_raises(session: Session) -> None:
    save_token(session, {"refresh_token": "r", "access_token": "old"})
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=Response(400, text="invalid_grant")
    )
    with pytest.raises(GoogleAuthError, match="refresh failed"):
        get_access_token(session, _config())


def _store_valid_token(session: Session) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    save_token(session, {"refresh_token": "r", "access_token": "tok", "expires_at": future})


@respx.mock
def test_list_events_today(session: Session) -> None:
    _store_valid_token(session)
    route = respx.get("https://www.googleapis.com/calendar/v3/calendars/primary/events").mock(
        return_value=Response(
            200,
            json={
                "items": [
                    {
                        "summary": "standup",
                        "start": {"dateTime": "2026-08-30T09:00:00Z"},
                        "end": {"dateTime": "2026-08-30T09:15:00Z"},
                    },
                    {"summary": "all-day", "start": {"date": "2026-08-30"}, "end": {}},
                ]
            },
        )
    )
    events = HttpGoogleClient(session, _config()).list_events_today()
    assert route.calls[0].request.headers["authorization"] == "Bearer tok"
    assert [e.summary for e in events] == ["standup", "all-day"]
    assert events[1].start == "2026-08-30"
    assert events[1].end == ""


@respx.mock
def test_send_email(session: Session) -> None:
    _store_valid_token(session)
    route = respx.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send").mock(
        return_value=Response(200, json={"id": "m1"})
    )
    HttpGoogleClient(session, _config()).send_email("me@x.com", "subject", "hello body")
    import json as jsonlib

    payload = jsonlib.loads(route.calls[0].request.content)
    decoded = base64.urlsafe_b64decode(payload["raw"]).decode()
    assert "To: me@x.com" in decoded
    assert "Subject: subject" in decoded
    assert "hello body" in decoded

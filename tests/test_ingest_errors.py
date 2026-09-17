"""Which HTTP failures `ingest.py` retries, and which it refuses to.

This file exists because of a four-day outage. On 2026-09-09 DataSF's retired
host began answering every request this module makes with a 403 from its edge
proxy. `_get_with_retries` treated it like a dropped connection: three attempts,
15 seconds of backoff, and a final `HTTPError` naming a 300-character
percent-encoded URL and nothing else. The job took 70 seconds to say nothing,
and the dev note written that week concluded from the empty `SOCRATA_APP_TOKEN`
in the runner env that the repository secret had been revoked. It had not. The
token was never involved, and the same 403 reproduces with no token at all.

So the behaviour pinned here is not "retry less". It is that a permanent
failure has to arrive as a sentence naming the things that separate its
possible causes -- which host answered, whether a token was sent, and whether
the body came from Socrata or from something sitting in front of it.

No network. Responses are built by hand, because the point is the branch taken
for a given status and body, not that the API can be reached.
"""

import time

import ingest
import pytest
import requests


def response(status: int, body: str, content_type: str, token: str | None = None):
    """A `requests.Response` shaped like the real one, with its request attached.

    `_check_response` reads `resp.request.headers`, so a bare Response is not
    enough: the token question it answers is about what was *sent*.
    """
    resp = requests.Response()
    resp.status_code = status
    resp._content = body.encode()
    resp.headers["Content-Type"] = content_type
    request = requests.Request(
        "GET",
        "https://example.invalid/resource/abcd-efgh.json",
        headers={"X-App-Token": token} if token else {},
    )
    resp.request = request.prepare()
    return resp


NGINX_403 = "<html>\r\n<head><title>403 Forbidden</title></head>\r\n<body>\r\n</body>\r\n</html>"
SOCRATA_TOKEN_403 = '{"message": "Invalid app_token provided."}'


# ---------------------------------------------------------------------------
# The outage, replayed
# ---------------------------------------------------------------------------


def test_a_proxy_403_says_it_is_not_the_token_and_names_the_host():
    """Every clause here is one the 2026-09-12 diagnosis got wrong."""
    with pytest.raises(RuntimeError) as caught:
        ingest._check_response(response(403, NGINX_403, "text/html"))
    message = str(caught.value)

    assert "403" in message
    assert ingest.SOCRATA_DOMAIN in message, "must name the host that answered"
    assert "Token sent: no" in message, "must close off the credentials theory"
    assert "SOCRATA_DOMAIN" in message, "must point at the thing to change"
    assert "retrying will not help" in message


def test_a_proxy_403_is_distinguished_from_socrata_refusing():
    """An HTML body means something in front of the API answered, not the API."""
    with pytest.raises(RuntimeError) as caught:
        ingest._check_response(response(403, NGINX_403, "text/html"))
    assert "in front of the API" in str(caught.value)

    with pytest.raises(RuntimeError) as caught:
        ingest._check_response(response(403, '{"message": "Forbidden."}', "application/json"))
    message = str(caught.value)
    assert "in front of the API" not in message
    assert "Socrata says" in message


def test_the_bad_token_case_still_wins_over_the_generic_one():
    """Most specific first: a token 403 must not be reported as a host problem."""
    with pytest.raises(RuntimeError) as caught:
        ingest._check_response(response(403, SOCRATA_TOKEN_403, "application/json", token="bad"))
    message = str(caught.value)
    assert "rejected the app token" in message
    assert "Secret Token" in message, "names the specific paste-the-wrong-value cause"
    assert "SOCRATA_DOMAIN" not in message, "must not send the reader to the host"


def test_a_token_that_was_sent_is_reported_as_sent():
    with pytest.raises(RuntimeError) as caught:
        ingest._check_response(response(403, NGINX_403, "text/html", token="present"))
    assert "Token sent: yes" in str(caught.value)


# ---------------------------------------------------------------------------
# The line between terminal and transient
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(ingest.TERMINAL_STATUSES))
def test_terminal_statuses_raise(status):
    with pytest.raises(RuntimeError):
        ingest._check_response(response(status, NGINX_403, "text/html"))


@pytest.mark.parametrize("status", [200, 429, 500, 502, 503])
def test_backoff_keeps_the_statuses_backoff_is_for(status):
    """429 and 5xx must stay retryable: those are the ones waiting fixes."""
    assert ingest._check_response(response(status, "{}", "application/json")) is None


def test_a_terminal_failure_short_circuits_the_retry_loop(monkeypatch):
    """The 15 seconds of backoff that made the outage expensive to read."""
    calls = []

    class FakeSession:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append(url)
            return response(403, NGINX_403, "text/html")

    monkeypatch.setattr(time, "sleep", lambda seconds: pytest.fail("slept on a 403"))

    with pytest.raises(RuntimeError):
        ingest._get_with_retries(FakeSession(), "https://example.invalid", {}, {})

    assert len(calls) == 1, f"tried {len(calls)} times; a 403 is permanent"


def test_a_transient_failure_still_retries(monkeypatch):
    """The negative control: without this, 'stop retrying' could just be a break."""
    calls = []
    slept = []

    class FlakySession:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append(url)
            if len(calls) < 3:
                raise requests.ConnectionError("connection reset")
            return response(200, "[]", "application/json")

    monkeypatch.setattr(time, "sleep", slept.append)

    assert ingest._get_with_retries(FlakySession(), "https://example.invalid", {}, {}) == []
    assert len(calls) == 3
    assert slept == [5, 10], "backoff still grows between attempts"

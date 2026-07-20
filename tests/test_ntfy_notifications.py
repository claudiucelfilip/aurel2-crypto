import httpx

from aurel2_crypto.notifications.ntfy import NtfyNotifier


def test_send_passes_explicit_timeout(monkeypatch):
    calls = []

    def fake_post(url, content, headers, timeout):
        calls.append(
            {
                "url": url,
                "content": content,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "post", fake_post)

    notifier = NtfyNotifier(
        topic="test-topic",
        server="https://example.test/",
        timeout_seconds=3.5,
    )

    assert notifier.send("hello", title="Title", tags=["one", "two"]) is True
    assert calls == [
        {
            "url": "https://example.test/test-topic",
            "content": "hello",
            "headers": {"Title": "Title", "Tags": "one,two"},
            "timeout": 3.5,
        }
    ]


def test_send_reports_timeout_as_false(monkeypatch):
    def fake_post(url, content, headers, timeout):
        raise httpx.ReadTimeout("ntfy timed out")

    monkeypatch.setattr(httpx, "post", fake_post)

    notifier = NtfyNotifier(topic="test-topic", timeout_seconds=1.0)

    assert notifier.send("hello") is False

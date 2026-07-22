from urllib.error import URLError

from app.modules import website_parser


class FakeResponse:
    def __init__(self, body: str, url: str, content_type: str = "text/html"):
        self.body = body
        self.url = url
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=None):
        return self.body.encode("utf-8")

    def geturl(self):
        return self.url


def test_parse_website_collects_public_contact_page_emails(monkeypatch):
    pages = {
        "https://example.com/robots.txt": FakeResponse("User-agent: *\nAllow: /", "https://example.com/robots.txt", "text/plain"),
        "https://example.com": FakeResponse('<html><title>Example</title><a href="/contact">Contact us</a>info@example.com</html>', "https://example.com"),
        "https://example.com/contact": FakeResponse('<html><a href="mailto:sales@example.com">Email sales</a>jane@example.com</html>', "https://example.com/contact"),
    }

    def fake_urlopen(request, timeout):
        assert timeout == 15
        return pages[request.full_url]

    monkeypatch.setattr(website_parser, "urlopen", fake_urlopen)

    result = website_parser.parse_website("example.com")

    emails = {item.address: item for item in result.emails}
    assert result.error is None
    assert result.pages_scanned == 2
    assert emails["info@example.com"].url == "https://example.com"
    assert emails["sales@example.com"].source == "mailto"
    assert emails["sales@example.com"].url == "https://example.com/contact"
    assert emails["jane@example.com"].url == "https://example.com/contact"


def test_parse_website_honors_robots_disallow(monkeypatch):
    def fake_urlopen(request, timeout):
        assert request.full_url == "https://example.com/robots.txt"
        return FakeResponse("User-agent: *\nDisallow: /", "https://example.com/robots.txt", "text/plain")

    monkeypatch.setattr(website_parser, "urlopen", fake_urlopen)

    result = website_parser.parse_website("example.com")

    assert result.error == "https://example.com: Disallowed by robots.txt"
    assert result.pages_scanned == 0


def test_parse_website_tries_www_variant_after_connection_error(monkeypatch):
    requested_urls = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if request.full_url == "https://example.com/robots.txt":
            return FakeResponse("User-agent: *\nAllow: /", request.full_url, "text/plain")
        if request.full_url == "https://example.com":
            raise URLError("TLS handshake failed")
        if request.full_url == "https://www.example.com/robots.txt":
            return FakeResponse("User-agent: *\nAllow: /", request.full_url, "text/plain")
        if request.full_url == "https://www.example.com":
            return FakeResponse("<html><title>Working site</title>hello@www.example.com</html>", request.full_url)
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    monkeypatch.setattr(website_parser, "urlopen", fake_urlopen)

    result = website_parser.parse_website("example.com")

    assert result.error is None
    assert result.url == "https://www.example.com"
    assert result.attempted_urls == ["https://example.com", "https://www.example.com"]
    assert "https://www.example.com" in requested_urls

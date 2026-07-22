"""Public website contact-page parser."""

import hashlib
import html
import re
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser


USER_AGENT = "BuyerReachContactResearch/1.0 (+https://buyerreach.local)"
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
MAILTO_RE = re.compile(r"""href=["']mailto:([^"']+)["']""", re.IGNORECASE)
PHONE_RE = re.compile(r"""href=["']tel:([^"']+)["']""", re.IGNORECASE)
ANCHOR_RE = re.compile(r"""<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", re.IGNORECASE | re.DOTALL)
SOCIAL_PATTERNS: dict[str, re.Pattern] = {
    "linkedin": re.compile(r"""https?://(?:www\.)?linkedin\.com/[^\s"'<>]+""", re.IGNORECASE),
    "facebook": re.compile(r"""https?://(?:www\.)?facebook\.com/[^\s"'<>]+""", re.IGNORECASE),
    "twitter": re.compile(r"""https?://(?:www\.)?(?:twitter|x)\.com/[^\s"'<>]+""", re.IGNORECASE),
    "instagram": re.compile(r"""https?://(?:www\.)?instagram\.com/[^\s"'<>]+""", re.IGNORECASE),
}
CONTACT_PAGE_TOKENS = ("contact", "about", "team", "wholesale", "trade", "dealer", "support", "help", "enquiry", "inquiry")
GENERIC_PREFIXES = {"info", "contact", "hello", "support", "admin", "service", "help", "sales", "marketing", "press", "media", "careers", "jobs", "hr", "office", "enquiries", "enquiry"}
SALES_PREFIXES = {"sales", "orders", "order", "wholesale", "trade", "dealer", "b2b", "business"}


@dataclass
class ParsedEmail:
    address: str
    type: str = "personal"
    source: str = "regex"
    confidence: int = 60
    url: str = ""


@dataclass
class WebsiteParseResult:
    url: str
    domain: str
    emails: list[ParsedEmail] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    social_links: dict[str, str] = field(default_factory=dict)
    page_title: str = ""
    text_snippet: str = ""
    content_hash: str = ""
    error: str | None = None
    elapsed_ms: int = 0
    pages_scanned: int = 0
    attempted_urls: list[str] = field(default_factory=list)


def parse_website(url: str, timeout: int = 15, max_pages: int = 4) -> WebsiteParseResult:
    normalised_url = _normalise_url(url)
    result = WebsiteParseResult(url=normalised_url, domain=_domain_from_url(normalised_url))
    started = time.monotonic()
    homepage: str | None = None
    homepage_url = normalised_url
    errors: list[str] = []
    for candidate_url in _homepage_candidates(normalised_url):
        result.attempted_urls.append(candidate_url)
        if not _robots_allows(candidate_url, timeout):
            errors.append(f"{candidate_url}: Disallowed by robots.txt")
            break
        candidate_html, final_url, error = _fetch_html(candidate_url, timeout)
        if error:
            errors.append(f"{candidate_url}: {error}")
            continue
        homepage = candidate_html
        homepage_url = final_url
        break
    if homepage is None:
        result.error = "; ".join(errors)[:500] or "Unable to fetch public website"
        result.elapsed_ms = _elapsed_ms(started)
        return result

    result.url = homepage_url
    result.domain = _domain_from_url(homepage_url)
    html_pages = [homepage]
    _merge_page(result, homepage, homepage_url)
    for contact_url in _contact_page_urls(homepage, homepage_url, result.domain, max_pages - 1):
        result.attempted_urls.append(contact_url)
        if not _robots_allows(contact_url, timeout):
            continue
        contact_page, final_url, contact_error = _fetch_html(contact_url, timeout)
        if contact_error:
            continue
        html_pages.append(contact_page)
        _merge_page(result, contact_page, final_url)

    result.content_hash = hashlib.sha256("\n".join(html_pages).encode("utf-8", errors="replace")).hexdigest()
    result.elapsed_ms = _elapsed_ms(started)
    return result


def _fetch_html(url: str, timeout: int) -> tuple[str, str, str | None]:
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"})
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return "", url, f"Unsupported content type: {content_type}"
            raw = response.read(1_000_000)
            final_url = response.geturl()
        return raw.decode("utf-8", errors="replace"), final_url, None
    except HTTPError as exc:
        return "", url, f"HTTP {exc.code}: {exc.reason}"
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        return "", url, str(exc)[:500]


def _robots_allows(url: str, timeout: int) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        request = Request(robots_url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.8"})
        with urlopen(request, timeout=timeout) as response:
            lines = response.read(200_000).decode("utf-8", errors="replace").splitlines()
    except HTTPError as exc:
        return exc.code == 404
    except (URLError, TimeoutError, OSError, ValueError):
        return True
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(lines)
    return parser.can_fetch(USER_AGENT, url)


def _merge_page(result: WebsiteParseResult, page_html: str, page_url: str) -> None:
    result.pages_scanned += 1
    page_title = _title(page_html)
    if not result.page_title and page_title:
        result.page_title = page_title
    page_text = _text(page_html)
    if len(result.text_snippet) < 2_000:
        result.text_snippet = f"{result.text_snippet} {page_text}".strip()[:2_000]
    for parsed_email in _emails(page_html, page_text, page_url):
        _add_email(result, parsed_email)
    for phone in _phones(page_html):
        if phone not in result.phones and len(result.phones) < 20:
            result.phones.append(phone)
    for platform, pattern in SOCIAL_PATTERNS.items():
        match = pattern.search(page_html)
        if match and platform not in result.social_links:
            result.social_links[platform] = match.group(0)


def _emails(page_html: str, page_text: str, page_url: str) -> list[ParsedEmail]:
    found: list[ParsedEmail] = []
    mailto_addresses: set[str] = set()
    for match in MAILTO_RE.finditer(page_html):
        address = match.group(1).split("?", 1)[0].strip().lower()
        if EMAIL_RE.fullmatch(address):
            mailto_addresses.add(address)
            found.append(ParsedEmail(address=address, type=_classify_email(address), source="mailto", confidence=90, url=page_url))
    for match in EMAIL_RE.finditer(page_text):
        address = match.group(0).strip().lower()
        if address not in mailto_addresses and not _looks_like_image_name(address, page_html):
            found.append(ParsedEmail(address=address, type=_classify_email(address), source="regex", confidence=60, url=page_url))
    return found


def _add_email(result: WebsiteParseResult, parsed_email: ParsedEmail) -> None:
    for index, existing in enumerate(result.emails):
        if existing.address == parsed_email.address:
            if parsed_email.confidence > existing.confidence:
                result.emails[index] = parsed_email
            return
    if len(result.emails) < 50:
        result.emails.append(parsed_email)


def _phones(page_html: str) -> list[str]:
    phones = []
    for match in PHONE_RE.finditer(page_html):
        phone = match.group(1).strip()
        if phone and len(phone) >= 7 and phone not in phones:
            phones.append(phone)
    return phones


def _contact_page_urls(page_html: str, base_url: str, domain: str, limit: int) -> list[str]:
    candidates = []
    for href, label in ANCHOR_RE.findall(page_html):
        candidate = urldefrag(urljoin(base_url, html.unescape(href).strip()))[0]
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not _same_domain(parsed.hostname or "", domain):
            continue
        searchable = f"{parsed.path} {re.sub(r'<[^>]+>', ' ', label)}".lower()
        if not any(token in searchable for token in CONTACT_PAGE_TOKENS):
            continue
        if candidate not in candidates:
            candidates.append(candidate)
        if len(candidates) >= max(limit, 0):
            break
    return candidates


def _homepage_candidates(url: str) -> list[str]:
    candidates = [url]
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not hostname:
        return candidates
    alternate_host = hostname.removeprefix("www.") if hostname.startswith("www.") else f"www.{hostname}"
    try:
        port_suffix = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return candidates
    alternate_url = parsed._replace(netloc=f"{alternate_host}{port_suffix}").geturl()
    if alternate_url not in candidates:
        candidates.append(alternate_url)
    return candidates


def _same_domain(hostname: str, domain: str) -> bool:
    normalized_host = hostname.removeprefix("www.").lower()
    return normalized_host == domain or normalized_host.endswith(f".{domain}")


def _title(page_html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1).strip())[:255] if match else ""


def _text(page_html: str) -> str:
    content = re.sub(r"<script[^>]*>.*?</script>", " ", page_html, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<style[^>]*>.*?</style>", " ", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"\s+", " ", html.unescape(content)).strip()


def _normalise_url(url: str) -> str:
    value = url.strip()
    return value if value.startswith(("http://", "https://")) else f"https://{value}"


def _domain_from_url(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.").lower()


def _classify_email(address: str) -> str:
    local_part = address.split("@", 1)[0].lower()
    if local_part in SALES_PREFIXES:
        return "sales"
    if local_part in GENERIC_PREFIXES:
        return "generic"
    return "personal"


def _looks_like_image_name(address: str, page_html: str) -> bool:
    local_part = address.split("@", 1)[0]
    if re.search(r"\.(png|jpg|jpeg|gif|svg|webp|ico)$", local_part, re.IGNORECASE):
        return True
    return bool(re.search(rf"<img[^>]+{re.escape(address)}", page_html, re.IGNORECASE))


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)

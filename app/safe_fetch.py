"""Public-only HTTP GET with DNS pinning, bounded bodies and checked redirects.

Connect to the validated IP, retaining the original TLS identity/Host header.
This avoids a second hostname lookup between validation and connection (rebinding).
No cookies, environment proxies, credentials, retries, or page scripts are used.
"""
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import certifi
import urllib3

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 3
FETCH_SECONDS = 20


class ExtractionError(Exception):
    def __init__(self, message: str, status: int = 422):
        self.status = status
        super().__init__(message)


def validate_url(raw: str) -> str:
    if (len(raw) > 2048 or re.search(r"[\x00-\x20\x7f\\]", raw)
            or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", raw, re.I)):
        raise ExtractionError("Use a public HTTP or HTTPS job link without control characters.")
    try:
        parts = urlsplit(raw)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError()
        if parts.username is not None or parts.password is not None:
            raise ValueError()
        host = parts.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if not host or "%" in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home", ".test", ".invalid")):
            raise ValueError()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port != (443 if parts.scheme == "https" else 80):
            raise ValueError()
        if ":" not in host and not re.fullmatch(r"[a-z0-9.-]+", host):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                raise ValueError()
        else:
            ensure_public(str(address))
        authority = f"[{host}]" if ":" in host else host
        return urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        raise ExtractionError("Only public HTTP/HTTPS links on standard ports are supported; no login credentials or local addresses.") from None


def ensure_public(address: str):
    ip = ipaddress.ip_address(address)
    # Reject special IPv6 translation/tunnelling forms as well as private targets.
    if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_loopback
            or ip.is_link_local or ip.is_unspecified
            or (ip.version == 6 and (ip.ipv4_mapped or ip.sixtofour or ip.teredo
                or ip in ipaddress.ip_network("64:ff9b::/96")))):
        raise ExtractionError("This link resolves to a private or special-use address and cannot be fetched.")


def resolve_public(host: str, port: int) -> str:
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM)))
    except OSError:
        raise ExtractionError("The job site's address could not be resolved. Check the link or enter details manually.", 502) from None
    if not addresses:
        raise ExtractionError("The job site's address could not be resolved.", 502)
    for address in addresses:
        ensure_public(address)
    # Prefer IPv4 when available, since some local networks lack usable IPv6.
    return next((a for a in addresses if ":" not in a), addresses[0])


@dataclass(frozen=True)
class Page:
    final_url: str
    body: bytes
    content_type: str


def open_connection(url: str, address: str, remaining: float):
    parts = urlsplit(url)
    host = parts.hostname
    timeout = urllib3.Timeout(connect=min(4, remaining), read=min(5, remaining))
    kwargs = {"port": 443 if parts.scheme == "https" else 80, "timeout": timeout,
              "retries": False}
    if parts.scheme == "https":
        pool = urllib3.HTTPSConnectionPool(address, server_hostname=host,
            assert_hostname=host, cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(), **kwargs)
    else:
        pool = urllib3.HTTPConnectionPool(address, **kwargs)
    target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    host_header = f"[{host}]" if ":" in host else host
    try:
        response = pool.urlopen("GET", target, headers={"Host": host_header,
            "User-Agent": "JobReviewLocal/0.2 (public job metadata reader)",
            "Accept": "text/html,application/json,application/ld+json", "Accept-Encoding": "identity"},
            redirect=False, retries=False, preload_content=False, assert_same_host=False)
        return pool, response
    except Exception:
        pool.close()
        raise


def fetch_public(raw: str) -> Page:
    current = validate_url(raw)
    deadline = time.monotonic() + FETCH_SECONDS
    for hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        address = resolve_public(parts.hostname, 443 if parts.scheme == "https" else 80)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExtractionError("The job site took too long to respond. Try again or enter details manually.", 504)
        pool = response = None
        try:
            pool, response = open_connection(current, address, remaining)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location or hop == MAX_REDIRECTS:
                    raise ExtractionError("The link has too many redirects or no usable destination.")
                next_url = validate_url(urljoin(current, location))
                if parts.scheme == "https" and urlsplit(next_url).scheme != "https":
                    raise ExtractionError("The link redirects from HTTPS to an insecure HTTP page.")
                current = next_url
                continue
            if response.status in (401, 403, 429):
                raise ExtractionError("This site requires login, blocks automated reading, or is rate-limiting requests. Use the public listing or enter details manually.")
            if response.status in (404, 410):
                raise ExtractionError("The job page was not found or has been removed. Check the link or enter details manually.")
            if response.status != 200:
                raise ExtractionError("The job site returned an unsuccessful response. Try again later or enter details manually.", 502)
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if mime not in ("text/html", "application/xhtml+xml", "application/json", "application/ld+json"):
                raise ExtractionError("This link is not a supported HTML or JSON job page.")
            # Reject compression rather than risking a decompression bomb. Most servers
            # honor identity. Compressed-only sites use the manual fallback for now.
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ExtractionError("This site returned an unsupported compressed response. Enter details manually.")
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > MAX_BYTES:
                raise ExtractionError("This page is too large to extract safely. Enter details manually.")
            chunks, total = [], 0
            while True:
                # read1 returns after a socket read instead of waiting to fill a large
                # buffer, so slowly trickled responses cannot hide the deadline.
                chunk = response.read1(16384, decode_content=False)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ExtractionError("This page is too large to extract safely. Enter details manually.")
                if time.monotonic() > deadline:
                    raise ExtractionError("The job site took too long to respond. Try again later.", 504)
                chunks.append(chunk)
            return Page(current, b"".join(chunks), mime)
        except (urllib3.exceptions.HTTPError, OSError, ValueError):
            raise ExtractionError("Could not securely read the job page. Check the link, network, or enter details manually.", 502) from None
        finally:
            if response is not None:
                response.close()
            if pool is not None:
                pool.close()
    raise ExtractionError("The link has too many redirects.")

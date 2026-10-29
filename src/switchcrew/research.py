"""Bounded, extractive research over operator-configured public HTTPS sources.

No model claims or invented citations: results contain matching source excerpts.
DNS is validated and the connection is pinned to that address to resist rebinding.
"""

import asyncio
import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit


class ResearchError(ValueError):
    pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def retrieve_https(url: str, max_bytes: int = 262144, timeout: float = 10) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise ResearchError("Only public HTTPS URLs on port 443 are allowed")
    addresses = {
        entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    }
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ResearchError("Source resolves to a non-public address")
    connection = _PinnedHTTPS(parsed.hostname, sorted(addresses)[0], timeout)
    try:
        connection.request(
            "GET",
            parsed.path + ("?" + parsed.query if parsed.query else "") or "/",
            headers={"User-Agent": "SwitchCrew-research/0.1", "Accept-Encoding": "identity"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ResearchError(f"Source returned HTTP {response.status}; redirects are disabled")
        content_type = response.getheader("Content-Type", "")
        if not any(
            kind in content_type.lower() for kind in ("text/plain", "text/html", "application/json")
        ):
            raise ResearchError("Unsupported source content type")
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ResearchError("Source exceeds byte limit")
        text = raw.decode("utf-8", errors="replace")
        if "html" in content_type.lower():
            parser = _Text()
            parser.feed(text)
            text = " ".join(parser.parts)
        return re.sub(r"\s+", " ", text).strip()
    finally:
        connection.close()


@dataclass
class ResearchProvider:
    source_urls: list[str]
    max_sources: int = 5
    fetch: object = retrieve_https

    async def investigate(self, question: str, acceptance_criteria: list[str]) -> dict:
        if (
            not question.strip()
            or not acceptance_criteria
            or any(not c.strip() for c in acceptance_criteria)
        ):
            raise ResearchError("A research question and explicit acceptance criteria are required")
        terms = {word.lower() for word in re.findall(r"\w{3,}", question)}
        evidence, errors = [], []
        for url in self.source_urls[: self.max_sources]:
            try:
                text = await asyncio.to_thread(self.fetch, url)
                sentences = re.split(r"(?<=[.!?])\s+", text)
                ranked = sorted(
                    enumerate(sentences),
                    key=lambda item: -sum(term in item[1].lower() for term in terms),
                )
                matches = [
                    sentence[:1200]
                    for _, sentence in ranked[:3]
                    if any(term in sentence.lower() for term in terms)
                ]
                if matches:
                    evidence.append({"url": url, "excerpts": matches})
            except (OSError, ValueError, http.client.HTTPException) as exc:
                errors.append({"url": url, "error": str(exc)[:300]})
        return {
            "status": "evidence_found" if evidence else "blocked",
            "question": question,
            "acceptance_criteria": acceptance_criteria,
            "evidence": evidence,
            "errors": errors,
            "limitations": (
                "Extractive matches from configured sources only; "
                "acceptance criteria require human assessment."
            ),
        }

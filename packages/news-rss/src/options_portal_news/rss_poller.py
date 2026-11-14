"""RSS news poller with symbol tagging heuristics."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Sequence
from xml.etree import ElementTree

import httpx

from options_portal_domain.types import NewsItem

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL_KEYWORDS = {
    "NIFTY": ["nifty", "nifty50", "benchmark"],
    "BANKNIFTY": ["bank nifty", "banknifty", "banking index"],
    "RELIANCE": ["reliance", "ril"],
    "TCS": ["tcs", "tata consultancy"],
}


class RssNewsIngestor:
    def __init__(
        self,
        source: str,
        url: str,
        *,
        symbol_keywords: dict[str, list[str]] | None = None,
        timeout_sec: float = 15.0,
    ) -> None:
        self.source = source
        self.url = url
        self.symbol_keywords = symbol_keywords or DEFAULT_SYMBOL_KEYWORDS
        self.timeout_sec = timeout_sec

    async def fetch_since(self, since: datetime) -> Sequence[NewsItem]:
        if "REPLACE_WITH" in self.url:
            logger.warning("Skipping placeholder RSS source %s", self.source)
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.get(self.url)
                resp.raise_for_status()
                return self._parse(resp.text, since)
        except Exception as exc:  # noqa: BLE001 — quarantine per source
            logger.exception("RSS fetch failed for %s: %s", self.source, exc)
            return []

    def _parse(self, xml_text: str, since: datetime) -> list[NewsItem]:
        items: list[NewsItem] = []
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError:
            logger.warning("Invalid RSS XML from %s", self.source)
            return []

        # RSS 2.0 <item> or Atom <entry>
        nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for node in nodes:
            title = _text(node, "title") or ""
            body = _text(node, "description") or _text(node, "summary") or ""
            link = _text(node, "link") or _attr_link(node)
            pub = _parse_date(_text(node, "pubDate") or _text(node, "published") or "")
            if pub and pub < since:
                continue
            published_at = pub or datetime.now(tz=timezone.utc)
            content_hash = hashlib.sha256(f"{title}|{link}".encode()).hexdigest()
            symbols = _tag_symbols(f"{title} {body}", self.symbol_keywords)
            items.append(
                NewsItem(
                    id=content_hash[:32],
                    source=self.source,
                    title=title.strip(),
                    body=body.strip()[:2000],
                    published_at=published_at,
                    symbols=symbols,
                    url=link,
                )
            )
        return items


class MultiSourceNewsPoller:
    def __init__(self, sources: Sequence[dict[str, Any]]) -> None:
        self.ingestors = [
            RssNewsIngestor(source=s["id"], url=s["url"])
            for s in sources
            if s.get("type", "rss") == "rss"
        ]

    async def fetch_all_since(self, since: datetime) -> list[NewsItem]:
        out: list[NewsItem] = []
        for ing in self.ingestors:
            out.extend(await ing.fetch_since(since))
        return out


def _text(node: ElementTree.Element, tag: str) -> str | None:
    child = node.find(tag)
    if child is not None and child.text:
        return child.text
    # Atom namespaced
    child = node.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
    if child is not None and child.text:
        return child.text
    return None


def _attr_link(node: ElementTree.Element) -> str | None:
    link = node.find("{http://www.w3.org/2005/Atom}link")
    if link is not None:
        return link.attrib.get("href")
    return None


def _parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    # Best-effort; RSS dates vary — full parser can use email.utils later
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(raw).astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def _tag_symbols(text: str, mapping: dict[str, list[str]]) -> list[str]:
    lower = text.lower()
    hits = [sym for sym, kws in mapping.items() if any(k in lower for k in kws)]
    return hits

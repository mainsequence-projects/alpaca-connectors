from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests

from etf_extraction.extractors.browser import (
    capture_response_text_with_playwright,
    fetch_page_html_with_playwright,
)
from etf_extraction.extractors.common import HoldingsExtractor, parse_html_table_tickers
from etf_extraction.settings import InvescoHoldingsSource, get_invesco_holdings_source


class InvescoHoldingsExtractor(HoldingsExtractor):
    _about_href_pattern = re.compile(
        r'href=["\'](?P<href>[^"\']*/about\.html(?:#[^"\']*)?)["\']',
        re.IGNORECASE,
    )
    _holdings_api_pattern = re.compile(
        r'data-[^=]*holding(?:s)?-api=["\'](?P<url>https://[^"\']+/holdings/fund[^"\']*)["\']',
        re.IGNORECASE,
    )
    _holdings_api_template_pattern = re.compile(
        r"https://dng-api\.invesco\.com/cache/v1/accounts/\{locale\}/shareclasses/\{id\}/holdings/fund\?idType=\{uniqueIdentifier\}[^\"']*productType=ETF",
        re.IGNORECASE,
    )
    _meta_content_pattern_template = r'<meta\s+name="{name}"\s+content="(?P<value>[^"]+)"'
    _json_value_pattern_template = r'"{name}"\s*:\s*"(?P<value>[^"]+)"'

    def __init__(
        self,
        *,
        requests_session: requests.Session | None = None,
        browser_fetcher: Callable[..., str] | None = None,
        browser_response_fetcher: Callable[..., str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.requests_session = requests_session or requests.Session()
        self.browser_fetcher = browser_fetcher or fetch_page_html_with_playwright
        self.browser_response_fetcher = (
            browser_response_fetcher or capture_response_text_with_playwright
        )
        self.timeout = timeout

    def supports_seed_ticker(self, seed_ticker: str) -> bool:
        return bool(seed_ticker.strip())

    def fetch_holdings_page_via_requests(self, seed_ticker: str) -> str:
        source = get_invesco_holdings_source(seed_ticker)
        if source is None:
            raise ValueError(f"Could not build an Invesco holdings source for {seed_ticker}.")

        try:
            response = self.requests_session.get(
                source.landing_url,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to fetch Invesco holdings for {seed_ticker} from {source.landing_url}"
            ) from exc

        return response.text

    def fetch_holdings_page_via_browser(self, seed_ticker: str) -> str:
        source = get_invesco_holdings_source(seed_ticker)
        if source is None:
            raise ValueError(f"Could not build an Invesco holdings source for {seed_ticker}.")
        return self.browser_fetcher(
            source.landing_url,
            wait_for_text="Excel Download",
            timeout=self.timeout,
        )

    @staticmethod
    def normalize_holdings_api_url(api_url: str) -> str:
        parsed = urlparse(html.unescape(api_url))
        query_items = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key != "loadType"
        ]
        return urlunparse(parsed._replace(query=urlencode(query_items)))

    @classmethod
    def extract_holdings_api_url(cls, page_html: str) -> str | None:
        match = cls._holdings_api_pattern.search(page_html)
        if match is None:
            return None
        return cls.normalize_holdings_api_url(match.group("url"))

    @classmethod
    def _extract_meta_content(cls, *, page_html: str, name: str) -> str | None:
        match = re.search(
            cls._meta_content_pattern_template.format(name=re.escape(name)),
            page_html,
            re.IGNORECASE,
        )
        if match is None:
            return None
        return html.unescape(match.group("value")).strip()

    @classmethod
    def _extract_json_value(cls, *, page_html: str, name: str) -> str | None:
        unescaped_html = html.unescape(page_html)
        match = re.search(
            cls._json_value_pattern_template.format(name=re.escape(name)),
            unescaped_html,
            re.IGNORECASE,
        )
        if match is None:
            return None
        return match.group("value").strip()

    @classmethod
    def extract_holdings_api_url_from_model(cls, page_html: str) -> str | None:
        unescaped_html = html.unescape(page_html)
        template_match = cls._holdings_api_template_pattern.search(unescaped_html)
        if template_match is None:
            return None

        unique_identifier = cls._extract_json_value(
            page_html=page_html,
            name="uniqueIdentifier",
        )
        locale = cls._extract_json_value(page_html=page_html, name="locale") or "en_US"
        ticker = cls._extract_meta_content(page_html=page_html, name="ticker")
        shareclass = cls._extract_meta_content(page_html=page_html, name="shareclass")
        cusip = cls._extract_json_value(page_html=page_html, name="cusip")

        identifier_values = {
            "ticker": ticker,
            "cusip": cusip,
            "shareClassIdentifier": shareclass,
        }
        identifier_value = identifier_values.get(unique_identifier or "")
        if not unique_identifier or not identifier_value:
            return None

        return cls.normalize_holdings_api_url(
            template_match.group(0)
            .replace("{locale}", locale)
            .replace("{id}", identifier_value)
            .replace("{uniqueIdentifier}", unique_identifier)
        )

    @classmethod
    def extract_about_page_url(cls, *, page_html: str, page_url: str) -> str | None:
        match = cls._about_href_pattern.search(page_html)
        if match is None:
            return None
        return urljoin(page_url, html.unescape(match.group("href")))

    def resolve_holdings_api_url(self, seed_ticker: str) -> str:
        source = get_invesco_holdings_source(seed_ticker)
        if source is None:
            raise ValueError(f"Could not build an Invesco holdings source for {seed_ticker}.")

        try:
            landing_response = self.requests_session.get(
                source.landing_url,
                timeout=self.timeout,
            )
            landing_response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to fetch Invesco holdings landing page for {seed_ticker} "
                f"from {source.landing_url}"
            ) from exc

        holdings_api_url = self.extract_holdings_api_url(landing_response.text)
        if holdings_api_url is not None:
            return holdings_api_url

        holdings_api_url = self.extract_holdings_api_url_from_model(landing_response.text)
        if holdings_api_url is not None:
            return holdings_api_url

        about_page_url = self.extract_about_page_url(
            page_html=landing_response.text,
            page_url=landing_response.url,
        )
        if about_page_url is None:
            raise RuntimeError(
                f"Could not find a published Invesco holdings API or about page for {seed_ticker} "
                f"from {landing_response.url}."
            )

        try:
            about_response = self.requests_session.get(
                about_page_url,
                timeout=self.timeout,
            )
            about_response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to fetch Invesco about page for {seed_ticker} from {about_page_url}"
            ) from exc

        holdings_api_url = self.extract_holdings_api_url(about_response.text)
        if holdings_api_url is None:
            holdings_api_url = self.extract_holdings_api_url_from_model(
                about_response.text
            )
        if holdings_api_url is None:
            raise RuntimeError(
                f"Could not find a published Invesco holdings API for {seed_ticker} "
                f"from {about_page_url}."
            )
        return holdings_api_url

    @staticmethod
    def parse_holdings_tickers_from_json(payload_text: str) -> list[str]:
        payload = json.loads(payload_text)
        holdings = payload.get("holdings") or []
        tickers = [
            str(holding.get("ticker", "")).strip().upper()
            for holding in holdings
            if str(holding.get("ticker", "")).strip()
        ]
        return sorted(set(tickers))

    def fetch_holdings_payload_via_browser(self, seed_ticker: str) -> str:
        source = get_invesco_holdings_source(seed_ticker)
        if source is None:
            raise ValueError(f"Could not build an Invesco holdings source for {seed_ticker}.")
        return self.browser_response_fetcher(
            source.landing_url,
            response_url_substring=f"/shareclasses/{source.holdings_ticker}/holdings/fund",
            exclude_response_url_substring="loadType=initial",
            timeout=self.timeout,
        )

    def fetch_holdings_payload_via_requests(self, seed_ticker: str) -> str:
        holdings_api_url = self.resolve_holdings_api_url(seed_ticker)
        try:
            response = self.requests_session.get(
                holdings_api_url,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to fetch Invesco holdings payload for {seed_ticker} "
                f"from {holdings_api_url}"
            ) from exc
        return response.text

    @staticmethod
    def parse_holdings_tickers(page_html: str) -> list[str]:
        return parse_html_table_tickers(
            page_html,
            ticker_headers=("Ticker", "Ticker Symbol", "Symbol"),
        )

    def extract_component_tickers(self, seed_ticker: str) -> list[str]:
        errors: list[str] = []

        try:
            payload_text = self.fetch_holdings_payload_via_requests(seed_ticker)
            tickers = self.parse_holdings_tickers_from_json(payload_text)
            if tickers:
                return tickers
            errors.append("requests holdings API returned no tickers")
        except Exception as exc:
            errors.append(f"requests holdings API failed: {exc}")

        try:
            payload_text = self.fetch_holdings_payload_via_browser(seed_ticker)
            tickers = self.parse_holdings_tickers_from_json(payload_text)
            if tickers:
                return tickers
            errors.append("browser holdings API returned no tickers")
        except Exception as exc:
            errors.append(f"browser holdings API failed: {exc}")

        try:
            page_html = self.fetch_holdings_page_via_requests(seed_ticker)
            tickers = self.parse_holdings_tickers(page_html)
            if tickers:
                return tickers
            errors.append("requests fetch returned no holdings rows")
        except Exception as exc:
            errors.append(f"requests path failed: {exc}")

        try:
            page_html = self.fetch_holdings_page_via_browser(seed_ticker)
            tickers = self.parse_holdings_tickers(page_html)
            if tickers:
                return tickers
            errors.append("browser fetch returned no holdings rows")
        except Exception as exc:
            errors.append(f"browser path failed: {exc}")

        raise RuntimeError(
            f"Failed to extract Invesco holdings for {seed_ticker}. " + "; ".join(errors)
        )


def fetch_invesco_component_tickers(
    seed_ticker: str,
    *,
    requests_session: requests.Session | None = None,
    timeout: float = 30.0,
) -> list[str]:
    extractor = InvescoHoldingsExtractor(
        requests_session=requests_session,
        timeout=timeout,
    )
    return extractor.extract_component_tickers(seed_ticker)

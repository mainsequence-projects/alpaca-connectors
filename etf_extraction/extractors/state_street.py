from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import requests

from etf_extraction.extractors.common import (
    HoldingsExtractor,
    parse_tabular_tickers,
    parse_xlsx_rows,
)
from etf_extraction.settings import StateStreetHoldingsSource, get_state_street_holdings_source


class StateStreetHoldingsExtractor(HoldingsExtractor):
    _download_href_pattern = re.compile(
        r"""href=["'](?P<href>[^"']*holdings-daily[^"']*\.xlsx)["']""",
        re.IGNORECASE,
    )
    _request_headers = {
        "User-Agent": "Mozilla/5.0",
    }

    def __init__(
        self,
        *,
        requests_session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.requests_session = requests_session or requests.Session()
        self.timeout = timeout

    def supports_seed_ticker(self, seed_ticker: str) -> bool:
        return bool(seed_ticker.strip())

    def resolve_product_url(self, seed_ticker: str) -> str:
        source = get_state_street_holdings_source(seed_ticker)
        if source is None:
            raise ValueError(
                f"Could not build a State Street holdings source for {seed_ticker}."
            )
        try:
            response = self.requests_session.get(
                source.quick_info_url,
                headers=self._request_headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to fetch State Street product metadata for {seed_ticker} "
                f"from {source.quick_info_url}"
            ) from exc

        payload = json.loads(response.text)
        links = payload.get("link") or []
        if not links:
            raise RuntimeError(
                f"State Street did not publish a product link for {seed_ticker} in "
                f"{source.quick_info_url}."
            )
        return urljoin("https://www.ssga.com", str(links[0]))

    @classmethod
    def extract_holdings_download_url(
        cls,
        *,
        page_html: str,
        product_url: str,
    ) -> str | None:
        match = cls._download_href_pattern.search(page_html)
        if match is None:
            return None
        return urljoin(product_url, match.group("href"))

    def fetch_holdings_file(self, seed_ticker: str) -> bytes:
        source = get_state_street_holdings_source(seed_ticker)
        if source is None:
            raise ValueError(
                f"Could not build a State Street holdings source for {seed_ticker}."
            )
        product_url = self.resolve_product_url(seed_ticker)

        try:
            page_response = self.requests_session.get(
                product_url,
                headers=self._request_headers,
                timeout=self.timeout,
            )
            page_response.raise_for_status()

            holdings_url = self.extract_holdings_download_url(
                page_html=page_response.text,
                product_url=product_url,
            )
            if holdings_url is None:
                raise RuntimeError(
                    f"Could not find a State Street daily holdings download link for {seed_ticker}."
                )

            holdings_response = self.requests_session.get(
                holdings_url,
                headers={
                    **self._request_headers,
                    "Referer": product_url,
                },
                timeout=self.timeout,
            )
            holdings_response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to fetch State Street holdings for {seed_ticker} from {product_url}"
            ) from exc

        return holdings_response.content

    @staticmethod
    def parse_holdings_tickers(holdings_file: bytes) -> list[str]:
        rows = parse_xlsx_rows(holdings_file, worksheet_name="holdings")
        return parse_tabular_tickers(
            rows,
            ticker_headers=("Ticker", "Ticker Symbol", "Symbol"),
        )

    def extract_component_tickers(self, seed_ticker: str) -> list[str]:
        return self.parse_holdings_tickers(self.fetch_holdings_file(seed_ticker))


def fetch_state_street_component_tickers(
    seed_ticker: str,
    *,
    requests_session: requests.Session | None = None,
    timeout: float = 30.0,
) -> list[str]:
    extractor = StateStreetHoldingsExtractor(
        requests_session=requests_session,
        timeout=timeout,
    )
    return extractor.extract_component_tickers(seed_ticker)

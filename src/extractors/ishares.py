from __future__ import annotations

import re
from urllib.parse import urljoin

import requests

from src.extractors.common import (
    HoldingsExtractor,
    parse_excel_xml_worksheet_rows,
    parse_tabular_tickers,
)
from src.settings import (
    IsharesHoldingsSource,
    get_ishares_holdings_source,
)


class IsharesHoldingsExtractor(HoldingsExtractor):
    _request_headers = {
        "User-Agent": "Mozilla/5.0",
    }
    _product_href_pattern_template = r'href=["\'](?P<href>/us/products/[^"\']+)["\'][^>]*>{ticker}</a>'

    def __init__(
        self,
        *,
        requests_session: requests.Session | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.requests_session = requests_session or requests.Session()
        self.timeout = timeout

    @staticmethod
    def normalize_ticker(ticker: str) -> str:
        return ticker.strip().upper()

    @classmethod
    def resolve_product_url_from_listing(
        cls,
        *,
        listing_html: str,
        source: IsharesHoldingsSource,
    ) -> str | None:
        pattern = re.compile(
            cls._product_href_pattern_template.format(
                ticker=re.escape(source.holdings_ticker)
            ),
            re.IGNORECASE,
        )
        match = pattern.search(listing_html)
        if match is None:
            return None
        return urljoin(source.product_listing_url, match.group("href"))

    @staticmethod
    def extract_holdings_download_url(
        *,
        page_html: str,
        product_url: str,
    ) -> str | None:
        pattern = re.compile(
            r'(?P<path>/\d+\.ajax\?[^"\']*fileType=xls[^"\']*dataType=fund[^"\']*)',
            re.IGNORECASE,
        )
        match = pattern.search(page_html)
        if match is None:
            return None
        path = match.group("path")
        return f"{product_url.rstrip('/')}{path}"

    @staticmethod
    def parse_holdings_tickers(workbook_text: str) -> list[str]:
        rows = parse_excel_xml_worksheet_rows(
            workbook_text,
            worksheet_name="Holdings",
        )
        return parse_tabular_tickers(
            rows,
            ticker_headers=("Ticker",),
            asset_class_headers=("Asset Class",),
            allowed_asset_classes=("Equity",),
        )

    def fetch_holdings_workbook(self, seed_ticker: str) -> str:
        source = get_ishares_holdings_source(seed_ticker)
        if source is None:
            raise ValueError(f"Could not build an iShares holdings source for {seed_ticker}.")

        try:
            listing_response = self.requests_session.get(
                source.product_listing_url,
                headers=self._request_headers,
                timeout=self.timeout,
            )
            listing_response.raise_for_status()

            product_url = self.resolve_product_url_from_listing(
                listing_html=listing_response.text,
                source=source,
            )
            if product_url is None:
                raise RuntimeError(
                    f"Could not resolve the iShares product page for {seed_ticker} "
                    f"from {source.product_listing_url}."
                )

            page_response = self.requests_session.get(
                product_url,
                headers=self._request_headers,
                timeout=self.timeout,
            )
            page_response.raise_for_status()

            workbook_url = self.extract_holdings_download_url(
                page_html=page_response.text,
                product_url=product_url,
            )
            if workbook_url is None:
                raise RuntimeError(
                    f"Could not find a published iShares workbook download link for {seed_ticker}."
                )

            workbook_response = self.requests_session.get(
                workbook_url,
                headers={
                    **self._request_headers,
                    "Referer": product_url,
                },
                timeout=self.timeout,
            )
            workbook_response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to fetch iShares holdings for {seed_ticker} from {source.product_listing_url}"
            ) from exc

        return workbook_response.content.decode("utf-8", "ignore")

    def extract_component_tickers(self, seed_ticker: str) -> list[str]:
        source = get_ishares_holdings_source(seed_ticker)
        if source is None:
            return []
        workbook_text = self.fetch_holdings_workbook(seed_ticker)
        return self.parse_holdings_tickers(workbook_text)

    def supports_seed_ticker(self, seed_ticker: str) -> bool:
        return bool(self.normalize_ticker(seed_ticker))


def parse_ishares_holdings_tickers(csv_text: str) -> list[str]:
    return IsharesHoldingsExtractor.parse_holdings_tickers(csv_text)


def fetch_ishares_component_tickers(
    seed_ticker: str,
    *,
    requests_session: requests.Session | None = None,
    timeout: float = 30.0,
) -> list[str]:
    extractor = IsharesHoldingsExtractor(
        requests_session=requests_session,
        timeout=timeout,
    )
    return extractor.extract_component_tickers(seed_ticker)

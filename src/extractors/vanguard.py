from __future__ import annotations

import json

import requests

from src.extractors.common import HoldingsExtractor
from src.settings import VanguardHoldingsSource, get_vanguard_holdings_source


class VanguardHoldingsExtractor(HoldingsExtractor):
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

    def fetch_holdings_payload(self, seed_ticker: str) -> str:
        source = get_vanguard_holdings_source(seed_ticker)
        if source is None:
            raise ValueError(f"Could not build a Vanguard holdings source for {seed_ticker}.")
        api_url = (
            f"https://investor.vanguard.com/vmf/api/{source.requested_ticker}"
            "/portfolio-holding/stock.json?asOfType=daily"
        )

        try:
            holdings_response = self.requests_session.get(
                api_url,
                headers={
                    **self._request_headers,
                    "Referer": source.profile_url,
                },
                timeout=self.timeout,
            )
            holdings_response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to fetch Vanguard holdings for {seed_ticker} from {api_url}"
            ) from exc

        return holdings_response.text

    @staticmethod
    def parse_holdings_tickers(payload_text: str) -> list[str]:
        payload = json.loads(payload_text)
        fund = payload.get("fund") or {}
        holdings = fund.get("entity") or []
        tickers = [
            str(holding.get("ticker", "")).strip().upper()
            for holding in holdings
            if str(holding.get("ticker", "")).strip()
        ]
        return sorted(set(tickers))

    def extract_component_tickers(self, seed_ticker: str) -> list[str]:
        return self.parse_holdings_tickers(self.fetch_holdings_payload(seed_ticker))


def fetch_vanguard_component_tickers(
    seed_ticker: str,
    *,
    requests_session: requests.Session | None = None,
    timeout: float = 30.0,
) -> list[str]:
    extractor = VanguardHoldingsExtractor(
        requests_session=requests_session,
        timeout=timeout,
    )
    return extractor.extract_component_tickers(seed_ticker)

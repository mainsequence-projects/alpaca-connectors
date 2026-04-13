from __future__ import annotations

import io
import unittest
import zipfile

from src.extractors import build_component_extractor
from src.extractors.invesco import InvescoHoldingsExtractor
from src.extractors.ishares import IsharesHoldingsExtractor, parse_ishares_holdings_tickers
from src.extractors.state_street import StateStreetHoldingsExtractor
from src.extractors.vanguard import VanguardHoldingsExtractor
from src.settings import IsharesHoldingsSource


class EtfComponentResolutionTests(unittest.TestCase):
    def test_parse_ishares_holdings_tickers_reads_equity_rows(self) -> None:
        workbook_text = """<?xml version="1.0"?>
<ss:Workbook xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
  <ss:Worksheet ss:Name="Holdings">
    <ss:Table>
      <ss:Row>
        <ss:Cell><ss:Data ss:Type="String">Ticker</ss:Data></ss:Cell>
        <ss:Cell><ss:Data ss:Type="String">Name</ss:Data></ss:Cell>
        <ss:Cell><ss:Data ss:Type="String">Asset Class</ss:Data></ss:Cell>
      </ss:Row>
      <ss:Row>
        <ss:Cell><ss:Data ss:Type="String">AAPL</ss:Data></ss:Cell>
        <ss:Cell><ss:Data ss:Type="String">Apple Inc.</ss:Data></ss:Cell>
        <ss:Cell><ss:Data ss:Type="String">Equity</ss:Data></ss:Cell>
      </ss:Row>
      <ss:Row>
        <ss:Cell><ss:Data ss:Type="String">MSFT</ss:Data></ss:Cell>
        <ss:Cell><ss:Data ss:Type="String">Microsoft Corp.</ss:Data></ss:Cell>
        <ss:Cell><ss:Data ss:Type="String">Equity</ss:Data></ss:Cell>
      </ss:Row>
      <ss:Row>
        <ss:Cell><ss:Data ss:Type="String">USD</ss:Data></ss:Cell>
        <ss:Cell><ss:Data ss:Type="String">USD CASH</ss:Data></ss:Cell>
        <ss:Cell><ss:Data ss:Type="String">Cash</ss:Data></ss:Cell>
      </ss:Row>
    </ss:Table>
  </ss:Worksheet>
</ss:Workbook>
        """
        self.assertEqual(parse_ishares_holdings_tickers(workbook_text), ["AAPL", "MSFT"])

    def test_ishares_resolves_product_url_from_listing(self) -> None:
        source = IsharesHoldingsSource(
            requested_ticker="IVV",
            holdings_ticker="IVV",
            product_listing_url="https://www.ishares.com/us/products/etf-investments",
        )
        listing_html = """
        <table>
          <tr>
            <td class="links"><a href="/us/products/239726/ishares-core-sp-500-etf">IVV</a></td>
            <td class="links"><a href="/us/products/239726/ishares-core-sp-500-etf">iShares Core S&P 500 ETF</a></td>
          </tr>
        </table>
        """

        self.assertEqual(
            IsharesHoldingsExtractor.resolve_product_url_from_listing(
                listing_html=listing_html,
                source=source,
            ),
            "https://www.ishares.com/us/products/239726/ishares-core-sp-500-etf",
        )

    def test_parse_invesco_holdings_tickers_reads_json_payload(self) -> None:
        payload_text = """
        {
          "holdings": [
            {"ticker": "AAPL", "securityTypeName": "Common Stock"},
            {"ticker": "MSFT", "securityTypeName": "Common Stock"}
          ]
        }
        """
        self.assertEqual(
            InvescoHoldingsExtractor.parse_holdings_tickers_from_json(payload_text),
            ["AAPL", "MSFT"],
        )

    def test_invesco_extracts_holdings_api_url_from_spa_model(self) -> None:
        page_html = """
        <meta name="ticker" content="PRF"/>
        <meta name="shareclass" content="09TB0PRFXXSCETF"/>
        <div
          id="spa-root"
          data-model-json="{&#34;productMetaData&#34;:{&#34;cusip&#34;:&#34;46137V613&#34;,&#34;locale&#34;:&#34;en_US&#34;,&#34;uniqueIdentifier&#34;:&#34;cusip&#34;},&#34;apiUrl&#34;:&#34;https://dng-api.invesco.com/cache/v1/accounts/{locale}/shareclasses/{id}/holdings/fund?idType={uniqueIdentifier}&amp;productType=ETF&#34;}"
        ></div>
        """
        self.assertEqual(
            InvescoHoldingsExtractor.extract_holdings_api_url_from_model(page_html),
            "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46137V613/holdings/fund?idType=cusip&productType=ETF",
        )

    def test_invesco_falls_back_to_browser_fetcher(self) -> None:
        class EmptySession:
            def get(self, *args, **kwargs):
                class Response:
                    text = "<html></html>"

                    @staticmethod
                    def raise_for_status():
                        return None

                return Response()

        payload_text = """
        {
          "holdings": [
            {"ticker": "AAPL", "securityTypeName": "Common Stock"},
            {"ticker": "MSFT", "securityTypeName": "Common Stock"}
          ]
        }
        """
        extractor = InvescoHoldingsExtractor(
            requests_session=EmptySession(),
            browser_response_fetcher=lambda *args, **kwargs: payload_text,
        )

        self.assertEqual(extractor.extract_component_tickers("QQQ"), ["AAPL", "MSFT"])

    def test_vanguard_parses_holdings_json(self) -> None:
        holdings_text = """
        {
          "fund": {
            "entity": [
              {"ticker": "AMT"},
              {"ticker": "PLD"}
            ]
          }
        }
        """
        self.assertEqual(
            VanguardHoldingsExtractor.parse_holdings_tickers(holdings_text),
            ["AMT", "PLD"],
        )

    def test_state_street_extracts_daily_holdings_link_and_parses_xlsx(self) -> None:
        page_html = '<a href="/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx">Daily</a>'
        holdings_file = _build_state_street_test_workbook()

        self.assertEqual(
            StateStreetHoldingsExtractor.extract_holdings_download_url(
                page_html=page_html,
                product_url="https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy",
            ),
            "https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx",
        )
        self.assertEqual(
            StateStreetHoldingsExtractor.parse_holdings_tickers(holdings_file),
            ["AAPL", "MSFT"],
        )

    def test_expand_seed_symbols_keeps_seed_and_adds_components(self) -> None:
        class StubIsharesHoldingsExtractor(IsharesHoldingsExtractor):
            def supports_seed_ticker(self, seed_ticker: str) -> bool:
                return seed_ticker == "IVV"

            def extract_component_tickers(self, seed_ticker: str) -> list[str]:
                return ["AAPL", "MSFT"] if seed_ticker == "IVV" else []

        expansion = StubIsharesHoldingsExtractor().expand_seed_symbols(["IVV", "AAPL"])

        self.assertEqual(expansion.seed_symbols, ["AAPL", "IVV"])
        self.assertEqual(expansion.component_symbols_by_seed, {"IVV": ["AAPL", "MSFT"]})
        self.assertEqual(expansion.expanded_symbols, ["AAPL", "IVV", "MSFT"])
        self.assertEqual(expansion.unsupported_seed_symbols, ["AAPL"])

    def test_component_extractor_registry_resolves_supported_providers(self) -> None:
        self.assertIsInstance(build_component_extractor("ishares"), IsharesHoldingsExtractor)
        self.assertIsInstance(build_component_extractor("invesco"), InvescoHoldingsExtractor)
        self.assertIsInstance(build_component_extractor("vanguard"), VanguardHoldingsExtractor)
        self.assertIsInstance(
            build_component_extractor("state_street"),
            StateStreetHoldingsExtractor,
        )

def _build_state_street_test_workbook() -> bytes:
    workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets>
        <sheet name="holdings" sheetId="1" r:id="rId1"/>
      </sheets>
    </workbook>
    """
    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    </Relationships>
    """
    shared_strings_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="8" uniqueCount="8">
      <si><t>Name</t></si>
      <si><t>Ticker</t></si>
      <si><t>Weight</t></si>
      <si><t>Apple Inc.</t></si>
      <si><t>AAPL</t></si>
      <si><t>Microsoft Corp.</t></si>
      <si><t>MSFT</t></si>
      <si><t>Fund Name:</t></si>
    </sst>
    """
    worksheet_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1">
          <c r="A1" t="s"><v>7</v></c>
          <c r="B1" t="s"><v>3</v></c>
        </row>
        <row r="5">
          <c r="A5" t="s"><v>0</v></c>
          <c r="B5" t="s"><v>1</v></c>
          <c r="C5" t="s"><v>2</v></c>
        </row>
        <row r="6">
          <c r="A6" t="s"><v>3</v></c>
          <c r="B6" t="s"><v>4</v></c>
          <c r="C6"><v>7.1</v></c>
        </row>
        <row r="7">
          <c r="A7" t="s"><v>5</v></c>
          <c r="B7" t="s"><v>6</v></c>
          <c r="C7"><v>6.5</v></c>
        </row>
      </sheetData>
    </worksheet>
    """

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as workbook_zip:
        workbook_zip.writestr("[Content_Types].xml", "")
        workbook_zip.writestr("xl/workbook.xml", workbook_xml)
        workbook_zip.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        workbook_zip.writestr("xl/sharedStrings.xml", shared_strings_xml)
        workbook_zip.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()

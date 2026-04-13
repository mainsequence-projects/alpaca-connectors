from __future__ import annotations

import csv
import html
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import BaseModel, Field


_HTML_TABLE_ROW_PATTERN = re.compile(r"<tr\b[^>]*>(?P<row>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_TABLE_CELL_PATTERN = re.compile(
    r"<(?:td|th)\b[^>]*>(?P<cell>.*?)</(?:td|th)>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_SPREADSHEET_ROW_PATTERN = re.compile(r"<ss:Row\b[^>]*>(?P<body>.*?)</ss:Row>", re.DOTALL)
_SPREADSHEET_CELL_PATTERN = re.compile(
    r"<ss:Cell\b(?P<attrs>[^>]*)>(?P<body>.*?)</ss:Cell>",
    re.DOTALL,
)
_SPREADSHEET_DATA_PATTERN = re.compile(r"<ss:Data\b[^>]*>(?P<data>.*?)</ss:Data>", re.DOTALL)
_SPREADSHEET_INDEX_PATTERN = re.compile(r'ss:Index="(?P<index>\d+)"')
_XML_BARE_AMPERSAND_PATTERN = re.compile(
    r"&(?!amp;|lt;|gt;|quot;|apos;|#[0-9]+;|#x[0-9A-Fa-f]+;)"
)
_INVALID_XML_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_IGNORED_TICKER_VALUES = {"", "-", "--", "N/A", "CASH", "USD"}


class ExpandedSymbolUniverse(BaseModel):
    seed_symbols: list[str]
    expanded_symbols: list[str]
    component_symbols_by_seed: dict[str, list[str]] = Field(default_factory=dict)
    unsupported_seed_symbols: list[str] = Field(default_factory=list)


def normalize_ticker(symbol: str) -> str:
    return symbol.strip().upper()


def normalize_ticker_list(symbols: Sequence[str]) -> list[str]:
    return sorted({normalize_ticker(symbol) for symbol in symbols if symbol.strip()})


def _clean_html_cell(raw_value: str) -> str:
    without_tags = _HTML_TAG_PATTERN.sub(" ", raw_value)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def parse_html_table_rows(page_html: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_match in _HTML_TABLE_ROW_PATTERN.finditer(page_html):
        row_html = row_match.group("row")
        cells = [
            _clean_html_cell(cell_match.group("cell"))
            for cell_match in _HTML_TABLE_CELL_PATTERN.finditer(row_html)
        ]
        if cells:
            rows.append(cells)
    return rows


def _resolve_header_index(headers: Sequence[str], candidates: Sequence[str]) -> int | None:
    normalized_headers = {header.strip().lower(): idx for idx, header in enumerate(headers)}
    for candidate in candidates:
        idx = normalized_headers.get(candidate.strip().lower())
        if idx is not None:
            return idx
    return None


def _is_probable_ticker(value: str) -> bool:
    normalized_value = normalize_ticker(value)
    if normalized_value in _IGNORED_TICKER_VALUES:
        return False
    return bool(_TICKER_PATTERN.fullmatch(normalized_value))


def parse_tabular_tickers(
    rows: Sequence[Sequence[str]],
    *,
    ticker_headers: Sequence[str] = ("Ticker", "Ticker Symbol", "Symbol"),
    asset_class_headers: Sequence[str] = ("Asset Class", "AssetType"),
    allowed_asset_classes: Sequence[str] | None = None,
) -> list[str]:
    if not rows:
        raise ValueError("No tabular holdings rows were found.")

    header_index = None
    ticker_index = None
    asset_class_index = None
    for idx, row in enumerate(rows):
        ticker_idx = _resolve_header_index(row, ticker_headers)
        if ticker_idx is None:
            continue
        header_index = idx
        ticker_index = ticker_idx
        asset_class_index = _resolve_header_index(row, asset_class_headers)
        break

    if header_index is None or ticker_index is None:
        raise ValueError("Could not locate a holdings header row containing a ticker column.")

    allowed_asset_classes_normalized = (
        {value.strip().lower() for value in allowed_asset_classes}
        if allowed_asset_classes
        else None
    )

    tickers: list[str] = []
    for row in rows[header_index + 1 :]:
        if ticker_index >= len(row):
            continue
        ticker = normalize_ticker(row[ticker_index])
        if not _is_probable_ticker(ticker):
            continue
        if asset_class_index is not None and asset_class_index < len(row) and allowed_asset_classes_normalized:
            asset_class = row[asset_class_index].strip().lower()
            if asset_class and asset_class not in allowed_asset_classes_normalized:
                continue
        tickers.append(ticker)

    return sorted(set(tickers))


def parse_delimited_tickers(
    text: str,
    *,
    delimiter: str | None = None,
    ticker_headers: Sequence[str] = ("Ticker", "Ticker Symbol", "Symbol"),
    asset_class_headers: Sequence[str] = ("Asset Class", "AssetType"),
    allowed_asset_classes: Sequence[str] | None = None,
) -> list[str]:
    sample = text[:2048]
    inferred_delimiter = delimiter
    if inferred_delimiter is None:
        try:
            inferred_delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except csv.Error:
            inferred_delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=inferred_delimiter)
    return parse_tabular_tickers(
        list(reader),
        ticker_headers=ticker_headers,
        asset_class_headers=asset_class_headers,
        allowed_asset_classes=allowed_asset_classes,
    )


def parse_html_table_tickers(
    page_html: str,
    *,
    ticker_headers: Sequence[str] = ("Ticker", "Ticker Symbol", "Symbol"),
    asset_class_headers: Sequence[str] = ("Asset Class", "AssetType"),
    allowed_asset_classes: Sequence[str] | None = None,
) -> list[str]:
    return parse_tabular_tickers(
        parse_html_table_rows(page_html),
        ticker_headers=ticker_headers,
        asset_class_headers=asset_class_headers,
        allowed_asset_classes=allowed_asset_classes,
    )


def parse_excel_xml_worksheet_rows(
    workbook_text: str,
    *,
    worksheet_name: str,
) -> list[list[str]]:
    workbook_text = _XML_BARE_AMPERSAND_PATTERN.sub("&amp;", workbook_text)
    workbook_text = _INVALID_XML_CHAR_PATTERN.sub("", workbook_text)

    worksheet_start_pattern = re.compile(
        rf'<ss:Worksheet\b[^>]*ss:Name="{re.escape(worksheet_name)}"[^>]*>',
        re.DOTALL,
    )
    worksheet_match = worksheet_start_pattern.search(workbook_text)
    if worksheet_match is None:
        raise ValueError(f"Could not find worksheet {worksheet_name!r} in workbook.")
    worksheet_body_start = worksheet_match.end()
    worksheet_body_end = workbook_text.find("</ss:Worksheet>", worksheet_body_start)
    if worksheet_body_end == -1:
        raise ValueError(f"Could not find the closing tag for worksheet {worksheet_name!r}.")
    worksheet_body = workbook_text[worksheet_body_start:worksheet_body_end]
    rows: list[list[str]] = []
    for row_match in _SPREADSHEET_ROW_PATTERN.finditer(worksheet_body):
        row: list[str] = []
        current_index = 1
        for cell_match in _SPREADSHEET_CELL_PATTERN.finditer(row_match.group("body")):
            index_match = _SPREADSHEET_INDEX_PATTERN.search(cell_match.group("attrs") or "")
            if index_match:
                target_index = int(index_match.group("index"))
                while current_index < target_index:
                    row.append("")
                    current_index += 1
            data_match = _SPREADSHEET_DATA_PATTERN.search(cell_match.group("body"))
            data = _clean_html_cell(data_match.group("data")) if data_match else ""
            row.append(data)
            current_index += 1
        rows.append(row)

    return rows


def parse_xlsx_rows(
    workbook_bytes: bytes,
    *,
    worksheet_name: str | None = None,
) -> list[list[str]]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(workbook_bytes)) as workbook_zip:
        workbook_root = ET.fromstring(workbook_zip.read("xl/workbook.xml"))
        relationships_root = ET.fromstring(workbook_zip.read("xl/_rels/workbook.xml.rels"))

        rels_by_id = {
            relationship.get("Id"): relationship.get("Target")
            for relationship in relationships_root.findall(
                "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
            )
        }
        sheets = workbook_root.findall(".//x:sheets/x:sheet", namespace)
        if not sheets:
            raise ValueError("Could not find any worksheets in the xlsx workbook.")

        selected_sheet = None
        if worksheet_name is None:
            selected_sheet = sheets[0]
        else:
            for sheet in sheets:
                if (sheet.get("name") or "").strip().lower() == worksheet_name.strip().lower():
                    selected_sheet = sheet
                    break
        if selected_sheet is None:
            raise ValueError(f"Could not find worksheet {worksheet_name!r} in the xlsx workbook.")

        relation_id = selected_sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        sheet_target = rels_by_id.get(relation_id)
        if not sheet_target:
            raise ValueError("Could not resolve the worksheet target in the xlsx workbook.")

        sheet_path = f"xl/{sheet_target.lstrip('/')}"

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook_zip.namelist():
            shared_strings_root = ET.fromstring(workbook_zip.read("xl/sharedStrings.xml"))
            for string_item in shared_strings_root.findall("x:si", namespace):
                texts = [
                    text_node.text or ""
                    for text_node in string_item.findall(".//x:t", namespace)
                ]
                shared_strings.append("".join(texts))

        worksheet_root = ET.fromstring(workbook_zip.read(sheet_path))
        rows: list[list[str]] = []
        for row_element in worksheet_root.findall(".//x:sheetData/x:row", namespace):
            row: list[str] = []
            current_index = 1
            for cell_element in row_element.findall("x:c", namespace):
                cell_ref = cell_element.get("r") or ""
                column_letters = re.match(r"([A-Z]+)", cell_ref)
                if column_letters:
                    target_index = 0
                    for letter in column_letters.group(1):
                        target_index = target_index * 26 + (ord(letter) - ord("A") + 1)
                    while current_index < target_index:
                        row.append("")
                        current_index += 1

                cell_type = cell_element.get("t")
                value_element = cell_element.find("x:v", namespace)
                inline_text_element = cell_element.find("x:is/x:t", namespace)
                if inline_text_element is not None:
                    value = inline_text_element.text or ""
                elif value_element is None:
                    value = ""
                else:
                    raw_value = value_element.text or ""
                    if cell_type == "s":
                        value = shared_strings[int(raw_value)]
                    else:
                        value = raw_value

                row.append(value)
                current_index += 1
            rows.append(row)

        return rows


class HoldingsExtractor(ABC):
    @staticmethod
    def normalize_ticker(ticker: str) -> str:
        return normalize_ticker(ticker)

    @abstractmethod
    def supports_seed_ticker(self, seed_ticker: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def extract_component_tickers(self, seed_ticker: str) -> list[str]:
        raise NotImplementedError

    def expand_seed_symbols(self, seed_symbols: Sequence[str]) -> ExpandedSymbolUniverse:
        normalized_seed_symbols = normalize_ticker_list(seed_symbols)
        expanded_symbols = set(normalized_seed_symbols)
        component_symbols_by_seed: dict[str, list[str]] = {}
        unsupported_seed_symbols: list[str] = []

        for seed_symbol in normalized_seed_symbols:
            if not self.supports_seed_ticker(seed_symbol):
                unsupported_seed_symbols.append(seed_symbol)
                continue
            component_symbols = normalize_ticker_list(self.extract_component_tickers(seed_symbol))
            component_symbols_by_seed[seed_symbol] = component_symbols
            expanded_symbols.update(component_symbols)

        return ExpandedSymbolUniverse(
            seed_symbols=normalized_seed_symbols,
            expanded_symbols=sorted(expanded_symbols),
            component_symbols_by_seed=component_symbols_by_seed,
            unsupported_seed_symbols=unsupported_seed_symbols,
        )

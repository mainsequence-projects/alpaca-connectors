from __future__ import annotations

import unittest

from src.account import (
    api_key_fingerprint,
    build_account_unique_identifier,
    environment_for_is_paper,
)


class AccountIdentityTests(unittest.TestCase):
    def test_unique_identifier_paper_vs_live_suffix(self) -> None:
        self.assertEqual(
            build_account_unique_identifier(account_number="010203ABCD", is_paper=False),
            "010203ABCD__ALPACA",
        )
        self.assertEqual(
            build_account_unique_identifier(account_number="010203ABCD", is_paper=True),
            "010203ABCD__ALPACA_PAPER",
        )

    def test_unique_identifier_is_stable_and_strips(self) -> None:
        self.assertEqual(
            build_account_unique_identifier(account_number="  PA123  ", is_paper=False),
            "PA123__ALPACA",
        )

    def test_unique_identifier_requires_account_number(self) -> None:
        with self.assertRaises(ValueError):
            build_account_unique_identifier(account_number="   ", is_paper=False)

    def test_api_key_fingerprint_is_stable_16_hex(self) -> None:
        fingerprint = api_key_fingerprint("PKTESTKEY123")
        self.assertEqual(len(fingerprint), 16)
        self.assertEqual(fingerprint, api_key_fingerprint("PKTESTKEY123"))
        self.assertNotEqual(fingerprint, api_key_fingerprint("PKOTHERKEY"))

    def test_api_key_fingerprint_requires_key(self) -> None:
        with self.assertRaises(ValueError):
            api_key_fingerprint("")

    def test_environment_label(self) -> None:
        self.assertEqual(environment_for_is_paper(True), "paper")
        self.assertEqual(environment_for_is_paper(False), "live")


if __name__ == "__main__":
    unittest.main()

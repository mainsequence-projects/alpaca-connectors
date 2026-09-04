from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import src.settings as settings


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        settings.get_platform_secret_value.cache_clear()

    def tearDown(self) -> None:
        settings.get_platform_secret_value.cache_clear()

    def test_get_alpaca_api_key_prefers_environment(self) -> None:
        with (
            patch.dict(os.environ, {"ALPACA_API_KEY": "env-api-key"}, clear=False),
            patch("src.settings.get_platform_secret_value") as get_platform_secret_value,
        ):
            self.assertEqual(settings.get_alpaca_api_key(), "env-api-key")

        get_platform_secret_value.assert_not_called()

    def test_get_alpaca_secret_key_falls_back_to_platform_secret(self) -> None:
        with (
            patch.dict(os.environ, {"ALPACA_SECRET_KEY": ""}, clear=False),
            patch("src.settings.get_platform_secret_value", return_value="platform-secret-key"),
        ):
            self.assertEqual(settings.get_alpaca_secret_key(), "platform-secret-key")

    def test_get_alpaca_api_key_fails_fast_when_missing_everywhere(self) -> None:
        with (
            patch.dict(os.environ, {"ALPACA_API_KEY": ""}, clear=False),
            patch("src.settings.get_platform_secret_value", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "Missing Alpaca API key"):
                settings.get_alpaca_api_key()

    def test_get_alpaca_secret_key_fails_fast_when_missing_everywhere(self) -> None:
        with (
            patch.dict(os.environ, {"ALPACA_SECRET_KEY": ""}, clear=False),
            patch("src.settings.get_platform_secret_value", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "Missing Alpaca secret key"):
                settings.get_alpaca_secret_key()

    def test_get_platform_secret_value_extracts_secretstr_value(self) -> None:
        with patch(
            "src.settings.read_platform_secret_value",
            return_value="platform-api-key",
        ):
            self.assertEqual(
                settings.get_platform_secret_value("ALPACA_API_KEY"),
                "platform-api-key",
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.account.credentials import (
    AlpacaSecretNames,
    ResolvedAlpacaCredentials,
    resolve_alpaca_credentials,
)


class AccountCredentialTests(unittest.TestCase):
    def test_secret_names_are_normalized_and_values_are_not_repr_visible(self) -> None:
        names = AlpacaSecretNames(" API_KEY_NAME ", "SECRET_KEY_NAME")
        self.assertEqual(names.api_key_secret_name, "API_KEY_NAME")
        credentials = ResolvedAlpacaCredentials("raw-api-value", "raw-secret-value")
        self.assertNotIn("raw-api-value", repr(credentials))
        self.assertNotIn("raw-secret-value", repr(credentials))

    def test_resolver_fetches_each_platform_secret_by_name(self) -> None:
        import mainsequence.client as msc

        values = {
            "API_KEY_NAME": SimpleNamespace(value="raw-api-value"),
            "SECRET_KEY_NAME": SimpleNamespace(value="raw-secret-value"),
        }
        with patch.object(
            msc.Secret, "get_or_none", side_effect=lambda **kwargs: values[kwargs["name"]]
        ) as get_secret:
            credentials = resolve_alpaca_credentials(
                AlpacaSecretNames("API_KEY_NAME", "SECRET_KEY_NAME")
            )

        self.assertEqual(credentials.api_key, "raw-api-value")
        self.assertEqual(credentials.secret_key, "raw-secret-value")
        self.assertEqual(
            [call.kwargs["name"] for call in get_secret.call_args_list],
            ["API_KEY_NAME", "SECRET_KEY_NAME"],
        )


if __name__ == "__main__":
    unittest.main()

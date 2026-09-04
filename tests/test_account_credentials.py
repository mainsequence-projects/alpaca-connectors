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

        summaries = {
            "API_KEY_NAME": [SimpleNamespace(uid="api-secret-uid")],
            "SECRET_KEY_NAME": [SimpleNamespace(uid="secret-secret-uid")],
        }
        details = {
            "api-secret-uid": SimpleNamespace(
                name="API_KEY_NAME",
                value="raw-api-value",
            ),
            "secret-secret-uid": SimpleNamespace(
                name="SECRET_KEY_NAME",
                value="raw-secret-value",
            ),
        }
        with (
            patch.object(
                msc.Secret,
                "filter",
                side_effect=lambda **kwargs: summaries[kwargs["name"]],
            ) as filter_secrets,
            patch.object(
                msc.Secret,
                "get_by_uid",
                side_effect=lambda uid: details[uid],
            ) as get_secret_detail,
        ):
            credentials = resolve_alpaca_credentials(
                AlpacaSecretNames("API_KEY_NAME", "SECRET_KEY_NAME")
            )

        self.assertEqual(credentials.api_key, "raw-api-value")
        self.assertEqual(credentials.secret_key, "raw-secret-value")
        self.assertEqual(
            [call.kwargs["name"] for call in filter_secrets.call_args_list],
            ["API_KEY_NAME", "SECRET_KEY_NAME"],
        )
        self.assertEqual(
            [call.args[0] for call in get_secret_detail.call_args_list],
            ["api-secret-uid", "secret-secret-uid"],
        )

    def test_resolver_does_not_treat_redacted_list_record_as_missing_value(self) -> None:
        import mainsequence.client as msc

        with (
            patch.object(
                msc.Secret,
                "filter",
                side_effect=[
                    [SimpleNamespace(uid="api-secret-uid", value=None)],
                    [SimpleNamespace(uid="secret-secret-uid", value=None)],
                ],
            ),
            patch.object(
                msc.Secret,
                "get_by_uid",
                side_effect=[
                    SimpleNamespace(name="API_KEY_NAME", value="raw-api-value"),
                    SimpleNamespace(name="SECRET_KEY_NAME", value="raw-secret-value"),
                ],
            ),
        ):
            credentials = resolve_alpaca_credentials(
                AlpacaSecretNames("API_KEY_NAME", "SECRET_KEY_NAME")
            )

        self.assertEqual(credentials.api_key, "raw-api-value")
        self.assertEqual(credentials.secret_key, "raw-secret-value")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from alpaca_connectors.account.credentials import (
    CREDENTIAL_SOURCE_EXTERNAL,
    CREDENTIAL_SOURCE_MANAGED,
    AccountSecretReferenceError,
    AlpacaCredentialsRejectedError,
    AlpacaSecretNames,
    AlpacaSecretReferences,
    ResolvedAlpacaCredentials,
    delete_managed_alpaca_secrets,
    managed_secret_names,
    plan_managed_secret_writes,
    read_alpaca_account_or_reject,
    registered_secret_references,
    resolve_alpaca_credentials,
    resolve_alpaca_secret_references,
    store_managed_alpaca_credentials,
    submitted_alpaca_credentials,
)
from alpaca_connectors.platform_secrets import PlatformSecretAccessError

RAW_API_KEY = "PKRAWVALUE123"
RAW_SECRET_KEY = "SKRAWVALUE456"


def _references(source: str = CREDENTIAL_SOURCE_MANAGED) -> AlpacaSecretReferences:
    names = managed_secret_names("010203ABCD__ALPACA_PAPER")
    return AlpacaSecretReferences(
        api_key_secret_name=names.api_key_secret_name,
        secret_key_secret_name=names.secret_key_secret_name,
        api_key_secret_uid="api-secret-uid",
        secret_key_secret_uid="secret-secret-uid",
        credential_source=source,
    )


class AccountCredentialTests(unittest.TestCase):
    def test_secret_names_are_normalized_and_values_are_not_repr_visible(self) -> None:
        names = AlpacaSecretNames(" API_KEY_NAME ", "SECRET_KEY_NAME")
        self.assertEqual(names.api_key_secret_name, "API_KEY_NAME")
        credentials = ResolvedAlpacaCredentials("raw-api-value", "raw-secret-value")
        self.assertNotIn("raw-api-value", repr(credentials))
        self.assertNotIn("raw-secret-value", repr(credentials))

    def test_external_names_resolve_to_uids_and_values_resolve_by_uid(self) -> None:
        import mainsequence.client as msc

        summaries = {
            "API_KEY_NAME": [SimpleNamespace(uid="api-secret-uid")],
            "SECRET_KEY_NAME": [SimpleNamespace(uid="secret-secret-uid")],
        }
        details = {
            "api-secret-uid": SimpleNamespace(name="API_KEY_NAME", value="raw-api-value"),
            "secret-secret-uid": SimpleNamespace(name="SECRET_KEY_NAME", value="raw-secret-value"),
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
            references = resolve_alpaca_secret_references(
                AlpacaSecretNames("API_KEY_NAME", "SECRET_KEY_NAME")
            )
            credentials = resolve_alpaca_credentials(references)

        self.assertEqual(references.credential_source, CREDENTIAL_SOURCE_EXTERNAL)
        self.assertEqual(references.api_key_secret_uid, "api-secret-uid")
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

    def test_registered_resolution_uses_stored_uids_without_a_name_lookup(self) -> None:
        import mainsequence.client as msc

        registration = {
            "account_uid": "account-uid",
            "api_key_secret_name": "RENAMED_LATER",
            "secret_key_secret_name": "SECRET_KEY_NAME",
            "api_key_secret_uid": "api-secret-uid",
            "secret_key_secret_uid": "secret-secret-uid",
            "credential_source": CREDENTIAL_SOURCE_EXTERNAL,
        }
        details = {
            "api-secret-uid": SimpleNamespace(name="RENAMED_LATER", value="raw-api-value"),
            "secret-secret-uid": SimpleNamespace(name="SECRET_KEY_NAME", value="raw-secret-value"),
        }
        with (
            patch.object(msc.Secret, "filter") as filter_secrets,
            patch.object(msc.Secret, "get_by_uid", side_effect=lambda uid: details[uid]),
        ):
            credentials = resolve_alpaca_credentials(registered_secret_references(registration))

        filter_secrets.assert_not_called()
        self.assertEqual(credentials.api_key, "raw-api-value")

    def test_registration_without_stored_uids_points_to_the_backfill(self) -> None:
        with self.assertRaisesRegex(AccountSecretReferenceError, "backfill-secret-uids"):
            registered_secret_references(
                {
                    "account_uid": "account-uid",
                    "api_key_secret_name": "API_KEY_NAME",
                    "secret_key_secret_name": "SECRET_KEY_NAME",
                    "api_key_secret_uid": None,
                    "secret_key_secret_uid": None,
                }
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
                resolve_alpaca_secret_references(
                    AlpacaSecretNames("API_KEY_NAME", "SECRET_KEY_NAME")
                )
            )

        self.assertEqual(credentials.api_key, "raw-api-value")
        self.assertEqual(credentials.secret_key, "raw-secret-value")

    def test_external_selection_cannot_reference_a_managed_secret(self) -> None:
        names = managed_secret_names("010203ABCD__ALPACA")
        with self.assertRaisesRegex(ValueError, "reserved"):
            resolve_alpaca_secret_references(names)

    def test_managed_secret_names_are_deterministic_per_account_identity(self) -> None:
        names = managed_secret_names("010203ABCD__ALPACA_PAPER")
        self.assertEqual(
            names.api_key_secret_name,
            "ALPACA_CONNECTORS__010203ABCD__ALPACA_PAPER__API_KEY",
        )
        self.assertEqual(
            names.secret_key_secret_name,
            "ALPACA_CONNECTORS__010203ABCD__ALPACA_PAPER__SECRET_KEY",
        )

    def test_submitted_values_are_trimmed_and_validated_without_echoing_them(self) -> None:
        credentials = submitted_alpaca_credentials(
            api_key=f"  {RAW_API_KEY}\n",
            secret_key=RAW_SECRET_KEY,
        )
        self.assertEqual(credentials.api_key, RAW_API_KEY)

        for api_key, secret_key in (
            ("", RAW_SECRET_KEY),
            ("PK WITH SPACE", RAW_SECRET_KEY),
            (RAW_API_KEY, RAW_API_KEY),
            ("P" * 257, RAW_SECRET_KEY),
        ):
            with self.subTest(api_key=api_key[:8]):
                with self.assertRaises(ValueError) as raised:
                    submitted_alpaca_credentials(api_key=api_key, secret_key=secret_key)
                self.assertNotIn(RAW_API_KEY, str(raised.exception))
                self.assertNotIn(RAW_SECRET_KEY, str(raised.exception))
                if api_key:
                    self.assertNotIn(api_key, str(raised.exception))

    def test_store_creates_missing_secrets_and_reads_back_their_uids(self) -> None:
        import mainsequence.client as msc

        created: dict[str, str] = {}

        def filter_secrets(**kwargs):
            name = kwargs["name"]
            return [SimpleNamespace(uid=created[name])] if name in created else []

        def create_secret(*, name, value):
            created[name] = f"uid-{len(created) + 1}"
            return SimpleNamespace(uid=None, name=name, value=None)

        with (
            patch.object(msc.Secret, "filter", side_effect=filter_secrets),
            patch.object(msc.Secret, "create", side_effect=create_secret) as create,
            patch.object(msc.Secret, "patch_by_uid") as patch_secret,
        ):
            write = store_managed_alpaca_credentials(
                "010203ABCD__ALPACA_PAPER",
                ResolvedAlpacaCredentials(RAW_API_KEY, RAW_SECRET_KEY),
            )

        patch_secret.assert_not_called()
        self.assertEqual(
            [call.kwargs for call in create.call_args_list],
            [
                {
                    "name": "ALPACA_CONNECTORS__010203ABCD__ALPACA_PAPER__API_KEY",
                    "value": RAW_API_KEY,
                },
                {
                    "name": "ALPACA_CONNECTORS__010203ABCD__ALPACA_PAPER__SECRET_KEY",
                    "value": RAW_SECRET_KEY,
                },
            ],
        )
        self.assertEqual(write.references.credential_source, CREDENTIAL_SOURCE_MANAGED)
        self.assertEqual(write.references.api_key_secret_uid, "uid-1")
        self.assertEqual(write.references.secret_key_secret_uid, "uid-2")
        self.assertEqual(len(write.created), 2)
        self.assertNotIn(RAW_API_KEY, repr(write))

    def test_store_overwrites_existing_managed_secrets_in_place(self) -> None:
        import mainsequence.client as msc

        with (
            patch.object(
                msc.Secret,
                "filter",
                side_effect=[[SimpleNamespace(uid="api-uid")], [SimpleNamespace(uid="secret-uid")]],
            ),
            patch.object(msc.Secret, "create") as create,
            patch.object(msc.Secret, "patch_by_uid") as patch_secret,
        ):
            write = store_managed_alpaca_credentials(
                "010203ABCD__ALPACA",
                ResolvedAlpacaCredentials(RAW_API_KEY, RAW_SECRET_KEY),
            )

        create.assert_not_called()
        self.assertEqual(
            [(call.args, call.kwargs) for call in patch_secret.call_args_list],
            [
                (("api-uid",), {"value": RAW_API_KEY}),
                (("secret-uid",), {"value": RAW_SECRET_KEY}),
            ],
        )
        self.assertEqual(write.created, ())

    def test_failed_second_write_removes_the_secret_created_by_the_first(self) -> None:
        import mainsequence.client as msc

        created: dict[str, str] = {}

        def filter_secrets(**kwargs):
            name = kwargs["name"]
            return [SimpleNamespace(uid=created[name])] if name in created else []

        def create_secret(*, name, value):
            if name.endswith("__SECRET_KEY"):
                raise RuntimeError(f"request body contained {value}")
            created[name] = "api-uid"

        with (
            patch.object(msc.Secret, "filter", side_effect=filter_secrets),
            patch.object(msc.Secret, "create", side_effect=create_secret),
            patch.object(msc.Secret, "destroy_by_uid") as destroy,
        ):
            with self.assertRaises(PlatformSecretAccessError) as raised:
                store_managed_alpaca_credentials(
                    "010203ABCD__ALPACA",
                    ResolvedAlpacaCredentials(RAW_API_KEY, RAW_SECRET_KEY),
                )

        destroy.assert_called_once_with("api-uid")
        # The SDK failure may describe the request body, so it is neither chained nor echoed.
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn(RAW_SECRET_KEY, str(raised.exception))

    def test_plan_reports_create_or_update_without_writing(self) -> None:
        import mainsequence.client as msc

        with (
            patch.object(
                msc.Secret,
                "filter",
                side_effect=[[SimpleNamespace(uid="api-uid")], []],
            ),
            patch.object(msc.Secret, "create") as create,
            patch.object(msc.Secret, "patch_by_uid") as patch_secret,
        ):
            writes = plan_managed_secret_writes("010203ABCD__ALPACA")

        create.assert_not_called()
        patch_secret.assert_not_called()
        self.assertEqual(
            [write["action"] for write in writes],
            ["update", "create"],
        )

    def test_only_managed_secrets_are_deleted(self) -> None:
        import mainsequence.client as msc

        with patch.object(msc.Secret, "destroy_by_uid") as destroy:
            deleted, warnings = delete_managed_alpaca_secrets(
                _references(CREDENTIAL_SOURCE_EXTERNAL)
            )
        destroy.assert_not_called()
        self.assertEqual((deleted, warnings), ([], []))

        with patch.object(
            msc.Secret,
            "destroy_by_uid",
            side_effect=[None, RuntimeError("platform unavailable")],
        ) as destroy:
            deleted, warnings = delete_managed_alpaca_secrets(_references())
        self.assertEqual(
            [call.args[0] for call in destroy.call_args_list],
            ["api-secret-uid", "secret-secret-uid"],
        )
        self.assertEqual(deleted, ["ALPACA_CONNECTORS__010203ABCD__ALPACA_PAPER__API_KEY"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("__SECRET_KEY", warnings[0])

    def test_alpaca_authentication_failure_maps_to_a_fixed_error(self) -> None:
        from alpaca.common.exceptions import APIError

        http_error = SimpleNamespace(response=SimpleNamespace(status_code=401))
        client = MagicMock()
        client.get_account.side_effect = APIError(
            '{"message": "provider detail"}', http_error=http_error
        )
        with self.assertRaises(AlpacaCredentialsRejectedError) as raised:
            read_alpaca_account_or_reject(client)
        self.assertEqual(str(raised.exception), "Alpaca rejected the supplied credentials.")
        self.assertIsNone(raised.exception.__cause__)


if __name__ == "__main__":
    unittest.main()

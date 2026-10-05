import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from azure.core.exceptions import (
    ClientAuthenticationError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.keyvault.secrets import SecretClient


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "code" / "snow-tickets-updater.py.txt"
LOADER = importlib.machinery.SourceFileLoader("snow_tickets_updater", str(SCRIPT_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
updater = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(updater)

VAULT_URL = "https://test.vault.azure.net/"


class Response(HttpResponse):
    def __init__(self, request, status_code, headers=None):
        super().__init__(request, None)
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.content_type = "application/json"

    def body(self):
        if self.status_code == 200:
            payload = {"value": "test-value", "id": self.request.url.split("?")[0]}
        else:
            payload = {"error": {"code": "TestError", "message": "Test failure"}}
        return json.dumps(payload).encode()

    def json(self):
        return json.loads(self.body())


class Transport(HttpTransport):
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []
        self.delays = []
        self.closed = False

    def open(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        self.requests.append(request)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple):
            return Response(request, *outcome)
        return Response(request, outcome)

    def sleep(self, duration):
        self.delays.append(duration)


class KeyVaultRetryTests(unittest.TestCase):
    def read_credentials(self, transport):
        identity = Mock()
        with (
            patch.object(updater, "ManagedIdentityCredential", return_value=identity),
            patch.object(
                updater,
                "SecretClient",
                side_effect=lambda **kwargs: SecretClient(transport=transport, **kwargs),
            ),
        ):
            try:
                return updater.get_servicenow_credentials(VAULT_URL, "test-client-id")
            finally:
                identity.close.assert_called_once_with()
                self.assertTrue(transport.closed)

    def test_success_reads_each_secret_once(self):
        transport = Transport([200] * 4)
        credentials = self.read_credentials(transport)
        self.assertEqual(credentials, dict.fromkeys(updater.SECRET_NAMES, "test-value"))
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual(transport.delays, [])

    def test_transient_http_errors_recover_with_exponential_backoff(self):
        for status in (408, 429, 500, 502, 503, 504, 599):
            with self.subTest(status=status):
                transport = Transport([status] * 4 + [200] * 4)
                self.read_credentials(transport)
                self.assertEqual(len(transport.requests), 8)
                self.assertEqual(transport.delays, [2, 4, 8])

    def test_transport_errors_recover_with_exponential_backoff(self):
        for error_type in (ServiceRequestError, ServiceResponseError):
            with self.subTest(error_type=error_type):
                transport = Transport([error_type("test")] * 4 + [200] * 4)
                self.read_credentials(transport)
                self.assertEqual(len(transport.requests), 8)
                self.assertEqual(transport.delays, [2, 4, 8])

    def test_retry_after_headers_override_local_backoff(self):
        for headers, delay in (
            ({"Retry-After": "15"}, 15),
            ({"x-ms-retry-after-ms": "1500"}, 1.5),
        ):
            with self.subTest(headers=headers):
                transport = Transport([(429, headers)] + [200] * 4)
                self.read_credentials(transport)
                self.assertEqual(transport.delays, [delay])

    def test_permanent_http_errors_fail_without_retry(self):
        for status, code in (
            (400, "KEY_VAULT_REQUEST_FAILED"),
            (401, "KEY_VAULT_AUTHENTICATION_FAILED"),
            (403, "KEY_VAULT_ACCESS_DENIED"),
            (404, "KEY_VAULT_SECRET_NOT_FOUND"),
        ):
            with self.subTest(status=status):
                transport = Transport([(status, {"Retry-After": "15"})])
                with self.assertRaises(updater.ToolError) as raised:
                    self.read_credentials(transport)
                self.assertEqual(raised.exception.code, code)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(len(transport.requests), 1)
                self.assertEqual(transport.delays, [])

    def test_authentication_exception_is_not_retried(self):
        transport = Transport([ClientAuthenticationError("test")])
        with self.assertRaises(updater.ToolError) as raised:
            self.read_credentials(transport)
        self.assertEqual(raised.exception.code, "KEY_VAULT_AUTHENTICATION_FAILED")
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(transport.delays, [])

    def test_http_retry_exhaustion_preserves_structured_error(self):
        transport = Transport([503] * 5)
        with self.assertRaises(updater.ToolError) as raised:
            self.read_credentials(transport)
        self.assertEqual(raised.exception.code, "KEY_VAULT_REQUEST_FAILED")
        self.assertEqual(raised.exception.http_status, 503)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.details["secret_name"], "snow-clientid")
        self.assertEqual(len(transport.requests), 5)
        self.assertEqual(transport.delays, [2, 4, 8])

    def test_transport_retry_exhaustion_preserves_structured_error(self):
        for error_type in (ServiceRequestError, ServiceResponseError):
            with self.subTest(error_type=error_type):
                transport = Transport([error_type("test")] * 5)
                with self.assertRaises(updater.ToolError) as raised:
                    self.read_credentials(transport)
                self.assertEqual(raised.exception.code, "KEY_VAULT_UNREACHABLE")
                self.assertTrue(raised.exception.retryable)
                self.assertEqual(len(transport.requests), 5)
                self.assertEqual(transport.delays, [2, 4, 8])

    def test_mixed_failures_share_total_retry_budget(self):
        transport = Transport([
            ServiceRequestError("test"), 503, ServiceResponseError("test"), 429, 503,
        ])
        with self.assertRaises(updater.ToolError):
            self.read_credentials(transport)
        self.assertEqual(len(transport.requests), 5)
        self.assertEqual(transport.delays, [2, 4, 8])

    def test_completed_secrets_are_not_replayed(self):
        transport = Transport([200, 503, 200, 200, 200])
        self.read_credentials(transport)
        secret_names = [
            request.url.split("/secrets/")[1].split("/")[0]
            for request in transport.requests
        ]
        self.assertEqual(secret_names, [
            "snow-clientid", "snow-clientsecret", "snow-clientsecret",
            "snow-username", "snow-password",
        ])

    def test_empty_secret_is_not_retried(self):
        transport = Transport([200])
        with patch.object(Response, "body", return_value=b'{"value": ""}'):
            with self.assertRaises(updater.ToolError) as raised:
                self.read_credentials(transport)
        self.assertEqual(raised.exception.code, "KEY_VAULT_SECRET_EMPTY")
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(transport.delays, [])


if __name__ == "__main__":
    unittest.main()

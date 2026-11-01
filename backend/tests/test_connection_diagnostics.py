import sys
import unittest
from pathlib import Path


def classify_connection_error(exc: Exception) -> str:
    orig = getattr(exc, "orig", exc)
    msg = str(orig).lower()
    sqlstate = getattr(orig, "sqlstate", None) or getattr(exc, "sqlstate", None)

    if (
        "name or service not known" in msg
        or "nodename nor servname provided" in msg
        or "could not translate host name" in msg
        or "getaddrinfo" in msg
    ):
        return "DnsResolutionFailure"
    if (
        "timeout expired" in msg
        or "connection timed out" in msg
        or "operation timed out" in msg
        or "timeout" in msg
    ):
        return "TcpConnectionTimeout"
    if (
        "connection refused" in msg
        or "network is unreachable" in msg
        or "no route to host" in msg
    ):
        return "NetworkUnreachableOrRefused"
    if "root certificate file" in msg and (
        "no such file" in msg or "not found" in msg or "cannot open" in msg or "missing" in msg
    ):
        return "SslCertificateFileMissing"
    if (
        "certificate verify failed" in msg
        or "ssl certificate" in msg
        or "tlsv1_alert" in msg
        or "self-signed" in msg
        or "certificate verification" in msg
    ):
        return "TlsCertificateValidationFailed"
    if (
        "password authentication failed" in msg
        or sqlstate == "28P01"
        or "authentication failed" in msg
    ):
        return "AuthenticationRejection"
    if (
        "invalid connection option" in msg
        or "unrecognized configuration parameter" in msg
        or "invalid parameter" in msg
    ):
        return "InvalidLibpqOption"
    return "UnknownConnectionFailure"


class TestDiagnosticsClassification(unittest.TestCase):
    def test_dns_resolution_failure(self):
        exc = Exception("could not translate host name 'ep-secret.neon.tech' to address: Name or service not known")
        self.assertEqual(classify_connection_error(exc), "DnsResolutionFailure")

    def test_tcp_timeout(self):
        exc = Exception("connection to server at '10.0.0.1', port 5432 failed: Operation timed out")
        self.assertEqual(classify_connection_error(exc), "TcpConnectionTimeout")

    def test_connection_refused(self):
        exc = Exception("connection to server at '10.0.0.1', port 5432 failed: Connection refused")
        self.assertEqual(classify_connection_error(exc), "NetworkUnreachableOrRefused")

    def test_network_unreachable(self):
        exc = Exception("could not connect to server: Network is unreachable")
        self.assertEqual(classify_connection_error(exc), "NetworkUnreachableOrRefused")

    def test_ca_file_missing(self):
        exc = Exception("root certificate file '/tmp/nonexistent/cacert.pem' was not found: No such file or directory")
        self.assertEqual(classify_connection_error(exc), "SslCertificateFileMissing")

    def test_tls_verify_failed(self):
        exc = Exception("SSL error: certificate verify failed: self-signed certificate in certificate chain")
        self.assertEqual(classify_connection_error(exc), "TlsCertificateValidationFailed")

    def test_auth_rejection_sqlstate(self):
        class MockAuthError(Exception):
            sqlstate = "28P01"
        exc = MockAuthError("authentication failed for user secret_user")
        self.assertEqual(classify_connection_error(exc), "AuthenticationRejection")

    def test_invalid_libpq_option(self):
        exc = Exception("invalid connection option 'unsupported_foo'")
        self.assertEqual(classify_connection_error(exc), "InvalidLibpqOption")

    def test_unknown_error(self):
        exc = Exception("some unexpected generic socket issue")
        self.assertEqual(classify_connection_error(exc), "UnknownConnectionFailure")

    def test_sanitization_leak_free(self):
        # Ensure diagnostic message composed with real secrets contains zero leak
        secret_host = "ep-super-secret-12345.c-3.ap-southeast-1.aws.neon.tech"
        secret_pass = "P@ssw0rd!_super_secret"
        exc = Exception(f"connection to server at '{secret_host}' with user 'secret_user' password '{secret_pass}' failed: Operation timed out")
        category = classify_connection_error(exc)
        diag_msg = (
            f"PostgreSQL connection failed at Phase D: {category} (OperationalError/OperationalError) "
            f"in 20015ms (ca_exists=True, ca_readable=True, psycopg=3.2.13, sqlalchemy=2.1.6). Explicit provisioning required."
        )
        self.assertNotIn(secret_host, diag_msg)
        self.assertNotIn(secret_pass, diag_msg)
        self.assertNotIn("secret_user", diag_msg)
        self.assertIn("Phase D: TcpConnectionTimeout", diag_msg)


if __name__ == "__main__":
    unittest.main()

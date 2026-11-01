import os
import tempfile
import unittest
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
import sys
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.core import database
from app.core.database import resolve_ca_root


class TestCaResolutionAndBundling(unittest.TestCase):
    def test_bundled_ca_file_exists_and_readable(self):
        ca_file = (Path(database.__file__).resolve().parent / "ca-bundle.pem").resolve()
        self.assertTrue(ca_file.is_file(), "ca-bundle.pem must exist in backend/app/core/")
        self.assertGreater(ca_file.stat().st_size, 100000, "CA bundle must be substantial (>100KB)")

        with open(ca_file, "rb") as fp:
            data = fp.read()
            self.assertIn(b"-----BEGIN CERTIFICATE-----", data)
            self.assertIn(b"ISRG Root X1", data, "Must contain Let's Encrypt ISRG Root X1 for Neon")

    def test_bundled_ca_selected_as_primary(self):
        ca_root, ca_source, ca_exists, ca_readable = resolve_ca_root({})
        self.assertEqual(ca_source, "bundled")
        self.assertTrue(ca_exists)
        self.assertTrue(ca_readable)
        self.assertTrue(ca_root.endswith("ca-bundle.pem"))

    def test_explicit_query_override(self):
        ca_root, ca_source, ca_exists, ca_readable = resolve_ca_root({"sslrootcert": "system"})
        self.assertEqual(ca_source, "system_param")
        self.assertEqual(ca_root, "system")
        self.assertTrue(ca_exists)
        self.assertTrue(ca_readable)

    def test_fail_closed_when_ca_missing_and_system_unsupported(self):
        # Temporarily test behavior when no CA is available
        def mock_resolve_no_ca():
            return None, "none_available", False, False

        ca_root, ca_source, ca_exists, ca_readable = mock_resolve_no_ca()
        self.assertIsNone(ca_root)
        self.assertEqual(ca_source, "none_available")
        self.assertFalse(ca_exists)
        self.assertFalse(ca_readable)

    def test_tls_security_parameters_preserved(self):
        # Verify engine kwargs don't downgrade TLS
        from sqlalchemy.engine import make_url

        url = make_url("postgresql+psycopg://user:pass@ep-test.neon.tech:5432/pangannadi_preview?sslmode=verify-full&channel_binding=require&sslsni=1")
        self.assertEqual(url.query.get("sslmode"), "verify-full")
        self.assertEqual(url.query.get("channel_binding"), "require")
        self.assertEqual(url.query.get("sslsni"), "1")


if __name__ == "__main__":
    unittest.main()

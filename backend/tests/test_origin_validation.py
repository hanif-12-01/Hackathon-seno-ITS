import base64
import hashlib
import json
import os
import unittest
from unittest.mock import MagicMock, patch
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

# Ensure backend path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import security
from app.core.security import (
    OperatorPerimeter,
    trusted_origins,
    is_valid_preview_origin,
    get_vercel_system_origins,
)


def make_auth_header(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


class TestOriginSecurityValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create deterministic operator account
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", b"testpass", salt, 600000)
        cls.pw_hash = f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"
        cls.accounts_json = json.dumps({
            "clara_op": {
                "password_hash": cls.pw_hash,
                "role": "operator"
            }
        })

    def setUp(self):
        self.patcher = patch.dict(os.environ, {
            "PANGANNADI_OPERATOR_ACCOUNTS": self.accounts_json,
            "PANGANNADI_CORS_ORIGINS": "https://pangannadi-adaptive.vercel.app",
            "PANGANNADI_HOSTED": "1",
            "VERCEL": "1",
            "VERCEL_ENV": "preview",
            "VERCEL_URL": "pangannadi-adaptive-1ajas5gj1-clara3.vercel.app",
        })
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_preview_origin_pattern_validation(self):
        # Valid PanganNadi preview URLs
        self.assertTrue(is_valid_preview_origin("https://pangannadi-adaptive-1ajas5gj1-clara3.vercel.app"))
        self.assertTrue(is_valid_preview_origin("https://pangannadi-adaptive-gb7dychn2-clara3.vercel.app"))
        self.assertTrue(is_valid_preview_origin("https://pangannadi-adaptive-git-feat-vercel-neon-stage1-clara3.vercel.app"))

        # Invalid origins - FAIL CLOSED
        self.assertFalse(is_valid_preview_origin("https://attacker-clara3.vercel.app"))
        self.assertFalse(is_valid_preview_origin("https://other-project.vercel.app"))
        self.assertFalse(is_valid_preview_origin("https://pangannadi-adaptive-attacker.vercel.app"))  # missing clara3
        self.assertFalse(is_valid_preview_origin("https://pangannadi-adaptive.evil.com"))
        self.assertFalse(is_valid_preview_origin("http://pangannadi-adaptive-1ajas5gj1-clara3.vercel.app"))  # insecure HTTP
        self.assertFalse(is_valid_preview_origin("https://pangannadi-adaptive-1ajas5gj1-clara3.vercel.app:8443"))  # port
        self.assertFalse(is_valid_preview_origin("https://pangannadi-adaptive-1ajas5gj1-clara3.vercel.app/cases"))  # path
        self.assertFalse(is_valid_preview_origin("https://*.vercel.app"))  # wildcard
        self.assertFalse(is_valid_preview_origin("*"))
        self.assertFalse(is_valid_preview_origin(""))
        self.assertFalse(is_valid_preview_origin("null"))

    def test_vercel_system_origins_extraction(self):
        os.environ["VERCEL_URL"] = "pangannadi-adaptive-1ajas5gj1-clara3.vercel.app"
        os.environ["VERCEL_BRANCH_URL"] = "pangannadi-adaptive-git-feat-vercel-neon-stage1-clara3.vercel.app"
        os.environ["VERCEL_PROJECT_PRODUCTION_URL"] = "pangannadi-adaptive.vercel.app"

        system_origins = get_vercel_system_origins()
        self.assertIn("https://pangannadi-adaptive-1ajas5gj1-clara3.vercel.app", system_origins)
        self.assertIn("https://pangannadi-adaptive-git-feat-vercel-neon-stage1-clara3.vercel.app", system_origins)
        self.assertIn("https://pangannadi-adaptive.vercel.app", system_origins)

        # Ensure malicious VERCEL_URL is rejected even if set
        os.environ["VERCEL_URL"] = "malicious-app.vercel.app"
        bad_origins = get_vercel_system_origins()
        self.assertNotIn("https://malicious-app.vercel.app", bad_origins)

    def test_operator_perimeter_allows_valid_preview_origin_on_post(self):
        async def dummy_endpoint(request):
            return JSONResponse({"status": "case_created"})

        app = Starlette(routes=[Route("/cases", dummy_endpoint, methods=["POST"])])
        origins = trusted_origins(production=True)
        mock_admission = MagicMock()
        mock_admission.consume.return_value = (True, 60)
        perimeter = OperatorPerimeter(app, origins=origins, admission=mock_admission)
        client = TestClient(perimeter, base_url="https://pangannadi-adaptive-1ajas5gj1-clara3.vercel.app")

        auth_header = make_auth_header("clara_op", "testpass")

        # 1. Valid Preview Origin -> 200 OK
        resp = client.post(
            "/cases",
            headers={
                "Authorization": auth_header,
                "Origin": "https://pangannadi-adaptive-1ajas5gj1-clara3.vercel.app",
                "X-PanganNadi-UI": "1",
            },
            json={"title": "Test Case"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "case_created"})

        # 2. Foreign / Untrusted Origin -> 403 Forbidden
        resp_untrusted = client.post(
            "/cases",
            headers={
                "Authorization": auth_header,
                "Origin": "https://malicious-site.test",
                "X-PanganNadi-UI": "1",
            },
            json={"title": "Test Case"},
        )
        self.assertEqual(resp_untrusted.status_code, 403)
        self.assertEqual(resp_untrusted.json(), {"detail": "Explicit trusted Origin required"})

        # 3. Missing Origin on POST -> 403 Forbidden
        resp_missing = client.post(
            "/cases",
            headers={
                "Authorization": auth_header,
                "X-PanganNadi-UI": "1",
            },
            json={"title": "Test Case"},
        )
        self.assertEqual(resp_missing.status_code, 403)

    def test_production_isolation_blocks_preview_origins_in_production(self):
        # In production mode (VERCEL_ENV=production), preview origins must NOT be accepted
        os.environ["VERCEL_ENV"] = "production"
        os.environ["VERCEL_URL"] = "pangannadi-adaptive.vercel.app"
        os.environ["PANGANNADI_CORS_ORIGINS"] = "https://pangannadi-adaptive.vercel.app"

        async def dummy_endpoint(request):
            return JSONResponse({"status": "created"})

        app = Starlette(routes=[Route("/cases", dummy_endpoint, methods=["POST"])])
        origins = trusted_origins(production=True)
        mock_admission = MagicMock()
        mock_admission.consume.return_value = (True, 60)
        perimeter = OperatorPerimeter(app, origins=origins, admission=mock_admission)
        client = TestClient(perimeter, base_url="https://pangannadi-adaptive.vercel.app")

        auth_header = make_auth_header("clara_op", "testpass")

        # In production, preview origin is REJECTED
        resp = client.post(
            "/cases",
            headers={
                "Authorization": auth_header,
                "Origin": "https://pangannadi-adaptive-1ajas5gj1-clara3.vercel.app",
                "X-PanganNadi-UI": "1",
            },
            json={},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"detail": "Explicit trusted Origin required"})

        # Production origin is ACCEPTED
        resp_prod = client.post(
            "/cases",
            headers={
                "Authorization": auth_header,
                "Origin": "https://pangannadi-adaptive.vercel.app",
                "X-PanganNadi-UI": "1",
            },
            json={},
        )
        self.assertEqual(resp_prod.status_code, 200)


if __name__ == "__main__":
    unittest.main()

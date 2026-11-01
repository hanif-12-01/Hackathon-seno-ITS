"""Stdlib-only packaging regression; no network or deployment."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import package_preview as p


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # In a public source-only checkout, private_runtime is intentionally omitted.
        cls.has_private_runtime = (p.ROOT / 'backend/private_runtime').is_dir()

    def test_real_inputs_and_entrypoints(self):
        if not self.has_private_runtime:
            self.skipTest("Private runtime assets intentionally excluded from public source checkout")
        data = p.package_data()
        self.assertEqual(len(p.ASSETS), 6)
        self.assertTrue(all('backend/private_runtime/' + n in data for n in p.ASSETS))
        for name in ('vercel.json', 'backend/app/vercel.py', 'frontend/src/main.tsx',
                     'backend/pyproject.toml', 'frontend/package-lock.json'):
            self.assertIn(name, data)
        self.assertFalse(any('/tests/' in n or n.startswith('.review/') for n in data))
        self.assertEqual(json.loads(data['vercel.json'])['services']['backend']['entrypoint'], 'app.vercel:app')

    def test_reproducible_exact_set_and_no_overwrite(self):
        if not self.has_private_runtime:
            self.skipTest("Private runtime assets intentionally excluded from public source checkout")
        with tempfile.TemporaryDirectory(prefix='pangannadi-pack-test-') as tmp:
            a, b = Path(tmp) / 'one', Path(tmp) / 'two'
            first, second = p.stage(a), p.stage(b)
            self.assertEqual(first['manifest_sha256'], second['manifest_sha256'])
            with self.assertRaises(ValueError):
                p.stage(a)
            (a / '.env').write_text('TEST_ONLY=not-a-secret')
            with self.assertRaises(ValueError):
                p.verify(a)

    def test_missing_and_modified_private_assets_fail(self):
        with tempfile.TemporaryDirectory(prefix='pangannadi-input-test-') as tmp:
            root = Path(tmp)
            with patch.object(p, 'SOURCE', ()):
                private = root / 'backend/private_runtime'
                private.mkdir(parents=True)
                with self.assertRaises(ValueError):
                    p.payloads(root)
                for name in p.ASSETS:
                    (private / name).write_bytes(b'incorrect test fixture')
                with self.assertRaises(ValueError):
                    p.payloads(root)

    def test_link_and_traversal_refused(self):
        with tempfile.TemporaryDirectory(prefix='pangannadi-link-test-') as tmp:
            root = Path(tmp)
            target = root / 'target'
            target.mkdir()
            link = root / 'link'
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                # Exercise Windows reparse rejection without requiring symlink privilege.
                fake = type('Info', (), {'st_mode': 0, 'st_file_attributes': 0x400})()
                with patch.object(Path, 'lstat', return_value=fake):
                    with self.assertRaises(ValueError):
                        p.regular(root)
            else:
                with self.assertRaises(ValueError):
                    p.regular(link)
            with self.assertRaises(ValueError):
                p.stage(p.ROOT / 'unsafe-stage')
            with patch.object(p, 'SOURCE', ('../outside',)):
                with self.assertRaises(ValueError):
                    p.payloads()

    def test_modified_stage_content_refused(self):
        if not self.has_private_runtime:
            self.skipTest("Private runtime assets intentionally excluded from public source checkout")
        with tempfile.TemporaryDirectory(prefix='pangannadi-tamper-test-') as tmp:
            root = Path(tmp) / 'stage'
            p.stage(root)
            (root / 'backend/app/vercel.py').write_text('tampered')
            with self.assertRaises(ValueError):
                p.verify(root)


if __name__ == '__main__':
    unittest.main()

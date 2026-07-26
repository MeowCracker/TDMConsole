from __future__ import annotations

import unittest

from yarl import URL

from tdm_cli.util import MASKED_PASSWORD, mask_proxy


class MaskProxyTests(unittest.TestCase):
    def test_empty_url_stays_empty(self) -> None:
        self.assertEqual(mask_proxy(URL()), "")
        self.assertEqual(mask_proxy(""), "")

    def test_url_without_credentials_is_unchanged(self) -> None:
        self.assertEqual(mask_proxy(URL("http://proxy.example:8080")), "http://proxy.example:8080")

    def test_password_is_masked(self) -> None:
        masked = mask_proxy(URL("http://user:hunter2@proxy.example:8080"))

        self.assertNotIn("hunter2", masked)
        self.assertIn(f":{MASKED_PASSWORD}@", masked)
        self.assertIn("user", masked)
        self.assertIn("proxy.example", masked)

    def test_accepts_plain_strings(self) -> None:
        masked = mask_proxy("socks5://user:secret@host:1080")

        self.assertNotIn("secret", masked)
        self.assertIn(f":{MASKED_PASSWORD}@", masked)


if __name__ == "__main__":
    unittest.main()

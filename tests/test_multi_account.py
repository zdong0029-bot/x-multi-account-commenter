import os
import tempfile
import unittest

os.environ.setdefault("X_ASSISTANT_DATA", tempfile.mkdtemp(prefix="xa-test-"))
import app  # noqa: E402


class MultiAccountTests(unittest.TestCase):
    def test_device_secret_resume(self):
        pool = app.BridgePool()
        ext = "b" * 32
        token, client = pool.pair(pool.code, ext, "client-resume-01", "t")
        secret = pool.issue_device_secret(client)
        token2, client2 = pool.resume(client, secret, ext)
        self.assertEqual(client2, client)
        self.assertNotEqual(token, token2)
        with self.assertRaises(ValueError):
            pool.resume(client, "wrong", ext)

    def test_profile_link_ignores_bad_ids(self):
        manager = app.ProfileManager()
        manager._save([dict(id="p0000abcd", label="x", client_id="", created=0)])
        manager.link("../evil", "c1")
        manager.link("p0000abcd", "client-xyz-0001")
        self.assertEqual(manager.listing()[0]["client_id"], "client-xyz-0001")

    def test_setup_key_expires_unknown(self):
        self.assertIsNone(app.ProfileManager().setup_info("nope"))


if __name__ == "__main__":
    unittest.main()

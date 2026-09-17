import os
import unittest

from agent_body.auth import AuthError, GatewayAuth, hash_key


class GatewayAuthTest(unittest.TestCase):
    def setUp(self):
        # 清掉可能的环境影响
        self._unset = []
        for k in ("SA_AUTH_ALLOWED_PRINCIPALS", "SA_AUTH_ALLOWED_API_KEYS"):
            if k in os.environ:
                self._unset.append((k, os.environ[k]))
                del os.environ[k]

    def tearDown(self):
        for k, v in self._unset:
            os.environ[k] = v

    def test_principal_allowed_and_denied(self):
        a = GatewayAuth(allowed_principals=["alice", "user-7"])
        self.assertTrue(a.authorize_principal("alice"))
        self.assertTrue(a.authorize_principal("user-7"))
        self.assertFalse(a.authorize_principal("bob"))
        self.assertFalse(a.authorize_principal(""))
        self.assertFalse(a.authorize_principal(None))

    def test_require_principal_raises(self):
        a = GatewayAuth(allowed_principals=["alice"])
        a.require_principal("alice")  # 不抛
        with self.assertRaises(AuthError):
            a.require_principal("mallory")

    def test_api_key_valid_invalid(self):
        a = GatewayAuth(api_key_hashes=[hash_key("secret-token")])
        self.assertTrue(a.authorize_api_key("secret-token"))
        self.assertFalse(a.authorize_api_key("wrong"))
        self.assertFalse(a.authorize_api_key(""))
        self.assertFalse(a.authorize_api_key(None))

    def test_key_stored_as_hash_not_plaintext(self):
        a = GatewayAuth(api_key_hashes=[hash_key("mykey")])
        self.assertNotIn("mykey", a.key_hashes)
        self.assertIn(hash_key("mykey"), a.key_hashes)

    def test_plaintext_in_config_auto_hashed(self):
        # 配置里直接给明文 key 时自动转哈希校验
        a = GatewayAuth(api_key_hashes=["plain-visible"])
        self.assertTrue(a.authorize_api_key("plain-visible"))

    def test_require_api_key_raises(self):
        a = GatewayAuth(api_key_hashes=[hash_key("ok")])
        a.require_api_key("ok")
        with self.assertRaises(AuthError):
            a.require_api_key("nope")

    def test_add_principal_and_key(self):
        a = GatewayAuth()
        a.add_principal("p1")
        a.add_api_key("k1")
        self.assertTrue(a.authorize_principal("p1"))
        self.assertTrue(a.authorize_api_key("k1"))

    def test_env_loading(self):
        os.environ["SA_AUTH_ALLOWED_PRINCIPALS"] = "p1,p2"
        os.environ["SA_AUTH_ALLOWED_API_KEYS"] = "envkey"  # 明文自动哈希
        a = GatewayAuth()
        self.assertTrue(a.authorize_principal("p2"))
        self.assertTrue(a.authorize_api_key("envkey"))

    def test_hash_key_stable(self):
        self.assertEqual(hash_key("x"), hash_key("x"))
        self.assertEqual(len(hash_key("x")), 64)
        self.assertNotEqual(hash_key("x"), hash_key("y"))


if __name__ == "__main__":
    unittest.main()
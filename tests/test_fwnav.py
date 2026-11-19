"""fwnav の平らにする処理・条件・版の範囲のテスト。python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fwnav  # noqa: E402

CFG = """#config-version=FGT60F-7.4.6-FW-build2726-241119:opmode=1:vdom=0:user=admin
config system global
    set hostname "fgt"
    set admin-sport 8443
end
config system interface
    edit "fortilink"
        set vdom "root"
        set fortilink enable
        set lldp-transmission enable
    next
    edit "port1"
        set vdom "root"
        set lldp-reception disable
    next
end
config firewall ssl-ssh-profile
    edit "deep"
        config https
            set ports 443
        end
    next
end
config system replacemsg mail "email-av-fail"
    set buffer "line1
line2 with \\"quote\\"
end of msg"
end
config firewall policy
    edit 1
        set srcintf "ssl.root"
        set dstintf "port1"
    next
    edit 2
        set name "空 白"
    next
end
"""

VDOM_CFG = """config global
config system global
    set hostname "x"
end
end
config vdom
edit root
config firewall policy
    edit 5
        set srcintf "port2"
    next
end
next
end
"""


class Flatten(unittest.TestCase):
    def setUp(self):
        self.cfg = fwnav.Config(CFG)

    def test_header(self):
        self.assertEqual(self.cfg.header, {"model": "FGT60F", "version": "7.4.6"})

    def test_paths(self):
        lines = {fwnav.fmt_line(*x) for x in self.cfg.lines}
        self.assertIn("system interface[fortilink] | fortilink = enable", lines)
        self.assertIn("firewall ssl-ssh-profile[deep] https | ports = 443", lines)
        self.assertIn("firewall policy[2] | name = 空 白", lines)

    def test_multiline_value_skipped(self):
        # 複数行の値の中の "end" でブロックが閉じないこと
        self.assertTrue(self.cfg.match_lines("firewall policy[1]", "srcintf"))

    def test_vdom(self):
        cfg = fwnav.Config(VDOM_CFG)
        got = [(v, p, k) for v, p, k, _ in cfg.lines if k]
        self.assertIn(("global", "system global", "hostname"), got)
        self.assertIn(("root", "firewall policy[5]", "srcintf"), got)


class Conditions(unittest.TestCase):
    def setUp(self):
        self.cfg = fwnav.Config(CFG)

    def ok(self, c):
        return fwnav.eval_cond(self.cfg, c)[0]

    def test_ops(self):
        self.assertTrue(self.ok({"path": "system global", "key": "admin-sport", "op": "eq", "value": "8443"}))
        self.assertFalse(self.ok({"path": "system global", "key": "admin-sport", "op": "unset"}))
        self.assertTrue(self.ok({"path": "firewall policy[*]", "key": "srcintf", "op": "regex", "value": "^ssl\\."}))
        self.assertTrue(self.ok({"path": "firewall ssl-ssh-profile[*] https", "key": "udp-not-quic", "op": "unset"}))
        self.assertTrue(self.ok({"path": "system interface[*]", "key": "lldp-reception", "op": "in", "value": ["vdom", "disable"]}))
        self.assertFalse(self.ok({"path": "vpn ipsec phase1-interface[*]"}))
        self.assertTrue(self.ok({"path": "vpn ipsec phase1-interface[*]", "op": "absent"}))

    def test_unset_singleton_block_missing(self):
        # system settings が丸ごと無い＝既定値で動いている
        self.assertTrue(self.ok({"path": "system settings", "key": "ike-tcp-port", "op": "unset"}))

    def test_per_block_does_not_mix_blocks(self):
        # fortilink の lldp-reception は未設定（既定）、port1 は disable。
        # per_block でないと port1 の disable を拾ってしまう
        c = {"per_block": "system interface[*]", "all": [
            {"key": "fortilink", "op": "eq", "value": "enable"},
            {"key": "lldp-transmission", "op": "in", "value": ["vdom", "disable"]}]}
        self.assertFalse(self.ok(c))
        c2 = {"per_block": "system interface[*]", "all": [
            {"key": "fortilink", "op": "eq", "value": "enable"},
            {"key": "lldp-reception", "op": "unset"}]}
        self.assertTrue(self.ok(c2))


class Versions(unittest.TestCase):
    KNOWN = ["7.4.6", "7.4.7", "7.4.8", "7.4.9", "7.6.0", "7.6.5", "7.6.6", "7.6.7", "7.6.8", "7.6.9"]

    def test_with_path(self):
        got = fwnav.versions_in_range(self.KNOWN, "7.4.6", "7.6.8", ["7.4.8", "7.6.6"])
        self.assertEqual(got, ["7.4.7", "7.4.8", "7.6.0", "7.6.5", "7.6.6", "7.6.7", "7.6.8"])

    def test_without_path_is_wider(self):
        got = fwnav.versions_in_range(self.KNOWN, "7.4.6", "7.6.8", [])
        self.assertIn("7.4.9", got)
        self.assertNotIn("7.6.9", got)

    def test_already_passed(self):
        self.assertTrue(fwnav.already_passed({"introduced": "7.6.3"}, "7.6.4"))
        self.assertFalse(fwnav.already_passed({"introduced": "7.6.3"}, "7.4.6"))
        by_branch = {"introduced": {"7.4": "7.4.6", "7.6": "7.6.1"}}
        self.assertTrue(fwnav.already_passed(by_branch, "7.4.6"))
        self.assertFalse(fwnav.already_passed(by_branch, "7.4.5"))
        self.assertTrue(fwnav.already_passed(by_branch, "7.6.2"))
        self.assertFalse(fwnav.already_passed(by_branch, "7.2.9"))

    def test_models(self):
        groups = {"ram_2gb": ["FGT60F"]}
        it = {"models": {"only": ["ram_2gb"]}}
        self.assertTrue(fwnav.model_ok(it, "FGT60F", groups))
        self.assertFalse(fwnav.model_ok(it, "FGT100F", groups))
        self.assertIsNone(fwnav.model_ok(it, None, groups))


if __name__ == "__main__":
    unittest.main()

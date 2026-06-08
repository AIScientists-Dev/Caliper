"""Tests for the domain pack registry + install-command allow-list."""
import unittest

from caliper import load_pack


class TestRegistry(unittest.TestCase):
    def test_bio_pack_loads(self):
        pack = load_pack("bio")
        self.assertEqual(pack.name, "bio")
        self.assertGreater(len(pack.tools), 10)
        self.assertIn("salmon", pack.tool_names())

    def test_install_command_allowlist(self):
        pack = load_pack("bio")
        cmd = pack.install_command("salmon")
        self.assertTrue(cmd and "salmon" in cmd)            # vetted command returned
        self.assertIsNone(pack.install_command("rm -rf /"))  # not a pack tool -> no command

    def test_context_renders_tools(self):
        pack = load_pack("bio")
        ctx = pack.as_context()
        self.assertIn("salmon", ctx)
        self.assertIn("when", ctx.lower())


if __name__ == "__main__":
    unittest.main()

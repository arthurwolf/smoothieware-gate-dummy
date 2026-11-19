import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import mdkit  # noqa: E402


class MdkitTests(unittest.TestCase):
    def test_code_block_fence_outgrows_backticks_in_text(self):
        block = mdkit.code_block("a ``` b\nc")
        self.assertTrue(block.startswith("````\n"))
        self.assertTrue(block.endswith("\n````"))

    def test_clip_closes_a_fence_it_cut_inside(self):
        out = mdkit.clip_markdown("```\n" + "line\n" * 100 + "```", 60)
        self.assertEqual(out.count("```"), 2)
        self.assertIn("cut", out)

    def test_clip_closes_an_unterminated_fence_without_a_cut(self):
        self.assertEqual(mdkit.clip_markdown("```\nx", 1000).count("```"), 2)

    def test_mentions_defused_outside_code_only(self):
        out = mdkit.neutralize_mentions("hi @bob\n```\n@x\n```")
        self.assertNotIn("@bob", out)
        self.assertIn("\n@x\n", out)

    def test_square_bar_always_shows_a_change(self):
        bar, _ = mdkit.square_bar(100000, 100016, 524288)
        self.assertEqual(len(bar), 32)
        self.assertIn("🟧", bar)
        bar, _ = mdkit.square_bar(100016, 100000, 524288)
        self.assertIn("🟩", bar)
        self.assertNotIn("🟧", mdkit.square_bar(1000, 1000, 32768)[0])

    def test_safe_prose_keeps_code_and_defuses_the_rest(self):
        out = mdkit.safe_prose("use `a<b>` @bob <script> https://x.y")
        self.assertIn("<code>a&lt;b&gt;</code>", out)
        self.assertNotIn("<script>", out)
        self.assertNotIn("@bob", out)
        self.assertNotIn("https://", out)

    def test_html_table_is_full_width(self):
        self.assertIn('<table width="100%">', mdkit.html_table(["a"], [["b"]]))


if __name__ == "__main__":
    unittest.main()

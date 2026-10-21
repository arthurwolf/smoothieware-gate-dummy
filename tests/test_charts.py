import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import charts  # noqa: E402
import report  # noqa: E402

CHART = json.dumps({"label": "Release (-O2)", "rows": [["Flash", 100000, 100080], ["RAM", 5000, 5000], ["AHB RAM", 0, 8]],
                    "symbols": [["foo<&>()", 80], ["bar", 8]]})


class ChartTests(unittest.TestCase):
    def test_svg_is_valid_xml_and_escapes_names(self):
        import xml.dom.minidom
        label, rows, symbols = charts.parse_chart(CHART)
        svg = charts.build_svg(label, rows, symbols, "abc123", 64, 4)
        xml.dom.minidom.parseString(svg)
        self.assertIn("foo&lt;&amp;&gt;()", svg)
        self.assertIn("added by this PR", svg)
        self.assertIn("+80 B", svg)

    def test_bad_data_is_rejected(self):
        self.assertIsNone(charts.parse_chart("not json"))
        self.assertIsNone(charts.parse_chart(json.dumps({"label": "x", "rows": [["Flash", "a", 1]]})))
        self.assertIsNone(charts.parse_chart(json.dumps({"label": "x", "rows": [["Evil", 1, 1]]})))

    def test_oversize_build_is_capped(self):
        widths = charts.segments(600000, 700000, 524288)
        self.assertAlmostEqual(sum(widths.values()), charts.BAR_W, places=3)

    def test_main_writes_charts_and_report_embeds_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = os.path.join(tmp, "r")
            os.makedirs(res)
            metrics = {"warn_flash": 64, "warn_ram": 4, "base_revision": "abc", "chart_release": CHART, "chart_debug": CHART}
            with open(os.path.join(res, "size.result.json"), "w") as handle:
                json.dump({"stage": "size", "status": "warn", "title": "t", "summary": "s", "details": "", "metrics": metrics}, handle)
            sys.argv = ["charts.py", "--results", res, "--out", os.path.join(tmp, "o")]
            self.assertEqual(charts.main(), 0)
            listing = json.load(open(os.path.join(tmp, "o", "charts.json")))
            self.assertEqual(sorted(listing), ["size-debug", "size-release"])
            self.assertTrue(os.path.exists(os.path.join(tmp, "o", "size-release.svg")))
        text = report.place_charts("a <!--chart:size-release--> b <!--chart:size-debug-->", {"size-release": {"file": "size-release.svg", "alt": 'x"y'}}, "https://h/p")
        self.assertIn('<img src="https://h/p/size-release.svg" alt="x&quot;y" width="760">', text)
        self.assertNotIn("chart:", text)
        self.assertEqual(report.place_charts("<!--chart:size-release-->", {"size-release": {"file": "f", "alt": "a"}}, ""), "")


if __name__ == "__main__":
    unittest.main()

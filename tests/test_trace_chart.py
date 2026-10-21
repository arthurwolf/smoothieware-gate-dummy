import os
import sys
import unittest
import xml.dom.minidom

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sim_stage  # noqa: E402
import trace_chart  # noqa: E402

SIM_DIR = os.environ.get("SMOOTHIE_SIM", os.path.join(ROOT, "..", "smoothie-sim"))


def load():
    return sim_stage.load_step_trace(SIM_DIR)


class TraceChartTests(unittest.TestCase):
    def setUp(self):
        self.st = load()
        with open(os.path.join(ROOT, "cases", "arcs-ijk", "expected.trace")) as handle:
            self.expected = handle.read()

    def mutate(self, indexes):
        lines = self.expected.splitlines()
        for i in indexes:
            lines[3 + i] = lines[3 + i].replace("counts=", "counts=9", 1).replace("ticks=", "ticks=9", 1)
        return "\n".join(lines) + "\n"

    def sample(self, indexes):
        e, a = self.st.parse(self.expected), self.st.parse(self.mutate(indexes))
        return trace_chart.sample_case(e, a, sim_stage.differing_blocks(self.st, e, a))

    def test_sample_is_small_and_representative(self):
        sample = self.sample(range(0, 40))
        self.assertEqual(sample["differing"], 40)
        shown = sum(len(w["blocks"]) for w in sample["windows"])
        self.assertLessEqual(len(sample["windows"]), trace_chart.MAX_WINDOWS)
        self.assertLessEqual(shown, 14)
        self.assertEqual(sample["windows"][0]["blocks"][0]["position"], 0)

    def test_svg_is_valid_xml(self):
        svg = trace_chart.draw_case("delta<&>", self.sample([2, 30]))
        xml.dom.minidom.parseString(svg)
        self.assertIn("delta&lt;&amp;&gt;", svg)

    def test_no_difference_gives_no_sample_and_bad_sample_no_chart(self):
        e = self.st.parse(self.expected)
        self.assertIsNone(trace_chart.sample_case(e, e, set()))
        self.assertIsNone(trace_chart.draw_case("x", {"total": 1}))


if __name__ == "__main__":
    unittest.main()

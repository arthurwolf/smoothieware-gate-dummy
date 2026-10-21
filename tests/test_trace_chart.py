import os
import sys
import unittest
import xml.dom.minidom

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sim_stage  # noqa: E402\nimport pulse_data  # noqa: E402,F401
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
        self.assertLessEqual(len(sample["rows"]), trace_chart.MAX_ROWS)
        self.assertLessEqual(len(sample["blocks"]), trace_chart.MAX_BLOCKS)
        self.assertTrue(trace_chart.valid_sample(sample))

    def test_block_only_chart_says_why_pulses_are_missing(self):
        e, a = self.st.parse(self.expected), self.st.parse(self.mutate([2]))
        sample = trace_chart.sample_case(e, a, sim_stage.differing_blocks(self.st, e, a), None, "no base simulator")
        self.assertIn("Pulse view unavailable: no base simulator", trace_chart.draw_case("x", sample))

    def pulse_sample(self):
        import pulse_data
        def result(shift):
            ticks = [10 * i + (shift if i >= 5 else 0) for i in range(1, 40)]
            return {"steps_per_mm": [100.0], "step_period_us": 10,
                    "blocks": [{"start_tick": 0, "step_ticks": [ticks], "step_tick_stride": [1], "dir_changes": [[[0, 1]]]}]}
        return pulse_data.build(result(0), result(30))

    def test_pulse_sampler_finds_first_shifted_pulse(self):
        sample, reason = self.pulse_sample()
        self.assertIsNone(reason)
        self.assertEqual(sample["anchor"]["index"], 4)
        self.assertEqual(sample["anchor"]["pr"] - sample["anchor"]["ref"], 30)

    def test_pulse_chart_is_valid_and_caps_are_enforced(self):
        import copy
        pulses, _ = self.pulse_sample()
        e = self.st.parse(self.expected)
        sample = trace_chart.sample_case(e, self.st.parse(self.mutate([1])), {1}, pulses)
        svg = trace_chart.draw_case("p", sample)
        xml.dom.minidom.parseString(svg)
        self.assertIn("this pulse moved +300 µs", svg)
        bad = copy.deepcopy(sample)
        bad["pulses"]["axes"][0]["ref_pulses"] = list(range(trace_chart.MAX_PULSES + 1))
        self.assertIsNone(trace_chart.draw_case("p", bad))

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


class RoundTwoFixes(unittest.TestCase):
    def test_reason_is_capped(self):
        import trace_chart
        sample = trace_chart.sample_case({"blocks": []}, {"blocks": []}, [], None, "x" * 201)
        self.assertFalse(trace_chart.valid_sample(sample))

    def test_dir_level_seeded_from_step_dirs(self):
        import pulse_data
        result = {"blocks": [{"start_tick": 0, "step_ticks": [[5, 10]], "step_tick_stride": [1], "step_dirs": [1]}]}
        _, dirs = pulse_data._axis_trains(result)
        self.assertEqual(dirs[0], [(5, 1)])

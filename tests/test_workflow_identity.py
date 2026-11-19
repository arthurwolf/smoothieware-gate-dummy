import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from workflow_identity import candidate, validate, validate_manifest_identity  # noqa: E402


class WorkflowIdentityTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.base = "b" * 40
        self.event = {"workflow_run": {"event": "pull_request", "head_sha": self.sha,
            "pull_requests": [{"number": 12, "head": {"sha": self.sha}, "base": {"sha": self.base}}]}}
        self.pr = {"number": 12, "state": "open", "head": {"sha": self.sha},
                   "base": {"sha": self.base, "repo": {"full_name": "org/repo"}}}

    def test_matching_live_pull_request_is_accepted(self):
        self.assertEqual(candidate(self.event), 12)
        self.assertTrue(validate(self.event, self.pr, "org/repo"))

    def test_ambiguous_or_missing_associations_are_rejected(self):
        for pulls in ([], [self.event["workflow_run"]["pull_requests"][0]] * 2):
            self.event["workflow_run"]["pull_requests"] = pulls
            with self.assertRaises(ValueError):
                candidate(self.event)

    def test_stale_or_wrong_pull_request_is_rejected(self):
        for field, value in (("number", 13), ("state", "closed")):
            changed = dict(self.pr, **{field: value})
            self.assertFalse(validate(self.event, changed, "org/repo"))
        self.pr["head"] = {"sha": "c" * 40}
        self.assertFalse(validate(self.event, self.pr, "org/repo"))

    def test_manifest_must_match_the_trusted_run_and_profile(self):
        manifest = {"profile": "v2-prime", "base": self.base, "head": self.sha}
        self.assertTrue(validate_manifest_identity(self.event, manifest, "v2-prime"))
        self.assertFalse(validate_manifest_identity(self.event, manifest, "v1-lpc1768"))
        self.assertFalse(validate_manifest_identity(self.event, dict(manifest, head="c" * 40), "v2-prime"))


import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scripts'))
from profiles import EXPECTED_STAGES, select_profile, validate_manifest


class ProfileTests(unittest.TestCase):
    def test_profiles_have_distinct_board_and_toolchain(self):
        v1 = select_profile('v1-lpc1768')
        v2 = select_profile('v2-prime')
        self.assertEqual((v1.board, v1.toolchain), ('LPC1768', 'gcc48'))
        self.assertEqual((v2.board, v2.toolchain), ('Prime', 'gcc10'))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            select_profile('v2-lpc1768')

    def test_manifest_requires_exact_controller_identity(self):
        manifest = {'profile': 'v1-lpc1768', 'board': 'LPC1768',
                    'firmware_repository': 'Smoothieware/Smoothieware',
                    'base': 'a'*40, 'head': 'b'*40, 'toolchain': 'gcc48',
                    'toolchain_version': 'GNU Tools for ARM Embedded Processors 4.8.3 20140228 (2014q1)',
                    'simulator_revision': 'c'*40, 'simulator_sha256': 'd'*64,
                    'references_sha256': 'e'*64, 'case_inventory_sha256': 'f'*64,
                    'expected_stages': list(EXPECTED_STAGES)}
        for invalid in (None, '', 'a'*63, 'A'*64, 123):
            with self.subTest(invalid=invalid):
                manifest['controller_sha256'] = invalid
                self.assertTrue(any('controller_sha256' in issue for issue in validate_manifest(manifest, EXPECTED_STAGES)))
        manifest['controller_sha256'] = '1'*64
        self.assertEqual(validate_manifest(manifest, EXPECTED_STAGES), [])

    def test_manifest_revisions_must_be_complete_git_object_ids(self):
        manifest = {'profile': 'v2-prime', 'board': 'Prime', 'firmware_repository': 'Smoothieware/SmoothieV2',
                    'base': 'a' * 40, 'head': 'b' * 40, 'toolchain': 'gcc10',
                    'toolchain_version': 'GNU Arm Embedded Toolchain 10.3-2021.10, 10.3.1 20210824',
                    'controller_sha256': '1' * 64,
                    'simulator_revision': 'c' * 40, 'simulator_sha256': 'd' * 64,
                    'references_sha256': 'e' * 64, 'case_inventory_sha256': 'f' * 64,
                    'expected_stages': list(EXPECTED_STAGES)}
        for length in (39, 41, 63, 65):
            with self.subTest(length=length):
                manifest['head'] = 'b' * length
                self.assertTrue(any('head' in problem for problem in validate_manifest(manifest, EXPECTED_STAGES)))
        for length in (40, 64):
            with self.subTest(valid_length=length):
                manifest['head'] = 'b' * length
                self.assertEqual(validate_manifest(manifest, EXPECTED_STAGES), [])

    def test_manifest_detects_missing_and_duplicate_stages(self):
        manifest = {'profile': 'v2-prime', 'board': 'Prime', 'firmware_repository': 'Smoothieware/SmoothieV2', 'base': 'a'*40, 'head': 'b'*40,
                    'toolchain': 'gcc10', 'simulator_revision': 'c'*40,
                    'toolchain_version': 'GNU Arm Embedded Toolchain 10.3-2021.10, 10.3.1 20210824',
                    'controller_sha256': '1'*64,
                    'simulator_sha256': 'd'*64, 'references_sha256': 'e'*64, 'case_inventory_sha256': 'f'*64,
                    'expected_stages': list(EXPECTED_STAGES)}
        self.assertEqual(validate_manifest(manifest, list(EXPECTED_STAGES)), [])
        self.assertTrue(any('missing' in x for x in validate_manifest(manifest, ['sim'])))
        self.assertTrue(any('duplicate' in x for x in validate_manifest(manifest, list(EXPECTED_STAGES) + ['sim'])))
        manifest['expected_stages'] = ['sim']
        self.assertTrue(any('expected_stages' in x for x in validate_manifest(manifest, ['sim'])))
        manifest['board'] = 'LPC1768'
        self.assertTrue(any('board' in x for x in validate_manifest(manifest, [])))


if __name__ == '__main__':
    unittest.main()

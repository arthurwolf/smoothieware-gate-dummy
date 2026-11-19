"""Run the gate's unittest suite from a snapshotted project tree."""
import os
import sys
import unittest

root = os.path.dirname(os.path.abspath(__file__))
suite = unittest.defaultTestLoader.discover(root, pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(not result.wasSuccessful())

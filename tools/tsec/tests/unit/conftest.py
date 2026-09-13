import os
import sys

# Tests run from the repository root so `tools.tsec` imports and specs/ paths resolve.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

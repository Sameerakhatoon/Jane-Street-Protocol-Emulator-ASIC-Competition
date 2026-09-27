"""python3 host/tse_check ... or python3 -m tse_check ... with host/ on the path."""
import os
import sys

HOST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (HOST, os.path.dirname(HOST)):
    if p not in sys.path:
        sys.path.insert(0, p)

from tse_check.cli import main  # noqa: E402

sys.exit(main())

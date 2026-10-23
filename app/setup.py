"""Install ZIM Creator's libraries into ~/.zim-creator (run by the Windows installer inside WSL)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zc import deps  # noqa: E402

if __name__ == "__main__":
    if sys.version_info < (3, 9):
        sys.exit(f"Python 3.9 or newer is needed in WSL (found {sys.version.split()[0]}).")
    status = deps.ensure(lambda m: print("   " + m, flush=True))
    print("   Libraries ready." + ("" if status["zimcheck"] else " (zimcheck unavailable; a simpler check will be used.)"))

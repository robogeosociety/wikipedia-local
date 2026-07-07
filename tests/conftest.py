import sys
from pathlib import Path

# Scripts are run as files, not a package — put them on the path for imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

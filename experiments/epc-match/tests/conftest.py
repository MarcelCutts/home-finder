"""Put the experiment's modules on the import path.

The experiment directory has a hyphen in its name, so it can't be a package;
its modules are imported by name, the way its scripts run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Print configuration diagnostics from the current process environment only."""
import json
import os
from pathlib import Path
import sys

# Do not load or display credentials from files in a diagnostic command.
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from actions.darwinbox_adapters.configuration import configuration_status


if __name__ == '__main__':
    result = configuration_status()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result['leave']['configured'] and result['holidays']['configured'] else 1)

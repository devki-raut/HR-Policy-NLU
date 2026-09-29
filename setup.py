#!/usr/bin/env python3
"""Create the project runtime virtual environment and install dependencies."""

from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv-runtime"
REQUIREMENTS = ROOT / "requirements.txt"


def run(command):
    print("+", " ".join(map(str, command)))
    subprocess.check_call(command, cwd=ROOT)


def main():
    if not REQUIREMENTS.exists():
        raise SystemExit(f"Missing requirements file: {REQUIREMENTS}")

    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required.")

    if not VENV_DIR.exists():
        print(f"Creating virtual environment: {VENV_DIR}")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
    else:
        print(f"Virtual environment already exists: {VENV_DIR}")

    if sys.platform == "win32":
        python = VENV_DIR / "Scripts" / "python.exe"
    else:
        python = VENV_DIR / "bin" / "python"

    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)])

    print("\nSetup complete.")
    print(f"Virtual environment: {VENV_DIR}")
    print("Next: make train")


if __name__ == "__main__":
    main()

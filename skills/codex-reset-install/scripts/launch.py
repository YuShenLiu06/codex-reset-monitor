#!/usr/bin/env python3
"""Thin Skill entry: exactly the same reviewed Release core."""
from pathlib import Path
import subprocess
import sys
package=Path(__file__).resolve().parents[1]/'assets/codex-reset-install.pyz'
if not package.is_file():raise SystemExit('Use the built Skill Release package; installer asset is not present in the source-only Skill folder')
raise SystemExit(subprocess.call([sys.executable,str(package),*sys.argv[1:]]))

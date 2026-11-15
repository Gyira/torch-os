"""Restore a downloaded encrypted Baidu namespace into ordinary files, offline."""
import argparse
import getpass
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.companion_restore import restore_companion

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cloud_directory", type=Path)
    parser.add_argument("pairing_code_file", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(restore_companion(args.cloud_directory, args.pairing_code_file.read_text(encoding="utf-8").strip(),
                                      getpass.getpass("配对码密码: "), args.destination), ensure_ascii=False))

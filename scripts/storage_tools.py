"""Copy-only preparation and independent restore; never switches active user data."""
from pathlib import Path
import argparse
import getpass
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.storage_layout import new_layout, StorageLayout
from backend.app.storage_migration import prepare_split
from backend.app.storage_bundle import create_bundle, restore_bundle


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("source", type=Path); prepare.add_argument("--database", type=Path, required=True)
    prepare.add_argument("--target", type=Path, required=True); prepare.add_argument("--writers-stopped", action="store_true")
    backup = commands.add_parser("backup"); backup.add_argument("device_config", type=Path); backup.add_argument("destination", type=Path)
    restore = commands.add_parser("restore"); restore.add_argument("package", type=Path); restore.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_split(args.source, new_layout(args.target), database_path=args.database, writers_stopped=args.writers_stopped)
        print(json.dumps({"status": result["status"], "device_config": result["device_config"]}, ensure_ascii=False))
    elif args.command == "backup":
        password = getpass.getpass("恢复包密码（至少8位，建议更长）: ")
        if len(password) < 8:
            parser.error("备份口令至少 8 位；未创建恢复包")
        print(json.dumps(create_bundle(StorageLayout.load(args.device_config), args.destination, password=password), ensure_ascii=False))
    else:
        print(json.dumps(restore_bundle(args.package, args.destination, password=getpass.getpass("恢复包密码: ")), ensure_ascii=False))

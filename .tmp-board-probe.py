import json
import sys

sys.path.insert(0, r"C:\Users\19814\Desktop\Torch\torch-os\backend")
from app import tasks  # noqa: E402

board = tasks.read_tasks(r"C:\Users\19814\Desktop\Torch\个人数据库", read_only=True)
print("lists:")
for entry in board["lists"]:
    print(" ", json.dumps(entry, ensure_ascii=False))
print("folders:", board["folders"])
print("tasks:", [(t["title"], t["list_name"]) for t in board["tasks"]])
print("warnings:", board["warnings"])
print("revision:", board["revision"])

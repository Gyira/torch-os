import sqlite3
import sys

path = r"C:\Users\19814\Desktop\Torch\个人数据库\.torch-os\torch_os.sqlite3"
with sqlite3.connect(path) as connection:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT event_type, detail_json, created_at FROM audit_events "
        "WHERE event_type LIKE 'list_%' OR event_type LIKE '%task_layout%' "
        "ORDER BY created_at"
    ).fetchall()
for row in rows:
    print(row["created_at"], row["event_type"], row["detail_json"])

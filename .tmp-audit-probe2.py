import sqlite3

path = r"C:\Users\19814\Desktop\Torch\个人数据库\.torch-os\torch_os.sqlite3"
with sqlite3.connect(path) as connection:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT event_type, detail_json, created_at FROM audit_events "
        "WHERE created_at >= '2026-10-30T12:00' ORDER BY created_at"
    ).fetchall()
for row in rows:
    print(row["created_at"], row["event_type"], row["detail_json"])

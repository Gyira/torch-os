import re
p=r"torch-os\backend\app\tasks.py"
s=open(p,encoding="utf-8").read()
for m in re.finditer(r"^(TASKS_[A-Z_0-9]*|TASK_[A-Z_0-9]*)\s*=.*$", s, re.M):
    print(m.group(0))
print("----- vault_layout constants -----")
p2=r"torch-os\backend\app\vault_layout.py"
s2=open(p2,encoding="utf-8").read()
for m in re.finditer(r"^(TASK_[A-Z_0-9]*|[A-Z_0-9]*TASK[A-Z_0-9]*)\s*=.*$", s2, re.M):
    print(m.group(0))

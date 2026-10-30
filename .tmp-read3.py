import re
p=r"torch-os\backend\app\tasks.py"
s=open(p,encoding="utf-8").read().splitlines()
for i,l in enumerate(s):
    if re.match(r"^(def |class )", l):
        print(f"{i+1}: {l}")

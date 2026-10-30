import io,sys
p=r"torch-os\backend\app\tasks.py"
lines=open(p,encoding="utf-8").read().splitlines()
for i in range(370,440):
    print(f"{i+1}: {lines[i]}")
print("-----")
for i in range(720,790):
    print(f"{i+1}: {lines[i]}")

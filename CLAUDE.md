# torch-os 项目规则

## 测试与环境（Windows / Git Bash）

### 后端测试

- 使用仓库虚拟环境：`./.venv/Scripts/python.exe -m pytest ...`。路径本身没有问题，不要改成 `/c/...`。
- **默认只跑改动相关的测试文件**，不要每次都跑完整套件。完整后端套件有 1027 个测试，
  实测约 **3 分 16 秒**（2026-10-18）；没有单个测试卡死，最慢的一个约 9 秒。
  感觉“卡半天”通常不是字符问题，而是命令把输出管道给了 `tail`。
- **长测试命令不要写成 `pytest ... 2>&1 | tail -N`**：`tail` 在管道关闭前不会输出，
  整个测试期间终端一片空白，看起来像卡死。需要看尾部结果时，先重定向到日志，再单独读：
  ```bash
  PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m pytest -q > .pytest.log 2>&1
  tail -12 .pytest.log
  ```
- Windows 下中文报错显示成乱码是终端/子进程编码问题，不是测试失败；始终加：
  `PYTHONUTF8=1 PYTHONIOENCODING=utf-8`。
- 跑完整套件时给至少 300 秒超时，或放到后台执行。已知有 5 个与业务无关的既有失败
  （缺少 `docs/` 文件、天气测试编码），不要把它们当成本次改动引入的问题。
- 推荐命令：
  - 改动相关：`... -m pytest tests/test_dida_sync.py tests/test_dida_mapping.py -q`
  - 快速失败：`... -m pytest -x -q --tb=short`
  - 完整套件：`... -m pytest -q --durations=10`
- `tests/test_qa_runtime.py` 在 Windows 下可能出现子进程 `UnicodeDecodeError` 警告，
  这是既有环境问题，不影响测试通过，不要据此反复排查。
- 前端测试：`node --experimental-vm-modules --test frontend/tests/*.test.mjs`。
- 文件操作一律使用完整绝对 Windows 路径（`C:\...`），不要用 `/c/...` 或相对路径。

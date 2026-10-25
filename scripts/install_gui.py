"""图形安装器：双击 `安装.cmd` 打开，把「建 venv → 装依赖 → 生成 .env → 铺示例」变成点一下。

这是项目**唯一的安装入口**。只用标准库（tkinter），所以它在项目依赖装好之前就能跑。
安装动作：`python -m venv`、`pip install -r requirements.lock.txt`、`scripts/check_deps.py`
（已装好就跳过，不重复下载）、复制 `.env`、铺示例数据、可选把 Obsidian 插件装进指定库。

界面在主线程，安装在后台线程；日志与步骤状态经 queue 回传——tkinter 不允许跨线程改控件。
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

URL = 'http://127.0.0.1:8765'
HEALTH_URL = URL + '/api/health'
MIRROR_URL = 'https://pypi.tuna.tsinghua.edu.cn/simple'
VENV_DIR = '.venv'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

STEP_ORDER = ('env', 'venv', 'deps', 'envfile', 'examples', 'obsidian')
STEP_LABELS = {
    'env': '检查本机环境',
    'venv': '创建虚拟环境 .venv',
    'deps': '安装依赖（已装好会自动跳过）',
    'envfile': '生成配置文件 .env',
    'examples': '铺示例数据',
    'obsidian': '安装 Obsidian 插件',
}
STEP_GLYPH = {'pending': '○', 'running': '●', 'ok': '✓', 'skip': '–', 'error': '✗'}
STEP_COLOR = {
    'pending': '#999999',
    'running': '#1a6fb5',
    'ok': '#2e7d32',
    'skip': '#999999',
    'error': '#c62828',
}
FINAL_STATES = ('ok', 'skip', 'error')


class Cancelled(Exception):
    """用户点了取消：正在跑的子进程已经结束，调用方别再往下走。"""


@dataclass
class InstallPlan:
    root: Path
    seed_examples: bool
    obsidian_vault: str
    mirror_retry: bool


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def venv_python(root: Path) -> Path:
    if os.name == 'nt':
        return Path(root) / VENV_DIR / 'Scripts' / 'python.exe'
    return Path(root) / VENV_DIR / 'bin' / 'python'


def python_version_supported(info) -> bool:
    return tuple(info[:2]) >= (3, 10)


def is_obsidian_vault(path: str | Path) -> bool:
    return bool(path) and (Path(path) / '.obsidian').is_dir()


def ensure_env_file(root: Path) -> bool:
    """缺 .env 就从 .env.example 复制一份；已有则一个字都不动。返回是否新建。"""
    target = Path(root) / '.env'
    if target.exists():
        return False
    shutil.copyfile(Path(root) / '.env.example', target)
    return True


def copy_examples(root: Path) -> int:
    """把 examples/vault 合并进 data/vault：不删文件，同名文件覆盖。"""
    source = Path(root) / 'examples' / 'vault'
    target = Path(root) / 'data' / 'vault'
    if not source.is_dir():
        raise FileNotFoundError(f'找不到示例资料：{source}')
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in source.rglob('*'):
        destination = target / path.relative_to(source)
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            copied += 1
    return copied


def is_service_up(timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def launch_service(root: Path) -> None:
    python = venv_python(root)
    if not python.exists():
        raise FileNotFoundError(f'找不到 {python}，请先完成安装。')
    flags = 0
    if os.name == 'nt':
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(python), '-m', 'backend.app.run'],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def run_command(cmd: list, emit, cancel: threading.Event, cwd: Path) -> int:
    """跑一条命令并把输出逐行送进日志；返回退出码。取消时 terminate 后抛 Cancelled。"""
    if cancel.is_set():
        raise Cancelled()
    cmd = [str(part) for part in cmd]
    emit('log', '')
    emit('log', '> ' + ' '.join(cmd))
    emit('busy', True)
    environment = os.environ.copy()
    environment.update({
        'PYTHONUTF8': '1',
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUNBUFFERED': '1',
        'PIP_DISABLE_PIP_VERSION_CHECK': '1',
    })
    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=environment,
            creationflags=NO_WINDOW,
        )
    except OSError:
        emit('busy', False)
        raise
    finished = threading.Event()

    def stop_on_cancel() -> None:
        while not finished.wait(0.1):
            if cancel.is_set():
                try:
                    process.terminate()
                except OSError:
                    pass
                return

    threading.Thread(target=stop_on_cancel, daemon=True).start()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            emit('log', line.rstrip())
        code = process.wait()
    finally:
        finished.set()
        emit('busy', False)
    if cancel.is_set():
        raise Cancelled()
    return code


def run_install(plan: InstallPlan, emit, cancel: threading.Event) -> tuple[bool, str]:
    """按顺序执行安装步骤。返回 (是否成功, 给用户看的一句话)。"""
    root = Path(plan.root)

    def mark(step: str, state: str) -> None:
        emit('step', (step, state))

    # 1. 本机环境
    mark('env', 'running')
    emit('log', f'Python {sys.version.split()[0]}（{sys.executable}）')
    emit('log', f'仓库目录：{root}')
    if not python_version_supported(sys.version_info):
        emit('log', '需要 Python 3.10 或更高版本（推荐 3.12）。')
        mark('env', 'error')
        return False, 'Python 版本过低'
    mark('env', 'ok')

    # 2. 虚拟环境
    mark('venv', 'running')
    python = venv_python(root)
    if python.exists():
        emit('log', f'虚拟环境已存在：{python.parent.parent}')
        mark('venv', 'skip')
    else:
        code = run_command([sys.executable, '-m', 'venv', str(root / VENV_DIR)], emit, cancel, root)
        if code != 0 or not python.exists():
            mark('venv', 'error')
            return False, '创建虚拟环境失败'
        emit('log', f'已创建：{root / VENV_DIR}')
        mark('venv', 'ok')

    # 3. 依赖：先体检，缺了才装；官方源失败且允许时换镜像再试一次。
    mark('deps', 'running')
    check = run_command([python, root / 'scripts' / 'check_deps.py'], emit, cancel, root)
    if check == 0:
        emit('log', '依赖已装好，跳过 pip install（不联网）。')
        mark('deps', 'skip')
    elif check == 2:
        mark('deps', 'error')
        return False, '读不了 requirements.lock.txt'
    else:
        base = [python, '-m', 'pip', 'install', '--progress-bar', 'off']
        code = run_command(base + ['-r', 'requirements.lock.txt'], emit, cancel, root)
        if code != 0 and plan.mirror_retry:
            emit('log', '')
            emit('log', '官方源安装失败，改用清华镜像重试一次…')
            code = run_command(base + ['-i', MIRROR_URL, '-r', 'requirements.lock.txt'],
                               emit, cancel, root)
        if code != 0:
            mark('deps', 'error')
            return False, '依赖安装失败（日志里有 pip 的原始报错）'
        verify = run_command([python, root / 'scripts' / 'check_deps.py'], emit, cancel, root)
        if verify != 0:
            mark('deps', 'error')
            return False, '依赖装完后复核仍未通过（镜像可能给了别的版本）'
        mark('deps', 'ok')

    # 4. .env
    mark('envfile', 'running')
    if ensure_env_file(root):
        emit('log', '已从 .env.example 生成 .env（DeepSeek Harness 启动命令可在这里覆盖）。')
        mark('envfile', 'ok')
    else:
        emit('log', '.env 已存在，未改动。')
        mark('envfile', 'skip')

    # 5. 示例数据
    mark('examples', 'running')
    if plan.seed_examples:
        try:
            copied = copy_examples(root)
        except OSError as error:
            emit('log', f'铺示例数据失败：{error}')
            mark('examples', 'error')
            return False, '铺示例数据失败'
        emit('log', f'已铺示例数据：{copied} 个文件 → data\\vault（同名文件已覆盖）')
        mark('examples', 'ok')
    else:
        emit('log', '未选择铺示例数据。')
        mark('examples', 'skip')

    # 6. Obsidian 插件（可选）
    mark('obsidian', 'running')
    if plan.obsidian_vault:
        if not is_obsidian_vault(plan.obsidian_vault):
            emit('log', f'不是 Obsidian 库根目录（缺 .obsidian）：{plan.obsidian_vault}')
            mark('obsidian', 'error')
            return False, 'Obsidian 库路径不对'
        script = root / 'scripts' / 'install-obsidian-plugin.ps1'
        code = run_command(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script,
             '-VaultPath', plan.obsidian_vault],
            emit, cancel, root,
        )
        if code != 0:
            mark('obsidian', 'error')
            return False, 'Obsidian 插件安装失败'
        mark('obsidian', 'ok')
    else:
        emit('log', '未选择安装 Obsidian 插件。')
        mark('obsidian', 'skip')

    return True, '安装完成'


class InstallerApp:
    def __init__(self, tk_root: tk.Tk):
        self.tk = tk_root
        self.repo = repo_root()
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.step_state: dict[str, str] = {}
        self.busy_mode = False
        self._build()
        self.tk.after(80, self._drain)

    # ---------- 界面 ----------

    def _build(self) -> None:
        self.tk.title('薪火OS 安装器')
        self.tk.minsize(760, 660)
        self._center(880, 820)
        style = ttk.Style(self.tk)
        for theme in ('vista', 'clam'):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure('Accent.TButton', font=('Segoe UI', 10, 'bold'))

        self.tk.columnconfigure(0, weight=1)
        self.tk.rowconfigure(4, weight=1)

        header = ttk.Frame(self.tk, padding=(18, 14, 18, 4))
        header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text='薪火OS 安装器', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(
            header,
            text='本地优先：数据和配置全部留在这台电脑上。只有缺少依赖时才会联网下载。',
            foreground='#666666',
        ).pack(anchor='w', pady=(2, 0))
        ttk.Label(
            self.tk,
            text=f'Python {sys.version.split()[0]}   ·   仓库：{self.repo}',
            foreground='#666666',
            padding=(18, 0, 18, 8),
        ).grid(row=1, column=0, sticky='w')

        steps = ttk.LabelFrame(self.tk, text='安装步骤', padding=(12, 2))
        steps.grid(row=2, column=0, sticky='ew', padx=18, pady=(0, 8))
        self.glyphs: dict[str, ttk.Label] = {}
        for row, key in enumerate(STEP_ORDER):
            glyph = ttk.Label(steps, text=STEP_GLYPH['pending'], width=3,
                              font=('Segoe UI', 10), foreground=STEP_COLOR['pending'])
            glyph.grid(row=row, column=0, sticky='w')
            ttk.Label(steps, text=STEP_LABELS[key], font=('Segoe UI', 10)).grid(
                row=row, column=1, sticky='w')
            self.glyphs[key] = glyph
            self.step_state[key] = 'pending'

        options = ttk.LabelFrame(self.tk, text='选项', padding=(12, 2))
        options.grid(row=3, column=0, sticky='ew', padx=18, pady=(0, 8))
        options.columnconfigure(1, weight=1)

        vault_exists = (self.repo / 'data' / 'vault').exists()
        self.var_seed = tk.BooleanVar(value=not vault_exists)
        ttk.Checkbutton(options, text='同时铺一份示例数据到 data\\vault',
                        variable=self.var_seed).grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(
            options,
            text=('data\\vault 已存在：勾选会用示例覆盖同名文件，不删其它文件。' if vault_exists
                  else '示例资料全是虚构数据，不影响以后改成自己的库。'),
            foreground='#777777',
        ).grid(row=1, column=0, columnspan=3, sticky='w', padx=(22, 0))

        self.var_obsidian = tk.BooleanVar(value=False)
        self.vault_var = tk.StringVar(value='')
        ttk.Checkbutton(options, text='把工作台装进 Obsidian（可选）',
                        variable=self.var_obsidian).grid(row=2, column=0, sticky='w', pady=(6, 0))
        ttk.Entry(options, textvariable=self.vault_var, state='readonly').grid(
            row=2, column=1, sticky='ew', padx=8, pady=(6, 0))
        ttk.Button(options, text='选择库目录…', command=self._choose_vault).grid(
            row=2, column=2, pady=(6, 0))
        ttk.Label(options, text='选 Obsidian 库的根目录（里面有 .obsidian 文件夹的那一层）。',
                  foreground='#777777').grid(row=3, column=0, columnspan=3, sticky='w', padx=(22, 0))

        self.var_mirror = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text='依赖装不上时，自动改用清华镜像重试一次',
                        variable=self.var_mirror).grid(row=4, column=0, columnspan=3,
                                                       sticky='w', pady=(6, 0))

        log_box = ttk.LabelFrame(self.tk, text='日志', padding=(6, 4))
        log_box.grid(row=4, column=0, sticky='nsew', padx=18, pady=(0, 8))
        log_box.rowconfigure(0, weight=1)
        log_box.columnconfigure(0, weight=1)
        self.log = tk.Text(log_box, height=8, state='disabled', wrap='none',
                           font=('Consolas', 9), background='#ffffff',
                           relief='flat', borderwidth=0)
        y_scroll = ttk.Scrollbar(log_box, orient='vertical', command=self.log.yview)
        x_scroll = ttk.Scrollbar(log_box, orient='horizontal', command=self.log.xview)
        self.log.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.log.grid(row=0, column=0, sticky='nsew')
        y_scroll.grid(row=0, column=1, sticky='ns')
        x_scroll.grid(row=1, column=0, sticky='ew')

        footer = ttk.Frame(self.tk, padding=(18, 0, 18, 12))
        footer.grid(row=5, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value='准备就绪。')
        ttk.Label(footer, textvariable=self.status, foreground='#444444').grid(
            row=0, column=0, sticky='w')

        controls = ttk.Frame(footer)
        controls.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        self.progress = ttk.Progressbar(controls, mode='determinate', maximum=len(STEP_ORDER),
                                        length=180)
        self.progress.pack(side='left')
        ttk.Button(controls, text='关闭', command=self._close).pack(side='right', padx=(8, 0))
        self.start_button = ttk.Button(controls, text='开始安装', style='Accent.TButton',
                                       command=self._start)
        self.start_button.pack(side='right', padx=(8, 0))
        self.cancel_button = ttk.Button(controls, text='取消', command=self._cancel)
        self.cancel_button.state(['disabled'])
        self.cancel_button.pack(side='right', padx=(8, 0))
        self.service_button = ttk.Button(controls, text='启动工作台', command=self._start_service)
        self.service_button.state(['disabled'])
        self.service_button.pack(side='right', padx=(8, 0))

        self.tk.protocol('WM_DELETE_WINDOW', self._close)
        self._append_log('点「开始安装」即可。已装好的部分会自动跳过，不会重复安装。')

    def _center(self, width: int, height: int) -> None:
        self.tk.update_idletasks()
        screen_width = self.tk.winfo_screenwidth()
        screen_height = self.tk.winfo_screenheight()
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2 - 20)
        self.tk.geometry(f'{width}x{height}+{x}+{y}')

    def _choose_vault(self) -> None:
        chosen = filedialog.askdirectory(title='选择 Obsidian 库根目录（含 .obsidian 的那一层）')
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        if not is_obsidian_vault(chosen):
            messagebox.showwarning(
                '这不像 Obsidian 库',
                f'{chosen}\n\n这个文件夹里没有 .obsidian。\n请选择库的根目录（有 .obsidian 的那一层）。',
            )
            return
        self.vault_var.set(chosen)
        self.var_obsidian.set(True)

    # ---------- 安装流程 ----------

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        vault = self.vault_var.get().strip()
        if self.var_obsidian.get():
            if not vault:
                messagebox.showwarning('还没有选库目录',
                                       '勾选了「装进 Obsidian」，请先点「选择库目录…」。')
                return
            if not is_obsidian_vault(vault):
                messagebox.showerror('路径不对', f'{vault}\n\n这里没有 .obsidian，不是库的根目录。')
                return
        plan = InstallPlan(
            root=self.repo,
            seed_examples=self.var_seed.get(),
            obsidian_vault=vault if self.var_obsidian.get() else '',
            mirror_retry=self.var_mirror.get(),
        )
        self._reset()
        self.status.set('正在安装…')
        self.start_button.state(['disabled'])
        self.cancel_button.state(['!disabled'])
        self.service_button.state(['disabled'])
        self.cancel_event.clear()
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self._append_log('薪火OS 安装器')
        self.worker = threading.Thread(target=self._work, args=(plan,), daemon=True)
        self.worker.start()

    def _work(self, plan: InstallPlan) -> None:
        ok, message = False, '安装失败'
        try:
            ok, message = run_install(plan, self._emit, self.cancel_event)
        except Cancelled:
            ok, message = False, '已取消'
        except Exception as error:
            self._emit('log', f'安装器内部错误：{error!r}')
            self._emit('log', traceback.format_exc().rstrip())
        self._emit('done', (ok, message))

    def _emit(self, kind: str, payload) -> None:
        self.events.put((kind, payload))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        try:
            self.tk.after(80, self._drain)
        except tk.TclError:  # 窗口已经关掉，不再排队
            pass

    def _handle(self, kind: str, payload) -> None:
        if kind == 'log':
            self._append_log(payload)
        elif kind == 'step':
            key, state = payload
            self._set_step(key, state)
        elif kind == 'busy':
            self._set_busy(payload)
        elif kind == 'done':
            self._finish(*payload)
        elif kind == 'service':
            self._service_ready(payload)

    def _set_step(self, key: str, state: str) -> None:
        self.step_state[key] = state
        glyph = self.glyphs[key]
        glyph.configure(text=STEP_GLYPH[state], foreground=STEP_COLOR[state])
        if state == 'running':
            self.status.set(f'正在{STEP_LABELS[key]}…')
        done = sum(1 for item in self.step_state.values() if item in FINAL_STATES)
        if not self.busy_mode:
            self.progress.configure(value=done)

    def _set_busy(self, busy: bool) -> None:
        if busy and not self.busy_mode:
            self.progress.stop()
            self.progress.configure(mode='indeterminate')
            self.progress.start(12)
            self.busy_mode = True
        elif not busy and self.busy_mode:
            self.progress.stop()
            self.progress.configure(mode='determinate')
            done = sum(1 for item in self.step_state.values() if item in FINAL_STATES)
            self.progress.configure(value=done)
            self.busy_mode = False

    def _finish(self, ok: bool, message: str) -> None:
        if self.busy_mode:
            self._set_busy(False)
        self.start_button.state(['!disabled'])
        self.cancel_button.state(['disabled'])
        if ok:
            self.status.set('安装完成，可以启动工作台了。')
            self._append_log('')
            self._append_log('安装完成。点「启动工作台」打开界面，或以后运行 scripts\\start.ps1。')
            self.service_button.state(['!disabled'])
            messagebox.showinfo('安装完成',
                                '依赖与配置都已就绪。\n\n点「启动工作台」即可打开浏览器；'
                                '以后也可以运行 scripts\\start.ps1。')
        elif message == '已取消':
            self.status.set('已取消。')
            self._append_log('已取消。已经完成的步骤会保留，重新点「开始安装」会从断点补齐。')
        else:
            self.status.set(f'未完成：{message}')
            self._append_log(f'未完成：{message}')
            messagebox.showerror(
                '安装未完成',
                f'{message}。\n\n窗口里的日志有完整的命令与报错；'
                '也可以按 README 的「卡住了看这里」手动处理。',
            )

    def _reset(self) -> None:
        for key in STEP_ORDER:
            self.step_state[key] = 'pending'
            self.glyphs[key].configure(text=STEP_GLYPH['pending'],
                                       foreground=STEP_COLOR['pending'])
        self.progress.configure(mode='determinate', value=0)

    def _cancel(self) -> None:
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self.status.set('正在取消…')
            self._append_log('正在取消…')

    # ---------- 启动工作台 ----------

    def _start_service(self) -> None:
        if is_service_up():
            webbrowser.open(URL)
            self.status.set('工作台已经在运行，已打开浏览器。')
            return
        self.service_button.state(['disabled'])
        self.status.set('正在启动工作台…')
        try:
            launch_service(self.repo)
        except Exception as error:
            self.status.set('启动失败。')
            messagebox.showerror('启动失败', str(error))
            self.service_button.state(['!disabled'])
            return
        threading.Thread(target=self._wait_service, daemon=True).start()

    def _wait_service(self) -> None:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if is_service_up():
                self._emit('service', True)
                return
            time.sleep(0.4)
        self._emit('service', False)

    def _service_ready(self, ok: bool) -> None:
        if ok:
            self.status.set('工作台已启动，正在打开浏览器…')
            webbrowser.open(URL)
            self._append_log(f'已在浏览器打开 {URL}')
            self.service_button.state(['!disabled'])
        else:
            self.status.set('服务没有在 30 秒内就绪。')
            messagebox.showwarning(
                '还没起来',
                '工作台没有在 30 秒内就绪。\n\n'
                '可能 8765 端口被占用；可以在终端运行 scripts\\start.ps1 查看日志。',
            )
            self.service_button.state(['!disabled'])

    # ---------- 杂项 ----------

    def _append_log(self, line: str) -> None:
        self.log.configure(state='normal')
        self.log.insert('end', line + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno('仍在安装', '安装还没结束，确定退出吗？\n\n已下载和已装好的部分会保留。'):
                return
            self.cancel_event.set()
        self.tk.destroy()


def main() -> int:
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    def show_unhandled(exc_type, exc, tb) -> None:
        text = ''.join(traceback.format_exception(exc_type, exc, tb))
        try:
            (repo_root() / 'data').mkdir(parents=True, exist_ok=True)
            (repo_root() / 'data' / 'installer-error.log').write_text(text, encoding='utf-8')
        except OSError:
            pass
        try:
            messagebox.showerror('安装器出错', text[-1500:])
        except Exception:
            pass

    sys.excepthook = show_unhandled
    try:
        tk_root = tk.Tk()
    except tk.TclError as error:
        if sys.stderr is not None:  # pythonw 下 stderr 是 None
            print(f'无法启动图形界面：{error}', file=sys.stderr)
        return 1
    tk_root.report_callback_exception = lambda *args: show_unhandled(*args)
    InstallerApp(tk_root)
    try:
        tk_root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

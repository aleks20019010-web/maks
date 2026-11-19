# brain/skills/apps/launcher.py
# Поиск, запуск (отвязанный от Job Object brain через explorer.exe),
# точечное закрытие приложений и массовое закрытие «закрой всё».

import os
import shutil
import subprocess
import tempfile
import time

from shared.log import log


# Процессы, которые НИКОГДА не закрываем при «закрой всё»
_NEVER_KILL_EXES = {
    "python.exe",
    "pythonw.exe",
    "explorer.exe",
    "dwm.exe",
    "csrss.exe",
    "winlogon.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "smss.exe",
    "sihost.exe",
    "ctfmon.exe",
    "fontdrvhost.exe",
    "searchhost.exe",
    "searchapp.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "textinputhost.exe",
    "runtimebroker.exe",
    "securityhealthsystray.exe",
    # Фоновые медиа/торрент-сервисы пользователя
    "jellyfin.exe",
    "jellyfinserver.exe",
    "qbittorrent.exe",
    "torrserver.exe",
}

_NEVER_KILL_PREFIXES = (
    "torrserver",
    "jellyfin",
)


def resolve_executable(info: dict) -> str | None:
    """Найти путь к .exe: по явным путям, в PATH или в реестре App Paths."""
    for raw_path in info.get("paths", []):
        expanded = os.path.expandvars(raw_path)
        if os.path.isfile(expanded):
            return expanded

    for cmd in info.get("commands", []):
        found = shutil.which(cmd)
        if found:
            return found

    if os.name == "nt":
        try:
            import winreg
            for cmd in info.get("commands", []):
                subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{cmd}"
                for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                    try:
                        with winreg.OpenKey(hive, subkey) as k:
                            val, _ = winreg.QueryValueEx(k, "")
                            if val and os.path.isfile(val):
                                return val
                    except OSError:
                        continue
        except Exception:
            pass

    return None


def is_process_running(proc_name: str) -> bool:
    """Локале-независимая проверка запущенного процесса в Windows (CSV-формат tasklist)."""
    if os.name != "nt" or not proc_name:
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {proc_name}", "/FO", "CSV", "/NH"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stderr=subprocess.DEVNULL,
        )
        text = out.decode("utf-8", errors="ignore")
        target = f'"{proc_name.lower()}"'
        return any(line.strip().lower().startswith(target) for line in text.splitlines())
    except Exception as e:
        log(f"apps.launcher: is_process_running({proc_name}) error: {e}", tag="BRAIN", level="WARN")
        return False


def _wait_process_gone(proc_name: str, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_running(proc_name):
            return True
        time.sleep(0.2)
    return not is_process_running(proc_name)


def launch_app(info: dict) -> bool:
    """Запустить приложение через explorer.exe, чтобы оно не попало в Job Object brain."""
    extra_args = [str(a) for a in info.get("args", []) if a]
    exe = resolve_executable(info)
    target = exe or next((c for c in info.get("commands", []) if c), None)
    if not target:
        return False

    if os.name == "nt":
        try:
            # 1. Без аргументов — запускаем напрямую через explorer.exe
            if not extra_args:
                subprocess.Popen(["explorer.exe", target], close_fds=True)
                return True

            # 2. С аргументами (например --restore-last-session) — через одноразовый .lnk
            lnk_path = os.path.join(tempfile.gettempdir(), f"maks_launch_{os.getpid()}.lnk")
            work_dir = os.path.dirname(target) if exe else ""
            args_str = subprocess.list2cmdline(extra_args)
            ps_script = (
                f'$s = (New-Object -ComObject WScript.Shell).CreateShortcut("{lnk_path}");'
                f'$s.TargetPath = "{target}";'
                f'$s.Arguments = "{args_str}";'
                f'$s.WorkingDirectory = "{work_dir}";'
                f'$s.Save()'
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=3.0,
            )
            if os.path.isfile(lnk_path):
                subprocess.Popen(["explorer.exe", lnk_path], close_fds=True)
                return True
        except Exception as e:
            log(f"apps.launcher: explorer launch({target}) error: {e}", tag="BRAIN", level="WARN")

    try:
        subprocess.Popen(
            [target, *extra_args],
            cwd=os.path.dirname(exe) if exe else None,
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return True
    except Exception as e:
        log(f"apps.launcher: Popen({target}) error: {e}", tag="BRAIN", level="WARN")
        return False


def close_app(info: dict) -> str:
    """Закрыть конкретное приложение. Возвращает: 'closed' | 'not_running' | 'failed'."""
    proc = info.get("process_name", "")
    if not proc:
        return "failed"

    if not is_process_running(proc):
        return "not_running"

    try:
        # 1. Мягкое закрытие главного окна (WM_CLOSE без /F — сохраняет вкладки Chrome)
        subprocess.run(
            ["taskkill", "/IM", proc],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=3.0,
        )
        if _wait_process_gone(proc, timeout=1.6):
            return "closed"

        # 2. Добиваем оставшиеся дочерние процессы, если программа не успела выйти сама
        subprocess.run(
            ["taskkill", "/F", "/IM", proc, "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=3.0,
        )
        return "closed" if _wait_process_gone(proc, timeout=2.0) else "failed"
    except Exception as e:
        log(f"apps.launcher: close_app({proc}) error: {e}", tag="BRAIN", level="WARN")
        return "failed"


def _get_proc_exe_name(pid: int) -> str:
    """Получить имя .exe (в нижнем регистре) по PID через Win32 API."""
    if os.name != "nt" or not pid:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = wintypes.DWORD(512)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def _is_protected_exe(exe_name: str) -> bool:
    if not exe_name:
        return False
    e = exe_name.lower()
    if e in _NEVER_KILL_EXES:
        return True
    return any(e.startswith(p) for p in _NEVER_KILL_PREFIXES)


def _get_maks_protected_pids() -> set[int]:
    """Защитить только сам процесс brain, supervisor и воркеры Макса (НО НЕ внешний PowerShell)."""
    protected = {os.getpid()}
    if os.name != "nt":
        return protected

    try:
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.c_char * 260),
            ]

        snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return protected

        parent_map: dict[int, int] = {}
        children_map: dict[int, list[int]] = {}
        exe_map: dict[int, str] = {}
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)

        if ctypes.windll.kernel32.Process32First(snap, ctypes.byref(entry)):
            while True:
                pid = int(entry.th32ProcessID)
                ppid = int(entry.th32ParentProcessID)
                exe = entry.szExeFile.decode("cp866", errors="ignore").lower()
                parent_map[pid] = ppid
                children_map.setdefault(ppid, []).append(pid)
                exe_map[pid] = exe
                if not ctypes.windll.kernel32.Process32Next(snap, ctypes.byref(entry)):
                    break
        ctypes.windll.kernel32.CloseHandle(snap)

        # Берём родителя brain (это supervisor: python.exe / pythonw.exe)
        sup_pid = parent_map.get(os.getpid())
        if sup_pid and exe_map.get(sup_pid, "") in ("python.exe", "pythonw.exe"):
            protected.add(sup_pid)
            # Защищаем все воркеры Макса (asr, tts, music, ui), запущенные этим supervisor
            for worker_pid in children_map.get(sup_pid, []):
                protected.add(worker_pid)
                for sub_pid in children_map.get(worker_pid, []):
                    protected.add(sub_pid)
    except Exception as e:
        log(f"apps.launcher: _get_maks_protected_pids error: {e}", tag="BRAIN", level="WARN")

    return protected


def _close_explorer_windows() -> int:
    """Закрыть только открытые папки Проводника (не трогая рабочий стол и панель задач)."""
    if os.name != "nt":
        return 0
    ps = (
        "$shell = New-Object -ComObject Shell.Application; "
        "$wins = @($shell.Windows()); "
        "$cnt = 0; "
        "foreach ($w in $wins) { try { $w.Quit(); $cnt++ } catch {} }; "
        "Write-Output $cnt"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=4.0,
        )
        return int(out.decode("utf-8", errors="ignore").strip() or "0")
    except Exception:
        return 0


def close_all_apps() -> int:
    """Закрыть все открытые окна, папки Проводника и Диспетчер задач,
    не трогая Макса, рабочий стол, python-процессы и медиа-сервисы."""
    if os.name != "nt":
        return 0

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010

    protected_pids = _get_maks_protected_pids()
    system_classes = {
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "Progman",
        "WorkerW",
        "Windows.UI.Core.CoreWindow",
    }

    target_hwnds: list[int] = []
    target_pids: set[int] = set()

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _enum_cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindowTextLengthW(hwnd) == 0:
            return True

        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        if cls_buf.value in system_classes:
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        p = int(pid.value)
        if not p or p in protected_pids:
            return True

        exe_name = _get_proc_exe_name(p)
        if _is_protected_exe(exe_name):
            return True

        target_hwnds.append(hwnd)
        target_pids.add(p)
        return True

    user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)

    # 1. Мягко закрываем все найденные окна (WM_CLOSE)
    for hwnd in target_hwnds:
        try:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass

    # 2. Закрываем только папки Проводника (рабочий стол и панель задач не трогаем)
    closed_folders = _close_explorer_windows()

    # 3. Ждём 1.0с и принудительно закрываем зависшие окна + Диспетчер задач
    time.sleep(1.0)
    for pid in list(target_pids):
        exe_name = _get_proc_exe_name(pid)
        if _is_protected_exe(exe_name):
            continue
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=2.0,
            )
        except Exception:
            pass

    # Только Диспетчер задач. PowerShell/pwsh не убиваем:
    # сам launcher использует powershell для _close_explorer_windows и .lnk,
    # а python.exe/pythonw.exe теперь в _NEVER_KILL_EXES — на случай,
    # если пользователь открыл ещё одну консоль с python.
    for extra_proc in ("Taskmgr.exe",):
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", extra_proc],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=2.0,
            )
        except Exception:
            pass

    total = len(target_pids) + closed_folders
    log(
        f"apps.launcher: close_all_apps → закрыто процессов={len(target_pids)}, папок={closed_folders}",
        tag="BRAIN",
    )
    return total
"""Identify a local session owner without process command lines or credentials."""
import os
from pathlib import Path


def process_identity(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, created, exited, system, user):
                return None
            started = (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel.CloseHandle(handle)
    else:
        try:
            # Linux /proc start ticks distinguish a live PID from its later reuse.
            started = int((Path('/proc')/str(pid)/'stat').read_text().rsplit(')', 1)[1].split()[19])
        except (OSError, ValueError, IndexError):
            return None
    return {'pid': pid, 'started': started}


def owner_alive(owner):
    return isinstance(owner, dict) and process_identity(owner.get('pid')) == owner


def processes():
    if os.name != 'nt':
        result = {}
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                fields = (path/'stat').read_text().rsplit(')', 1)[1].split()
                result[int(path.name)] = (int(fields[1]), (path/'comm').read_text().strip())
            except (OSError, ValueError, IndexError):
                pass
        return result
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('name', wintypes.WCHAR*260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        return {}
    result = {}
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            result[entry.pid] = (entry.parent, entry.name)
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return result


def session_owner(provider):
    table = processes()
    pid = os.getppid()
    names = {'codex.exe', 'codex'} if provider == 'codex' else {'claude.exe', 'claude'}
    for _ in range(20):
        parent, name = table.get(pid, (0, ''))
        if name.lower() in names:
            return process_identity(pid)
        if not parent or parent == pid:
            break
        pid = parent
    return None


def desktop_owner():
    table = processes()
    for pid, (parent, name) in table.items():
        if name.lower() == 'codex.exe' and table.get(parent, (0, ''))[1].lower() == 'chatgpt.exe':
            return process_identity(pid)
    return None

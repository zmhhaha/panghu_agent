"""Windows fixture-only Job Object containment; production uses Linux cgroups."""
import ctypes
from ctypes import wintypes


class WindowsJob:
    def __init__(self, process):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle or not self.api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            if self.handle:
                self.api.CloseHandle(self.handle)
            raise OSError("Cannot establish fixture Job Object")

    def resume(self, process):
        # Popen closes the primary-thread handle. NtResumeProcess resumes the
        # suspended process through its retained process handle instead.
        native = ctypes.WinDLL("ntdll")
        native.NtResumeProcess.argtypes = [wintypes.HANDLE]
        native.NtResumeProcess.restype = ctypes.c_long
        if native.NtResumeProcess(wintypes.HANDLE(int(process._handle))) != 0:
            raise OSError("Cannot resume contained fixture process")

    def alive(self):
        # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: 4 LARGE_INTEGER, 4 DWORD.
        buffer = ctypes.create_string_buffer(48)
        if not self.api.QueryInformationJobObject(self.handle, 1, buffer, 48, None):
            raise OSError("Cannot verify fixture Job Object")
        return int.from_bytes(buffer.raw[40:44], "little") > 0

    def kill(self):
        if not self.api.TerminateJobObject(self.handle, 137):
            raise OSError("Cannot stop fixture Job Object")

    def close(self):
        self.api.CloseHandle(self.handle)

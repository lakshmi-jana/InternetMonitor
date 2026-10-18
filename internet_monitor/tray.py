from __future__ import annotations

import ctypes
import webbrowser
from ctypes import wintypes
from typing import Callable

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_USER = 0x0400
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IDI_APPLICATION = 32512
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
ERROR_ALREADY_EXISTS = 183

CMD_OPEN = 1001
CMD_RUN_TEST = 1002
CMD_EXIT = 1003
TRAY_MESSAGE = WM_USER + 20

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class SingleInstance:
    def __init__(self, name: str = "Local\\InternetConnectionMonitor") -> None:
        self._handle = kernel32.CreateMutexW(None, False, name)
        if not self._handle:
            raise OSError("Could not create the application mutex")
        self.is_first = kernel32.GetLastError() != ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


class TrayApplication:
    def __init__(
        self,
        dashboard_url: str,
        request_test: Callable[[], bool],
        on_exit: Callable[[], None],
    ) -> None:
        self.dashboard_url = dashboard_url
        self.request_test = request_test
        self.on_exit = on_exit
        self._class_name = "InternetMonitorTrayWindow"
        self._window: int | None = None
        self._icon_data: NOTIFYICONDATAW | None = None
        self._closing = False
        self._wnd_proc = WNDPROC(self._window_proc)

    def run(self) -> None:
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = self._wnd_proc
        window_class.hInstance = instance
        window_class.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
        window_class.lpszClassName = self._class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            error = kernel32.GetLastError()
            if error != ERROR_ALREADY_EXISTS:
                raise ctypes.WinError(error)

        self._window = user32.CreateWindowExW(
            0,
            self._class_name,
            "Internet Connection Monitor",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            instance,
            None,
        )
        if not self._window:
            raise ctypes.WinError()

        icon_data = NOTIFYICONDATAW()
        icon_data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        icon_data.hWnd = self._window
        icon_data.uID = 1
        icon_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        icon_data.uCallbackMessage = TRAY_MESSAGE
        icon_data.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
        icon_data.szTip = "Internet Connection Monitor"
        self._icon_data = icon_data
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(icon_data)):
            raise OSError("Windows could not create the tray icon")

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def open_dashboard(self) -> None:
        webbrowser.open(self.dashboard_url)

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == TRAY_MESSAGE:
            if lparam == WM_LBUTTONDBLCLK:
                self.open_dashboard()
                return 0
            if lparam == WM_RBUTTONUP:
                self._show_menu()
                return 0
        elif message == WM_COMMAND:
            self._handle_command(wparam & 0xFFFF)
            return 0
        elif message == WM_CLOSE:
            self._exit()
            return 0
        elif message == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        try:
            user32.AppendMenuW(menu, MF_STRING, CMD_OPEN, "Open dashboard")
            user32.AppendMenuW(menu, MF_STRING, CMD_RUN_TEST, "Run speed test now")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, "Exit")
            position = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(position))
            user32.SetForegroundWindow(self._window)
            command = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD,
                position.x,
                position.y,
                0,
                self._window,
                None,
            )
            if command:
                self._handle_command(command)
        finally:
            user32.DestroyMenu(menu)

    def _handle_command(self, command: int) -> None:
        if command == CMD_OPEN:
            self.open_dashboard()
        elif command == CMD_RUN_TEST:
            self.request_test()
        elif command == CMD_EXIT:
            self._exit()

    def _exit(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._icon_data is not None:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._icon_data))
        try:
            self.on_exit()
        finally:
            if self._window:
                user32.DestroyWindow(self._window)

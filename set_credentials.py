#!/usr/bin/env python3
import getpass
import sys

import keyring

SERVICE_NAME = "mcp-sqlserver-pccom"

FIELDS = [
    ("host",          "SQL Server host (e.g. SQLEXPRESS\\192.168.1.100)", False),
    ("user",          "SQL Server user",                                  False),
    ("password",      "SQL Server password",                              False),
    ("database_pccom","Database name (e.g. pccom)",                       False),
    ("database_dat",  "Database name (e.g. dat1)",                        False),
]


def _prefill_win32(text):
    """Inject text into the Windows console input buffer so it appears pre-typed."""
    import ctypes
    import ctypes.wintypes as wt

    class _uChar(ctypes.Union):
        _fields_ = [("UnicodeChar", wt.WCHAR), ("AsciiChar", ctypes.c_char)]

    class KEY_EVENT_RECORD(ctypes.Structure):
        _anonymous_ = ("uChar",)
        _fields_ = [
            ("bKeyDown", wt.BOOL),
            ("wRepeatCount", wt.WORD),
            ("wVirtualKeyCode", wt.WORD),
            ("wVirtualScanCode", wt.WORD),
            ("uChar", _uChar),
            ("dwControlKeyState", wt.DWORD),
        ]

    class INPUT_RECORD(ctypes.Structure):
        _fields_ = [("EventType", wt.WORD), ("KeyEvent", KEY_EVENT_RECORD)]

    stdin = ctypes.windll.kernel32.GetStdHandle(-10)
    records = (INPUT_RECORD * (len(text) * 2))()
    for i, ch in enumerate(text):
        for j, down in enumerate((True, False)):
            r = records[i * 2 + j]
            r.EventType = 1  # KEY_EVENT
            r.KeyEvent.bKeyDown = down
            r.KeyEvent.wRepeatCount = 1
            r.KeyEvent.UnicodeChar = ch
    written = wt.DWORD(0)
    ctypes.windll.kernel32.WriteConsoleInputW(stdin, records, len(records), ctypes.byref(written))


def input_with_prefill(prompt, prefill):
    """Show an input prompt with the given text already typed in the input buffer."""
    if sys.platform == "win32":
        try:
            _prefill_win32(prefill)
            return input(prompt).strip()
        except Exception:
            value = input(f"{prompt}[{prefill}] ").strip()
            return value if value else prefill
    else:
        try:
            import readline
            readline.set_pre_input_hook(lambda: readline.insert_text(prefill))
            try:
                return input(prompt).strip()
            finally:
                readline.set_pre_input_hook()
        except (ImportError, AttributeError):
            value = input(f"{prompt}[{prefill}] ").strip()
            return value if value else prefill


def prompt_field(label, current, is_secret):
    if current:
        return input_with_prefill(f"  {label}: ", current)
    elif is_secret:
        return getpass.getpass(f"  {label}: ")
    else:
        return input(f"  {label}: ").strip()


def main():
    print("SQL Server MCP - Credential Setup")
    print("Credentials are stored in the system keychain (Windows Credential Manager on Windows).")
    print("Press Enter to keep the current value.\n")

    for key, label, is_secret in FIELDS:
        current = keyring.get_password(SERVICE_NAME, key)
        value = prompt_field(label, current, is_secret)
        if value:
            keyring.set_password(SERVICE_NAME, key, value)

    print("\nCredentials saved. Restart Claude Code to reload the MCP server.")


if __name__ == "__main__":
    main()

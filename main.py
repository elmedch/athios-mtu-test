"""
ATHIOS MTU Test
Entry point. Run with:  python main.py

Finds the largest ICMP payload that crosses your internet connection
without fragmentation (binary search between 1300-1500 bytes), then
reports the real path MTU (payload + 28 bytes of IP/ICMP overhead).

Windows only - relies on ping.exe's "-f" (Don't Fragment) flag.
"""

import sys
import tkinter as tk

from gui import MtuTestApp


def main() -> None:
    if sys.platform != "win32":
        # Still let it run (useful for quick UI checks during development),
        # but be upfront that the actual MTU test needs Windows' ping.exe.
        print("Warning: ATHIOS MTU Test relies on Windows' ping -f flag "
              "and is only fully functional on Windows.")

    root = tk.Tk()
    MtuTestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

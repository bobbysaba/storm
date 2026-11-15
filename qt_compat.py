"""Qt load-order workarounds.

On Windows, the PyQt6 wheels' Qt6Core.dll links Windows' own ICU as
icuuc.dll and calls its unversioned functions (ucnv_open). conda-forge's
icu package, which harfbuzz, cairo, libxml2 and libpsl pull into the env,
also installs a Library\\bin\\icuuc.dll, but one whose functions carry the
version (ucnv_open_78), and the conda env's DLL search finds that one first.
Importing PyQt6.QtCore then fails with "DLL load failed while importing
QtCore: The specified procedure could not be found."

Loading the system copy before Qt makes Windows reuse it, by name, when
Qt6Core asks for icuuc.dll. conda's own libraries link icuuc78.dll and are
unaffected.
"""

import os
import sys

_system_icu = None


def use_windows_icu() -> None:
    """Load System32's icuuc.dll so Qt gets it. Windows only; call before
    anything imports PyQt6."""
    global _system_icu
    if sys.platform != "win32" or _system_icu is not None:
        return
    path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icuuc.dll")
    if os.path.isfile(path):      # Windows 10 1903 and later
        import ctypes
        _system_icu = ctypes.WinDLL(path)

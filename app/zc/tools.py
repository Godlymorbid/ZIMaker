"""Optional programs ZIMaker can use when they're installed:

  FFmpeg       audio and video
  7-Zip / unar 7z and RAR archives, CBR/CB7 comics, Windows Help (CHM) files
  LibreOffice  old Office files (DOC, XLS, PPT) and other office formats

ZIMaker never downloads these itself: they come from the Linux distribution's own packages, installed
by the user with one command (shown in Settings). This module finds them and says what's missing.
"""
import os
import shutil

from . import common as C

TOOLS = {
    "ffmpeg": {"bins": ("ffmpeg",), "packages": ("ffmpeg",),
               "label": "FFmpeg", "for": "audio and video"},
    "7zip": {"bins": ("7zz", "7z", "unar"), "packages": ("7zip", "unar"),
             "label": "7-Zip", "for": "7Z and RAR archives, CBR/CB7 comics, CHM help files"},
    "libreoffice": {"bins": ("soffice", "libreoffice"),
                    "packages": ("libreoffice-writer-nogui", "libreoffice-calc-nogui",
                                 "libreoffice-impress-nogui", "libreoffice-draw-nogui"),
                    "label": "LibreOffice", "for": "old Office files (DOC, XLS, PPT) and other office formats"},
}


def find(name, binary=None):
    """Path of the tool (or one specific binary of it), or None."""
    for b in ((binary,) if binary else TOOLS[name]["bins"]):
        p = shutil.which(b)
        if p:
            return p
    return None


def install_command(names=None):
    names = names or list(TOOLS)
    pkgs = " ".join(p for n in names for p in TOOLS[n]["packages"])
    if C.IS_WSL:  # typed into Windows PowerShell; runs the package manager inside ZIMaker's WSL distribution as root
        distro = os.environ.get("WSL_DISTRO_NAME", "")
        d = f"-d {distro} " if distro and " " not in distro else ""
        return f"wsl {d}-u root -- sh -c \"apt-get update && apt-get install -y {pkgs}\""
    return f"sudo apt install {pkgs}"


def status():
    return {"tools": [{"id": n, "label": t["label"], "for": t["for"], "installed": bool(find(n))}
                      for n, t in TOOLS.items()],
            "command": install_command(),
            "windows": C.IS_WSL,
            "debian_like": os.path.exists("/usr/bin/apt-get")}


def need(name, what):
    """The tool's path, or a clear error saying how to get it."""
    p = find(name)
    if p:
        return p
    t = TOOLS[name]
    where = "in Windows PowerShell" if C.IS_WSL else "in a terminal"
    raise RuntimeError(f"{what} needs {t['label']}, which isn't installed. Install it once {where}:  "
                       f"{install_command([name])}   (also shown in ZIMaker's Settings → Optional tools)")

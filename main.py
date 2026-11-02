"""
pix
"""
import os
import subprocess

def check_distro():
    distro = subprocess.run(["cat", "/etc/os-release"], capture_output=True, text=True)

    if "ID_LIKE=arch" in distro.stdout or "ID=arch" in distro.stdout:
        return "arch"
    elif "ID_LIKE=debian" in distro.stdout or "ID=debian" in distro.stdout:
        return "debian"
    elif "ID_LIKE=fedora" in distro.stdout or "ID=fedora" in distro.stdout:
        return "fedora"
    elif "ID_LIKE="



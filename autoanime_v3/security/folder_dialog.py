"""Native folder picker helpers for local WebUI sessions."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class FolderDialogError(RuntimeError):
    """Raised when the native folder dialog cannot be shown."""

    def __init__(self, message, code="folder_dialog_failed"):
        super().__init__(message)
        self.message = message
        self.code = code


def pick_folder_windows(initial_directory=None, title="选择文件夹"):
    """Open the Windows folder browser and return a path string.

    Returns None when the user cancels. Raises FolderDialogError for hard failures.
    """
    if os.name != "nt":
        raise FolderDialogError(
            "Native folder picker is only available on Windows",
            "folder_dialog_unsupported",
        )

    initial = ""
    if initial_directory:
        candidate = Path(str(initial_directory)).expanduser()
        if candidate.exists() and candidate.is_dir():
            initial = str(candidate.resolve(strict=False))

    # FolderBrowserDialog needs STA; PowerShell -STA avoids a pywin32 dependency.
    script = r"""
param(
  [string]$Description = '选择文件夹',
  [string]$InitialDirectory = ''
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms | Out-Null
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $Description
$dialog.ShowNewFolderButton = $true
if ($InitialDirectory) {
  try { $dialog.SelectedPath = $InitialDirectory } catch {}
}
$result = $dialog.ShowDialog()
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
  exit 2
}
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Write-Output $dialog.SelectedPath
"""
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
            "-Description",
            str(title),
            "-InitialDirectory",
            initial,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    if completed.returncode == 2:
        return None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise FolderDialogError(detail or "Folder dialog failed", "folder_dialog_failed")
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        return None
    return str(Path(lines[-1]).expanduser().resolve(strict=False))

"""Machine-bound and development secret-encryption adapters."""

import ctypes
import os
from pathlib import Path

from cryptography.fernet import Fernet


class EncryptedFileSecretStore:
    provider = "fernet_file"

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key_path = self.directory / "master.key"
        if not self.key_path.exists():
            self.key_path.write_bytes(Fernet.generate_key())
            try:
                os.chmod(str(self.key_path), 0o600)
            except OSError:
                pass
        self.fernet = Fernet(self.key_path.read_bytes())

    def protect(self, value):
        return self.fernet.encrypt(value.encode("utf-8"))

    def unprotect(self, ciphertext):
        return self.fernet.decrypt(bytes(ciphertext)).decode("utf-8")


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(data):
    buffer = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


class DpapiSecretStore:
    provider = "dpapi"

    def __init__(self, entropy=b"AutoAnime-v3-WebConsole"):
        if os.name != "nt":
            raise OSError("Windows DPAPI is only available on Windows")
        self.entropy = entropy

    def protect(self, value):
        plaintext, plaintext_buffer = _blob_from_bytes(value.encode("utf-8"))
        entropy, entropy_buffer = _blob_from_bytes(self.entropy)
        output = _DataBlob()
        success = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(plaintext),
            "AutoAnime secret",
            ctypes.byref(entropy),
            None,
            None,
            0x01,
            ctypes.byref(output),
        )
        if not success:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)

    def unprotect(self, ciphertext):
        encrypted, encrypted_buffer = _blob_from_bytes(bytes(ciphertext))
        entropy, entropy_buffer = _blob_from_bytes(self.entropy)
        output = _DataBlob()
        success = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(encrypted),
            None,
            ctypes.byref(entropy),
            None,
            None,
            0x01,
            ctypes.byref(output),
        )
        if not success:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(output.pbData)


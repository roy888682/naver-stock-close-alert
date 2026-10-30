"""Local SMTP credential storage; Windows DPAPI, private file on POSIX."""
import base64
import ctypes
import json
import os
import tempfile
import threading
from pathlib import Path


class CredentialError(RuntimeError):
    pass


def _dpapi(data, decrypt=False):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = None
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        description = "Close Watch SMTP credential"
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN: never prompt from a background check.
    if not function(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(target)):
        raise CredentialError("Windows 비밀번호 보관함을 사용할 수 없습니다. 이 PC의 사용자 계정에서 다시 저장하세요.")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


class PasswordStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _write(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        descriptor = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".smtp-", dir=self.path.parent)
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = None
                json.dump(record, stream, ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError:
            raise CredentialError("비밀번호를 이 PC에 저장하지 못했습니다. 프로그램 데이터 폴더의 권한을 확인하세요.") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def set(self, password, username, host):
        with self.lock:
            protected = _dpapi(password.encode("utf-8")) if os.name == "nt" else password.encode("utf-8")
            self._write(dict(version=1, platform="windows-dpapi" if os.name == "nt" else "private-file",
                             username=username, host=host.lower(), secret=base64.b64encode(protected).decode("ascii")))

    def clear(self):
        # A marker also disables optional legacy environment credentials, across restarts.
        with self.lock:
            self._write(dict(version=1, platform="cleared", username="", host="", secret=""))

    def get(self, username, host):
        with self.lock:
            try:
                if not self.path.exists():
                    return ""
                if self.path.stat().st_size > 32768:
                    raise ValueError()
                record = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or record.get("version") != 1:
                    raise ValueError()
                if record.get("platform") == "cleared":
                    return ""
                if record.get("username") != username or record.get("host") != host.lower():
                    return ""
                raw = base64.b64decode(record["secret"], validate=True)
                if record.get("platform") == "windows-dpapi" and os.name == "nt":
                    raw = _dpapi(raw, decrypt=True)
                elif record.get("platform") != "private-file" or os.name == "nt":
                    raise CredentialError("다른 PC에서 저장한 비밀번호입니다. 이 PC에서 앱 비밀번호를 다시 등록하세요.")
                return raw.decode("utf-8")
            except CredentialError:
                raise
            except (OSError, ValueError, KeyError, TypeError, UnicodeError):
                raise CredentialError("저장된 비밀번호를 읽을 수 없습니다. 웹 화면에서 다시 등록하세요.") from None

    def configured(self, username, host):
        return bool(self.get(username, host))

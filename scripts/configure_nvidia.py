# -*- coding: utf-8 -*-
"""一次性脚本：把 autoanime-webui 的 WebUI 运行时配置切换为 NVIDIA API。

通过项目自身的 SettingsService / SecretService 写入，保证：
- app_settings 表 revision 正确递增；
- openai.api_key 走 DPAPI 加密（Windows）或 Fernet secret-store；
- 与 WebUI 的 PATCH /api/v1/settings、PUT /api/v1/settings/secrets 行为一致。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autoanime_v3.services.settings import (
    OPENAI_API_KEY_SECRET,
    OPENAI_BASE_URL_KEY,
    OPENAI_ENABLED_KEY,
    OPENAI_MODEL_KEY,
    OPENAI_TIMEOUT_KEY,
    SettingsService,
)
from autoanime_v3.services.auth import SecretService
from autoanime_v3.security.secrets import DpapiSecretStore

DATABASE = Path(__file__).resolve().parent.parent / ".dev-data" / "data" / "library.sqlite3"
API_KEY = "sk-ZqpxKnClyDMZ5q28838oTsx97G5hfKQUJTYb0GIow2ZTqLLv"
BASE_URL = "https://api.ymxc.asia"
MODEL = "deepseek-v4-flash"


def main():
    settings = SettingsService(DATABASE)
    revisions = {item["key"]: item["revision"] for item in settings.list()}

    def apply(key, value):
        rev = int(revisions.get(key, 0))
        result = settings.update(key, value, rev)
        print("set %s = %r (rev %d -> %d)" % (key, value, rev, result["revision"]))

    apply(OPENAI_ENABLED_KEY, True)
    apply(OPENAI_BASE_URL_KEY, BASE_URL)
    apply(OPENAI_MODEL_KEY, MODEL)
    apply(OPENAI_TIMEOUT_KEY, 30)

    try:
        store = DpapiSecretStore()
    except OSError:
        store = None
        print("DPAPI 不可用，改用 Fernet secret-store")
        from autoanime_v3.security.secrets import EncryptedFileSecretStore
        candidates = [
            DATABASE.parent / "secret-store",
            DATABASE.parent.parent / "secret-store",
        ]
        store_path = next((p for p in candidates if p.exists()), candidates[0])
        store = EncryptedFileSecretStore(store_path)

    secrets = SecretService(DATABASE, store)
    status = secrets.set_secret(OPENAI_API_KEY_SECRET, API_KEY)
    print("set %s provider=%s configured=%s" % (OPENAI_API_KEY_SECRET, status.provider, status.configured))
    revealed = secrets.reveal_for_integration(OPENAI_API_KEY_SECRET)
    print("reveal ok:", bool(revealed), "prefix:", (revealed or "")[:8])


if __name__ == "__main__":
    main()

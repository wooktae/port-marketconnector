"""Non-secret EC2 intraday settings. Missing settings always disable submission."""
import json
import os
from pathlib import Path
import stat

DEFAULT_PATH = "/home/ec2-user/apps/port-marketconnector/runtime/intraday-auto.json"


def apply_settings(path=None):
    path = Path(path or os.environ.get("INTRADAY_AUTO_SETTINGS_FILE", DEFAULT_PATH))
    config = {"enabled": False, "fillSync": {}}
    if path.exists():
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or (os.name == "posix" and (
                metadata.st_mode & 0o022 or metadata.st_uid not in (0, os.geteuid())))):
            raise ValueError("UNSAFE_INTRADAY_SETTINGS_FILE")
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or type(config.get("enabled")) is not bool:
            raise ValueError("INVALID_INTRADAY_SETTINGS")
    # Do not inherit a stale enable flag from the generic Daily environment file.
    os.environ["INTRADAY_AUTO_SELL_ENABLED"] = "true" if config["enabled"] else "false"
    return config

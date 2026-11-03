"""Non-secret EC2 intraday settings. Missing settings always disable submission."""
import json
import os
from pathlib import Path
import stat
from datetime import datetime, timedelta, timezone

DEFAULT_PATH = "/home/ec2-user/apps/port-marketconnector/runtime/intraday-auto.json"


def intraday_window_open(now=None):
    """Allow weekdays 09:10 through the 15:10 minute, including delivery jitter."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return False
    local = now.astimezone(timezone(timedelta(hours=9)))
    return local.weekday() < 5 and 550 <= local.hour * 60 + local.minute <= 910


def trusted_owners(path):
    owners = {0, os.geteuid()}
    # SSM runs as root; the installer intentionally owns runtime files as ec2-user.
    # Trust that named application user only at the fixed, protected runtime path.
    if os.geteuid() == 0 and str(path) == DEFAULT_PATH:
        import pwd
        app_uid = pwd.getpwnam("ec2-user").pw_uid
        for parent in path.parents:
            metadata = parent.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o022
                    or metadata.st_uid not in (0, app_uid)):
                raise ValueError("UNSAFE_INTRADAY_SETTINGS_DIRECTORY")
        owners.add(app_uid)
    return owners


def apply_settings(path=None):
    os.environ["INTRADAY_AUTO_SELL_ENABLED"] = "false"
    path = Path(path or os.environ.get("INTRADAY_AUTO_SETTINGS_FILE", DEFAULT_PATH))
    config = {"enabled": False, "fillSync": {}}
    if path.exists():
        metadata = path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or (os.name == "posix" and (
                metadata.st_mode & 0o022 or metadata.st_uid not in trusted_owners(path)))):
            raise ValueError("UNSAFE_INTRADAY_SETTINGS_FILE")
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or type(config.get("enabled")) is not bool:
            raise ValueError("INVALID_INTRADAY_SETTINGS")
    # Do not inherit a stale enable flag from the generic Daily environment file.
    os.environ["INTRADAY_AUTO_SELL_ENABLED"] = "true" if config["enabled"] else "false"
    return config

"""Read the signed native host's fresh, private Dock observation."""
import json
import os
import plistlib
from pathlib import Path
import stat
import time

from .service import check_directory


def host_diagnostics(path=None, *, host_pid=None, now=None):
    path = Path(path or f"/private/tmp/opengcua-service-{os.getuid()}/dock-status.json")
    host_pid = os.getppid() if host_pid is None else host_pid
    now = time.time() if now is None else now
    try:
        check_directory(path.parent)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 64 * 1024):
                raise ValueError("Invalid native host diagnostic file")
            report = json.load(source)
        if report['host_pid'] != host_pid or not 0 <= now - report['captured_at'] < 5:
            raise ValueError("Stale or different-host diagnostic report")
        return report
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"host_pid": host_pid, "dock": {"status": "unavailable", "count": None},
                "error": str(exc)}


def driver_metadata(app):
    """Read packaged version metadata without executing or importing the driver."""
    app = Path(app)
    report = {"app": str(app), "version": None, "version_source": "bundle_metadata",
              "signature_checked": False}
    try:
        with (app / 'Contents/Info.plist').open('rb') as source:
            data = source.read(64 * 1024 + 1)
        if len(data) > 64 * 1024:
            raise ValueError('Oversized driver metadata')
        info = plistlib.loads(data)
        version = info.get('CFBundleShortVersionString')
        if not isinstance(version, str) or not version.strip():
            raise ValueError('Missing driver version')
        report['version'] = version
    except (OSError, ValueError, plistlib.InvalidFileException, AttributeError):
        report['metadata_status'] = 'unavailable'
    else:
        report['metadata_status'] = 'ok'
    return report

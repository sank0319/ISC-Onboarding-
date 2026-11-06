"""Upload an account feed with SailPoint's File Upload Utility (the official JAR) instead of calling the API.

Enabled by setting ISC_UPLOAD_JAR to the path of the downloaded JAR. The utility finds the target source from the
32-character source ID at the start of the file name, so the feed is copied to <sourceID>-<name> in a temp folder.
Credentials reach the JAR through SAIL_CLIENT_ID / SAIL_CLIENT_SECRET (never the command line).
"""
import os
import re
import shutil
import subprocess
import tempfile

from isc_client import ISCError
from run_log import redact

_run = subprocess.run  # looked up at call time so tests can replace it
SOURCE_ID = re.compile(r"^[0-9a-f]{32}$")


def upload_with_utility(jar, base_url, client_id, client_secret, source_id, file_path, timeout=1800, run=None):
    """Run the utility for one feed; returns its (redacted) output. Raises ISCError on any failure."""
    run = run or _run
    if not os.path.isfile(jar):
        raise ISCError(f"File Upload Utility JAR not found: {jar}")
    if not SOURCE_ID.match(source_id or ""):
        raise ISCError(f"source id {source_id!r} is not a 32-character ISC id; the utility cannot find the source")
    if not os.path.isfile(file_path):
        raise ISCError(f"feed file not found: {file_path}")
    env = dict(os.environ, SAIL_CLIENT_ID=client_id, SAIL_CLIENT_SECRET=client_secret)
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, f"{source_id}-{os.path.basename(file_path)}")
        shutil.copyfile(file_path, staged)
        cmd = ["java", "-jar", jar, "-u", base_url, "--clientId", "env", "--clientSecret", "env", "-f", staged]
        try:
            done = run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ISCError(f"File Upload Utility did not run: {type(e).__name__}") from None
    out = redact(f"{done.stdout or ''}\n{done.stderr or ''}").strip()
    if done.returncode != 0:
        raise ISCError(f"File Upload Utility failed (exit {done.returncode}): {out[-500:]}")
    return out

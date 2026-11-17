"""External process integration, with timeouts and bounded diagnostics."""
import os
import shutil
import subprocess
import time

class ToolError(ValueError):
    pass

def binary(name):
    candidate = os.environ.get('MEDIA_' + name.upper()) or shutil.which(name)
    if not candidate or not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        raise ToolError(f'{name} unavailable; set MEDIA_{name.upper()} to its executable')
    return candidate

def run(name, arguments, timeout=120, tick=None):
    with subprocess.Popen([binary(name), *arguments], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if tick is not None:
                    tick()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ToolError(f'{name} timed out')
                try:
                    stdout, stderr = process.communicate(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            process.kill()
            process.communicate()
            raise
    if process.returncode:
        raise ToolError(stderr.decode('utf-8', 'replace')[-4000:])
    return stdout

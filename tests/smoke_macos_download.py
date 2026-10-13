"""Run the packaged Mac app and require a verified warm-up HTTPS download."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time


def main():
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        config = root / "config.ini"
        config.write_text("[ui]\nwarmup = true\nauto_update = false\nrun_history = true\n")
        env = dict(os.environ)
        # The executable must succeed without the runner's certificate paths.
        env.pop("SSL_CERT_FILE", None)
        env.pop("SSL_CERT_DIR", None)
        with (root / "app.log").open("w+") as output:
            process = subprocess.Popen(
                [str(executable), "--config", str(config)],
                cwd=root, env=env, stdout=output, stderr=output,
            )
            try:
                deadline = time.monotonic() + 90
                database = root / "cache" / "warm.sqlite"
                while time.monotonic() < deadline:
                    if database.exists():
                        with sqlite3.connect(database) as connection:
                            count = connection.execute("SELECT COUNT(*) FROM warm").fetchone()[0]
                        assert count > 0, "Downloaded warm-up database is empty"
                        print(f"Frozen HTTPS download passed: {count} warm-up records")
                        return
                    if process.poll() is not None:
                        raise RuntimeError(f"Packaged app exited with {process.returncode}")
                    time.sleep(1)
                raise TimeoutError("Packaged app did not download warm.sqlite within 90 seconds")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                output.seek(0)
                print(output.read())


if __name__ == "__main__":
    main()

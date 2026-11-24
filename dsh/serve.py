import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

base = Path(__file__).resolve().parent
address_file = Path(os.environ["RUNTIME_DIRECTORY"]) / "launch-url"
address_file.unlink(missing_ok=True)
command = [base / "package/app/node_modules/.bin/dsh", "web", "--no-open", "--host", "127.0.0.1",
           "--port", "3080", "--trusted-host", "127.0.0.1:13081"]
with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as server:
    for line in server.stdout:
        for url in re.findall(r"https?://[^\s\x1b<>]+", line):
            parsed = urlsplit(url)
            if parsed.hostname == "127.0.0.1" and parsed.port == 3080 and parsed.query:
                pending = address_file.with_suffix(".new")
                pending.write_text(url)
                pending.replace(address_file)
                print("DSH Web 已启动", flush=True)
    raise SystemExit(server.wait())

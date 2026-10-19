"""Exercise the installed CLI, native dependencies, audio tools and web assets offline."""

import http.cookiejar
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


package, tools, version = sys.argv[1:]
salmon = str(Path(package) / "bin/salmon")

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    for directory in ["config", ".music", ".torrents"]:
        (root / directory).mkdir()
    default = Path(package) / "share/smoked-salmon/config.default.toml"
    (root / "config/config.toml").write_text(
        default.read_text() + "\n[upload]\nupdate_notification = false\n"
    )
    env = {
        "HOME": temporary,
        "XDG_CONFIG_HOME": str(root / "config-home"),
        "SALMON_CONFIG_DIR": str(root / "config"),
        "SALMON_WEB_TOKEN": "smoked-flake-local-test-token-123456789",
        "PATH": "",
        "PYTHONDONTWRITEBYTECODE": "1",
    }

    initial_config = root / "first-run"
    subprocess.run(
        [salmon], cwd=root,
        env={**env, "SALMON_CONFIG_DIR": str(initial_config)},
        input="y\n", text=True, capture_output=True, check=True, timeout=30,
    )
    assert (initial_config / "config.toml").read_text() == default.read_text()

    def run(*command, tool=False):
        executable = shutil.which(command[0], path=tools) if tool else command[0]
        result = subprocess.run(
            [executable, *command[1:]],
            cwd=root,
            env={**env, "PATH": tools} if tool else env,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout

    assert "Commands:" in run(salmon, "--help")
    assert "Start the web interface" in run(salmon, "web", "--help")
    assert "Required Dependencies:" in run(salmon, "health")

    # An enclosing Nix shell or user install must not override the locked packages.
    user_base = root / "external-python"
    user_site = user_base / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    (user_site / "PIL").mkdir(parents=True)
    (user_site / "PIL/__init__.py").write_text(
        "raise RuntimeError('salmon loaded Pillow from outside its environment')\n"
    )
    for inherited_python in [
        {"PYTHONPATH": str(user_site)},
        {"PYTHONHOME": str(root / "missing-python")},
        {"PYTHONUSERBASE": str(user_base), "PYTHONNOUSERSITE": "0"},
    ]:
        result = subprocess.run(
            [salmon, "--help"], cwd=root, env={**env, **inherited_python},
            text=True, capture_output=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "Commands:" in result.stdout

    run("sox", "-n", "tone.wav", "synth", "2", "sine", "440", tool=True)
    run("flac", "--silent", "tone.wav", "-o", "tone.flac", tool=True)
    run("lame", "--silent", "tone.wav", "tone.mp3", tool=True)
    run("mp3val", "tone.mp3", tool=True)
    run(
        "sox", "tone.mp3", "-n", "spectrogram", "-x", "500", "-y", "513",
        "-o", "spectral.png", tool=True,
    )
    run("oxipng", "-o", "1", "spectral.png", tool=True)
    run("rclone", "version", tool=True)
    assert (root / "spectral.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    # Load binary extension modules and actually decode audio through PyAV's FFmpeg libraries.
    run(
        sys.executable, "-c",
        "import av, numpy, oxipng, backports.zstd, lxml.etree; "
        "from PIL import Image; from importlib.metadata import version; "
        f"assert version('salmon') == {version!r}; "
        "assert next(av.open('tone.flac').decode(audio=0)).samples > 0; "
        "assert Image.open('spectral.png').width > 0",
    )

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )

    def request(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Origin": base, "Content-Type": "application/json"} if data else {}
        with client.open(urllib.request.Request(base + path, data, headers), timeout=2) as response:
            return response.read()

    with (root / "server.log").open("w+") as log:
        server = subprocess.Popen(
            [salmon, "web", "--host", "127.0.0.1", "--port", str(port)],
            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    assert json.loads(request("/api/health")) == {"status": "ok"}
                    break
                except (urllib.error.URLError, TimeoutError):
                    if server.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        raise AssertionError(log.read())
                    time.sleep(0.1)

            html = request("/").decode()
            assets = re.findall(r'(?:src|href)="(/[^\"]+)"', html)
            assert any(asset.endswith(".js") for asset in assets), html
            for asset in assets:
                assert request(asset), asset
            request("/api/login", {"token": env["SALMON_WEB_TOKEN"]})
            dashboard = json.loads(request("/api/dashboard"))
            assert dashboard["version"] == version, dashboard
            assert all(dashboard["tools"]["required"].values()), dashboard["tools"]
            assert dashboard["tools"]["optional"]["rclone"], dashboard["tools"]
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()

print(f"smoked-salmon {version}: CLI, audio processing and web interface passed")

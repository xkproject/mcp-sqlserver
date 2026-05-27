#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from pathlib import Path

import keyring

SERVICE_NAME = "mcp-sqlserver-pccom"
SCRIPT_DIR = Path(__file__).parent

INSTALL_URLS = {
    "node": "https://nodejs.org/  (or via fnm: https://github.com/Schniz/fnm)",
    "pnpm": "https://pnpm.io/installation",
}


def get_config(db_target: str) -> dict:
    host     = os.environ.get("SQLSERVER_HOST")     or keyring.get_password(SERVICE_NAME, "host")
    user     = os.environ.get("SQLSERVER_USER")     or keyring.get_password(SERVICE_NAME, "user")
    password = os.environ.get("SQLSERVER_PASSWORD") or keyring.get_password(SERVICE_NAME, "password")
    db_key   = "database_pccom" if db_target == "pccom" else "database_dat"
    database = os.environ.get("SQLSERVER_DATABASE") or keyring.get_password(SERVICE_NAME, db_key)

    missing = [k for k, v in {"host": host, "user": user, "password": password, "database": database}.items() if not v]
    if missing:
        print(
            f"Missing SQL Server credentials: {', '.join(missing)}.\n"
            f"Run: uv run --directory .mcp-servers/mcp-sqlserver set_credentials.py",
            file=sys.stderr,
        )
        sys.exit(1)

    return {"host": host, "user": user, "password": password, "database": database}


def find_executable_or_die(name: str) -> Path:
    """
    Locate an executable by name. Search order:
      1. PATH (via shutil.which), trying common Windows suffixes.
      2. On Windows, fnm's node-versions directory (some shells — e.g. VS
         Code's extension host — do not initialise fnm shims, so the executable
         is on disk but not on PATH).

    Exits with a clear, actionable error message if not found.
    """
    if sys.platform == "win32":
        candidates = [f"{name}.cmd", f"{name}.exe", name]
    else:
        candidates = [name]

    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return Path(found)

    if sys.platform == "win32":
        fnm_versions = Path(os.environ.get("APPDATA", "")) / "fnm" / "node-versions"
        if fnm_versions.exists():
            for version_dir in sorted(fnm_versions.iterdir(), reverse=True):
                for candidate in candidates:
                    p = version_dir / "installation" / candidate
                    if p.exists():
                        return p

    install_url = INSTALL_URLS.get(name, "")
    print(
        f"Error: '{name}' is not installed or not on PATH.\n"
        f"\n"
        f"The SQL Server MCP server requires '{name}' to bootstrap and run.\n"
        f"Install it from: {install_url}\n"
        f"\n"
        f"After installing, open a new terminal session so the PATH is\n"
        f"refreshed, then restart your MCP client.",
        file=sys.stderr,
    )
    sys.exit(1)


def build_env(config: dict, extra_paths: list[Path]) -> dict:
    """
    Build the subprocess environment. Prepends the directories of the located
    executables to PATH so any child process the MCP server may spawn can find
    them even when the parent shell environment was not initialised (e.g. under
    VS Code's extension host).
    """
    path_prefix = os.pathsep.join(str(p) for p in extra_paths)
    return {
        "SQLSERVER_HOST":       config["host"],
        "SQLSERVER_USER":       config["user"],
        "SQLSERVER_PASSWORD":   config["password"],
        "SQLSERVER_DATABASE":   config["database"],
        "SQLSERVER_ENCRYPT":    "false",
        "SQLSERVER_TRUST_CERT": "true",
        "PATH":                 path_prefix + os.pathsep + os.environ.get("PATH", ""),
        "SYSTEMROOT":           os.environ.get("SYSTEMROOT", ""),
        "TEMP":                 os.environ.get("TEMP", ""),
        "TMP":                  os.environ.get("TMP", ""),
    }


def build_dist(pnpm: Path, env: dict) -> None:
    """Install dependencies and compile TypeScript on first run."""
    if not (SCRIPT_DIR / "pnpm-lock.yaml").exists():
        print(
            "pnpm-lock.yaml not found in the submodule. The fork is in an\n"
            "inconsistent state — re-clone or run 'pnpm install' here and\n"
            "commit the lockfile.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Building MCP server (first run, ~15 seconds)...", file=sys.stderr)
    subprocess.run(
        [str(pnpm), "install", "--frozen-lockfile"],
        cwd=SCRIPT_DIR,
        env=env,
        check=True,
    )
    subprocess.run(
        [str(pnpm), "run", "build"],
        cwd=SCRIPT_DIR,
        env=env,
        check=True,
    )


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("pccom", "dat"):
        print("Usage: start.py <pccom|dat>", file=sys.stderr)
        sys.exit(1)

    config = get_config(sys.argv[1])

    dist_file = SCRIPT_DIR / "dist" / "index.js"

    node = find_executable_or_die("node")
    extra_paths = [node.parent]

    if not dist_file.exists():
        pnpm = find_executable_or_die("pnpm")
        if pnpm.parent != node.parent:
            extra_paths.insert(0, pnpm.parent)
        env = build_env(config, extra_paths)
        build_dist(pnpm, env)
    else:
        env = build_env(config, extra_paths)

    result = subprocess.run([str(node), str(dist_file)], env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()

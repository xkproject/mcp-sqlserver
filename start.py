#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
from pathlib import Path

import keyring

SERVICE_NAME = "mcp-sqlserver-pccom"
SCRIPT_DIR = Path(__file__).parent


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


def find_pnpm() -> Path:
    """
    Locate the pnpm executable.

    On most systems pnpm is on PATH.  Under VS Code's extension host the shell
    environment is not initialised (no fnm shims), so we fall back to scanning
    the stable fnm node-versions directory for the newest installed version.
    """
    pnpm_name = "pnpm.cmd" if sys.platform == "win32" else "pnpm"

    found = shutil.which(pnpm_name)
    if found:
        return Path(found)

    if sys.platform == "win32":
        fnm_versions = Path(os.environ.get("APPDATA", "")) / "fnm" / "node-versions"
        if fnm_versions.exists():
            for version_dir in sorted(fnm_versions.iterdir(), reverse=True):
                candidate = version_dir / "installation" / pnpm_name
                if candidate.exists():
                    return candidate

    return Path(pnpm_name)  # fallback — OS will raise a clear FileNotFoundError


def build_env(config: dict, pnpm: Path) -> dict:
    """
    Build the subprocess environment.

    Prepends the pnpm installation directory to PATH so that node.exe (placed
    next to pnpm.cmd by fnm) is always resolvable, even when VS Code's
    extension host has not initialised the shell environment.
    """
    return {
        "SQLSERVER_HOST":       config["host"],
        "SQLSERVER_USER":       config["user"],
        "SQLSERVER_PASSWORD":   config["password"],
        "SQLSERVER_DATABASE":   config["database"],
        "SQLSERVER_ENCRYPT":    "false",
        "SQLSERVER_TRUST_CERT": "true",
        "PATH":                 str(pnpm.parent) + os.pathsep + os.environ.get("PATH", ""),
        "SYSTEMROOT":           os.environ.get("SYSTEMROOT", ""),
        "TEMP":                 os.environ.get("TEMP", ""),
        "TMP":                  os.environ.get("TMP", ""),
    }


def ensure_dependencies(pnpm: Path, env: dict) -> None:
    """Ensure node_modules and dist/ are built."""
    dist_file = SCRIPT_DIR / "dist" / "index.js"

    if dist_file.exists():
        return

    print("Building MCP server (this may take a minute on first run)...", file=sys.stderr)

    if not (SCRIPT_DIR / "pnpm-lock.yaml").exists():
        print(
            "pnpm-lock.yaml not found.\n"
            "Run 'pnpm install' in .mcp-servers/mcp-sqlserver/ and commit the lockfile.",
            file=sys.stderr,
        )
        sys.exit(1)

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
    pnpm   = find_pnpm()
    env    = build_env(config, pnpm)

    ensure_dependencies(pnpm, env)

    result = subprocess.run(["node", str(SCRIPT_DIR / "dist" / "index.js")], env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()

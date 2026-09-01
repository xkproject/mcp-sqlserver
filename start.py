#!/usr/bin/env python3
import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import keyring

SERVICE_NAME = "mcp-sqlserver-pccom"
SCRIPT_DIR = Path(__file__).parent

INSTALL_URLS = {
    "node": "https://nodejs.org/  (or via fnm: https://github.com/Schniz/fnm)",
    "pnpm": "https://pnpm.io/installation",
}

# Bootstrap = the 'pnpm install' + 'tsc' that materialises dist/.
BOOTSTRAP_LOCK     = SCRIPT_DIR / ".bootstrap.lock"
BOOTSTRAP_STAMP    = SCRIPT_DIR / "dist" / ".bootstrap-ok"
LOCK_WAIT_SECONDS  = 300
LOCK_STALE_SECONDS = 900

# Everything the compiled dist/ is derived from, beyond src/**/*.ts.
BOOTSTRAP_INPUTS = ("package.json", "pnpm-lock.yaml", "tsconfig.json")


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


def sources_fingerprint() -> str:
    """
    Digest of everything dist/ is compiled from: src/**/*.ts plus the manifests
    that drive install and compilation. Paths go into the digest alongside the
    bytes, so adding, renaming or deleting a source file counts as a change.
    """
    digest = hashlib.sha256()
    files  = sorted((SCRIPT_DIR / "src").rglob("*.ts"))
    files += [SCRIPT_DIR / name for name in BOOTSTRAP_INPUTS]

    for path in files:
        relative = path.relative_to(SCRIPT_DIR).as_posix()
        try:
            data = path.read_bytes()
        except OSError:
            # A missing input is itself part of the identity of this checkout.
            digest.update(b"missing\0" + relative.encode("utf-8") + b"\0")
            continue
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(data)

    return digest.hexdigest()


def needs_bootstrap(dist_file: Path) -> bool:
    """
    dist/ is trustworthy only when the stamp beside it records the fingerprint
    of the sources currently on disk. Two failure modes this closes:

      - Stale build. Checking that index.js merely exists meant a checkout to
        another commit kept running the previously compiled code forever, since
        nothing ever asked whether dist/ still matched src/.
      - Half-written build. The MCP client drops the connection after ~30s; a
        slow build killed midway left a partial dist/ that later runs executed
        as if it were good. The stamp is written only after 'tsc' succeeds.
    """
    if not dist_file.exists():
        return True
    try:
        return BOOTSTRAP_STAMP.read_text(encoding="ascii").strip() != sources_fingerprint()
    except OSError:
        return True


def node_modules_in_sync() -> bool:
    """
    True when pnpm's installed-state marker is at least as new as the lockfile,
    i.e. 'pnpm install' has nothing left to do. Skipping a redundant install is
    what keeps a plain recompile short enough to finish inside the MCP client's
    connection timeout: the install dominates the bootstrap cost.
    """
    marker   = SCRIPT_DIR / "node_modules" / ".modules.yaml"
    lockfile = SCRIPT_DIR / "pnpm-lock.yaml"
    try:
        return marker.stat().st_mtime >= lockfile.stat().st_mtime
    except OSError:
        return False


@contextlib.contextmanager
def bootstrap_lock():
    """
    Cross-process mutex around the bootstrap build.

    The MCP client starts one process per configured server (sqlserver-pccom
    and sqlserver-dat) against this same directory, simultaneously. Without a
    mutex both ran 'pnpm install' and 'tsc' over the same node_modules and
    dist/, clobbering each other; the loser died with a bare 'Connection
    closed'. Now only the winner builds and the others wait here, finding
    dist/ already in place when they wake up.

    Implemented with an O_EXCL lock file rather than a third-party file-lock
    package, so the wrapper keeps running from a bare 'uv run' with keyring as
    its only dependency.
    """
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fd = os.open(str(BOOTSTRAP_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            break
        except FileExistsError:
            pass

        try:
            age = time.time() - BOOTSTRAP_LOCK.stat().st_mtime
        except OSError:
            continue  # holder released it between the two calls; retry at once

        if age > LOCK_STALE_SECONDS:
            print(f"Removing stale bootstrap lock ({int(age)}s old).", file=sys.stderr)
            BOOTSTRAP_LOCK.unlink(missing_ok=True)
            continue

        if time.monotonic() >= deadline:
            print(
                f"Timed out after {LOCK_WAIT_SECONDS}s waiting for another MCP server "
                f"process to finish building this directory.",
                f"",
                f"Build it by hand, then restart your MCP client:",
                f"  cd {SCRIPT_DIR}",
                f"  pnpm install --frozen-lockfile && pnpm run build",
                f"",
                f"If no build is actually running, delete {BOOTSTRAP_LOCK.name} first.",
                sep="\n",
                file=sys.stderr,
            )
            sys.exit(1)

        time.sleep(0.5)

    try:
        yield
    finally:
        BOOTSTRAP_LOCK.unlink(missing_ok=True)


def build_dist(pnpm: Path, env: dict) -> None:
    """
    Install dependencies and compile TypeScript. Call only under
    bootstrap_lock(); writes BOOTSTRAP_STAMP once both steps have succeeded.
    """
    # Captured before compiling on purpose. If a source file changes while tsc
    # runs, the stamp records the older fingerprint and the next start rebuilds
    # -- the safe direction. Stamping afterwards would bless code never built.
    fingerprint = sources_fingerprint()

    if not (SCRIPT_DIR / "pnpm-lock.yaml").exists():
        print(
            "pnpm-lock.yaml not found in the submodule. The fork is in an\n"
            "inconsistent state — re-clone or run 'pnpm install' here and\n"
            "commit the lockfile.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        "Sources changed, rebuilding MCP server. This can outlast your MCP",
        "client's connection timeout: if the server shows up as timed out,",
        "just reconnect it - the build is kept and the retry is instant.",
        sep="\n",
        file=sys.stderr,
    )

    # tsc does not prune, so a rebuild after files were renamed or deleted
    # would otherwise leave orphaned .js behind next to the fresh output.
    shutil.rmtree(SCRIPT_DIR / "dist", ignore_errors=True)

    if node_modules_in_sync():
        print("Dependencies already in sync, skipping 'pnpm install'.", file=sys.stderr)
    else:
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
    BOOTSTRAP_STAMP.write_text(fingerprint + "\n", encoding="ascii")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("pccom", "dat"):
        print("Usage: start.py <pccom|dat>", file=sys.stderr)
        sys.exit(1)

    config = get_config(sys.argv[1])

    dist_file = SCRIPT_DIR / "dist" / "index.js"

    node = find_executable_or_die("node")
    extra_paths = [node.parent]

    if needs_bootstrap(dist_file):
        pnpm = find_executable_or_die("pnpm")
        if pnpm.parent != node.parent:
            extra_paths.insert(0, pnpm.parent)
        env = build_env(config, extra_paths)
        with bootstrap_lock():
            # A peer process may have finished the build while we waited.
            if needs_bootstrap(dist_file):
                build_dist(pnpm, env)
    else:
        env = build_env(config, extra_paths)

    result = subprocess.run([str(node), str(dist_file)], env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()

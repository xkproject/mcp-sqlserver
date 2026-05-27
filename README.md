# MCP SQL Server

A read-only Model Context Protocol (MCP) server for Microsoft SQL Server that enables AI agents to safely explore and query SQL Server databases.

This is a fork of [bilims/mcp-sqlserver](https://github.com/bilims/mcp-sqlserver) with two integrated additions:

1. **Secure credential storage** — credentials are read from the system keychain (Windows Credential Manager via DPAPI) instead of being passed in plain text by the MCP client config.
2. **Supply-chain hardening** — pnpm v11 security policies applied to mitigate npm supply-chain attacks (postinstall malware, typosquatting, premature releases).

The server is launched through a small Python wrapper (`start.py`) that handles credential injection and automated build-on-demand. The MCP server itself is unchanged from upstream.

## Prerequisites

The developer needs these tools installed on their machine. They are NOT installed automatically:

- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — Python package manager (also installs Python if not present)
- **[Node.js](https://nodejs.org/)** 18+ — recommended via [fnm](https://github.com/Schniz/fnm) for version management
- **[pnpm](https://pnpm.io/installation)** 11+ — package manager (`npm install -g pnpm` or via fnm/Corepack)

Everything else (Python dependencies, Node dependencies, TypeScript build output) is bootstrapped automatically on first run.

## Setup

### 1. Store SQL Server credentials

Run the credential setup script once:

```bash
uv run --directory <path-to-this-repo> set_credentials.py
```

The script asks for:
- **Host** (e.g. `localhost\SQLEXPRESS` or `your-server.database.windows.net`)
- **User** (SQL Server username)
- **Password** (SQL Server password)
- **Database name (pccom)** (e.g. `pccom`)
- **Database name (dat)** (e.g. `dat1`)

Credentials are stored in the **system keychain**:
- **Windows**: Credential Manager (encrypted with DPAPI, tied to your Windows session)
- **macOS**: Keychain
- **Linux**: Secret Service (libsecret)

To update credentials later, run `set_credentials.py` again — current values are pre-filled; press Enter to keep them or type a new value.

**Alternative: environment variables** — if `SQLSERVER_HOST`, `SQLSERVER_USER`, `SQLSERVER_PASSWORD` and `SQLSERVER_DATABASE` are set in the environment, they take precedence over the keychain.

### 2. Configure your MCP client

Add the server entry to your MCP client config:

```json
{
  "mcpServers": {
    "sqlserver-pccom": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "<path-to-this-repo>", "start.py", "pccom"]
    },
    "sqlserver-dat": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "<path-to-this-repo>", "start.py", "dat"]
    }
  }
}
```

Restart your MCP client.

### 3. First-run bootstrap (automatic)

On the **first time** the MCP client launches the server, `start.py` will:

1. Load credentials from the keychain (or env vars)
2. Detect that `dist/index.js` is missing
3. Run `pnpm install --frozen-lockfile` to install dependencies (~10–15s)
4. Run `pnpm run build` to compile TypeScript to `dist/` (~1s)
5. Launch `node dist/index.js` with injected credentials

This takes roughly 15 seconds on a typical machine. Subsequent launches skip steps 3–4 entirely and start in under a second.

If your MCP client times out during the first bootstrap, just restart it — the build artifacts are persistent.

## Supply-chain security

This fork applies the defensive policies introduced in **pnpm v11**, which mitigate the supply-chain attack class demonstrated by the TanStack incident (May 2026).

### `.npmrc`

| Setting | Value | Purpose |
|---|---|---|
| `ignore-scripts` | `true` | No package may execute `preinstall` / `postinstall` automatically. Neutralises the primary infection vector. |
| `minimum-release-age` | `1440` | Versions younger than 24 hours are not installed. Gives the community time to detect and revoke compromised releases. |
| `block-exotic-subdeps` | `true` | Transitive dependencies cannot pull from git URLs or tarballs — only the official npm registry. |
| `auto-install-peers` | `true` | Operational, not security: pnpm resolves TypeScript peer deps transparently. |

### `pnpm-workspace.yaml`

Build scripts are blocked globally by `ignore-scripts=true`. The packages that genuinely need their `postinstall` (currently only `esbuild`, which downloads a platform-specific binary) are listed explicitly:

```yaml
allowBuilds:
  esbuild: true
```

When upgrading dependencies in the future, `pnpm install` will prompt to approve any new package that wants to run scripts — `pnpm approve-builds` updates this allowlist.

### `pnpm-lock.yaml`

The lockfile pins every direct and transitive dependency to a specific version + integrity hash. Installs use `--frozen-lockfile` (in `start.py`), which refuses to deviate from the lockfile. There is no resolution / fetch of newer versions at install time.

## How it works

```
┌────────────────────────┐    stdio    ┌─────────────────────────┐
│      MCP client        │ <─────────> │  start.py (Python)      │
│  (Claude Code, etc.)   │             │  - reads keychain       │
└────────────────────────┘             │  - bootstraps build     │
                                        │  - exec node            │
                                        └───────────┬─────────────┘
                                                    │ spawn with env vars
                                                    ▼
                                        ┌─────────────────────────┐
                                        │  node dist/index.js     │
                                        │  (MCP server, mssql)    │
                                        └───────────┬─────────────┘
                                                    │ TDS
                                                    ▼
                                        ┌─────────────────────────┐
                                        │  SQL Server             │
                                        └─────────────────────────┘
```

The MCP client never sees the credentials directly — they live in the system keychain, are read by `start.py` at launch time, and passed to the Node process as environment variables (not arguments, not files on disk).

## Available tools

### Schema discovery
- `list_databases` — list all databases on the SQL Server instance
- `list_tables` — list tables in a database or schema
- `list_views` — list views in a database or schema
- `describe_table` — get column types, constraints, indexes
- `get_foreign_keys` — get foreign key relationships for tables
- `get_table_stats` — get row counts and size information

### Data exploration
- `execute_query` — run read-only SELECT queries (validated, rate-limited)
- `get_server_info` — SQL Server version, edition, configuration
- `test_connection` — connectivity check

## Read-only safety

The server only accepts SELECT / WITH / SHOW / DESCRIBE / EXPLAIN statements. Any DML or DDL (INSERT, UPDATE, DELETE, DROP, ALTER, EXEC, MERGE, …) is rejected before reaching the database. There is also a default `TOP` clause injection and a configurable row limit.

## Environment variables

These can be set to override the per-call defaults, normally inside `start.py`:

| Variable | Default in `start.py` | Description |
|---|---|---|
| `SQLSERVER_HOST` | from keychain | Server hostname or `HOST\INSTANCE` |
| `SQLSERVER_USER` | from keychain | SQL login |
| `SQLSERVER_PASSWORD` | from keychain | SQL password |
| `SQLSERVER_DATABASE` | from keychain (`pccom` or `dat`) | Initial database |
| `SQLSERVER_ENCRYPT` | `false` | TLS to the server |
| `SQLSERVER_TRUST_CERT` | `true` | Accept self-signed certificates |
| `SQLSERVER_PORT` | `1433` | Override the port |
| `SQLSERVER_CONNECTION_TIMEOUT` | `30000` | ms |
| `SQLSERVER_REQUEST_TIMEOUT` | `60000` | ms |
| `SQLSERVER_MAX_ROWS` | `1000` | Hard row limit per query |

For production / cloud SQL targets, change `SQLSERVER_ENCRYPT` to `true` and `SQLSERVER_TRUST_CERT` to `false` in `start.py` (or set the env vars upstream).

## Development

When modifying `src/`:

```bash
pnpm install --frozen-lockfile   # if you don't have node_modules yet
pnpm run dev                     # tsx watcher
# or
pnpm run build                   # one-off compile to dist/
```

After committing new TypeScript sources, the next launch from any developer's machine will pick up the change automatically (build-on-demand sees `dist/` outdated… actually `dist/` is gitignored, so it always rebuilds when missing).

If you add a new dependency that needs to execute `postinstall`, `pnpm install` will prompt you to approve it. Run `pnpm approve-builds` and review the additions to `pnpm-workspace.yaml` before committing.

## Troubleshooting

### "Missing SQL Server credentials"
Run `uv run set_credentials.py` to populate the keychain, or export the `SQLSERVER_*` variables in your shell.

### MCP client reports a startup timeout on first launch
The first bootstrap installs ~430 packages and compiles TypeScript. On a slow machine this can exceed your MCP client's default timeout. Restart the MCP client — by then `dist/` exists and launch is sub-second.

### "Lockfile fails supply-chain policies"
pnpm refused to install a package that doesn't meet `minimum-release-age` or `block-exotic-subdeps`. This is by design. Check the offending package — if it is legitimate and you accept the risk, you can temporarily relax `.npmrc` for the install (do NOT commit the relaxed version).

### Credentials don't load
1. Run `set_credentials.py` again — the current values are shown pre-filled. If they're empty, they weren't saved.
2. On Windows: open *Credential Manager → Windows Credentials* and look for entries named `mcp-sqlserver-pccom`.
3. Try setting `SQLSERVER_HOST` etc. as environment variables — they take precedence over the keychain and will surface configuration problems immediately.

### Build fails with "esbuild postinstall: Failed"
The esbuild postinstall is allowed by `pnpm-workspace.yaml`. If it fails, the cause is usually a network problem fetching the platform binary. Retry `pnpm install --frozen-lockfile`.

## License

MIT, same as upstream. See `LICENSE`.

---

Built on the [Model Context Protocol SDK](https://github.com/modelcontextprotocol/typescript-sdk). Fork of [bilims/mcp-sqlserver](https://github.com/bilims/mcp-sqlserver).

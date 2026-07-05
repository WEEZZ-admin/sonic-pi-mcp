# sonic-pi-mcp

`sonic-pi-mcp` is a Python MCP server that lets an MCP client control a local
Sonic Pi runtime over the standard `stdio` transport.

It starts Sonic Pi's Ruby daemon, sends OSC messages to the Spider runtime, runs
Sonic Pi code, stops jobs, reads runtime events/logs, and searches the local
Sonic Pi docs/samples/synthdefs.

## Requirements

- Python 3.11+
- A local Sonic Pi installation or checkout.
- The Sonic Pi root directory must contain:
  - `app/server/ruby/bin/daemon.rb`
  - `app/server/ruby/bin/spider-server.rb`
  - usually `etc/doc`, `etc/samples`, and `etc/synthdefs`

No machine-specific path is baked into this package. Set `SONIC_PI_ROOT` in the
MCP client environment unless you pass `root_path` to `sonic_start`.

## Install

From PyPI:

```bash
pip install sonic-pi-mcp
```

From a local checkout:

```bash
pip install .
```

Build a wheel/sdist:

```bash
python -m build
```

Then install the wheel on another machine:

```bash
pip install dist/sonic_pi_mcp-*.whl
```

The wheel only contains the Python package under `src/sonic_pi_mcp`. Generated
runtime files, exported audio, examples, tests, and local scripts are excluded
from distribution.

## Configuration

Required in most deployments:

```text
SONIC_PI_ROOT=<path to the Sonic Pi root directory>
```

Useful optional variables:

```text
SONIC_PI_MCP_RUNTIME_DIR=<writable directory for temporary run_file buffers>
SONIC_PI_MCP_STARTUP_TIMEOUT=60
SONIC_PI_MCP_KEEPALIVE_INTERVAL=4
SONIC_PI_MCP_EVENT_BUFFER_SIZE=5000
SONIC_PI_MCP_DEFAULT_COLLECT_MS=1500
SONIC_PI_HOME=<custom Sonic Pi user-home root for logs, if needed>
```

`SONIC_PI_MCP_RUNTIME_DIR` is used when code is too large for Sonic Pi's OSC
packet size and must be submitted with `run_file`. If it is not set, the server
uses a per-user cache directory such as `%LOCALAPPDATA%\sonic-pi-mcp` on
Windows, `~/Library/Caches/sonic-pi-mcp` on macOS, or
`${XDG_CACHE_HOME:-~/.cache}/sonic-pi-mcp` on Linux.

PowerShell example without hard-coding a drive:

```powershell
$env:SONIC_PI_ROOT = Join-Path $env:ProgramFiles 'Sonic Pi'
$env:SONIC_PI_MCP_RUNTIME_DIR = Join-Path $env:LOCALAPPDATA 'sonic-pi-mcp'
sonic-pi-mcp
```

POSIX shell example:

```bash
export SONIC_PI_ROOT="$HOME/apps/sonic-pi"
export SONIC_PI_MCP_RUNTIME_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/sonic-pi-mcp"
sonic-pi-mcp
```

## MCP Client Setup

This package is a `stdio` MCP server. Configure clients to run the installed
console command `sonic-pi-mcp`.

Generic MCP JSON shape:

```json
{
  "mcpServers": {
    "sonic-pi": {
      "command": "sonic-pi-mcp",
      "args": [],
      "env": {
        "SONIC_PI_ROOT": "<path to Sonic Pi root>",
        "SONIC_PI_MCP_RUNTIME_DIR": "<writable runtime directory>"
      }
    }
  }
}
```

If your client does not inherit shell environment variables, put the variables
in the client config. Avoid relying on the terminal profile of the user who
installed the package.

If startup fails, the error includes preflight results, recent daemon output,
Sonic Pi log tails, and likely fixes for common path, permission, and audio
backend issues.

## Run Manually

```bash
python -m sonic_pi_mcp
```

or:

```bash
sonic-pi-mcp
```

Both commands run the same `stdio` MCP server. They do not open an HTTP port.

## MCP Tools

- `sonic_start(root_path?, no_inputs?)`
- `sonic_status()`
- `sonic_preflight(root_path?)`
- `sonic_run_code(code, buffer_name?, collect_ms?)`
- `sonic_play_file(path, buffer_name?, collect_ms?)`
- `sonic_start_recording(collect_ms?)`
- `sonic_stop_recording(collect_ms?)`
- `sonic_save_recording(output_path, collect_ms?, wait_timeout?)`
- `sonic_delete_recording(collect_ms?)`
- `sonic_record_file(path, output_path, duration_seconds, bit_depth?, buffer_name?,
  root_path?, no_inputs?, overwrite?, shutdown_after?, save_timeout?)`
- `sonic_stop(collect_ms?)`
- `sonic_shutdown()`
- `sonic_read_events(since?, limit?)`
- `sonic_get_logs(source?, tail?)`
- `sonic_send_cue(path, args?)`
- `sonic_search_docs(query, limit?, root_path?)`
- `sonic_list_samples(limit?, root_path?)`
- `sonic_list_synths(limit?, root_path?)`
- `sonic_list_fx(limit?, root_path?)`

## Suggested Agent Workflow

1. Call `sonic_preflight()` when setting up a new machine or after a boot
   failure.
2. Call `sonic_start(no_inputs=true)` unless the user needs audio input.
3. Call `sonic_status()` and confirm `state` is `ready`.
4. Use `sonic_search_docs`, `sonic_list_samples`, `sonic_list_synths`, and
   `sonic_list_fx` to stay within the user's installed Sonic Pi version.
5. Send music with `sonic_run_code(code, buffer_name, collect_ms)` or run a
   local `.rb` file with `sonic_play_file(path, buffer_name, collect_ms)`.
6. Export a fixed-duration WAV with `sonic_record_file(path, output_path,
   duration_seconds, bit_depth=24)` when the user asks for a rendered file.
7. Inspect returned events for `syntax_error`, `runtime_error`, or missing
   `Defining fn :live_loop_...` messages.
8. Call `sonic_stop()` before replacing a long-running composition.
9. Call `sonic_shutdown()` when the session is no longer needed.

## Security

`sonic_run_code` executes local Sonic Pi code through the same token-protected
Spider API used by the Sonic Pi GUI. Treat access to this MCP server as local
code execution and local audio-device control.

## License

MIT License. You may use, copy, modify, publish, distribute, sublicense, and
sell copies of this package, provided the license text is included.

## Packaging Notes

The package is intentionally path-neutral:

- No repository-local absolute path is embedded.
- `SONIC_PI_ROOT` or the `root_path` tool argument identifies Sonic Pi.
- `SONIC_PI_MCP_RUNTIME_DIR` controls where temporary large-buffer files are
  written.
- Build configuration excludes generated files such as `.runtime/`, `exports/`,
  examples, tests, and local playback/export scripts.

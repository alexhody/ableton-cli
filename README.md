# ableton-cli

Control and inspect Ableton Live from your terminal, your scripts, and your AI agents.

`ableton-cli` is a Python command-line tool that talks to Ableton Live through a small
Remote Script running inside Live. It works well for people at a keyboard and for
automation: every command can return stable JSON and uses fixed exit codes.

## Credits

This repository is a fork of **[6uclz1/ableton-cli](https://github.com/6uclz1/ableton-cli)**.
The CLI, the Remote Script, and most of this documentation come from that project and its
contributors. Thank you for building it.

This fork adds fixes for bugs found while running the tool against Ableton Live 12.4 Suite
on macOS. Both projects are released under the [MIT License](LICENSE).

## Contents

- [What you can do](#what-you-can-do)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Automation](#automation)
- [Protocol](#protocol)
- [Security Model](#security-model)
- [Limitations](#limitations)
- [Skills Integration](#skills-integration)
- [Plugins](#plugins)
- [Shell completion and man page](#shell-completion-and-man-page)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

## What you can do

- **Transport and song:** play, stop, set tempo, move the playhead, change the scale, undo and redo.
- **Tracks and mixer:** volume, pan, sends, routing, output meters, return tracks, the main track.
- **Devices:** insert devices and set any parameter by name, in its display unit (`-10` dB, `180` ms).
- **Clips and arrangement:** create clips, write and edit MIDI notes, build arrangements from scenes.
- **Browser:** search Live's browser and load instruments, effects, samples, and drum kits.
- **Recording:** capture tracks from the session to audio files.
- **Offline Live Sets:** inspect, lint, and diff `.als` files, and write arrangement automation, without Live running.
- **Automation:** JSON output, batch files, a streaming mode, retries, and safety flags such as `--read-only`.
- **AI agents:** install a ready-made skill for Claude Code or Cursor.
- **Plugins:** add command groups from separate packages.

## Requirements

| Requirement | Notes |
| --- | --- |
| Ableton Live 12 or later | The Remote Script needs the Python 3.11 interpreter bundled with Live 12. Live 11 ships Python 3.7 and cannot load it. |
| Python 3.14 or later | For the CLI itself. |
| [uv](https://docs.astral.sh/uv/) | Installs and runs the CLI. |

## Install

### Option A: install the tool

No checkout needed:

```bash
uv tool install "git+https://github.com/alexhody/ableton-cli"
# or pin a release tag:
uv tool install "git+https://github.com/alexhody/ableton-cli@vX.Y.Z"
```

This puts `ableton-cli` on your `PATH`. Release tags and prebuilt artifacts are on the
GitHub Releases page.

### Option B: run from source

```bash
git clone https://github.com/alexhody/ableton-cli
cd ableton-cli
uv sync
```

From a checkout, prefix every command with `uv run`, for example `uv run ableton-cli doctor`.

## Quick start

1. **Install the Remote Script** into your Ableton User Library:

   ```bash
   ableton-cli install-remote-script --yes
   ```

2. **Turn it on in Live.** Open **Preferences → Link, Tempo & MIDI** and set a
   **Control Surface** slot to `AbletonCliRemote`.

3. **Check your setup:**

   ```bash
   ableton-cli doctor
   ```

4. **Check the connection:**

   ```bash
   ableton-cli ping
   ```

5. **Try a command:**

   ```bash
   ableton-cli song info
   ableton-cli tracks list
   ```

> [!TIP]
> After you reinstall or update the Remote Script, restart Live so it loads the new version.

## Usage

The examples below use the installed `ableton-cli` command. From a source checkout, write
`uv run ableton-cli` instead.

For every command and option, see:

- [`skills/ableton-cli/SKILL.md`](skills/ableton-cli/SKILL.md): the full command catalog
- [`docs/skills/skill-actions.md`](docs/skills/skill-actions.md): stable action names and CLI mappings
- [`docs/skills/examples/`](docs/skills/examples/): JSON output examples
- `ableton-cli <group> --help`

### Song and transport

```bash
ableton-cli song info
ableton-cli song undo
ableton-cli song redo
ableton-cli song scale set --root A --name Minor --mode
ableton-cli transport play
ableton-cli transport tempo set 128
ableton-cli transport position get
ableton-cli transport position set 32
ableton-cli transport rewind
```

**Where playback starts.** `transport play` (and `transport toggle` when it starts playback)
plays from the current playhead. To choose where playback and arrangement recording begin,
run `transport position set <beats>` first.

<details>
<summary>How the CLI keeps the playhead where you put it</summary>

Live's `start_playing` restarts from the start marker, and `continue_playing` restarts from
where playback last stopped. So when you move the playhead while stopped, the CLI writes it
again in a follow-up request once playback runs (Live ignores the write before then) and
checks the result by reading it back.

Live applies playhead writes after the request returns. `transport position set` and
`transport rewind` therefore read the position back in a second request, and fail with
`INVALID_ARGUMENT` and `error.details.mismatches` when a stopped playhead did not move.

</details>

### Tracks, returns, and the main track

```bash
ableton-cli tracks list
ableton-cli track volume get --track-index 0
ableton-cli track volume set 0.7 --track-index 0
ableton-cli track panning set -0.25 --track-index 0
ableton-cli track send set 1 0.6 --track-index 0
ableton-cli track routing input get --selected-track
ableton-cli track routing output set --type Master --channel 3/4 --track-name "Bass"
ableton-cli track meter get --track-index 0 --samples 20 --interval-ms 50

ableton-cli return-tracks list
ableton-cli return-tracks create
ableton-cli return-track volume set 0 0.5
ableton-cli return-track meter get 0 --samples 20

ableton-cli master info
ableton-cli master devices list
ableton-cli master meter get --samples 20 --interval-ms 50

ableton-cli mixer crossfader set -0.2
ableton-cli mixer cue-routing get
```

- **Routing:** `--channel` is optional on `track routing input|output set`. Leave it out to
  keep the channel Live picks for the routing type (for example `--type Resampling`, whose
  only channel is empty).
- **Meters:** `track meter get`, `return-track meter get`, and `master meter get` read
  Live's output meters (`left`, `right`, `level`) on Live's 0.0–1.0 meter scale, not dB.
  They take `--samples` readings `--interval-ms` apart and report each channel's `peak` and
  `mean`, plus `is_playing`. Meters read 0.0 while the transport is stopped.

### Choosing tracks, devices, and parameters

Track, device, and parameter commands pick their target with selector flags. Use exactly
one flag from each row that applies.

| Target | Selector flags |
| --- | --- |
| Track | `--track-index`, `--track-name`, `--selected-track`, `--track-query`, `--track-ref` |
| Return track (`device` commands) | `--return-track-index`, `--return-track-name` |
| Main track (`device` commands) | `--main` |
| Device | `--device-index`, `--device-name`, `--selected-device`, `--device-query`, `--device-ref` |
| Parameter | `--parameter-index`, `--parameter-name`, `--parameter-query`, `--parameter-key`, `--parameter-ref` |

- Results for return-track devices report `"track": {"kind": "return", "index": N}`, and
  main-track devices report `"track": {"kind": "main"}`.
- Edit main-track devices only through `device ... --main`. `master devices list` lists
  them, and `master effect <type>` keeps the typed wrappers.
- `tracks list`, `track info`, synth and effect discovery, and parameter listings include
  `stable_ref` fields. Pass them to `--track-ref`, `--device-ref`, or `--parameter-ref` to
  reach the exact same object later in the session.

### Devices and parameters

```bash
ableton-cli device insert Compressor --track-name Bass
ableton-cli device insert Utility --main
ableton-cli device parameters list --track-name Vox --device-name "Auto Shift"
ableton-cli device parameter set 0.25 --track-index 0 --device-index 0 --parameter-index 0
ableton-cli device parameter set-display -18 --track-name Bass --device-name Compressor --parameter-name Threshold
ableton-cli device parameter set-display -10 --main --device-name "Glue Compressor" --parameter-name Threshold
ableton-cli device parameter set-display 4.5 --return-track-index 0 --device-index 0 --parameter-name Decay
ableton-cli device parameter select RMS --track-name Bass --device-name Compressor --parameter-name Model
ableton-cli device sidechain set --type "1-Kick" --channel "Post FX" --track-name Bass --device-name Compressor
ableton-cli device preset set 2 --track-name Vox --device-index 0

ableton-cli synth find --type wavetable
ableton-cli synth wavetable keys
ableton-cli synth wavetable set 0.6 --selected-track --selected-device --parameter-key filter_cutoff
ableton-cli effect find --type eq8
ableton-cli effect eq8 keys
ableton-cli effect eq8 set 0.6 --track-query Bass --device-query EQ --parameter-key band1_freq
```

There are three ways to set a parameter:

| Command | Use it for | Value |
| --- | --- | --- |
| `device parameter set` | Any parameter | Normalized raw value |
| `device parameter set-display` | Continuous parameters | A bare number in the display unit: `180` for 180 ms, `2000` for 2.00 s. Unit text such as `180 ms` is rejected. Negative values such as `-10` work directly. |
| `device parameter select` | Choice and stepped parameters | The label, such as `RMS`, `1/32`, or `4` |

- `device parameters list` reports `value_items` for choice parameters and `step_labels`
  for stepped ones. Live reports some stepped parameters, such as Beat Repeat Grid or Glue
  Compressor Ratio, as non-quantized, but each integer raw value has its own label.
- A display value Live rejects fails with `INVALID_ARGUMENT`.
- Glue Compressor is not a `compressor` wrapper target: it has no Makeup parameter and uses
  raw dB and stepped values. `effect find` skips it. Edit it with `device parameter
  set-display` or `select`.

### Clips, notes, and arrangement

```bash
ableton-cli clip create 0 0 --length 4
ableton-cli clip notes add 0 0 --notes-json '[{"pitch":60,"start_time":0.0,"duration":0.5,"velocity":100,"mute":false}]'
ableton-cli clip notes update 0 0 --notes-json '[{"note_id":3,"velocity":90,"probability":0.75}]'
ableton-cli clip fire 0 0

ableton-cli arrangement clip create 0 --start 8 --length 4 --notes-json '[{"pitch":60,"start_time":0.0,"duration":0.5,"velocity":100,"mute":false}]'
ableton-cli arrangement clip notes get 0 0 --start-time 0.0 --end-time 4.0 --pitch 60
ableton-cli arrangement clip notes import-browser 0 0 sounds/Bass\ Loop.alc --mode replace --import-length --import-groove
ableton-cli arrangement clip delete 0 --start 0 --end 16
ableton-cli arrangement from-session --scenes "0:24,1:48"
```

**Slice a loop onto a Drum Rack.** `clip cut-to-drum-rack` puts one slice on each pad and
can create a clip that triggers them:

```bash
ableton-cli clip cut-to-drum-rack --source-file ./loops/drum-break.wav --transient --bpm 174 --max-slices 16 --create-trigger-clip --trigger-clip-slot 1
ableton-cli clip cut-to-drum-rack --source-track 1 --source-clip 0 --slice-count 8 --create-trigger-clip --trigger-clip-slot 1
```

**Audio clip length.** `arrangement clip create --audio-path` sizes the new clip to
`--length` by moving its loop end, because Live first sizes an audio clip to the file's
warped length and a clip's `length` is read-only. If Live does not apply the length, the
command fails with `INVALID_ARGUMENT` and leaves the clip at Live's length. This happens
with unwarped clips, which measure their loop in seconds.

### Scenes and recording

```bash
ableton-cli scenes list
ableton-cli scenes delete 3
ableton-cli session capture-tracks --track-index 0 --track-index 9 --start 304 --bars 8 --bypass-main-devices
```

`session capture` and `session capture-tracks` return the `file_path` of each recording.

> [!WARNING]
> In an unsaved Set, recordings go into Live's temporary project, which Live deletes when it
> closes without saving. Pass `--copy-to DIR` to keep them.

### Browser: find first, then load

Browser contents depend on your Live version, installed packs, language, and User Library.
Don't assume a Drum Rack or kit URI exists. Search first, then pass the exact `uri` or
`path` from the results:

```bash
ableton-cli browser categories all
ableton-cli browser search drift --item-type loadable
ableton-cli browser search "Drum Rack" --path drums --item-type loadable --limit 10
ableton-cli browser search "Kit" --path drums --item-type loadable --limit 10
ableton-cli browser search "Operator" --path instruments --item-type loadable --limit 10

ableton-cli browser load 0 query:Synths#Operator
ableton-cli browser load 1 <synth-uri-or-path-from-search>
ableton-cli browser load-drum-kit 0 <rack-uri-from-search> --kit-uri <kit-uri-from-search>
```

For agent-driven composition, run a preflight with a long timeout before changing the Set:

```bash
ableton-cli --timeout-ms 15000 --output json wait-ready
ableton-cli --timeout-ms 15000 --output json doctor
ableton-cli --timeout-ms 15000 --output json tracks list
```

### Audio analysis and remix mastering

```bash
ableton-cli audio transient analyze --path ./loops/drum-break.wav --bpm 174 --max-slices 16
ableton-cli remix mastering target set --project ./proj/remix_project.json --profile anime-club-demo
ableton-cli remix mastering report add --project ./proj/remix_project.json --kind loudness --report ./proj/reports/remix_loudness.json
ableton-cli remix mastering plan --project ./proj/remix_project.json
ableton-cli remix mastering apply --project ./proj/remix_project.json --dry-run
ableton-cli remix mastering qa --project ./proj/remix_project.json --render ./renders/remix.wav
```

Loudness, spectrum, band-share, and reference analysis (through ffmpeg) live in the
`analysis` plugin, not in the core. The plugin is the `ableton-cli-analysis` repository; see
[docs/mastering.md](docs/mastering.md) for the install command. It adds:

- `analysis loudness analyze`
- `analysis spectrum analyze`
- `analysis spectrum shares`
- `analysis reference compare`

Write their reports with `--report-out` and register them with
`remix mastering report add --kind loudness|spectrum|reference_compare` so
`remix mastering qa` can read them. `report add` fails with `INVALID_ARGUMENT` when a report
lacks a field QA reads. The plugin can measure the files that `session capture` and
`session capture-tracks` return.

### Offline Live Set files

The `set` command group reads Live Set (`.als`) files directly. It doesn't need Live or the
Remote Script. It is a separate surface, not a fallback: no command switches between the
Remote Script and file access on its own.

```bash
ableton-cli set inspect --path ./Song\ Project/Song.als
ableton-cli set lint --path ./Song\ Project/Song.als
ableton-cli set diff --from ./Song\ Project/Song.als --to ./Song\ Project/Song-v2.als
ableton-cli set automation write --path ./Song\ Project/Song.als --out ./Song\ Project/Song-auto.als --track-index 0 --target device.1.Frequency --points-json '[{"time":0,"value":500},{"time":64,"value":8000}]'
```

| Command | What it does |
| --- | --- |
| `set inspect` | Tempo, time signature, scale, tracks (type, group, devices, clip counts), return tracks, scenes, locators, and plug-in inventory. Tempo is the effective value: an arrangement tempo envelope overrides the manual value. |
| `set lint` | Reports errors in the global ID space (duplicate or dangling IDs, `NextPointeeId`) and warns about missing project samples. |
| `set diff` | Lists `added`, `removed`, and `changed` items. Tracks are matched by their set-local ID. |
| `set automation write` | Writes one arrangement automation lane to a new file, replacing any lane for the same parameter. |

<details>
<summary><code>set lint</code> details</summary>

Project samples resolve against the nearest folder that contains `Ableton Project Info`, as
in Live. Library and other non-project samples resolve against locations the CLI does not
assume, so they are not checked; `result.media` counts them by `RelativePathType`. Findings
list relative paths and file names, not absolute paths.

</details>

<details>
<summary><code>set automation write</code> details</summary>

This is the way to write arrangement automation. The Remote Script cannot, because the Live
API only exposes session clip envelopes.

- **Choosing the path:** put clip-level changes into a session clip with
  `clip envelope set|shape`. Live 12 refuses clip envelopes on arrangement clips ("Not a
  session clip"), and `arrangement from-session` copies notes into new clips without their
  envelopes. For track-wide arrangement automation, save the Set by hand in Live, then run
  `set automation write`. `song save` and `song export audio` fail with
  `not_supported_by_live_api`, because Live exposes no save or export to scripts.
- **Selector:** exactly one of `--track-index`, `--track-name`, `--return-track-index`,
  `--main`.
- **Target:** `mixer.volume`, `mixer.pan`, `mixer.tempo` (`--main` only),
  `send.<return index>`, or `device.<top-level device index>.<parameter XML tag>`.
- **Points:** `[{"time": <beats>, "value": <number>}]`, with times `>= 0` and strictly
  increasing. Values are Live's internal parameter values, checked against the parameter's
  `MidiControllerRange` (tempo: 20–999 BPM). They are not the Remote Script's normalized
  values: `mixer.volume` and `send.<n>` are linear gain (1.0 = 0 dB, 0.5 ≈ −6 dB), and device
  frequencies are in Hz.
- Only continuous parameters are written. On/off and choice parameters are rejected; the
  error for an unknown device parameter lists the writable tags.
- **Output file:** `--out` must not exist, must differ from `--path`, and its directory must
  exist (normally the project folder, so project samples still resolve). The result is
  integrity-checked before an atomic write. The source file is never modified.
- `--read-only` blocks the command. `--dry-run` plans without reading or writing.

</details>

**Errors from `set` commands:**

| Error code | Exit code | Meaning |
| --- | --- | --- |
| `SET_FILE_INVALID` | `2` | Not gzip, not XML, or not a Live Set. |
| `SET_SCHEMA_UNSUPPORTED` | `2` | Not a Live 12 Set, or a structure this version does not understand. |
| `SET_INTEGRITY_FAILED` | `20` | `set lint` found integrity errors, or a write command's result failed the integrity check. `error.details.findings` lists them, and no file is written. |

The `.als` format is undocumented. Only Live 12 Sets are accepted, and widening that range
requires checking real Sets in Live. See
[docs/offline-set-editing-design.md](docs/offline-set-editing-design.md).

## Automation

### JSON output

Add `--output json` for machine-readable output:

```bash
ableton-cli --output json ping
```

Every command returns the same envelope:

```json
{
  "ok": true,
  "command": "ping",
  "args": {},
  "result": {},
  "error": null
}
```

The stream commands `session watch` and `session events` print one JSON object per line
instead.

### Global options

Global options go **before** the subcommand:

```bash
ableton-cli --output json doctor   # correct
ableton-cli doctor --output json   # wrong
```

| Option | What it does |
| --- | --- |
| `--host`, `--port` | Remote Script address (default `127.0.0.1:8765`) |
| `--timeout-ms` | Request timeout in milliseconds |
| `--protocol-version` | Protocol version for the CLI/Remote handshake |
| `--auth-token` | Shared secret sent to the Remote Script |
| `--output [human\|json]` | Output mode (default `human`) |
| `--compact` | Summarize large JSON arrays |
| `--verbose` | Verbose logging |
| `--log-file` | Log file path |
| `--config <path>` | Config file path |
| `--no-color` | Disable color output |
| `--quiet` | Suppress human-readable success output |
| `--record <path>`, `--replay <path>` | Record or replay request/response traffic as JSONL |
| `--read-only` | Reject write commands before they are sent |
| `--require-confirmation` | Reject destructive commands unless `--yes` is also given |
| `--yes` | Confirm destructive commands |
| `--plan` | Show the command's side effects without sending it |
| `--dry-run` | Show the payload that would be sent without sending it |
| `--version` | Show the version and exit |

Installer and config commands also accept `--yes` and `--dry-run` after the subcommand.

### Safe automation

- Treat write and destructive commands like a person clicking in Live.
- Use `--read-only` for inspection and agent preflight.
- Use `--plan` or `--dry-run` to see the side effect and payload before anything runs.
- Use `--require-confirmation --yes` when automation should fail closed around destructive
  commands.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `2` | Invalid arguments |
| `3` | Invalid configuration |
| `10` | Ableton not connected |
| `11` | Remote Script not installed or not detected |
| `12` | Timeout |
| `13` | Protocol mismatch |
| `20` | Execution failure |
| `99` | Internal error |

### Batches

`batch run` runs a list of steps from a file:

```bash
ableton-cli batch run --steps-file ./steps.json
```

`batch stream` keeps one process alive, which avoids process startup time when you send many
commands. It reads one JSON object per stdin line and writes one JSON response per line:

```bash
cat <<'JSONL' | ableton-cli batch stream
{"id":"a1","steps":[{"name":"song_info","args":{}}]}
{"id":"a2","steps":[{"name":"track_volume_get","args":{"track_ref":{"mode":"index","index":0}}}]}
JSONL
```

#### Retries

A step runs once unless it opts in with a `retry` object:

```json
{"name": "tracks_list", "args": {}, "retry": {"max_attempts": 3, "backoff_ms": 200}}
```

- `retry.on` defaults to `["REMOTE_BUSY"]`. The Remote Script rejects `REMOTE_BUSY` before
  it queues the command, so a retry cannot apply it twice.
- `TIMEOUT` is not retried by default, but adding it to `retry.on` is safe for any command.
  A retrying step sends the same `meta.idempotency_key` on every attempt, so the Remote
  Script replays its stored response instead of applying the command again. A replayed
  response carries `idempotent_replay: true`.

A step that fails with `TIMEOUT` and is not retried reports `error.details.may_have_executed`:

| Value | Meaning |
| --- | --- |
| `true` | Live had already started the command. Inspect the Set before retrying. |
| `false` | The command was cancelled before it ran and can be resent safely. |
| `null` | No structured answer reached the CLI. Assume it may have been applied. |

#### Failures

By default a batch stops at the first failing step and keeps that step's error code (and
exit code). `error.details` tells you where it stopped:

- `step_index` and `step_name`: the step that failed
- `completed_steps`: how many steps succeeded before it
- `results`: the results of those steps (Live state they changed is not rolled back)

`--continue-on-error` (on both `batch run` and `batch stream`) runs every step regardless of
failures:

```bash
ableton-cli --output json batch run --continue-on-error --steps-file ./steps.json
```

Each `results[]` entry then carries `ok` plus either `result` or `error`. If any step failed,
the whole batch still fails with `BATCH_STEP_FAILED` (exit code `20`), and the per-step
entries are in `error.details.results` together with `failed_step_indices`.

Assertion failures count as step failures. A failing `previous` assertion skips the step
without running it. A failing `current` assertion marks the already-executed step as failed.
The step after any failed step sees no `previous` payload.

### Speed

The Remote Script serves requests on Live's main thread, from Live's display tick (about
every 100 ms). A request waits at most one tick, so a single command answers in about 50 ms
on average, plus CLI startup. Requests sent one after another (including the steps of
`batch run` and `batch stream`) run at about one per tick. For many commands in a row, use
`batch stream` to skip process startup.

## Protocol

The CLI and the Remote Script talk over local TCP with one JSON object per line (JSONL), on
`<host>:<port>`. The default is `127.0.0.1:8765`; see [Security Model](#security-model) to
change the host, port, or `auth_token`.

- A connection can stay open: one line is one request, and a client may send several
  requests over the same connection.
- Responses come back in request order.
- A malformed request line closes the connection. A well-formed request that fails
  validation or execution returns a structured error and keeps the connection open.
- To check compatibility, use `ableton-cli ping` for protocol and version metadata, and
  `ableton-cli doctor` to check that every supported command is present.

> [!IMPORTANT]
> Protocol versions must match exactly. After upgrading the CLI, reinstall the Remote Script
> and restart Live:
>
> ```bash
> ableton-cli install-remote-script --yes
> ```

### Request

```json
{
  "type": "command",
  "name": "song_info",
  "args": {},
  "meta": {
    "request_timeout_ms": 15000,
    "idempotency_key": "0f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e"
  },
  "request_id": "8c9f9b0c1a9d4dc2abdf2d53f3a19be9",
  "protocol_version": 3
}
```

- `request_id` is new on every send. It never identifies a retry.
- `meta.idempotency_key` is optional (up to 128 characters) and is what makes a resend safe.
  The Remote Script keeps the responses of the last 256 completed keys. A request that
  repeats a completed key is **not executed again**: the stored response comes back with
  `idempotent_replay: true` added to `result` (or to `error.details` for a stored failure).
  `batch run` and `batch stream` create one key per step and reuse it for every retry, so a
  step that times out and is retried cannot be applied twice.

### Response

Success:

```json
{
  "ok": true,
  "request_id": "8c9f9b0c1a9d4dc2abdf2d53f3a19be9",
  "protocol_version": 3,
  "result": {
    "tempo": 120.0
  },
  "error": null
}
```

Failure:

```json
{
  "ok": false,
  "request_id": "8c9f9b0c1a9d4dc2abdf2d53f3a19be9",
  "protocol_version": 3,
  "result": null,
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "bpm must be between 20.0 and 999.0",
    "hint": "Fix command arguments and retry.",
    "details": null
  }
}
```

### Event subscriptions

Commands are strictly request and response, so the CLI can only notice what you do in Live
by polling. That is what `session watch` does. Instead, a connection can send one
`"type": "subscribe"` request and become an event stream:

```json
{"type":"subscribe","name":"events","args":{"events":["tempo","is_playing"]},"meta":{},"request_id":"...","protocol_version":3}
```

The Remote Script answers with the usual response envelope (`result.subscribed` lists the
accepted names, `result.stream` is `true`) and then pushes one line per event:

```json
{"type":"event","protocol_version":3,"event":"tempo","ts":1730000000.0,"data":{"tempo":174.0},"dropped":0}
```

- A subscribing connection stops accepting commands, so events can never delay a command.
  `ableton-cli session events` opens its own connection.
- Event names are `tempo`, `is_playing`, `playing_position`, and `selected_track`. Which ones
  a Live version can deliver depends on the listener APIs it exposes. Subscribing to one that
  is unavailable fails with `INVALID_ARGUMENT` and a hint listing what is available, instead
  of accepting the subscription and never delivering.
- `playing_position` is limited to one event per 100 ms.
- A subscriber that can't keep up loses the oldest events instead of growing an unbounded
  queue. `dropped` reports how many were lost since the previous event.
- A Remote Script that predates subscriptions rejects `"type": "subscribe"` with
  `INVALID_ARGUMENT`, so an old install fails loudly instead of hanging.

## Security Model

> [!CAUTION]
> By default the Remote Script listens on 127.0.0.1 only (local loopback), port 8765, with
> no authentication: any process on the same machine can send it commands. Do not expose,
> forward, or bind the port to untrusted networks.

### Changing the host, port, or token

Set them in `remote_script/AbletonCliRemote/remote_config.json` (copied next to the Remote
Script on install):

```json
{
  "host": "127.0.0.1",
  "port": 8765,
  "auth_token": "replace-with-a-shared-secret"
}
```

Or set environment variables for the Live process: `ABLETON_CLI_REMOTE_HOST`,
`ABLETON_CLI_REMOTE_PORT`, `ABLETON_CLI_REMOTE_AUTH_TOKEN`. Invalid values (empty host,
out-of-range port, non-string token) fail explicitly instead of falling back to defaults.

- To run several Live instances, give each Remote Script its own port.
- On the CLI side, set the matching token with `--auth-token`, the `ABLETON_CLI_AUTH_TOKEN`
  environment variable, or `ableton-cli config set auth_token <token>`. `config show` and
  `config set` always print `auth_token` as `***` (or `null` when unset). The raw value is
  only used on the wire and when writing the local config file.
- `auth_token` is a plain shared-secret check, not encrypted or transport-secured
  authentication. It deters other local processes; it is not a security boundary against a
  hostile user on the same machine.

### Updates and backups

- `install-remote-script` copies the Remote Script directory. A `remote_config.json` already
  in the source tree is kept, but reinstalling overwrites the installed `AbletonCliRemote`
  directory. If your `remote_config.json` lives only in the installed copy, put it back after
  each update.
- Before an update, the previous `AbletonCliRemote` directory moves to
  `User Library/Remote Script Backups/AbletonCliRemote.backup-<YYYYMMDDHHMMSS>` (reported in
  the `backup`/`backups` JSON fields). Backups live outside `Remote Scripts/` because Live
  tries to import every folder there as a control surface.
- Backups are never deleted automatically; remove old ones by hand.
- Older releases left `AbletonCliRemote.backup-*` folders inside `Remote Scripts/`. Each one
  logs a `SyntaxError` import error in Live's `Log.txt` on every launch. Delete or move them.

## Limitations

### Things Live's API can't do

When the CLI exposes a command but Live's API cannot perform it, the command fails with
`error.code=INVALID_ARGUMENT` and `error.details.reason=not_supported_by_live_api`.

On Live 12.4 this applies to:

- `song new`, `song save`, `song export audio`
- `scenes move`
- `mixer cue-routing set`
- `clip file replace`, `arrangement clip file replace`
- `clip groove amount set`, and `clip groove clear` on a clip that has a groove

`song undo|redo`, `arrangement record start|stop`, and `tracks delete` fail the same way only
on Live versions whose API lacks them.

**Note editing needs Live 12.** Note write and edit commands (`clip notes add`,
`clip notes update`, `clip notes quantize|humanize|velocity-scale|transpose`,
`arrangement clip notes add`, and the trigger clip of `clip cut-to-drum-rack`) need Live 12's
extended note API (`Clip.add_new_notes`, `Clip.apply_note_modifications`, and the `note_id`,
`probability`, `velocity_deviation`, and `release_velocity` note fields). On older versions
they fail with `not_supported_by_live_api` instead of falling back to the old note API.

**`clip cut-to-drum-rack` sources.** Each pad is built from Live 12.3+ primitives
(`Track.insert_device`, `RackDevice.insert_chain`, `Chain.insert_device("Simpler")`,
`SimplerDevice.replace_sample`), and each slice is trimmed with the Simpler sample markers.
Browser sources (`--source`) fail with `not_supported_by_live_api`, because Live's browser
items don't expose the file path that `replace_sample` needs. Use
`--source-file <path> --transient` or `--source-track`/`--source-clip`.

### Commands that are not implemented

A few commands are part of the published surface but have nothing behind them, either
because Live's API lacks the primitive (creating return or group tracks, sidechain routing)
or because the work happens outside this CLI (stem separation, browser target discovery).
They fail explicitly instead of returning a plan nobody runs:

- `remix setup-sound`, `remix mix-macro`, `remix setup-mix`, `remix setup-returns`,
  `remix setup-sidechain`, `remix device-chain apply`
- `audio stems split`

They return `error.code=NOT_IMPLEMENTED`, `error.details.reason=not_implemented`, and exit
code `20`, and `error.hint` names the commands that do the job. Arguments are still checked
first, so a bad argument reports `INVALID_ARGUMENT` and exit code `2`.

## Skills Integration

The `ableton-cli` skill teaches AI agents how to use the CLI.

1. Finish the [Quick start](#quick-start) and confirm the connection with `ableton-cli ping`.
2. Install the skill for your agent. Skill examples use the source-checkout form,
   `uv run ableton-cli ...`:

   ```bash
   uv run ableton-cli install-skill --yes
   uv run ableton-cli install-skill --target claude --yes
   uv run ableton-cli install-skill --target cursor --yes
   ```

3. See [docs/skills/install.md](docs/skills/install.md) for detailed install notes.

**Cursor inside this repository needs no install.** `.cursor/rules/*.mdc` and
`.cursor/skills/ableton-cli/SKILL.md` are committed, so the agent picks up the conventions
when you open the project. `install-skill --target cursor` only copies the same skill file to
`~/.cursor/skills/` so other workspaces can find it.

Skill references:

- Skill spec: [`skills/ableton-cli/SKILL.md`](skills/ableton-cli/SKILL.md)
- Stable action mappings: [`docs/skills/skill-actions.md`](docs/skills/skill-actions.md)
- JSON output examples: [`docs/skills/examples/`](docs/skills/examples/)

## Plugins

Features that don't belong in the core CLI (for example a local music generator or a gating
tool) ship as plugins: separate Python packages that add a top-level command group,
`ableton-cli <plugin> ...`. Plugin commands use the core JSON envelope, exit codes,
`--plan`/`--dry-run`, `--read-only`, and `--require-confirmation`.

```bash
ableton-cli --plan plugins install https://github.com/<owner>/<repo>   # preview
ableton-cli plugins install https://github.com/<owner>/<repo>
ableton-cli plugins install https://github.com/<owner>/<repo>/tree/v1.2.0
ableton-cli plugins list
ableton-cli plugins info <name>
ableton-cli plugins update <name> --ref <branch|tag|sha>
ableton-cli plugins uninstall <name>
ableton-cli plugins sync
```

> [!WARNING]
> A plugin runs its code with the same access as `ableton-cli`, including write access to
> your Live Set. Only install repositories you trust. `plugins install`, `update`,
> `uninstall`, and `sync` are destructive commands, so `--require-confirmation` makes them
> require `--yes`.

<details>
<summary>How plugin installs work</summary>

- **Sources:** only `https://github.com/<owner>/<repo>` URLs are accepted, optionally ending
  in `/tree/<ref>` (or pass `--ref`). The ref is resolved with `git ls-remote` to a full
  commit SHA, and the install is pinned to that commit. `--plan` shows the commit without
  installing.
- **Environment:** the package is installed with `uv pip install` into the Python
  environment that runs `ableton-cli`. Every package already installed is pinned through a
  constraints file, so a plugin can add dependencies but never change ableton-cli's or
  another plugin's. `git` and `uv` must be on `PATH` (`CONFIG_INVALID` otherwise).
- **Load check:** after installing, the plugin is loaded in a fresh interpreter. If it
  doesn't load (wrong plugin API version, name clash, import error, ...), the install is
  rolled back and the command fails with `PLUGIN_INVALID`.
- **Manifest:** installed plugins are recorded in `plugins.toml` next to the config file
  (override the path with `ABLETON_CLI_PLUGIN_MANIFEST`). Recreating the environment
  (reinstalling ableton-cli, `uv sync`) removes plugin packages; `doctor` then fails its
  `plugins` check, and `plugins sync` reinstalls the recorded commits.
- **Load failures:** a plugin that fails to load answers every call with
  `PLUGIN_LOAD_FAILED` and the reason in `error.details.load_failure`. Core commands keep
  working. `ABLETON_CLI_PLUGINS=off` disables all plugins (`on` is the default; any other
  value is `CONFIG_INVALID`).
- **Error codes:** plugin error codes start with the plugin name, for example
  `ACE_STEP_GENERATION_FAILED`.
- **Skills:** `install-skill` also installs the skills of loaded plugins
  (`result.plugin_skills`).

</details>

To write a plugin, see [docs/plugins.md](docs/plugins.md).

## Shell completion and man page

Shell completion (from Typer):

```bash
ableton-cli --install-completion
ableton-cli --show-completion
```

Man page, from a source checkout:

```bash
scripts/generate_man.sh   # generate
scripts/install_man.sh    # install locally (optional)
man ableton-cli
```

## Troubleshooting

**Start here:**

1. Run `ableton-cli doctor`.
2. Check that Live is running and `AbletonCliRemote` is selected as a Control Surface.
3. Check the host and port (`127.0.0.1:8765` by default).
4. Try a longer timeout: `--timeout-ms 30000`.

**`doctor` fails `remote_script_version`, or the protocol doesn't match.** The Remote Script
running in Live comes from a different release than the CLI. Run
`ableton-cli install-remote-script --yes`, restart Live, and select `AbletonCliRemote` as
the Control Surface again. Use `--protocol-version <n>` or
`ableton-cli config set protocol_version <n>` only to talk to a Remote Script you pinned on
purpose.

**`AbletonCliRemote` isn't in the Control Surface list.**

1. Reinstall the files: `ableton-cli install-remote-script --yes`.
2. Quit and restart Live.
3. Run `ableton-cli --output json doctor` and check `remote_script_files` and
   `remote_script_entrypoint`.
4. Check that the Remote Script is installed in one of:
   - `~/Music/Ableton/User Library/Remote Scripts/AbletonCliRemote`
   - `~/Documents/Ableton/User Library/Remote Scripts/AbletonCliRemote`

**A Remote Script update doesn't take effect.** If the behavior still looks old after
`install-remote-script --yes` (for example, the same command errors):

1. In Live's Preferences, set the Control Surface to `None`.
2. Set it back to `AbletonCliRemote`.
3. Run `ableton-cli --output json ping` and `ableton-cli --output json doctor` again.
4. If nothing changed, restart Live and try again.

## Contributing

Local checks, quality gates, and merge criteria are in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Original work © the ableton-cli contributors.

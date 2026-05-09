# Rigol-DS1102E-Oscilloscope

If you get a reading with no numbers, use `rigol_ds1102e_protocol_command` with `key="auto_setup"` or send `:AUTO`.

MCP tools:

- `rigol_ds1102e_list_devices()`
- `rigol_ds1102e_identify(device="/dev/usbtmc0", delay=0.2, read_size=4096)`
- `rigol_ds1102e_query(scpi, device="/dev/usbtmc0", delay=0.2, read_size=4096)`
- `rigol_ds1102e_write(scpi, device="/dev/usbtmc0")`
- `rigol_ds1102e_list_protocol_commands()`
- `rigol_ds1102e_protocol_command(key, params=None, device="/dev/usbtmc0", delay=0.2, read_size=4096)`
- `rigol_ds1102e_snapshot_get(device="/dev/usbtmc0", delay=0.2, read_size=4096, channels=None)`
- `rigol_ds1102e_snapshot_cached(device="/dev/usbtmc0")`
- `rigol_ds1102e_snapshot_refresh(device="/dev/usbtmc0", delay=0.2, read_size=4096, channels=None)`
- `rigol_ds1102e_apply_profile(profile, device="/dev/usbtmc0", delay=0.2, read_size=4096, refresh_after=True)`

For `rigol_ds1102e_protocol_command`, `key` must match a supported command key from `rigol_ds1102e_list_protocol_commands()`. `params` is a JSON object whose fields match that key's `args`. Examples: `{"channel": 1}` for `channel_scale_get`, `{"channel": 1, "scale": 0.5}` for `channel_scale_set`.

Settings snapshots read identity, channel setup, timebase, trigger, acquire, waveform point mode, and trigger status directly from the scope. `rigol_ds1102e_snapshot_get` returns the cached snapshot when it is fresh and refreshes only when the cache is missing or stale. `rigol_ds1102e_snapshot_cached` never queries the scope. `rigol_ds1102e_snapshot_refresh` always queries the scope and stores a fresh snapshot.

`rigol_ds1102e_apply_profile` accepts a profile map with optional `channels`, `timebase`, `trigger`, `acquire`, `waveform`, and `session` sections, applies those settings, and refreshes the cache by default. Measurement protocol commands remain direct/live scope queries.

Example calls:

- `rigol_ds1102e_list_devices()`
- `rigol_ds1102e_identify(device="/dev/usbtmc0")`
- `rigol_ds1102e_protocol_command(key="channel_scale_get", params={"channel": 1})`
- `rigol_ds1102e_snapshot_get(device="/dev/usbtmc0", channels=[1])`
- `rigol_ds1102e_snapshot_cached(device="/dev/usbtmc0")`
- `rigol_ds1102e_snapshot_refresh(device="/dev/usbtmc0", channels=[1, 2])`
- `rigol_ds1102e_apply_profile(profile={"channels":{"1":{"display":true,"coupling":"DC","scale":0.5}},"timebase":{"scale":0.001},"trigger":{"mode":"EDGE","source":"CHAN1","level":0.0},"acquire":{"type":"NORM"}}, device="/dev/usbtmc0")`

To test the Rigol MCP through a real stdio client in the local environment, run `./bin/mcp dev rigol_ds1102e_mcp_server.py` from the repo root.

Example Inspector calls for `rigol_ds1102e_protocol_command`:

- `{"key":"trigger_status","device":"/dev/usbtmc0"}`
- `{"key":"channel_scale_get","params":{"channel":1},"device":"/dev/usbtmc0"}`

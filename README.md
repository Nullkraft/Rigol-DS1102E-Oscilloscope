# Rigol-DS1102E-Oscope-MCP

If you get a reading with no numbers, use `rigol_ds1102e_protocol_command` with `key="auto_setup"` or send `:AUTO`.

Canonical scope selection is done by probing every plausible `usbtmc` device with `*IDN?` and selecting the one that reports `RIGOL TECHNOLOGIES,DS1102E`. The server does not trust `/dev/usbtmc` numbering for identity, and MCP tools do not accept caller-supplied device paths.

MCP tools:

- `list_ports()`
- `rigol_ds1102e_identify()`
- `rigol_ds1102e_query(scpi)`
- `rigol_ds1102e_write(scpi)`
- `rigol_ds1102e_list_protocol_commands()`
- `rigol_ds1102e_protocol_command(key, params=None)`
- `rigol_ds1102e_get_scope_config(channels=None)`
- `rigol_ds1102e_apply_profile(profile)`
- `rigol_ds1102e_prepare_to_capture_spi_bus(channels=None, trigger_mode="EDGE", sweep="SINGLE", points_mode="RAW", run=False)`
- `rigol_ds1102e_data_capture(channels=None, freeze=True, points_mode="RAW", encoding="list")`
- `rigol_ds1102e_spi_sample_indexes(chan_1=None, chan_2=None, clock_source=None, data_source=None, freeze=True, points_mode="RAW", clock_low_ratio=0.3, clock_high_ratio=0.6)`
- `rigol_ds1102e_spi_decode(chan_1=None, chan_2=None, clock_source=None, data_source=None, freeze=True, points_mode="RAW", clock_low_ratio=0.3, clock_high_ratio=0.6, low_ratio=0.2, high_ratio=0.8, expected_writes=None, expected_addresses=None, window_scan=True, max_extra_edges=16, time_scale=None, time_scale_margin=1.5)`
- `rigol_ds1102e_scope_io(delay=None, read_size=None)`

For `rigol_ds1102e_protocol_command`, `key` must match a supported command key from `rigol_ds1102e_list_protocol_commands()`. `params` is a JSON object whose fields match that key's `args`. Examples: `{"channel": 1}` for `channel_scale_get`, `{"channel": 1, "scale": 0.5}` for `channel_scale_set`.

If no DS1102E can be identified at startup, the MCP server exits and tells the technician to try plugging in the USB cable to the scope.

`rigol_ds1102e_get_scope_config` reads identity, channel setup, timebase, trigger, acquire, waveform point mode, and trigger status directly from the scope. It always queries the scope for live normalized scope data.

`rigol_ds1102e_apply_profile` accepts a profile map with optional `channels`, `timebase`, `trigger`, `acquire`, `waveform`, and `session` sections and applies those settings. Use `rigol_ds1102e_get_scope_config` for an explicit post-write readback. Measurement protocol commands remain direct/live scope queries.

`rigol_ds1102e_data_capture` captures selected waveform channels with no one's-complement, normalization, thresholding, or edge detection. Use it for raw single-channel or combined CH1/CH2 capture. With `freeze=True`, the shared capture helper sends `:STOP` and verifies `:TRIGger:STATus?` reports `STOP` before waveform reads, resending `:STOP` if the scope reports `WAIT`. If the stopped scope returns the known short 600-byte waveform response, the helper rereads the channel a few times before returning data.

`rigol_ds1102e_spi_sample_indexes` captures the two waveform channels, one's-complements and normalizes both, then returns the clock-derived sample indexes used for SPI data analysis on channel 2. Rising clock edges use Schmitt-style hysteresis: one edge is recorded when the normalized clock crosses `clock_high_ratio` of its maximum, and the detector does not re-arm until the clock falls below `clock_low_ratio` of its maximum. This replaced the older slope-based detector because clean 32-edge captures could still be overcounted depending on scale and sample shape.

`rigol_ds1102e_spi_decode` uses the same internal capture helper as `rigol_ds1102e_data_capture`, then applies SPI-specific processing: one's-complement and normalize CH1/CH2 independently, detect rising-edge sample indexes from the clock channel with hysteresis, decode the data channel as MSB-first 32-bit words, and return decoded hex values plus the low-3-bit address map. For wider captures, set `expected_writes` and leave `window_scan=True`; the tool expects `expected_writes * 32` clock edges and can recover from up to `max_extra_edges` leading/trailing edges. Set `expected_addresses` to require a MAX2871 register-address pattern such as `[4, 1, 0]`; if `expected_writes` is omitted, it is inferred from the address list length. Bounded tuning inputs are `clock_low_ratio=0.05..0.45`, `clock_high_ratio=0.55..0.95`, `low_ratio=0.05..0.4`, `high_ratio=0.6..0.95`, `expected_writes=1..6`, `expected_addresses` values `0..5`, `max_extra_edges=0..16`, `time_scale=500e-9..20e-6` seconds/div, and `time_scale_margin=1.0..2.0`.

Example calls:

- `list_ports()`
- `rigol_ds1102e_identify()`
- `rigol_ds1102e_protocol_command(key="channel_scale_get", params={"channel": 1})`
- `rigol_ds1102e_get_scope_config(channels=[1])`
- `rigol_ds1102e_apply_profile(profile={"channels":{"1":{"display":true,"coupling":"DC","scale":0.5}},"timebase":{"scale":0.001},"trigger":{"mode":"EDGE","source":"CHAN1","level":0.0},"acquire":{"type":"NORM"}})`

To test the Rigol MCP through a real stdio client in the local environment, run `./bin/mcp dev rigol_ds1102e_mcp_server.py` from the repo root.

`list_ports()` now reports:

- `devices`: plausible USBTMC nodes
- `discovered_ds1102e_device`: the DS1102E found by `*IDN?`, or `null`
- `discovery_error`: the discovery error string when no DS1102E is found
- `patterns`: the device-glob patterns used during discovery

Example Inspector calls for `rigol_ds1102e_protocol_command`:

- `{"key":"trigger_status"}`
- `{"key":"channel_scale_get","params":{"channel":1}}`

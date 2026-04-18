# Rigol-DS1102E-Oscilloscope

If you get a reading with no numbers, use `rigol_ds1102e_protocol_command` with `key="auto_setup"` or send `:AUTO`.

MCP tools:

- `rigol_ds1102e_list_devices()`
- `rigol_ds1102e_identify(device="/dev/usbtmc0", delay=0.2, read_size=4096)`
- `rigol_ds1102e_query(scpi, device="/dev/usbtmc0", delay=0.2, read_size=4096)`
- `rigol_ds1102e_write(scpi, device="/dev/usbtmc0")`
- `rigol_ds1102e_list_protocol_commands()`
- `rigol_ds1102e_protocol_command(key, params=None, device="/dev/usbtmc0", delay=0.2, read_size=4096)`

For `rigol_ds1102e_protocol_command`, `key` must match a supported command key from `rigol_ds1102e_list_protocol_commands()`. `params` is a JSON object whose fields match that key's `args`. Examples: `{"channel": 1}` for `channel_scale_get`, `{"channel": 1, "scale": 0.5}` for `channel_scale_set`.

Example calls:

- `rigol_ds1102e_list_devices()`
- `rigol_ds1102e_identify(device="/dev/usbtmc0")`
- `rigol_ds1102e_protocol_command(key="channel_scale_get", params={"channel": 1})`

To test the Rigol MCP through a real stdio client in the local environment, run `./bin/mcp dev rigol_ds1102e_mcp_server.py` from the repo root.

Example Inspector calls for `rigol_ds1102e_protocol_command`:

- `{"key":"trigger_status","device":"/dev/usbtmc0"}`
- `{"key":"channel_scale_get","params":{"channel":1},"device":"/dev/usbtmc0"}`

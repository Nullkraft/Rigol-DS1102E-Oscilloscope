Understood. The persistent port is for the `saTech` technician console, not for the Rigol.

So the test routine has three coordinated parts:

1. `saTech` technician console
   - Open one persistent serial session and keep it open across the routine.
   - Close and reopen that session on `redo`.

2. Rigol MCP server
   - Use MCP tools for scope discovery, setup, verification, capture, and SPI decode.
   - No persistent technician-style session on my side; just MCP tool calls.

3. BK390A MCP server
   - Use MCP tools for meter discovery and measurement steps when the routine reaches that part.

Operationally, I’ll treat the workflow this way:

- Start persistent `saTech` session.
- Use `rigol_ds1102e.list_ports()` once for this session, then Rigol MCP tools.
- When we first use the BK meter in this session, call `bk390a.list_ports()` once, per your repo rule.
- On `redo`, close the held `saTech` session first, then restart the routine cleanly.

That matches your intent better than what I said earlier.

Redo 1 - 2026-06-03

- User terminated the persistent `saTech` technician-console session.
- Restarting the routine from a fresh SA session and MCP-driven scope flow.
- Reopened the persistent `saTech` technician-console session and confirmed:
  - `bytes_waiting_after_open_delay=2037`
  - `cleared startup buffer`
  - `serial quiet`
- Re-ran `rigol_ds1102e.list_ports()` and resolved `/dev/usbtmc0`.
- Applied Rigol setup with `rigol_ds1102e_apply_profile(...)` using:
  - CH1 display `true`, scale `2.0`
  - CH2 display `true`
  - timebase scale `5e-06`
  - trigger mode `EDGE`, source `CHAN1`, level `1.28`, sweep `SINGLE`
  - waveform points mode `RAW`
  - session stop `true`
- Immediate `rigol_ds1102e_get_scope_config(channels=[1,2])` readback did not match the requested setup:
  - CH1 scale read back as `1.280e+00`
  - timebase scale read back as `2.000e+00`
  - trigger level, sweep, points mode, and session `STOP` matched
- Retried scope setup without parallel MCP calls and with one setting change per `rigol_ds1102e_apply_profile(...)` request:
  - `session.stop=true`
  - `channels.1.display=true`
  - `channels.2.display=true`
  - `channels.1.scale=2.0`
  - `trigger.mode=EDGE`
  - `trigger.source=CHAN1`
  - `trigger.level=1.28`
  - `trigger.sweep=SINGLE`
  - `waveform.points_mode=RAW`
  - `timebase.scale=5e-06`
- Follow-up `rigol_ds1102e_get_scope_config(channels=[1,2])` matched the intended setup:
  - CH1 scale `2.000e+00`
  - trigger level `1.28e+00`
  - timebase scale `5.000e-06`
  - trigger sweep `SINGLE`
  - waveform points mode `RAW`
  - session trigger status `STOP`

Scope setup retry - 2026-06-03

- Re-applied the Rigol setup with serialized `rigol_ds1102e_apply_profile(...)` calls only, one setting per request, with no intermediate readback:
  - `session.stop=true`
  - `channels.1.display=true`
  - `channels.2.display=true`
  - `channels.1.scale=2.0`
  - `trigger.mode=EDGE`
  - `trigger.source=CHAN1`
  - `trigger.level=1.28`
  - `trigger.sweep=SINGLE`
  - `waveform.points_mode=RAW`
  - `timebase.scale=5e-06`
- Performed one verification pass at the end with `rigol_ds1102e_get_scope_config(channels=[1,2])`.
- End-of-sequence readback matched the intended setup:
  - CH1 scale `2.000e+00`
  - trigger level `1.28e+00`
  - timebase scale `5.000e-06`
  - trigger sweep `SINGLE`
  - waveform points mode `RAW`
  - session trigger status `STOP`

Scope setup tool delay tuning - 2026-06-03

- Added `scope_setup_for_spi_bus_analysis(...)` as a single MCP tool for the `saTech` SPI bus scope setup sequence.
- The tool groups the ten SCPI setup writes inside one MCP call while still sending one SCPI command at a time to the Rigol.
- Tested grouped setup with per-command delay values:
  - `0.0 s`: failed; CH1 scale and trigger level did not stick.
  - `0.01 s`: failed; CH1 scale and trigger level did not stick.
  - `0.05 s`: failed; CH1 scale and trigger level did not stick.
  - `0.10 s`: partially passed; CH1 scale stuck, trigger level did not.
  - `0.12 s`: passed.
  - `0.15 s`: passed.
- Chosen default delay: `0.15 s`, giving a small safety buffer over the observed `0.12 s` passing threshold.

Grouped Rigol setup MCP tool - 2026-06-03

- Final grouped setup tool name: `rigol_ds1102e_setup_for_spi_bus_analysis(...)`.
- The tool replaces ten visible `rigol_ds1102e_write(...)` MCP calls with one MCP call while still sending one SCPI command at a time to the Rigol.
- Default setup values:
  - `clock_channel=1`
  - `data_channel=2`
  - `delay=0.15`
  - `clock_vertical_scale=2.0`
  - `trigger_level=1.28`
  - `timebase_scale="5.0us"`
  - `verify=False`
- Implementation detail:
  - Uses the managed scope's persistent file descriptor via `self.scope.open()`.
  - Holds `self.scope._io_lock` around the full setup command series.
  - Uses direct `os.write(...)` inside the grouped setup sequence to avoid the ten-call MCP round-trip cost.
- Live verification with `rigol_ds1102e_setup_for_spi_bus_analysis(verify=True)` passed:
  - CH1 scale `2.000e+00`
  - trigger level `1.28e+00`
  - timebase scale `5.000e-06`
  - trigger sweep `SINGLE`
  - waveform points mode `RAW`
  - trigger status `STOP`

Full LO1/LO2 grouped-MCP routine test - 2026-06-03

- Opened persistent `saTech` technician-console session on `/dev/ttyUSB1` and confirmed:
  - `bytes_waiting_after_open_delay=2037`
  - `cleared startup buffer`
  - `serial quiet`
- Resolved Rigol DS1102E with `list_ports()`:
  - `/dev/usbtmc0`
- Set up the scope with `rigol_ds1102e_setup_for_spi_bus_analysis(verify=True)`.
- Scope setup verification passed:
  - CH1 scale `2.000e+00`
  - trigger level `1.28e+00`
  - timebase scale `5.000e-06`
  - trigger sweep `SINGLE`
  - waveform points mode `RAW`
  - trigger status `STOP`
- In the held SA session:
  - Sent `RFin 123.345`
  - Sent `fulltest plan 10.000`
- `fulltest plan 10.000` produced:
  - LO1 frequency `3630.000 MHz`, `M=4095`, `F=0`, `N=55`, `DIVA=1`
  - LO2 frequency `3935.000 MHz`, `M=2603`, `F=1617`, `N=59`, `DIVA=1`
- LO1 capture:
  - Armed the scope with `:RUN`
  - Scope status before program: `WAIT`
  - Sent `fulltest program lo1`
  - Scope status after program: `STOP`
  - Decoded dirty write: `R0 0x001B8000`
  - Expected dirty write from `max2871_expected.py 3762.000 3630.000`: `R0 0x001B8000`
  - Result: PASS
- LO2 capture:
  - Armed the scope with `:RUN`
  - Scope status before program: `WAIT`
  - Sent `fulltest program lo2`
  - Scope status after program: `STOP`
  - Decoded dirty writes: `R1 0x40015159`, `R0 0x001DB288`
  - Expected dirty writes from `max2871_expected.py 3953.655 3935.000`: `R1 0x40015159`, `R0 0x001DB288`
  - Result: PASS
- Closed the held `saTech` technician-console session after the routine completed.

BK390A reading and AD8307 dB conversion - 2026-06-03

- Added final measurement steps to append after LO1/LO2 SPI register verification:
  - Call `bk390a.list_ports()` once per Codex session before first BK390A use.
  - Use the returned `resolved_default_port`.
  - Call `bk390a_read(require_stable=True, max_frames=6, timeout_s=2.0)`.
  - Confirm the decoded measurement is DC voltage.
  - Convert AD8307 output voltage to input power with:
    - `Vout_mV = measurement.value * 1000`
    - `Pin_dBm = (Vout_mV / 25) - 84`
- Live BK390A discovery:
  - `resolved_default_port=/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller_D-if00-port0`
- Live BK390A reading:
  - Stable after `2` frames.
  - Raw frame `10616;00:`
  - Measurement `0.616 V DC`
- AD8307 conversion:
  - `Vout_mV = 616.0`
  - `Pin_dBm = (616.0 / 25) - 84 = -59.36 dBm`

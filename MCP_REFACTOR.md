Here is the guide I would use before touching code.

## Objective

Refactor this repo so the MCP server is centered on one explicit scope object, with thin tool handlers layered over it. The server should represent “a Rigol scope and its generic operations,” not saTech workflow.

## Current Problem

Right now the design is split awkwardly:

  - rigol_ds1102e.py:7 has a small RigolDS1102E wrapper.
  - rigol_ds1102e_mcp_server.py:35 owns real state through globals like _ACTIVE_DEVICE_FD, _ACTIVE_DEVICE, snapshot state, and retry logic.
  - Tool functions repeatedly call build_scope(...) instead of operating on a managed server-side object.

That gives you request-oriented code, but not a coherent object model.

## Target Shape

One managed object inside the server, something like RigolScopeServer or RigolScopeSession, responsible for:

  - device resolution
  - connection lifecycle
  - active file descriptor
  - retry/reconnect behavior
  - scope snapshot handling
  - raw SCPI read/write/query
  - waveform capture
  - generic SPI decode helpers

Then MCP tool functions become thin wrappers that call methods on that object.

### Storyboard

1. MCP tool request arrives.
2. Tool handler validates simple arguments.
3. Tool handler calls the shared scope object.
4. Shared scope object performs all stateful behavior:
  - resolve device
  - ensure connection
  - send/query SCPI
  - return updated scope state
  - recover from disconnects if needed
5. Tool handler returns JSON result.

That means the server owns scope behavior, while the tool layer only translates request/response.

### Refactor Plan

1. Define the central object.
  - Add a class in this repo whose job is “managed scope server state.”
  - Move globals into instance fields.
  - Keep it generic: no saTech, no MAX2871 workflow knowledge.

2. Move connection/state behavior into methods.
  - discover_ds1102e_device
  - IDN-based scope discovery
  - active fd management
  - close/reopen logic
  - snapshot read behavior

3. Move generic operations into methods.
  - raw write
  - raw query
  - protocol command render/dispatch
  - snapshot read/refresh
  - waveform capture
  - SPI decode from captured data (Generic decode is 8-bits at a time)

4. Make MCP tools thin.
  - Each @mcp.tool() should mostly:
  - normalize args
  - call one method
  - return its result
  - No tool should own connection policy or snapshot policy. (or scope settings)

5. Decide the device model explicitly.
  - Single active scope object for the server process.
  - Tool call can optionally override device path.
  - If override happens, the shared object updates its target device intentionally.
  - No fake “rebuild_scope” wrapper.

6. Keep project logic out.
  - No LO programming assumptions.
  - No saTech-specific expected register sequences.
  - SPI decode is okay if it stays generic (8-bit decodes only): channels, thresholds, expected addresses, expected writes.

7. Verify behavior parity.
  - list_ports
  - identify/query/write
  - snapshot tools
  - scope setup
  - data capture
  - SPI sample indexes
  - SPI decode
  - disconnect/reconnect path

### Non-Goals

For this branch, I would avoid:

  - changing tool schemas unless necessary
  - mixing in saTech orchestration
  - redesigning rigol_ds1102e_protocol.py
  - adding a client layer or changing transport

### Acceptance Criteria

#### The refactor is done when:

  - there are no module-level globals for active scope state or snapshot state
  - tool functions no longer construct ad hoc scope wrappers all over the file
  - one explicit object owns scope/session behavior
  - existing tools still work with the same external behavior
  - the server reads as “generic Rigol scope server,” not “hidden project adapter”

#### Implementation Order

[ ] 1. Extract the managed scope class.
[ ] 2. Move fd/snapshot/device logic into it.
[ ] 3. Convert low-level helpers to instance methods.
[ ] 4. Convert tool handlers one group at a time.
[ ] 5. Remove dead wrappers like build_scope/rebuild_scope if they become obsolete.
[ ] 6. Run live smoke tests against the scope.

If this matches what you want, the next useful step is to turn it into a file-local checklist and start with the smallest structural cut:
introducing the managed scope class without changing tool behavior yet.

----------------------
1. Finish the managed-object refactor on the remaining live tool paths.
2. Do one more dead-code pass after that structural move.
3. Then decide whether to split generic scope logic from higher-level analysis logic.

What I would do next is the first remaining live slice in rigol_ds1102e_mcp_server.py:

  - move rigol_ds1102e_scope_setup(...) onto ManagedScopeState
  - then move rigol_ds1102e_protocol_command(...)

  Reason: those are simpler than the capture/decode tools, and they still keep tool logic outside the managed object.

  After that, do the heavier slice:

  - rigol_ds1102e_data_capture(...)
  - rigol_ds1102e_spi_sample_indexes(...)
  - rigol_ds1102e_spi_decode(...)

At that point the file will tell the truth about ownership. Then a second dead-code review will be more meaningful, because more wrappers/helpers will likely become removable.

----------------
What’s next is no longer dead-code cleanup. The next question is whether you want to keep helper functions like query_protocol_value, write_protocol_value, query_waveform_bytes, require_stopped_scope, and capture_waveform_channels at module scope, or move those onto ManagedScopeState too so the file becomes structurally honest end to end.

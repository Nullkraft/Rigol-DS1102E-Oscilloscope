# MCP Refactor Notes

## Objective

Refactor this repo so the MCP server is centered on one explicit scope object, with thin tool handlers layered over it. The server should represent a Rigol scope and its generic operations, not a saTech workflow.

## Current Shape

The MCP server now has one managed scope session:

- `RigolDS1102E` owns persistent hardware I/O and the open device handle.
- `ManagedScopeState` owns server-side scope/session behavior.
- MCP tool functions validate and normalize caller input, then call `ManagedScopeState`.
- Scope discovery is IDN-based and does not trust caller-supplied device paths.
- Scope config readback uses `rigol_ds1102e_get_scope_config`.
- Profile application is write-only; readback is explicit through `get_scope_config`.

## Ownership Model

The server process owns one active scope object. Tool calls operate on that object rather than constructing ad hoc scope wrappers.

`RigolDS1102E` owns:

- device path
- open file descriptor
- raw write/query/query-bytes behavior
- reconnect/open/close behavior
- query delay and read size

`ManagedScopeState` owns:

- IDN-based discovery at startup and reconnect
- SCPI query/write helpers
- protocol command dispatch
- scope config readback
- profile application
- waveform capture
- SPI sample-index and decode helpers

MCP tool functions own:

- public tool signatures
- caller input validation
- argument normalization
- response handoff to `ManagedScopeState`

## Tool Surface

Current high-level MCP tools include:

- `list_ports`
- `rigol_ds1102e_identify`
- `rigol_ds1102e_query`
- `rigol_ds1102e_write`
- `rigol_ds1102e_list_protocol_commands`
- `rigol_ds1102e_protocol_command`
- `rigol_ds1102e_get_scope_config`
- `rigol_ds1102e_apply_profile`
- `rigol_ds1102e_prepare_to_capture_spi_bus`
- `rigol_ds1102e_data_capture`
- `rigol_ds1102e_spi_sample_indexes`
- `rigol_ds1102e_spi_decode`
- `rigol_ds1102e_scope_io`

## Completed Direction

- Removed `build_scope` / `rebuild_scope` style wrapper churn.
- Removed snapshot cache behavior.
- Renamed snapshot terminology to scope config / normalized scope data.
- Removed implicit post-apply readback.
- Moved caller-input validation to the MCP tool boundary.
- Moved managed helpers to operate directly on the shared scope object.
- Kept project-specific saTech orchestration out of the MCP server.

## Remaining Cleanup Candidates

- Decide whether `ManagedScopeState` should move to its own module.
- Decide whether `prepare_to_capture_spi_bus` should remain a public convenience tool or be replaced by profile-driven setup.
- Rename `require_stopped_scope` if the intended behavior remains "send stop and monitor".
- Continue pruning stale terminology as the public API stabilizes.

## Acceptance Criteria

The refactor is healthy when:

- there are no module-level globals for active scope hardware state
- one explicit object owns scope/session behavior
- MCP tools are thin validation and dispatch wrappers
- source terminology matches the current API
- the server reads as a generic Rigol scope server, not a hidden project adapter

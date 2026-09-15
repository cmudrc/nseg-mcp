# NSEG MCP

An MCP server for **NSEG-style segment-based aircraft mission analysis**.

Part of the shared-CPACS aircraft analysis pipeline at the [Design Research Collective](https://github.com/cmudrc):

- [`tigl-mcp`](https://github.com/cmudrc/tigl-mcp) — geometry / STEP CAD export
- [`su2-mcp`](https://github.com/cmudrc/su2-mcp) — Euler / RANS aerodynamics
- [`pycycle-mcp`](https://github.com/cmudrc/pycycle-mcp) — turbofan engine cycle analysis
- [`aviary-cpacs-mcp`](https://github.com/cmudrc/aviary-cpacs-mcp) — NASA Aviary trajectory-coupled mission optimization (sibling)
- **`nseg-mcp` (this repo)** — fast segment-based mission analysis (Breguet range, energy methods)
- [`aircraft-analysis`](https://github.com/cmudrc/aircraft-analysis) — pipeline orchestrator + documentation

## What this MCP does

Decomposes a flight into ordered segments — `taxi`, `takeoff`, `climb`, `cruise`, `descent`, `approach`, `landing`, `hold` — solves each via Breguet range and energy methods, and returns block fuel, range, time, and per-segment metrics.

This is the **lower-fidelity, faster** sibling to `aviary-cpacs-mcp`. NSEG is ideal for trade-study sweeps where you want point-performance numbers in milliseconds rather than running a full Dymos trajectory optimization.

Per Boeing's "one tool per MCP" guidance: NSEG and Aviary are deliberately split into separate MCP servers. The agent picks one per run based on the design question.

## Tools exposed

| Tool | Description |
|------|-------------|
| `create_mission` | Open a new analysis session |
| `close_mission` | Free session resources |
| `set_vehicle` | `weight_kg`, `wing_area_m2`, `cd0`, `k`, `tsfc_1_per_s`, `max_thrust_n` |
| `set_segments` | Ordered list of segment dicts (`taxi` takes an optional idle `fuel_flow_kg_s`; `hold` flies at the minimum-drag speed) |
| `configure_mission` | Convenience: `range_nmi`, `num_passengers`, `cruise_mach`, `cruise_altitude_ft` |
| `run_mission` | Execute the segment chain (now also reports a `thrust_closure` block — see below) |
| `get_results` | Block fuel, range, time, fuel fraction, per-segment summaries |
| `get_trajectory` | Per-segment summaries (NSEG does not produce continuous timeseries) |
| `check_constraints` | Pass/fail evaluation of `<=`, `>=`, `==` constraints on results |

## Quick start

```bash
pip install -e .

# Stdio transport (for FastMCP clients / Claude Desktop / etc.)
nseg-mcp --transport stdio

# HTTP transport
nseg-mcp --transport http --host 0.0.0.0 --port 8003
```

## Shared-CPACS integration

The CPACS adapter (`src/nseg_mcp/cpacs_adapter.py`) reads:

- `//vehicles/aircraft/model/reference/area`
- `//vehicles/aircraft/model/analysisResults/aero/coefficients/{CL,CD,CD0}`
- `//vehicles/engines/engine/analysis/mcpResults/{TSFC_1_per_s,Fn_N}`
- `//vehicles/aircraft/model/analyses/massBreakdown/designMasses/mTOM/mass`,
  used as the take-off weight only when the mission profile carries no
  `weight_kg`. The result records `weight_source` either way
  (`mission_profile:weight_kg` or `cpacs:analyses/massBreakdown/designMasses/mTOM/mass`).
  A file that states no mass and a caller that gives none is a structured
  `missing_input` error, never a default.

and writes results to `//vehicles/aircraft/model/analysisResults/mission` with `<backend>nseg</backend>`.

### Mission profiles

Without `rules` in the mission profile the adapter flies the simple
taxi-takeoff-climb-cruise-descent-approach-landing chain, with `range_m` as
the cruise distance. Cruise Mach, altitude and range have documented
defaults; the aircraft's own properties (weight, area, polar, TSFC, thrust)
never do.

With `"rules": "ATA"` it flies the ATA Standard Mission Rules profile
(`src/nseg_mcp/ata_mission.py`):

- Mission: take-off, climb at 250 kt to 10,000 ft, climb at the climb Mach
  to cruise altitude, cruise sized so the still-air range from climb-out to
  top of approach equals `range_m`, descent to 10,000 ft, descent at 250 kt
  to 1,500 ft, approach, landing. Trip fuel and flight time span take-off to
  landing.
- Reserves, flown from the landing weight: 5% of trip fuel, missed approach,
  climb to `diversion_altitude_ft` (required, no default), 200 nm cruise,
  descent to 1,500 ft, a 30-minute hold at 1,500 ft at the minimum-drag
  speed, approach and landing, and the 5-minute taxi-in.
- `taxi_fuel_flow_kg_s` (idle fuel flow) enables the 10-minute taxi-out and
  the taxi-in. Without it the taxi legs are omitted, named under `gaps`, and
  `block_fuel_complete` is `false`. Nothing is estimated in their place.
- `passengers` becomes `payload_kg` at the ATA 225 lb per passenger, labelled
  `payload_source`; a passenger count is never assumed.
- `climb_mach`, `descent_mach` and `climb_roc_m_s` are mission conditions
  with documented defaults (`min(cruise_mach, 0.6)`, `min(cruise_mach, 0.5)`,
  7.62 m/s).
- `rules_note` in every result lists the simplifications: no winds, cruise
  Mach in place of long-range cruise, no IAS/TAS conversion, constant-Mach
  legs, single-altitude cruise (no step cruise, RVSM levels not enforced),
  missed approach as a climb from the runway to 1,500 ft, standard day.

The results dict and the CPACS mission node carry the same fields:
`tripFuelKg`, `blockFuelKg`, `blockFuelComplete`, `fuelLoadKg`,
`stillAirRangeM`, the `reserves` breakdown, `gaps`, `rulesNote`, and a
`reserveSegments` table beside `segments`.

### Provenance

Every write appends an `<update>` under `header/updates` (created directly
after `cpacsVersion` when the header has none) with, in order,
`modification`, `creator` (`nseg-mcp <installed version>`), `timestamp`
(UTC, ISO 8601), `version` (a running count of update entries) and
`cpacsVersion` (copied from the header). Existing header children are never
removed or reordered. The other servers follow the same convention.

## Thrust closure (does the engine actually close the mission?)

The segment integrators assume thrust is always available, so on their own they
never tell you whether an engine is big enough. `run_mission` therefore also
reports a `thrust_closure` block evaluated at the binding sizing point — **top
of climb** — where the engine must deliver the cruise drag plus enough excess
thrust for a residual climb rate (~300 ft/min):

```
thrust_required_n = D_cruise + W * (ROC_residual / V)
thrust_margin_n   = max_thrust_n (Fn from pyCycle) - thrust_required_n
thrust_limited    = thrust_margin_n < 0
```

A negative margin means the mission does not close. This is the real signal the
agent's engine-resizing skill (`agent-mcp/skills/SKILL_ENGINE_RESIZE.md`) drives
on. The field is additive — existing callers and `success` semantics are
unchanged.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).

## Maintainers

Mayank Dixit ([@Kugel-Blitz-13](https://github.com/Kugel-Blitz-13)), Carnegie
Mellon University — mayankd@cmu.edu
Christopher McComb, Carnegie Mellon University — Design Research Collective

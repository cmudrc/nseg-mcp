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

Decomposes a flight into ordered segments — `taxi`, `takeoff`, `climb`, `cruise`, `descent`, `approach`, `landing` — solves each via Breguet range and energy methods, and returns block fuel, range, time, and per-segment metrics.

This is the **lower-fidelity, faster** sibling to `aviary-cpacs-mcp`. NSEG is ideal for trade-study sweeps where you want point-performance numbers in milliseconds rather than running a full Dymos trajectory optimization.

Per Boeing's "one tool per MCP" guidance: NSEG and Aviary are deliberately split into separate MCP servers. The agent picks one per run based on the design question.

## Tools exposed

| Tool | Description |
|------|-------------|
| `create_mission` | Open a new analysis session |
| `close_mission` | Free session resources |
| `set_vehicle` | `weight_kg`, `wing_area_m2`, `cd0`, `k`, `tsfc_1_per_s`, `max_thrust_n` |
| `set_segments` | Ordered list of segment dicts |
| `configure_mission` | Convenience: `range_nmi`, `num_passengers`, `cruise_mach`, `cruise_altitude_ft` |
| `run_mission` | Execute the segment chain |
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

and writes results to `//vehicles/aircraft/model/analysisResults/mission` with `<backend>nseg</backend>`.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).

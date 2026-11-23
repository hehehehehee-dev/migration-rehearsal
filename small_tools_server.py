"""MCP baseline exposing the same six rehearsal phases as separate tools."""
from mcp.server.fastmcp import FastMCP
from rehearsal import PHASES

mcp = FastMCP("Migration Rehearsal — Separate Tools")
context = None
next_phase = 0

def advance(index, value=None):
    global context, next_phase
    if index != next_phase:
        return {"error": f"Run {PHASES[next_phase].__name__} next."}
    context = PHASES[index](value if index == 0 else context)
    if index == len(PHASES) - 1:
        report, context = context, None
        next_phase = 0
        return report
    next_phase += 1
    return {"next": PHASES[next_phase].__name__,
            "issues_so_far": sorted(context["issues"])}

@mcp.tool()
def validate_request(plan_json: str) -> dict:
    """First phase. Validate the operator-approved JSON plan."""
    return advance(0, plan_json)

@mcp.tool()
def create_test_snapshot() -> dict:
    """Second phase. Open a disposable copy of the pinned database."""
    return advance(1)

@mcp.tool()
def capture_baseline() -> dict:
    """Third phase. Record the protected baseline before mutation."""
    return advance(2)

@mcp.tool()
def execute_migration() -> dict:
    """Fourth phase. Rehearse the plan on the disposable copy."""
    return advance(3)

@mcp.tool()
def verify_postconditions() -> dict:
    """Fifth phase. Independently verify the contract."""
    return advance(4)

@mcp.tool()
def build_evidence_report() -> dict:
    """Sixth phase. Return the final verdict and counterexamples."""
    return advance(5)

if __name__ == "__main__":
    mcp.run()

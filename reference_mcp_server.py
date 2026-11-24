"""Reference MCP wrapper for verifying the deterministic workflow."""
from mcp.server.fastmcp import FastMCP
from mcp_plan import MigrationPlan
from rehearsal import rehearse_locally

mcp = FastMCP("Migration Rehearsal")

@mcp.tool()
def rehearse_database_migration(plan: MigrationPlan) -> dict:
    """Rehearse an operator-approved price migration on disposable Chinook."""
    return rehearse_locally(plan.canonical_json())

if __name__ == "__main__":
    mcp.run()

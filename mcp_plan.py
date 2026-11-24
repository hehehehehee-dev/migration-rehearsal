"""Schema for MCP callers. The shared engine checks approval and operation policy."""
from pydantic import BaseModel, ConfigDict, Field

from rehearsal import canonical


class Backfill(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    expression: str = Field(description="Named price expression, for example rounded_cents.")
    scope: str | None = Field(description="Named track scope, for example all. Null requests clarification.")


class MigrationPlan(BaseModel):
    # Keep unknown fields visible so the engine can reject them in its evidence report.
    model_config = ConfigDict(extra="allow", strict=True)

    add_column: bool = Field(description="Whether to add the integer UnitPriceCents column.")
    backfills: list[Backfill] = Field(description="Ordered price backfills to run on the copy.")
    post_step: str = Field(description="Named final step. Use none for a price-only migration.")

    def canonical_json(self) -> str:
        return canonical(self.model_dump(mode="json"))

"""The model's draft of a research note: the structured output the `draft` node must produce (S11).

Claim text refers to figures by placeholder (`"Revenue grew {f3} to {f2}."`). A figure is either quoted
(a value as printed in one of the claim's citations) or computed (an operation on quoted figures). The
model names the operation and its inputs; code computes the result (spec R8).

Operations, for inputs (a, b, ...):
- difference(a, b) = b - a        (change from a to b, in their unit)
- percent_change(a, b) = (b - a) / |a| x 100
- ratio(a, b) = a / b
- sum(a, b, ...) = a + b + ...
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

FigureId = Annotated[str, Field(pattern=r"^f\d{1,3}$")]


class DraftCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accession: str
    page: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=1000)


class QuotedFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["quoted"] = "quoted"
    id: FigureId
    value: str = Field(min_length=1, max_length=50)  # exactly as printed in the excerpt
    unit: str | None = None  # e.g. "$ millions", "%", "employees"
    citation: int = Field(default=0, ge=0)  # index into the claim's citations


class ComputedFigure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["computed"] = "computed"
    id: FigureId
    op: Literal["difference", "percent_change", "ratio", "sum"]
    inputs: list[FigureId] = Field(min_length=2, max_length=10)


DraftFigure = Annotated[QuotedFigure | ComputedFigure, Field(discriminator="kind")]


class DraftClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    citations: list[DraftCitation] = Field(min_length=1, max_length=10)
    figures: list[DraftFigure] = Field(default=[], max_length=20)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "not_found"] = "answered"
    summary: str = Field(default="", max_length=2000)
    claims: list[DraftClaim] = Field(default=[], max_length=30)

"""The research note returned for a question (spec R5). Model output is parsed into these models (S11).

M3 adds the citation validator and computed figures; M5 fills in the run metadata.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accession: str
    page: int = Field(ge=1)  # page index from app.extract, the citation key
    excerpt: str = Field(min_length=1)
    form: str | None = None  # filled in by the validator from the filing (R5)
    filing_date: str | None = None


class Figure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str  # as printed, e.g. "130,497" or "$60.9 billion"; computed values formatted by code
    unit: str | None = None
    period: str | None = None  # source filing and its fiscal period, set by code (R9)
    computed: str | None = None  # e.g. "percent_change(f1, f2)" when code computed the value (R8)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    citations: list[Citation] = Field(min_length=1)
    figures: list[Figure] = []


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "not_found"]
    summary: str = ""
    claims: list[Claim] = []
    disclaimer: str = ""
    dropped: dict[str, int] = {}  # claims removed by the validator, by reason (R6)

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


class Figure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str  # as printed, e.g. "130,497" or "$60.9 billion"
    unit: str | None = None


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

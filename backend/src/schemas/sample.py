"""Input models for versioned POS ticket ingestion."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

TicketId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=60)]
SkuCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=30)]
StoreCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=20)]
SourceFile = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
BatchNo = Annotated[str, StringConstraints(strip_whitespace=True,
                                           pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{2,39}$")]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=240)]


class TicketLineIn(BaseModel):
    sku: SkuCode
    quantity: int = Field(ge=1, le=10000)
    unit_price: float | None = Field(default=None, gt=0)


class TicketIn(BaseModel):
    """One full-ticket statement. ``expected_version`` implements optimistic
    concurrency: a late, stale correction cannot overwrite a newer statement."""

    external_id: TicketId
    store_code: StoreCode | None = None
    order_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    lines: list[TicketLineIn] = Field(default_factory=list)
    expected_version: int | None = Field(default=None, ge=1)


class TicketBatchIn(BaseModel):
    batch_no: BatchNo
    source_file: SourceFile = "orders_correction.csv"
    note: Note = ""
    tickets: list[TicketIn] = Field(min_length=1, max_length=500)


class VoidTicketIn(BaseModel):
    external_id: TicketId
    expected_version: int | None = Field(default=None, ge=1)
    reason: Reason


class VoidBatchIn(BaseModel):
    batch_no: BatchNo
    source_file: SourceFile = "orders_revocation.csv"
    note: Note = ""
    tickets: list[VoidTicketIn] = Field(min_length=1, max_length=500)

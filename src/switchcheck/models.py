from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class InterfaceMode(StrEnum):
    ACCESS = "access"
    TAGGED = "tagged"
    OTHER = "other"


class Interface(BaseModel):
    name: str
    enabled: bool = True
    description: str = ""
    mode: InterfaceMode = InterfaceMode.OTHER
    untagged_vlan: int | None = None
    tagged_vlans: list[int] = Field(default_factory=list)


class CompareStatus(StrEnum):
    MATCH = "match"
    DIFFERENT = "different"
    ONLY_ARUBA = "only_aruba"
    ONLY_NETBOX = "only_netbox"


class FieldDifference(BaseModel):
    field: str
    aruba: str | int | bool | list[int] | None
    netbox: str | int | bool | list[int] | None


class InterfaceComparison(BaseModel):
    name: str
    status: CompareStatus
    aruba: Interface | None = None
    netbox: Interface | None = None
    differences: list[FieldDifference] = Field(default_factory=list)


class ComparisonSummary(BaseModel):
    total: int
    matches: int
    differences: int
    only_aruba: int
    only_netbox: int


class ComparisonResult(BaseModel):
    summary: ComparisonSummary
    interfaces: list[InterfaceComparison]


class CompareRequest(BaseModel):
    config: str = Field(min_length=1)
    netbox_url: HttpUrl
    token: str = Field(min_length=1)
    device: str = Field(min_length=1)
    verify_tls: bool = True

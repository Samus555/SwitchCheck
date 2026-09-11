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
    lag: str | None = None


class Vlan(BaseModel):
    vid: int
    name: str = ""
    description: str = ""


class ConfigurationData(BaseModel):
    interfaces: list[Interface] = Field(default_factory=list)
    vlans: list[Vlan] = Field(default_factory=list)
    rendered_config: str | None = None
    rendered_config_error: str | None = None


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


class VlanComparison(BaseModel):
    vid: int
    status: CompareStatus
    aruba: Vlan | None = None
    netbox: Vlan | None = None
    differences: list[FieldDifference] = Field(default_factory=list)


class ConfigDiffLine(BaseModel):
    status: str
    current_number: int | None = None
    current_text: str | None = None
    rendered_number: int | None = None
    rendered_text: str | None = None


class ConfigDiffSummary(BaseModel):
    unchanged: int
    changed: int
    current_only: int
    rendered_only: int


class ConfigComparison(BaseModel):
    available: bool = True
    message: str | None = None
    summary: ConfigDiffSummary
    lines: list[ConfigDiffLine]


class ComparisonSummary(BaseModel):
    total: int
    matches: int
    differences: int
    only_aruba: int
    only_netbox: int


class ComparisonResult(BaseModel):
    summary: ComparisonSummary
    interfaces: list[InterfaceComparison]
    vlan_summary: ComparisonSummary
    vlans: list[VlanComparison]
    config: ConfigComparison


class CompareRequest(BaseModel):
    config: str = Field(min_length=1)
    netbox_url: HttpUrl
    token: str = Field(min_length=1)
    device: str = Field(min_length=1)
    verify_tls: bool = True


class ImportResource(StrEnum):
    INTERFACE = "interface"
    VLAN = "vlan"


class NetBoxImportAction(BaseModel):
    resource: ImportResource
    create: bool = False
    fields: list[str] = Field(default_factory=list)
    interface: Interface | None = None
    vlan: Vlan | None = None


class NetBoxImportRequest(NetBoxImportAction):
    netbox_url: HttpUrl
    token: str = Field(min_length=1)
    device: str = Field(min_length=1)
    verify_tls: bool = True


class NetBoxBatchImportRequest(BaseModel):
    netbox_url: HttpUrl
    token: str = Field(min_length=1)
    device: str = Field(min_length=1)
    verify_tls: bool = True
    actions: list[NetBoxImportAction] = Field(min_length=1, max_length=100)


class NetBoxImportResult(BaseModel):
    success: bool
    message: str


class NetBoxBatchImportResult(BaseModel):
    applied: int
    failed: int
    results: list[NetBoxImportResult]

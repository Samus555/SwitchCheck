import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from switchcheck.aruba import parse_aruba_configuration
from switchcheck.comparison import compare_interfaces
from switchcheck.models import (
    BulkAuditResult,
    BulkCompareRequest,
    BulkComparisonResult,
    ChangePlanItem,
    CompareRequest,
    ComparisonResult,
    DeviceDiscoveryRequest,
    DeviceSummary,
    ImportResource,
    NetBoxBatchImportRequest,
    NetBoxBatchImportResult,
    NetBoxChangePlan,
    NetBoxChangePlanRequest,
    NetBoxImportAction,
    NetBoxImportRequest,
    NetBoxImportResult,
    SshConfigRequest,
    SshConfigResult,
)
from switchcheck.netbox import NetBoxClient, NetBoxError
from switchcheck.ssh import SshConfigError, fetch_configuration

PACKAGE_DIR = Path(__file__).parent

app = FastAPI(
    title="SwitchCheck",
    description="Compare an Aruba switch configuration with NetBox.",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/compare", response_model=ComparisonResult)
async def compare(payload: CompareRequest) -> ComparisonResult:
    aruba = parse_aruba_configuration(payload.config)
    if not aruba.interfaces:
        raise HTTPException(
            status_code=422,
            detail="No supported interface configuration was found in the Aruba config.",
        )

    try:
        async with NetBoxClient(
            str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
        ) as client:
            netbox = await client.get_configuration(
                payload.device, {vlan.vid for vlan in aruba.vlans}
            )
    except NetBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return compare_interfaces(
        aruba.interfaces,
        netbox.interfaces,
        aruba.vlans,
        netbox.vlans,
        payload.config,
        netbox.rendered_config,
        netbox.rendered_config_error,
    )


@app.post("/api/netbox/devices", response_model=list[DeviceSummary])
async def discover_devices(payload: DeviceDiscoveryRequest) -> list[DeviceSummary]:
    try:
        async with NetBoxClient(
            str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
        ) as client:
            return await client.list_devices(payload.query)
    except NetBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/config/ssh", response_model=SshConfigResult)
async def retrieve_config(payload: SshConfigRequest) -> SshConfigResult:
    try:
        return SshConfigResult(config=await fetch_configuration(payload))
    except SshConfigError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/compare-bulk", response_model=BulkComparisonResult)
async def compare_bulk(payload: BulkCompareRequest) -> BulkComparisonResult:
    async def audit(device: str, config: str) -> BulkAuditResult:
        aruba = parse_aruba_configuration(config)
        if not aruba.interfaces:
            return BulkAuditResult(
                device=device, success=False, error="No supported interfaces were found."
            )
        try:
            async with NetBoxClient(
                str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
            ) as client:
                netbox = await client.get_configuration(device, {vlan.vid for vlan in aruba.vlans})
            comparison = compare_interfaces(
                aruba.interfaces,
                netbox.interfaces,
                aruba.vlans,
                netbox.vlans,
                config,
                netbox.rendered_config,
                netbox.rendered_config_error,
            )
            return BulkAuditResult(device=device, success=True, comparison=comparison)
        except NetBoxError as exc:
            return BulkAuditResult(device=device, success=False, error=str(exc))

    results = await asyncio.gather(*(audit(item.device, item.config) for item in payload.audits))
    successful = sum(item.success for item in results)
    drift = sum(
        item.success
        and item.comparison is not None
        and (
            item.comparison.summary.differences
            + item.comparison.summary.only_aruba
            + item.comparison.summary.only_netbox
            > 0
        )
        for item in results
    )
    return BulkComparisonResult(
        total=len(results),
        successful=successful,
        failed=len(results) - successful,
        devices_with_drift=drift,
        results=results,
    )


@app.post("/api/netbox/change-plan", response_model=NetBoxChangePlan)
async def plan_netbox_changes(payload: NetBoxChangePlanRequest) -> NetBoxChangePlan:
    try:
        async with NetBoxClient(
            str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
        ) as client:
            current = await client.get_configuration(payload.device)
    except NetBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    plans: list[ChangePlanItem] = []
    for action in sorted(payload.actions, key=_import_priority):
        source = action.interface if action.resource is ImportResource.INTERFACE else action.vlan
        if source is None:
            raise HTTPException(status_code=422, detail="Change action is missing source data.")
        identifier = source.name if action.resource is ImportResource.INTERFACE else str(source.vid)
        existing_items = (
            current.interfaces if action.resource is ImportResource.INTERFACE else current.vlans
        )
        existing = next(
            (
                item
                for item in existing_items
                if (
                    getattr(item, "name", "").replace(" ", "").lower()
                    == identifier.replace(" ", "").lower()
                    if action.resource is ImportResource.INTERFACE
                    else getattr(item, "vid", None) == source.vid
                )
            ),
            None,
        )
        fields = list(source.model_fields) if action.create else action.fields
        changes = {
            field: {
                "before": getattr(existing, field, None) if existing else None,
                "after": getattr(source, field, None),
            }
            for field in fields
            if field not in {"name", "vid"} or action.create
        }
        plans.append(
            ChangePlanItem(
                resource=action.resource,
                identifier=identifier,
                operation="create" if action.create else "update",
                fields=fields,
                changes=changes,
            )
        )
    return NetBoxChangePlan(actions=plans)


@app.post("/api/netbox/import")
async def import_to_netbox(payload: NetBoxImportRequest) -> dict[str, str]:
    try:
        async with NetBoxClient(
            str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
        ) as client:
            message = await _execute_import(client, payload.device, payload)
    except NetBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"message": message}


@app.post("/api/netbox/import-batch", response_model=NetBoxBatchImportResult)
async def import_batch_to_netbox(payload: NetBoxBatchImportRequest) -> NetBoxBatchImportResult:
    results: list[NetBoxImportResult] = []
    actions = sorted(payload.actions, key=_import_priority)
    async with NetBoxClient(
        str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
    ) as client:
        try:
            await client.prepare_import(payload.device)
        except NetBoxError as exc:
            results = [NetBoxImportResult(success=False, message=str(exc)) for _action in actions]
        else:
            for priority in sorted({_import_priority(action) for action in actions}):
                group = [action for action in actions if _import_priority(action) == priority]
                results.extend(await client.import_many(payload.device, group))

    applied = sum(result.success for result in results)
    return NetBoxBatchImportResult(
        applied=applied,
        failed=len(results) - applied,
        results=results,
    )


async def _execute_import(client: NetBoxClient, device: str, action: NetBoxImportAction) -> str:
    if action.resource is ImportResource.INTERFACE:
        if action.interface is None:
            raise NetBoxError("Interface data is required.")
        return await client.import_interface(
            device,
            action.interface,
            action.fields,
            create=action.create,
        )
    if action.vlan is None:
        raise NetBoxError("VLAN data is required.")
    return await client.import_vlan(
        device,
        action.vlan,
        action.fields,
        create=action.create,
    )


def _import_priority(action: NetBoxImportAction) -> int:
    if action.create and action.resource is ImportResource.VLAN:
        return 0
    if action.create and action.interface is not None:
        name = action.interface.name.replace(" ", "").lower()
        if name.startswith(("lag", "trk")):
            return 1
        return 2
    if action.resource is ImportResource.VLAN:
        return 3
    if "mode" in action.fields:
        return 4
    if {"untagged_vlan", "tagged_vlans"} & set(action.fields):
        return 6
    return 5

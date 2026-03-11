import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from app.config import settings
from app.schemas.region import RegionOut, RegionUpdate
from app.services.region_service import (
    bulk_upsert_regions,
    delete_region,
    get_region_by_storecode,
    insert_or_upsert_region,
    list_regions,
    parse_uploaded_file,
)
from app.timezone_utils import now_ist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/regions", tags=["regions"])


@router.get("/", response_model=List[dict])
async def list_regions_handler(
    storeCode: Optional[str] = Query(None),
    name: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, list_regions, None, storeCode, name, limit, offset)
    return rows


@router.get("/{store_code}", response_model=RegionOut)
async def get_region(store_code: str):
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, get_region_by_storecode, None, store_code)
    if not row:
        raise HTTPException(status_code=404, detail="region not found")
    return row


@router.put("/{store_code}", response_model=RegionOut)
async def update_region(store_code: str, payload: RegionUpdate):
    loop = asyncio.get_running_loop()
    existing = await loop.run_in_executor(None, get_region_by_storecode, None, store_code)
    now = now_ist()

    new_row = {
        "storeCode": store_code,
        "title": payload.title if payload.title is not None else (existing.get("title") if existing else None),
        "name": payload.name if payload.name is not None else (existing.get("name") if existing else None),
        "region": payload.region if payload.region is not None else (existing.get("region") if existing else None),
        "createdAt": existing.get("createdAt") if existing else now,
        "modifiedAt": now,
    }

    await loop.run_in_executor(None, insert_or_upsert_region, new_row, None)
    return new_row


@router.patch("/{store_code}", response_model=RegionOut)
async def patch_region(store_code: str, payload: RegionUpdate):
    loop = asyncio.get_running_loop()
    existing = await loop.run_in_executor(None, get_region_by_storecode, None, store_code)
    if not existing:
        raise HTTPException(status_code=404, detail="region not found")

    now = now_ist()
    new_row = {
        "storeCode": store_code,
        "title": payload.title if payload.title is not None else existing.get("title"),
        "name": payload.name if payload.name is not None else existing.get("name"),
        "region": payload.region if payload.region is not None else existing.get("region"),
        "createdAt": existing.get("createdAt"),
        "modifiedAt": now,
    }
    await loop.run_in_executor(None, insert_or_upsert_region, new_row, None)
    return new_row


@router.delete("/{store_code}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_region_handler(store_code: str):
    loop = asyncio.get_running_loop()
    deleted = await loop.run_in_executor(None, delete_region, None, store_code)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="region not found")
    return None


@router.post("/upload", summary="Upload CSV or Excel to bulk upsert regions")
async def upload_regions_file(file: UploadFile = File(...)):
    file_bytes = await file.read()
    loop = asyncio.get_running_loop()

    try:
        rows = await loop.run_in_executor(None, parse_uploaded_file, file_bytes, file.filename)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    try:
        result = await loop.run_in_executor(None, bulk_upsert_regions, rows, None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bulk upsert failed: {e}")

    return {"filename": file.filename, "rows_processed": result.get("rows_processed", 0)}

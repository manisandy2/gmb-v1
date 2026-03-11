from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RegionBase(BaseModel):
    storeCode: str = Field(..., description="Store code (unique key for upserts)")
    title: Optional[str] = None
    name: Optional[str] = None
    region: Optional[str] = None


class RegionCreate(RegionBase):
    pass


class RegionUpdate(BaseModel):
    title: Optional[str] = None
    name: Optional[str] = None
    region: Optional[str] = None


class RegionOut(RegionBase):
    createdAt: Optional[datetime] = None
    modifiedAt: Optional[datetime] = None

    class Config:
        from_attributes = True

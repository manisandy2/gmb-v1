import io
import logging
from typing import Dict, List, Optional

import pandas as pd

from app.config import settings
from app.connections import db
from app.timezone_utils import now_ist

logger = logging.getLogger(__name__)


def escape_sql_literal(s: str) -> str:
    if s is None:
        return ""
    return s.replace("'", "''")


def normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    return df


def insert_or_upsert_region(payload: Dict, namespace: str) -> None:
    sql = """
    INSERT INTO region 
    (storeCode, title, name, region, createdAt, modifiedAt)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        title = VALUES(title),
        name = VALUES(name),
        region = VALUES(region),
        modifiedAt = VALUES(modifiedAt)
    """
    now = now_ist()
    db.execute_update(sql, (
        payload.get("storeCode"),
        payload.get("title"),
        payload.get("name"),
        payload.get("region"),
        payload.get("createdAt", now),
        payload.get("modifiedAt", now)
    ))


def list_regions(namespace: str, storeCode: Optional[str], name: Optional[str], limit: int, offset: int) -> List[Dict]:
    where_clauses = []
    params = []
    
    if storeCode:
        where_clauses.append("storeCode = %s")
        params.append(storeCode)
    if name:
        where_clauses.append("name = %s")
        params.append(name)
    
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    
    sql = f"""
    SELECT storeCode, title, name, region, createdAt, modifiedAt
    FROM region
    {where_sql}
    ORDER BY modifiedAt DESC
    LIMIT %s OFFSET %s
    """
    
    params.extend([limit, offset])
    rows = db.execute_query(sql, tuple(params))
    
    return [
        {
            "storeCode": r["storeCode"],
            "title": r["title"],
            "name": r["name"],
            "region": r["region"],
            "createdAt": r["createdAt"],
            "modifiedAt": r["modifiedAt"],
        }
        for r in rows
    ]


def get_region_by_storecode(namespace: str, storeCode: str) -> Optional[Dict]:
    sql = """
    SELECT storeCode, title, name, region, createdAt, modifiedAt
    FROM region
    WHERE storeCode = %s LIMIT 1
    """
    rows = db.execute_query(sql, (storeCode,))
    if not rows:
        return None
    r = rows[0]
    return {
        "storeCode": r["storeCode"],
        "title": r["title"],
        "name": r["name"],
        "region": r["region"],
        "createdAt": r["createdAt"],
        "modifiedAt": r["modifiedAt"],
    }


def delete_region(namespace: str, storeCode: str) -> int:
    result = db.execute_query("SELECT COUNT(*) as cnt FROM region WHERE storeCode = %s", (storeCode,))
    cnt = result[0]["cnt"] if result else 0
    if cnt == 0:
        return 0
    db.execute_update("DELETE FROM region WHERE storeCode = %s", (storeCode,))
    return cnt


def parse_uploaded_file(file_bytes: bytes, filename: str) -> List[Dict[str, Optional[str]]]:
    filename_lower = filename.lower()
    try:
        if filename_lower.endswith((".xls", ".xlsx")):
            df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
        else:
            df = pd.read_csv(io.BytesIO(file_bytes), dtype=str)
    except Exception as e:
        raise ValueError(f"Failed to parse uploaded file: {e}")

    df = normalize_column_names(df)

    col_map = {}
    for c in df.columns:
        key = c.lower()
        if key == "storecode" and "storeCode" not in col_map:
            col_map["storeCode"] = c
        elif key == "title" and "title" not in col_map:
            col_map["title"] = c
        elif key == "name" and "name" not in col_map:
            col_map["name"] = c
        elif key == "region" and "region" not in col_map:
            col_map["region"] = c

    if "storeCode" not in col_map:
        raise ValueError("Uploaded file must contain a 'storeCode' column (case-insensitive).")

    rows: List[Dict[str, Optional[str]]] = []
    for _, r in df.iterrows():
        store_col = col_map["storeCode"]
        store_code = r.get(store_col)
        if pd.isna(store_code) or str(store_code).strip() == "":
            continue
        row = {
            "storeCode": str(store_code).strip(),
            "title": str(r.get(col_map["title"])).strip() if "title" in col_map and not pd.isna(r.get(col_map["title"])) else None,
            "name": str(r.get(col_map["name"])).strip() if "name" in col_map and not pd.isna(r.get(col_map["name"])) else None,
            "region": str(r.get(col_map["region"])).strip() if "region" in col_map and not pd.isna(r.get(col_map["region"])) else None,
        }
        rows.append(row)

    if not rows:
        raise ValueError("No valid rows with `storeCode` found in uploaded file.")
    return rows


def bulk_upsert_regions(records: List[Dict[str, Optional[str]]], namespace: str) -> Dict[str, int]:
    if not records:
        return {"rows_processed": 0}

    now = now_ist()
    sql = """
    INSERT INTO region 
    (storeCode, title, name, region, createdAt, modifiedAt)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        title = VALUES(title),
        name = VALUES(name),
        region = VALUES(region),
        modifiedAt = VALUES(modifiedAt)
    """
    
    processed = 0
    for r in records:
        try:
            db.execute_update(sql, (
                r.get("storeCode"),
                r.get("title"),
                r.get("name"),
                r.get("region"),
                r.get("createdAt", now),
                r.get("modifiedAt", now)
            ))
            processed += 1
        except Exception as e:
            logger.warning(f"Failed to upsert region {r.get('storeCode')}: {e}")

    return {"rows_processed": processed}

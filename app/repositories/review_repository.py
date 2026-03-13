from app.connections import db


TABLE_NAME = "location_reviews"


def fetch_reviews(query, params):

    return db.execute_query(query, params)


def bulk_upsert_reviews(columns, batch):

    db.execute_batch_upsert(
        TABLE_NAME,
        columns,
        batch,
        unique_key="reviewId",
    )
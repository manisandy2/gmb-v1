

def fetch_reviews(filters, max_reviews):

    query = """
    SELECT id, reviewId, name, comment, rating,
           createTime, reviewer_displayName, reviewReply, title
    FROM location_reviews
    WHERE (reviewReply IS NULL OR reviewReply = '')
    """

    params = []

    if filters["location_id"]:
        query += " AND name = %s"
        params.append(filters["location_id"])

    query += " LIMIT %s"
    params.append(max_reviews)

    return db.execute_query(query, tuple(params))

def bulk_upsert(batch):

    db.execute_batch_upsert(
        "location_reviews",
        columns,
        batch,
        unique_key="reviewId",
        update_columns=update_columns
    )
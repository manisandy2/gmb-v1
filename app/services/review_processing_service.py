

async def process_reviews(
    payload,
    dry_run,
    location_id,
    date_from,
    date_to,
    max_reviews,
    batch_size,
    process_delay,
    skip_gemini,
    skip_gmb_post
):

    filters = build_filters(payload, location_id, date_from, date_to)

    reviews = review_repository.fetch_reviews(filters, max_reviews)

    if not reviews:
        return build_empty_response(filters)

    batch = []
    processed = 0

    for row in reviews:

        review_data = prepare_review_data(row)

        analysis = analyze_review(
            review_data,
            skip_gemini
        )

        reply = generate_reply(review_data, analysis)

        post_status = await gmb_service.post_reply(
            review_data,
            reply,
            skip_gmb_post
        )

        batch.append(build_db_row(review_data, analysis, reply, post_status))

        processed += 1

        if len(batch) >= batch_size:
            review_repository.bulk_upsert(batch)
            batch.clear()

    if batch:
        review_repository.bulk_upsert(batch)

    return build_success_response(processed)
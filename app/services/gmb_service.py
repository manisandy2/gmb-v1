

async def post_reply(review_data, reply, skip):

    if skip:
        return None

    url = build_gmb_url(review_data)

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            url,
            headers=build_headers(),
            json={"comment": reply}
        )

    return resp.status_code
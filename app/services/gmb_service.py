import httpx

async def post_reply(account_id, location_id, review_id, token, reply):

    url = f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews/{review_id}/reply"

    async with httpx.AsyncClient(timeout=10) as client:

        resp = await client.put(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"comment": reply},
        )

    return resp.status_code
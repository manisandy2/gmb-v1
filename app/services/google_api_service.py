"""Service for interacting with Google APIs."""
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from app.auth import get_google_credentials
from app.config import settings

logger = logging.getLogger(__name__)

MAX_CONCURRENT_REQUESTS = 10
DEFAULT_TIMEOUT = 20.0
DELETE_TIMEOUT = 60.0


class GoogleAPIService:
    """Service for making requests to Google APIs."""

    @staticmethod
    async def _get_headers() -> Dict[str, str]:
        """Get authorization headers for Google API requests."""
        credentials = await get_google_credentials()
        return {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    async def bulk_update(
        operation_name: str,
        location_ids: List[str],
        make_body_and_mask: Callable,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> List[Dict[str, Any]]:
        """
        Execute bulk updates across multiple locations.

        Args:
            operation_name: Friendly name for the operation
            location_ids: List of location IDs to update
            make_body_and_mask: Callable that returns (body, mask_list) for a location
            timeout: Request timeout in seconds

        Returns:
            List of per-location result dictionaries
        """
        results: List[Dict[str, Any]] = []

        try:
            headers = await GoogleAPIService._get_headers()
        except Exception as exc:
            logger.exception("Failed to get Google credentials")
            for location_id in location_ids:
                results.append(
                    {
                        "location_id": location_id,
                        "status": "error",
                        "error": f"Google auth error: {str(exc)}",
                    }
                )
            return results

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async with httpx.AsyncClient(timeout=timeout) as client:

            async def update_single(location_id: str) -> Dict[str, Any]:
                async with semaphore:
                    loc_short = (
                        location_id.split("/")[-1]
                        if "/" in location_id
                        else location_id
                    )

                    try:
                        body, mask_list = make_body_and_mask(location_id)
                    except Exception as exc:
                        logger.exception(
                            "make_body_and_mask failed for %s", location_id
                        )
                        return {
                            "location_id": location_id,
                            "status": "error",
                            "error": f"make_body_and_mask error: {str(exc)}",
                            "request_payload": None,
                        }

                    update_mask = ",".join(mask_list) if mask_list else ""
                    resource_name = f"accounts/{settings.GOOGLE_ACCOUNT_ID}/locations/{loc_short}"
                    url = f"https://businessprofile.googleapis.com/v1/{resource_name}"

                    if update_mask:
                        url = f"{url}?updateMask={update_mask}"

                    try:
                        response = await client.patch(url, headers=headers, json=body)
                        response.raise_for_status()
                        response_body = response.json()

                        return {
                            "location_id": location_id,
                            "status": "success",
                            "request_payload": body,
                            "response_body": response_body,
                        }
                    except httpx.HTTPStatusError as exc:
                        try:
                            error_text = exc.response.text
                        except Exception:
                            error_text = "<could not read response.text>"

                        logger.error(
                            "HTTP error on bulk update %s for %s: %s",
                            operation_name,
                            location_id,
                            error_text,
                        )

                        return {
                            "location_id": location_id,
                            "status": "error",
                            "error": {
                                "status_code": exc.response.status_code,
                                "body": error_text,
                            },
                            "request_payload": body,
                        }
                    except Exception as exc:
                        logger.exception(
                            "Unexpected error on bulk update %s for %s",
                            operation_name,
                            location_id,
                        )
                        return {
                            "location_id": location_id,
                            "status": "error",
                            "error": str(exc),
                            "request_payload": body,
                        }

            tasks = [update_single(loc_id) for loc_id in location_ids]
            results = await asyncio.gather(*tasks)

        return results

    @staticmethod
    async def create_posts(
        location_ids: List[str], payload_builder: Callable, timeout: float = DEFAULT_TIMEOUT
    ) -> List[Dict[str, Any]]:
        """
        Create posts across multiple locations.

        Args:
            location_ids: List of location IDs
            payload_builder: Callable that builds payload for each location
            timeout: Request timeout in seconds

        Returns:
            List of per-location result dictionaries
        """
        results: List[Dict[str, Any]] = []

        try:
            headers = await GoogleAPIService._get_headers()
        except Exception as exc:
            logger.exception("Failed to get Google credentials")
            for location_id in location_ids:
                results.append(
                    {
                        "location_id": location_id,
                        "status": "error",
                        "error": f"Google auth error: {str(exc)}",
                    }
                )
            return results

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async with httpx.AsyncClient(timeout=timeout) as client:

            async def post_to_location(location_id: str) -> Dict[str, Any]:
                async with semaphore:
                    loc_short = (
                        location_id.split("/")[-1]
                        if "/" in location_id
                        else location_id
                    )
                    location_name = (
                        f"accounts/{settings.GOOGLE_ACCOUNT_ID}/locations/{loc_short}"
                    )

                    try:
                        payload = payload_builder(location_id)
                    except Exception as exc:
                        logger.exception(
                            "payload_builder failed for %s", location_id
                        )
                        return {
                            "location_id": location_id,
                            "status": "error",
                            "error": f"payload_builder error: {str(exc)}",
                        }

                    try:
                        response = await client.post(
                            f"https://mybusiness.googleapis.com/v4/{location_name}/localPosts",
                            headers=headers,
                            json=payload,
                        )
                        response.raise_for_status()
                        post_data = response.json()

                        post_name = post_data.get("name", "")
                        post_id_short = post_name.split("/")[-1] if "/" in post_name else post_name

                        return {
                            "location_id": location_id,
                            "status": "success",
                            "post_url": f"https://business.google.com/posts/l/{loc_short}",
                            "post_id": post_id_short,
                            "request_payload": payload,
                            "post_response": post_data,
                        }
                    except httpx.HTTPStatusError as exc:
                        try:
                            error_text = exc.response.text
                        except Exception:
                            error_text = "<could not read response.text>"

                        logger.error(
                            "HTTP error posting to GMB for %s: %s",
                            location_id,
                            error_text,
                        )

                        return {
                            "location_id": location_id,
                            "status": "error",
                            "error": {
                                "status_code": exc.response.status_code,
                                "body": error_text,
                            },
                            "request_payload": payload,
                        }
                    except Exception as exc:
                        logger.exception(
                            "Unexpected error posting to GMB for %s", location_id
                        )
                        return {
                            "location_id": location_id,
                            "status": "error",
                            "error": str(exc),
                            "request_payload": payload,
                        }

            tasks = [post_to_location(loc_id) for loc_id in location_ids]
            results = await asyncio.gather(*tasks)

        return results

    @staticmethod
    async def delete_posts(
        deletion_jobs: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """
        Delete posts from Google My Business.

        Args:
            deletion_jobs: List of dicts with 'location_id' and 'post_id'

        Returns:
            List of per-post deletion result dictionaries
        """
        results: List[Dict[str, Any]] = []

        try:
            headers = await GoogleAPIService._get_headers()
        except Exception as exc:
            logger.exception("Failed to get Google credentials for delete")
            for job in deletion_jobs:
                results.append(
                    {
                        "location_id": job.get("location_id"),
                        "post_id": job.get("post_id"),
                        "status": "error",
                        "error": f"Google auth error: {str(exc)}",
                    }
                )
            return results

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async with httpx.AsyncClient(timeout=DELETE_TIMEOUT) as client:

            async def delete_post(post_info: Dict[str, str]) -> Dict[str, Any]:
                async with semaphore:
                    location_id = post_info["location_id"]
                    post_id = post_info["post_id"]

                    loc_short = (
                        location_id.split("/")[-1]
                        if "/" in location_id
                        else location_id
                    )

                    post_short = (
                        post_id.split("/")[-1]
                        if "/" in post_id
                        else post_id
                    )

                    url = (
                        f"https://mybusiness.googleapis.com/v4/"
                        f"accounts/{settings.GOOGLE_ACCOUNT_ID}/"
                        f"locations/{loc_short}/localPosts/{post_short}"
                    )

                    try:
                        response = await client.delete(url, headers=headers)

                        if response.status_code in (200, 204):
                            logger.info(
                                "Successfully deleted post %s for location %s",
                                post_id,
                                location_id,
                            )
                            return {
                                "location_id": location_id,
                                "post_id": post_id,
                                "status": "deleted",
                                "post_status": "deleted",
                                "message": "Successfully deleted",
                            }
                        elif response.status_code == 404:
                            logger.warning(
                                "Post %s not found for location %s "
                                "(may be already deleted)",
                                post_id,
                                location_id,
                            )
                            return {
                                "location_id": location_id,
                                "post_id": post_id,
                                "status": "not_found",
                                "post_status": "deleted",
                                "message": "Post not found (may be already deleted)",
                            }
                        elif response.status_code == 401:
                            logger.error(
                                "Unauthorized (401) deleting post %s "
                                "for location %s",
                                post_id,
                                location_id,
                            )
                            return {
                                "location_id": location_id,
                                "post_id": post_id,
                                "status": "error",
                                "post_status": "active",
                                "message": (
                                    "Unauthorized - token may be expired "
                                    "or missing scopes"
                                ),
                                "status_code": 401,
                            }
                        else:
                            error_body = response.text[:500]
                            logger.error(
                                "Failed to delete post %s for location %s: %s",
                                post_id,
                                location_id,
                                error_body,
                            )
                            return {
                                "location_id": location_id,
                                "post_id": post_id,
                                "status": "error",
                                "post_status": "active",
                                "message": error_body,
                                "status_code": response.status_code,
                            }
                    except httpx.HTTPStatusError as exc:
                        error_body = (
                            exc.response.text[:500]
                            if hasattr(exc.response, "text")
                            else str(exc)
                        )
                        logger.exception(
                            "HTTP error deleting post %s for location %s",
                            post_id,
                            location_id,
                        )
                        return {
                            "location_id": location_id,
                            "post_id": post_id,
                            "status": "error",
                            "post_status": "active",
                            "message": error_body,
                        }
                    except Exception as exc:
                        logger.exception(
                            "Unexpected error deleting post %s for location %s",
                            post_id,
                            location_id,
                        )
                        return {
                            "location_id": location_id,
                            "post_id": post_id,
                            "status": "error",
                            "post_status": "active",
                            "message": str(exc),
                        }

            tasks = [delete_post(job) for job in deletion_jobs]
            results = await asyncio.gather(*tasks)

        return results

    @staticmethod
    async def list_locations() -> Dict[str, Any]:
        """
        List all locations for the configured account.

        Returns:
            Dictionary with 'locations' key containing list of location dicts
        """
        try:
            headers = await GoogleAPIService._get_headers()
        except Exception as exc:
            logger.exception("Failed to get Google credentials")
            raise

        url = f"https://businessinformation.googleapis.com/v1/accounts/{settings.GOOGLE_ACCOUNT_ID}/locations"

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()

                return {"locations": data.get("locations", [])}
            except httpx.HTTPStatusError as exc:
                try:
                    error_text = exc.response.text
                except Exception:
                    error_text = "<could not read response.text>"

                logger.error("Failed to list locations: %s", error_text)
                raise
            except Exception as exc:
                logger.exception("Unexpected error listing locations")
                raise

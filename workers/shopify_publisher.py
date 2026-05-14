"""Shopify Pages publisher for Hive Mind issues.

  upload_image_to_shopify(url, alt) — fileCreate via Admin GraphQL.
  create_page(...)                  — pageCreate (new page).
  update_page(page_id, ...)         — pageUpdate (in-place body/SEO update, preserves ID).

SEO is set via metafields (global.title_tag, global.description_tag) since
PageCreateInput / PageUpdateInput do NOT have a `seo` field in Admin API 2025-10.

Requires SHOPIFY_SHOP_DOMAIN and SHOPIFY_ACCESS_TOKEN in env.
Required app scopes: write_content, write_files.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from lib.shopify_admin import graphql


PUBLIC_HOST = "https://trybeezybeez.com"


def upload_image_to_shopify(source_url: str, alt: str = "",
                            poll_timeout_seconds: float = 90.0) -> dict[str, str]:
    if not source_url:
        raise ValueError("source_url is required")

    create_mutation = """
    mutation fileCreate($files: [FileCreateInput!]!) {
        fileCreate(files: $files) {
            files {
                id
                fileStatus
                alt
                ... on MediaImage {
                    image { url }
                }
            }
            userErrors { field message code }
        }
    }
    """
    variables = {
        "files": [{
            "originalSource": source_url,
            "contentType": "IMAGE",
            "alt": alt or "",
        }]
    }
    data = graphql(create_mutation, variables)
    result = data.get("fileCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"fileCreate userErrors: {user_errors}")
    files = result.get("files") or []
    if not files:
        raise RuntimeError(f"fileCreate returned no files: {result}")

    file_obj = files[0]
    file_id = file_obj["id"]
    image_url = _extract_image_url(file_obj)
    if image_url:
        return {"id": file_id, "url": image_url}

    deadline = time.time() + poll_timeout_seconds
    while time.time() < deadline:
        time.sleep(2.0)
        file_obj = _get_file(file_id)
        status = file_obj.get("fileStatus")
        image_url = _extract_image_url(file_obj)
        if image_url:
            return {"id": file_id, "url": image_url}
        if status == "FAILED":
            raise RuntimeError(f"File ingestion FAILED for {file_id}: {file_obj}")

    raise RuntimeError(f"File ingestion did not complete in {poll_timeout_seconds}s (file_id={file_id})")


def _get_file(file_id: str) -> dict[str, Any]:
    query = """
    query getFile($id: ID!) {
        node(id: $id) {
            ... on MediaImage {
                id
                fileStatus
                alt
                image { url }
            }
        }
    }
    """
    data = graphql(query, {"id": file_id})
    return data.get("node") or {}


def _extract_image_url(file_obj: dict[str, Any]) -> Optional[str]:
    img = file_obj.get("image") or {}
    return img.get("url") if isinstance(img, dict) else None


def _build_metafields(seo_title: Optional[str], seo_description: Optional[str],
                     image_file_id: Optional[str]) -> list[dict[str, str]]:
    mf: list[dict[str, str]] = []
    if seo_title:
        mf.append({"namespace": "global", "key": "title_tag",
                   "value": seo_title, "type": "single_line_text_field"})
    if seo_description:
        mf.append({"namespace": "global", "key": "description_tag",
                   "value": seo_description, "type": "multi_line_text_field"})
    if image_file_id:
        mf.append({"namespace": "global", "key": "image",
                   "value": image_file_id, "type": "file_reference"})
    return mf


def create_page(
    title: str,
    body_html: str,
    handle: str,
    *,
    seo_title: Optional[str] = None,
    seo_description: Optional[str] = None,
    is_published: bool = False,
    image_file_id: Optional[str] = None,
    template_suffix: Optional[str] = None,
) -> dict[str, Any]:
    mutation = """
    mutation pageCreate($page: PageCreateInput!) {
        pageCreate(page: $page) {
            page {
                id
                handle
                title
                isPublished
                publishedAt
                templateSuffix
            }
            userErrors { field message code }
        }
    }
    """
    page_input: dict[str, Any] = {
        "title": title,
        "body": body_html,
        "handle": handle,
        "isPublished": is_published,
    }
    if template_suffix is not None:
        page_input["templateSuffix"] = template_suffix
    metafields = _build_metafields(seo_title, seo_description, image_file_id)
    if metafields:
        page_input["metafields"] = metafields

    data = graphql(mutation, {"page": page_input})
    result = data.get("pageCreate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"pageCreate userErrors: {user_errors}")

    page = result.get("page") or {}
    page_handle = page.get("handle") or handle
    public_url = f"{PUBLIC_HOST}/pages/{page_handle}"

    return {
        "id": page.get("id"),
        "handle": page_handle,
        "title": page.get("title"),
        "url": public_url,
        "is_published": bool(page.get("isPublished")),
        "published_at": page.get("publishedAt"),
        "template_suffix": page.get("templateSuffix"),
    }


def update_page(
    page_id: str,
    title: str,
    body_html: str,
    *,
    seo_title: Optional[str] = None,
    seo_description: Optional[str] = None,
    image_file_id: Optional[str] = None,
    template_suffix: Optional[str] = None,
) -> dict[str, Any]:
    """Update an existing Shopify Page in-place. Preserves page ID, handle, URL.

    Does NOT toggle isPublished — set visibility separately if needed.
    """
    mutation = """
    mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
        pageUpdate(id: $id, page: $page) {
            page {
                id
                handle
                title
                isPublished
                publishedAt
                templateSuffix
            }
            userErrors { field message code }
        }
    }
    """
    page_input: dict[str, Any] = {
        "title": title,
        "body": body_html,
    }
    if template_suffix is not None:
        page_input["templateSuffix"] = template_suffix
    metafields = _build_metafields(seo_title, seo_description, image_file_id)
    if metafields:
        page_input["metafields"] = metafields

    data = graphql(mutation, {"id": page_id, "page": page_input})
    result = data.get("pageUpdate") or {}
    user_errors = result.get("userErrors") or []
    if user_errors:
        raise RuntimeError(f"pageUpdate userErrors: {user_errors}")

    page = result.get("page") or {}
    page_handle = page.get("handle")
    public_url = f"{PUBLIC_HOST}/pages/{page_handle}"

    return {
        "id": page.get("id"),
        "handle": page_handle,
        "title": page.get("title"),
        "url": public_url,
        "is_published": bool(page.get("isPublished")),
        "published_at": page.get("publishedAt"),
        "template_suffix": page.get("templateSuffix"),
    }

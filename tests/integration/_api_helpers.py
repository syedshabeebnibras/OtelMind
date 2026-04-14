"""Helpers for FastAPI integration tests.

This module deliberately does NOT enable `from __future__ import annotations`.
FastAPI's dependency analyzer cannot resolve string forward-refs for the
`Request` parameter when an override is defined inside a test fixture that
has PEP 563 annotations. We define the override factory here so the
annotation is a real class object.
"""

from fastapi import Request


def make_auth_override(tenant, fake_api_key):
    async def _override(request: Request):
        request.state.tenant = tenant
        request.state.tenant_id = tenant.id
        request.state.api_key = fake_api_key
        return tenant

    return _override

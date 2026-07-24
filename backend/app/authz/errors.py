"""Unified authorization error semantics.

- 403: The caller lacks the required operation permission (generic).
- 404: Entity does not exist OR caller lacks data-scope access (prevents info leak).
"""

from fastapi import HTTPException, status


def forbidden(detail: str = "Insufficient permissions") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def not_found(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

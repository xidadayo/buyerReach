"""Organization unit hierarchy utilities.

- Ancestor / descendant lookups via materialized path.
- Cycle detection and move validation.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def ancestor_ids(db: Session, org_unit_id: str, *, include_self: bool = True) -> list[str]:
    """Return all ancestor IDs for a unit, from root down.

    Uses the materialized `path` column for O(1) lookups.
    """
    from app.modules.models import OrganizationUnit

    unit = db.get(OrganizationUnit, UUID(str(org_unit_id)))
    if unit is None:
        return []
    path = unit.path or ""
    # path looks like "/root-uuid/sales-uuid/team-uuid/"
    segments = [s for s in path.strip("/").split("/") if s]
    result: list[str] = []
    for seg in segments:
        result.append(seg)
    if include_self:
        result.append(org_unit_id)
    # Deduplicate while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for uid in result:
        if uid not in seen:
            seen.add(uid)
            ordered.append(uid)
    return ordered


def descendant_ids(db: Session, org_unit_id: str, *, include_self: bool = True) -> list[str]:
    """Return all descendant IDs (children, grandchildren, etc.) for a unit.

    Uses the materialized `path` column.
    """
    from app.modules.models import OrganizationUnit

    unit = db.get(OrganizationUnit, UUID(str(org_unit_id)))
    if unit is None:
        return []
    prefix = f"{unit.path or '/'}{org_unit_id}/"
    rows = db.scalars(
        select(OrganizationUnit.id).where(
            OrganizationUnit.path.startswith(prefix),
            OrganizationUnit.status != "disabled",
        )
    ).all()
    result = [str(r) for r in rows]
    if include_self:
        result.insert(0, org_unit_id)
    return result


def is_descendant_of(db: Session, child_id: str, parent_id: str) -> bool:
    """Check if child_id is a descendant of parent_id in the org tree."""
    if child_id == parent_id:
        return True
    ancestors = ancestor_ids(db, child_id, include_self=False)
    return parent_id in ancestors


def validate_move(
    db: Session,
    unit_id: str,
    new_parent_id: str | None,
) -> tuple[bool, str | None]:
    """Validate that a unit can be moved to a new parent.

    Returns (valid, error_message).
    """
    from app.modules.models import OrganizationUnit

    unit = db.get(OrganizationUnit, UUID(str(unit_id)))
    if unit is None:
        return False, "组织单元不存在"

    if new_parent_id is None:
        return False, "不能移动到根节点以上"

    if new_parent_id == unit_id:
        return False, "不能将节点移动到自身"

    new_parent = db.get(OrganizationUnit, UUID(str(new_parent_id)))
    if new_parent is None:
        return False, "目标父节点不存在"

    # Cross-organization check
    if str(unit.organization_id) != str(new_parent.organization_id):
        return False, "不能跨组织移动"

    # Cycle check: cannot move to own descendant
    if is_descendant_of(db, new_parent_id, unit_id):
        return False, "不能移动到自身的下级节点"

    return True, None


def count_active_users_in_unit(db: Session, unit_id: str) -> int:
    """Count active users assigned to this unit."""
    from app.modules.models import User

    count_val = db.scalar(
        select(func.count(User.id)).where(
            User.organization_unit_id == UUID(str(unit_id)),
            User.status == "active",
            User.deleted_at.is_(None),
        )
    )
    return count_val or 0


def count_active_children(db: Session, unit_id: str) -> int:
    """Count active child units."""
    from app.modules.models import OrganizationUnit

    count_val = db.scalar(
        select(func.count(OrganizationUnit.id)).where(
            OrganizationUnit.parent_id == UUID(str(unit_id)),
            OrganizationUnit.status == "active",
        )
    )
    return count_val or 0


def count_business_data_in_unit(db: Session, unit_id: str) -> int:
    """Count business records (tasks + brands) owned by this unit."""
    from app.modules.models import SearchTask, Brand

    unit_uuid = UUID(str(unit_id))
    task_count = db.scalar(
        select(func.count(SearchTask.id)).where(SearchTask.department_id == unit_uuid)
    ) or 0
    brand_count = db.scalar(
        select(func.count(Brand.id)).where(Brand.department_id == unit_uuid)
    ) or 0
    return task_count + brand_count

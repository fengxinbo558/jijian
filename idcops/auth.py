"""Small local-role authorization boundary for the private MVP."""

from __future__ import annotations


VALID_ROLES = {"onsite_operator", "facility_lead", "interface_person", "ai_admin", "super_admin"}


def normalize_role(value: str) -> str:
    role = str(value or "").strip()
    return role if role in VALID_ROLES else "onsite_operator"


def is_ai_admin(role: str) -> bool:
    return normalize_role(role) in {"ai_admin", "super_admin"}


def is_super_admin(role: str) -> bool:
    return normalize_role(role) == "super_admin"


def can_import_work_order(role: str) -> bool:
    return normalize_role(role) in {"interface_person", "ai_admin", "super_admin"}


def can_decide_permission(role: str) -> bool:
    return normalize_role(role) in {"interface_person", "facility_lead", "ai_admin", "super_admin"}


def can_operate_onsite(role: str) -> bool:
    return normalize_role(role) in {"onsite_operator", "ai_admin", "super_admin"}


def can_review_operation(role: str) -> bool:
    return normalize_role(role) in {
        "onsite_operator",
        "facility_lead",
        "interface_person",
        "ai_admin",
        "super_admin",
    }


def can_view_governance(role: str) -> bool:
    return normalize_role(role) in VALID_ROLES


def can_manage_maintenance(role: str) -> bool:
    return normalize_role(role) in {"facility_lead", "interface_person", "ai_admin", "super_admin"}


def can_manage_incident_governance(role: str) -> bool:
    return normalize_role(role) in {"interface_person", "ai_admin", "super_admin"}


def can_manage_trust_data(role: str) -> bool:
    return normalize_role(role) in {"ai_admin", "super_admin"}


def can_manage_drills(role: str) -> bool:
    return normalize_role(role) in {"ai_admin", "super_admin"}

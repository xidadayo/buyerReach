"""Versioned permission catalog and compatibility mapping.

PERMISSION_CATALOG — canonical v1 operations per resource.
V1_COMPAT_MAP — converts legacy permission strings → v1 set for read-only compatibility.
"""

from typing import Any

PERMISSION_VERSION = 1

# ── Canonical v1 permission catalog ─────────────────────────────────────────
# Every non-public API endpoint MUST map to an entry in this catalog.

PERMISSION_CATALOG: dict[str, dict[str, Any]] = {
    "tasks": {
        "label": "搜索任务",
        "label_en": "Search Tasks",
        "operations": [
            "read", "create", "update", "start", "pause", "resume",
            "cancel", "retry", "delete", "export", "assign",
        ],
        "data_scoped": True,
    },
    "brands": {
        "label": "品牌",
        "label_en": "Brands",
        "operations": [
            "read", "create", "update", "review", "archive",
            "delete", "export", "promote", "assign",
        ],
        "data_scoped": True,
    },
    "contacts": {
        "label": "联系人",
        "label_en": "Contacts",
        "operations": [
            "read", "create", "update", "delete", "bulk_delete", "export", "assign",
        ],
        "data_scoped": True,
    },
    "emails": {
        "label": "邮箱",
        "label_en": "Emails",
        "operations": [
            "read", "create", "update", "verify", "bulk_verify",
            "delete", "export", "assign",
        ],
        "data_scoped": True,
    },
    "outreach": {
        "label": "邮件触达",
        "label_en": "Outreach",
        # Sending is intentionally absent from all legacy mappings and role defaults.
        "operations": ["read", "draft", "review", "schedule", "send", "cancel", "manage_accounts", "export"],
        "data_scoped": True,
    },
    "data_migrations": {
        "label": "数据迁移与合并",
        "label_en": "Data migration and merge",
        "operations": ["read", "preview", "execute", "resolve_conflicts", "rollback"],
        "data_scoped": False,
    },
    "imports": {
        "label": "导入",
        "label_en": "Imports",
        "operations": ["read", "preview", "execute", "retry", "cancel"],
        "data_scoped": True,
    },
    "dedup": {
        "label": "去重",
        "label_en": "Dedup",
        "operations": ["read", "execute", "merge"],
        "data_scoped": False,
    },
    "blacklist": {
        "label": "黑名单",
        "label_en": "Blacklist",
        "operations": ["read", "create", "update", "delete"],
        "data_scoped": False,
    },
    "tags": {
        "label": "标签",
        "label_en": "Tags",
        "operations": ["read", "create", "update", "delete", "assign"],
        "data_scoped": False,
    },
    "custom_fields": {
        "label": "自定义字段",
        "label_en": "Custom Fields",
        "operations": ["read", "create", "update", "delete", "assign"],
        "data_scoped": False,
    },
    "providers": {
        "label": "提供商配置",
        "label_en": "Providers",
        "operations": [
            "read", "create", "update", "enable", "disable",
            "test", "delete", "read_usage",
        ],
        "data_scoped": False,
    },
    "settings": {
        "label": "系统设置",
        "label_en": "Settings",
        "operations": ["read", "update"],
        "data_scoped": False,
    },
    "organizations": {
        "label": "组织",
        "label_en": "Organizations",
        "operations": ["read", "update"],
        "data_scoped": False,
    },
    "organization_units": {
        "label": "组织单元",
        "label_en": "Organization Units",
        "operations": [
            "read", "create", "update", "move", "enable", "disable",
            "delete", "assign_manager",
        ],
        "data_scoped": False,
    },
    "users": {
        "label": "用户",
        "label_en": "Users",
        "operations": [
            "read", "create", "update", "enable", "disable",
            "reset_password", "move_unit", "assign_role",
        ],
        "data_scoped": False,
    },
    "roles": {
        "label": "角色",
        "label_en": "Roles",
        "operations": ["read", "create", "update", "clone", "delete", "assign"],
        "data_scoped": False,
    },
    "audit": {
        "label": "审计",
        "label_en": "Audit",
        "operations": ["read", "export"],
        "data_scoped": False,
    },
    "exports": {
        "label": "导出",
        "label_en": "Exports",
        "operations": ["execute"],
        "data_scoped": False,
    },
}

# ── Data scope resources ────────────────────────────────────────────────────
DATA_SCOPED_RESOURCES = frozenset({
    k for k, v in PERMISSION_CATALOG.items() if v.get("data_scoped")
})

# ── Legacy → v1 compatibility mapping (read-only, for migration backfill) ───
V1_COMPAT_MAP: dict[str, list[str]] = {
    "admin:*": [],
    # Generic read
    "brands:read": ["brands:read"],
    "contacts:read": ["contacts:read"],
    "emails:read": ["emails:read"],
    "tasks:read": ["tasks:read"],
    "tags:read": ["tags:read"],
    "custom_fields:read": ["custom_fields:read"],
    "providers:read": ["providers:read"],
    "settings:read": ["settings:read"],
    "roles:read": ["roles:read"],
    "users:read": ["users:read"],
    "audit:read": ["audit:read"],
    "blacklist:read": ["blacklist:read"],
    # Legacy write → create + update + delete
    "brands:write": [
        "brands:create", "brands:update", "brands:review", "brands:archive",
        "brands:delete", "brands:promote",
    ],
    "contacts:write": [
        "contacts:create", "contacts:update", "contacts:delete", "contacts:bulk_delete",
    ],
    "emails:write": ["emails:create", "emails:update", "emails:delete"],
    "tasks:write": ["tasks:create", "tasks:update"],
    "tags:write": ["tags:create", "tags:update", "tags:delete"],
    "custom_fields:write": ["custom_fields:create", "custom_fields:update", "custom_fields:delete"],
    "providers:write": [
        "providers:create", "providers:update", "providers:enable", "providers:disable",
        "providers:test", "providers:delete",
    ],
    "settings:write": ["settings:update"],
    "roles:write": [
        "roles:create", "roles:update", "roles:clone", "roles:delete", "roles:assign",
    ],
    "users:write": [
        "users:create", "users:update", "users:enable", "users:disable",
        "users:reset_password", "users:move_unit", "users:assign_role",
    ],
    "blacklist:write": ["blacklist:create", "blacklist:update", "blacklist:delete"],
    # Legacy execute → decomposed
    "tasks:execute": ["tasks:start", "tasks:pause", "tasks:resume", "tasks:cancel", "tasks:retry"],
    "import:execute": ["imports:read", "imports:preview", "imports:execute"],
    "export:execute": [
        "exports:execute", "tasks:export", "brands:export", "contacts:export", "emails:export",
    ],
    "dedup:execute": ["dedup:read", "dedup:execute"],
    "emails:verify": ["emails:verify", "emails:bulk_verify"],
    # Legacy exports
    "brands:export": ["brands:export"],
    "contacts:export": ["contacts:export"],
    "emails:export": ["emails:export"],
}


def _expand_legacy(permissions: dict) -> set[str]:
    """Expand legacy permission keys into their v1 equivalents."""
    expanded: set[str] = set()
    for resource, actions in permissions.items():
        if isinstance(actions, list):
            for action in actions:
                key = f"{resource}:{action}"
                if key in V1_COMPAT_MAP:
                    expanded.update(V1_COMPAT_MAP[key])
                else:
                    expanded.add(key)
        elif isinstance(actions, str):
            key = f"{resource}:{actions}"
            if key in V1_COMPAT_MAP:
                expanded.update(V1_COMPAT_MAP[key])
            else:
                expanded.add(key)
    return expanded


def flatten_permissions(permissions: dict) -> set[str]:
    """Flatten a Role.permissions dict into a flat set of v1 permission strings."""
    return _expand_legacy(permissions)


def permission_display_name(perm_key: str) -> str:
    """Return a Chinese display label for a permission key."""
    parts = perm_key.split(":", 1)
    if len(parts) != 2:
        return perm_key
    resource, action = parts
    resource_info = PERMISSION_CATALOG.get(resource, {})
    resource_label = resource_info.get("label", resource) if isinstance(resource_info, dict) else resource
    action_labels: dict[str, str] = {
        "read": "查看", "create": "创建", "update": "编辑", "delete": "删除",
        "start": "启动", "pause": "暂停", "resume": "继续", "cancel": "取消",
        "retry": "重试", "export": "导出", "review": "审核", "archive": "归档",
        "promote": "提升", "bulk_delete": "批量删除", "verify": "验证",
        "bulk_verify": "批量验证", "preview": "预览", "execute": "执行",
        "merge": "合并", "assign": "分配", "enable": "启用", "disable": "停用",
        "test": "测试", "read_usage": "查看用量", "move": "移动",
        "assign_manager": "设置主管", "reset_password": "重置密码",
        "move_unit": "调岗", "assign_role": "分配角色", "clone": "克隆",
    }
    action_label = action_labels.get(action, action)
    return f"{resource_label} - {action_label}"

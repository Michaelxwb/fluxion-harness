from muad_api.context import current_tenant_id
from muad_common import SharedSettings


def get_tenant_id() -> str:
    return current_tenant_id() or SharedSettings().default_tenant_id

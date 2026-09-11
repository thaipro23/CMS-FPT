"""Install routing for both LMS and CMS without changing platform defaults."""
from ..database import ROUTER


def plugin_settings(settings):
    routers = list(getattr(settings, 'DATABASE_ROUTERS', []))
    settings.DATABASE_ROUTERS = [ROUTER] + [item for item in routers if item != ROUTER]
    settings.AI_CONNECTOR_READ_DB_ALIAS = getattr(settings, 'AI_CONNECTOR_READ_DB_ALIAS', 'read_replica')

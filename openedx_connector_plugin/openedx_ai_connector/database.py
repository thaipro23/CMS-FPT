"""Strict, request-local SQL routing for connector learning reports only."""

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
import os


class ConnectorReplicaError(RuntimeError):
    """A report cannot safely read its configured replica."""


_read_alias = ContextVar('connector_read_alias', default=None)
ROUTER = 'openedx_ai_connector.database.ConnectorDatabaseRouter'


def validate_replica_alias(databases, alias='read_replica'):
    """Reject absent aliases and obvious primary aliases; never choose default."""
    if not isinstance(alias, str) or not alias.strip() or alias == 'default' or alias not in databases:
        raise ConnectorReplicaError('AI_CONNECTOR_READ_DB_ALIAS must name a configured replica, not default.')
    primary = databases.get('default', {})
    replica = databases[alias]
    if 'sqlite3' in replica.get('ENGINE', ''):
        if not replica.get('NAME') or replica.get('NAME') == primary.get('NAME'):
            raise ConnectorReplicaError('The reporting database must be distinct from default.')
    else:
        def endpoint(config):
            return (str(config.get('HOST', '')).strip().lower(), str(config.get('PORT') or ''))
        if not endpoint(replica)[0] or endpoint(replica) == endpoint(primary):
            raise ConnectorReplicaError('The reporting database must have an explicit replica endpoint distinct from default.')
    return alias


@contextmanager
def database_scope(alias):
    token = _read_alias.set(alias)
    try:
        yield
    finally:
        _read_alias.reset(token)


class ConnectorDatabaseRouter:
    """Leave other requests and migrations to the existing Open edX routers."""

    def db_for_read(self, model, **hints):
        return _read_alias.get()

    def db_for_write(self, model, **hints):
        return 'default' if _read_alias.get() is not None else None

    def allow_relation(self, obj1, obj2, **hints):
        return None


def replica_reads(func):
    """Run a materialized reporting helper on its replica, or fail explicitly.

    The SQL guard also catches nested Open edX APIs that explicitly select a
    database and therefore bypass Django routers. Report helpers must be read
    only: SQL on any other connection is refused before execution. Normal
    enrollment/user/publish mutations never enter this scope.
    """
    @wraps(func)
    def wrapped(*args, **kwargs):
        from django.conf import settings
        from django.db import DatabaseError, connections, router

        alias = validate_replica_alias(
            settings.DATABASES,
            os.environ.get('AI_CONNECTOR_READ_DB_ALIAS', getattr(settings, 'AI_CONNECTOR_READ_DB_ALIAS', 'read_replica')),
        )
        if not router.routers or not isinstance(router.routers[0], ConnectorDatabaseRouter):
            raise ConnectorReplicaError('The connector database router must be first in DATABASE_ROUTERS.')
        failures = []

        def guard(execute, sql, params, many, context):
            if context['connection'].alias != alias:
                error = ConnectorReplicaError('A reporting dependency attempted SQL outside the configured replica.')
                failures.append(error)
                raise error
            try:
                return execute(sql, params, many, context)
            except DatabaseError as exc:
                error = ConnectorReplicaError('The configured reporting replica query failed.')
                failures.append(error)
                raise error from exc

        try:
            # Validate connectivity before best-effort helpers can swallow errors.
            connections[alias].ensure_connection()
            with database_scope(alias), ExitStack() as stack:
                for connection_alias in connections:
                    stack.enter_context(connections[connection_alias].execute_wrapper(guard))
                result = func(*args, **kwargs)
                if failures:
                    raise failures[0]
                return result
        except DatabaseError as exc:
            raise ConnectorReplicaError('The configured reporting replica is unavailable.') from exc

    return wrapped

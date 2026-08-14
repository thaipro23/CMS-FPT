#!/usr/bin/env python3
from pathlib import Path


def require(text: str, marker: str, message: str) -> None:
    if marker not in text:
        raise SystemExit(message)


repo = Path(__file__).resolve().parents[1]
plugin = (repo / "tutor-plugins/fpt_indigo_ui.py").read_text(encoding="utf-8")
openedx_common = (repo / "openedx/envs/common.py").read_text(encoding="utf-8")
lms_common = (repo / "lms/envs/common.py").read_text(encoding="utf-8")
cms_common = (repo / "cms/envs/common.py").read_text(encoding="utf-8")
lms_production = (repo / "lms/envs/production.py").read_text(encoding="utf-8")
cms_production = (repo / "cms/envs/production.py").read_text(encoding="utf-8")

for marker, message in (
    ('("MYSQL_REPLICA_HOST", "{{ MYSQL_HOST }}")', "MYSQL_REPLICA_HOST must safely fall back to MYSQL_HOST"),
    ('("MYSQL_REPLICA_PORT", "{{ MYSQL_PORT }}")', "MYSQL_REPLICA_PORT must safely fall back to MYSQL_PORT"),
    ('"openedx-common-settings"', "MySQL replica patch must be applied to shared LMS/CMS settings"),
    ('# FPT_MYSQL_READ_REPLICA_V1', "MySQL replica patch marker is missing"),
    ('_fpt_read_replica = DATABASES["default"].copy()', "read_replica must inherit the primary DB credentials/options"),
    ('_fpt_read_replica.pop("ATOMIC_REQUESTS", None)', "read_replica must remain non-atomic/read-only oriented"),
    ('_fpt_read_replica["HOST"] = "{{ MYSQL_REPLICA_HOST }}"', "read_replica host is not wired to MYSQL_REPLICA_HOST"),
    ('_fpt_read_replica["PORT"] = "{{ MYSQL_REPLICA_PORT }}"', "read_replica port is not wired to MYSQL_REPLICA_PORT"),
    ('DATABASES["read_replica"] = _fpt_read_replica', "read_replica alias assignment is missing"),
):
    require(plugin, marker, message)

require(openedx_common, "'read_replica': {", "Open edX base DATABASES no longer defines read_replica")
require(lms_common, "edx_django_utils.db.read_replica.ReadReplicaRouter", "LMS ReadReplicaRouter is missing")
if "edx_django_utils.db.read_replica.ReadReplicaRouter" in cms_common:
    raise SystemExit("CMS must not automatically register ReadReplicaRouter")

for name, production in (("LMS", lms_production), ("CMS", cms_production)):
    require(
        production,
        "if name != 'read_replica':",
        f"{name} production migration guard must continue skipping read_replica",
    )

print("[fpt-db-replica] Primary/read-replica Tutor/Open edX contract PASS")

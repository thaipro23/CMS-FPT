from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "tutor-plugins" / "fpt_haproxy_ingress.py"
SETUP = ROOT / "scripts" / "fpt-haproxy-ingress-setup.sh"


def require(text: str, token: str, source: Path) -> None:
    if token not in text:
        raise SystemExit(f"Missing required token {token!r} in {source}")


def main() -> None:
    plugin = PLUGIN.read_text(encoding="utf-8")
    setup = SETUP.read_text(encoding="utf-8")

    for token in (
        'FPT_HAPROXY_INGRESS_ENABLED',
        '"k8s-override"',
        'name: caddy',
        '$patch: delete',
        'name: mfe',
        'type: ClusterIP',
        '"k8s-services"',
        'kind: Ingress',
        'ingressClassName: "{{ FPT_HAPROXY_INGRESS_CLASS }}"',
        'cert-manager.io/cluster-issuer',
        'haproxy.org/ssl-redirect: "true"',
        'X-Forwarded-Proto https',
        'X-Forwarded-Port 443',
        'name: lms',
        'number: 8000',
        'name: cms',
        'name: mfe',
        'number: 8002',
        '{{ LMS_HOST }}',
        '{{ CMS_HOST }}',
        '{{ MFE_HOST }}',
    ):
        require(plugin, token, PLUGIN)

    for token in (
        'tutor plugins enable fpt_haproxy_ingress',
        '--set ENABLE_WEB_PROXY=false',
        '--set ENABLE_HTTPS=true',
        '--set FPT_HAPROXY_INGRESS_ENABLED=true',
        '--set FPT_HAPROXY_INGRESS_CLASS=',
        '--set FPT_HAPROXY_CLUSTER_ISSUER=',
        '--set FPT_HAPROXY_TLS_SECRET=',
    ):
        require(setup, token, SETUP)

    print("PASS HAProxy ingress Tutor contract")


if __name__ == "__main__":
    main()

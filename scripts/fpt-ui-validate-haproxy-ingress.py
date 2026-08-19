from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "tutor-plugins" / "fpt_haproxy_ingress.py"
SETUP = ROOT / "scripts" / "fpt-haproxy-ingress-setup.sh"


def require(text: str, token: str, source: Path) -> None:
    if token not in text:
        raise SystemExit(f"Missing required token {token!r} in {source}")


def path_is_owned_by_mfe(path: str, app_names: tuple[str, ...]) -> bool:
    """Model the exact-path/prefix exclusions rendered into the Caddy matcher."""
    if path.startswith("/api/mfe_config/v1"):
        return True
    if "authoring" in app_names and path.startswith("/course-authoring/"):
        return True
    return any(path == f"/{name}" or path.startswith(f"/{name}/") for name in app_names)


def main() -> None:
    plugin = PLUGIN.read_text(encoding="utf-8")
    setup = SETUP.read_text(encoding="utf-8")

    for token in (
        'FPT_HAPROXY_INGRESS_ENABLED',
        '"mfe-caddyfile"',
        '@fpt_unknown_mfe_path {',
        'not path /api/mfe_config/v1*',
        '{% if is_mfe_enabled("authoring") %} /course-authoring/*{% endif %}',
        '{% for app_name, app in iter_mfes() %}',
        '/{{ app_name }} /{{ app_name }}/*',
        'redir @fpt_unknown_mfe_path',
        '{{ LMS_HOST }} 302',
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
        '_make_haproxy_aware_wait_for_deployment_ready',
        'from tutor.commands import k8s as tutor_k8s',
        'name == "caddy" and config.get("FPT_HAPROXY_INGRESS_ENABLED")',
        'tutor_k8s.wait_for_deployment_ready = wait_for_deployment_ready',
    ):
        require(plugin, token, PLUGIN)

    if 'redir / ' in plugin:
        raise SystemExit("MFE fallback must cover all unknown paths, not only '/'")

    app_names = ("account", "authoring", "authn", "learner-dashboard", "learning", "profile")
    for path in (
        "/api/mfe_config/v1",
        "/api/mfe_config/v1?mfe=authn",
        "/course-authoring/course-v1:FPT+TEST+2026",
        "/authn",
        "/authn/login",
        "/learner-dashboard/",
        "/learning/course/course-v1:FPT+TEST+2026/home",
    ):
        if not path_is_owned_by_mfe(path.split("?", 1)[0], app_names):
            raise SystemExit(f"Valid MFE route would be redirected: {path}")

    for path in ("/", "/not-found", "/course-authoring", "/learner-dashboardx", "/missing/deep/path"):
        if path_is_owned_by_mfe(path, app_names):
            raise SystemExit(f"Unknown MFE route would not be redirected: {path}")

    without_authoring = tuple(name for name in app_names if name != "authoring")
    if path_is_owned_by_mfe("/course-authoring/course-v1:FPT+TEST+2026", without_authoring):
        raise SystemExit("Disabled authoring alias would incorrectly bypass the fallback")

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

    print("PASS HAProxy ingress Tutor contract, including all-path MFE fallback")


if __name__ == "__main__":
    main()

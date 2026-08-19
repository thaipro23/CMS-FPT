from __future__ import annotations

from tutor import hooks


# Kubernetes-native ingress for the FPT Open edX deployment.
#
# This plugin intentionally removes Tutor's edge Caddy Deployment/Service from
# the rendered Kubernetes resources and routes public traffic directly from
# HAProxy Ingress to the LMS/CMS/MFE ClusterIP services.
#
# HAProxy Ingress Controller, MetalLB and cert-manager are cluster-level
# infrastructure and are provisioned outside Tutor.
hooks.Filters.CONFIG_DEFAULTS.add_items([
    ("FPT_HAPROXY_INGRESS_ENABLED", False),
    ("FPT_HAPROXY_INGRESS_CLASS", "haproxy"),
    ("FPT_HAPROXY_CLUSTER_ISSUER", "letsencrypt-prod"),
    ("FPT_HAPROXY_TLS_SECRET", "openedx-web-tls"),
])


# HAProxy sends every path on MFE_HOST directly to the internal MFE Caddy.
# Tutor's public edge Caddy normally redirects the bare MFE host to the LMS,
# but that edge service is deliberately removed in this deployment. Preserve
# all configured MFE routes (plus the MFE config API and legacy authoring
# alias), and redirect every path not owned by an MFE to the LMS instead of
# allowing internal Caddy to return an empty HTTP 200 response.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-caddyfile",
    """
{% if FPT_HAPROXY_INGRESS_ENABLED %}
@fpt_unknown_mfe_path {
    not path /api/mfe_config/v1*{% if is_mfe_enabled("authoring") %} /course-authoring/*{% endif %}{% for app_name, app in iter_mfes() %} /{{ app_name }} /{{ app_name }}/*{% endfor %}
}
redir @fpt_unknown_mfe_path {% if ENABLE_HTTPS %}https://{% else %}http://{% endif %}{{ LMS_HOST }} 302
{% endif %}
""",
))


# Tutor-mfe 21.0.1 renders Service/mfe as NodePort. In the target architecture
# every application backend is private ClusterIP and only HAProxy Ingress owns
# the external LoadBalancer/VIP.
#
# The Caddy Deployment and Service are deleted with strategic-merge patches.
# ENABLE_WEB_PROXY=false (set by the setup helper) also prevents Tutor from
# rendering the Caddy PVC.
hooks.Filters.ENV_PATCHES.add_item((
    "k8s-override",
    """
{% if FPT_HAPROXY_INGRESS_ENABLED %}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: caddy
$patch: delete
---
apiVersion: v1
kind: Service
metadata:
  name: caddy
$patch: delete
{% if MFE_HOST is defined and MFE_HOST %}
---
apiVersion: v1
kind: Service
metadata:
  name: mfe
spec:
  type: ClusterIP
{% endif %}
{% endif %}
""",
))


# Public routing terminates TLS at HAProxy and forwards clear HTTP inside the
# cluster to the application services. ENABLE_HTTPS must remain true so Open
# edX generates canonical https:// URLs even though TLS is terminated before
# LMS/CMS/MFE.
hooks.Filters.ENV_PATCHES.add_item((
    "k8s-services",
    """
{% if FPT_HAPROXY_INGRESS_ENABLED %}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: openedx-web
  annotations:
    cert-manager.io/cluster-issuer: "{{ FPT_HAPROXY_CLUSTER_ISSUER }}"
    haproxy.org/ssl-redirect: "true"
    haproxy.org/ssl-redirect-code: "301"
    haproxy.org/request-set-header: |
      X-Forwarded-Proto https
      X-Forwarded-Port 443
spec:
  ingressClassName: "{{ FPT_HAPROXY_INGRESS_CLASS }}"
  tls:
    - secretName: "{{ FPT_HAPROXY_TLS_SECRET }}"
      hosts:
        - "{{ LMS_HOST }}"
        - "{{ CMS_HOST }}"
{% if MFE_HOST is defined and MFE_HOST %}
        - "{{ MFE_HOST }}"
{% endif %}
  rules:
    - host: "{{ LMS_HOST }}"
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: lms
                port:
                  number: 8000
    - host: "{{ CMS_HOST }}"
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: cms
                port:
                  number: 8000
{% if MFE_HOST is defined and MFE_HOST %}
    - host: "{{ MFE_HOST }}"
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: mfe
                port:
                  number: 8002
{% endif %}
{% endif %}
""",
))


def _make_haproxy_aware_wait_for_deployment_ready():
    """Do not make Tutor init/do wait for a Caddy deployment that we removed."""
    from tutor.commands import k8s as tutor_k8s

    original_wait = tutor_k8s.wait_for_deployment_ready
    if getattr(original_wait, "_fpt_haproxy_aware", False):
        return

    def wait_for_deployment_ready(config, name):
        if name == "caddy" and config.get("FPT_HAPROXY_INGRESS_ENABLED"):
            return
        return original_wait(config, name)

    wait_for_deployment_ready._fpt_haproxy_aware = True
    tutor_k8s.wait_for_deployment_ready = wait_for_deployment_ready


_make_haproxy_aware_wait_for_deployment_ready()

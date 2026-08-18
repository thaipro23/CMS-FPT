from __future__ import annotations

from tutor import hooks


# Kubernetes-native ingress for FPT Open edX.
#
# Public topology:
#
# Internet
#   -> HAProxy Ingress :80/:443
#
# HAProxy terminates TLS for LMS/CMS/MFE/Meilisearch.
#
# MinIO is different:
# s3.fpl.edu.vn uses TLS passthrough so the existing MinIO certificate
# terminates directly on the external MinIO server.
hooks.Filters.CONFIG_DEFAULTS.add_items([
    ("FPT_HAPROXY_INGRESS_ENABLED", False),
    ("FPT_HAPROXY_INGRESS_CLASS", "haproxy"),
    ("FPT_HAPROXY_CLUSTER_ISSUER", "letsencrypt-prod"),
    ("FPT_HAPROXY_TLS_SECRET", "openedx-web-tls"),

    # External MinIO S3 origin.
    ("FPT_MINIO_PUBLIC_HOST", "s3.fpl.edu.vn"),
    ("FPT_MINIO_ORIGIN_IP", "10.205.194.48"),
    ("FPT_MINIO_ORIGIN_PORT", 443),
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



# Remove Tutor edge Caddy.
# MFE remains internal ClusterIP behind HAProxy.
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


hooks.Filters.ENV_PATCHES.add_item((
    "k8s-services",
    """
{% if FPT_HAPROXY_INGRESS_ENABLED %}

{% if FPT_MINIO_ENABLED %}
# External MinIO origin.
#
# No selector is used because MinIO runs outside Kubernetes.
# EndpointSlice supplies the external origin address directly.
---
apiVersion: v1
kind: Service
metadata:
  name: minio-external
spec:
  type: ClusterIP
  ports:
    - name: https
      protocol: TCP
      port: {{ FPT_MINIO_ORIGIN_PORT }}
      targetPort: {{ FPT_MINIO_ORIGIN_PORT }}

---
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: minio-external
  labels:
    kubernetes.io/service-name: minio-external
addressType: IPv4
ports:
  - name: https
    protocol: TCP
    port: {{ FPT_MINIO_ORIGIN_PORT }}
endpoints:
  - addresses:
      - "{{ FPT_MINIO_ORIGIN_IP }}"
    conditions:
      ready: true
{% endif %}


# LMS / CMS / MFE / Meilisearch:
# TLS terminates directly on HAProxy using cert-manager certificate.
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
{% if RUN_MEILISEARCH and MEILISEARCH_HOST %}
        - "{{ MEILISEARCH_HOST }}"
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

{% if RUN_MEILISEARCH and MEILISEARCH_HOST %}
    - host: "{{ MEILISEARCH_HOST }}"
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: meilisearch
                port:
                  number: 7700
{% endif %}


{% if FPT_MINIO_ENABLED %}
# MinIO already owns a valid TLS certificate.
#
# Do NOT terminate/re-encrypt MinIO TLS at HAProxy.
# Preserve TLS end-to-end and route by SNI to the external MinIO origin.
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: minio-s3
  annotations:
    haproxy.org/ssl-passthrough: "true"
    haproxy.org/ssl-redirect: "true"
    haproxy.org/ssl-redirect-code: "301"
spec:
  ingressClassName: "{{ FPT_HAPROXY_INGRESS_CLASS }}"

  rules:
    - host: "{{ FPT_MINIO_PUBLIC_HOST }}"
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: minio-external
                port:
                  number: {{ FPT_MINIO_ORIGIN_PORT }}
{% endif %}

{% endif %}
""",
))


def _make_haproxy_aware_wait_for_deployment_ready():
    """Do not wait for Tutor edge Caddy when HAProxy ingress is enabled."""
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

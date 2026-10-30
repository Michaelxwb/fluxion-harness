# syntax=docker/dockerfile:1
# Custom nginx entry for muad: one NodePort serves the console UI and the
# gateway chat/SSE APIs by path.
#
# The intranet base is PlatOS/Kylin (glibc 2.28, no apt). Install nginx from the
# base's own RPM repo when available; otherwise build with NGINX_BUILD_MODE=copy.
ARG BASE_IMAGE=docker.sangfor.com/mss-base/platos-kylin:1.3.2
FROM ${BASE_IMAGE}

USER root

RUN set -eux; \
    if ! command -v nginx >/dev/null 2>&1; then \
      if [ -x /opt/sangfor/scripts/install_rpm.sh ]; then \
        /opt/sangfor/scripts/install_rpm.sh nginx; \
      elif command -v dnf >/dev/null 2>&1; then \
        dnf install -y nginx; \
        dnf clean all; \
      elif command -v yum >/dev/null 2>&1; then \
        yum install -y nginx; \
        yum clean all; \
      else \
        echo "nginx is not installed and no package manager is available" >&2; \
        exit 1; \
      fi; \
    fi; \
    if ! nginx -v >/dev/null 2>&1; then \
      echo "nginx is installed but its shared libraries are missing:" >&2; \
      ldd "$(command -v nginx)" | grep 'not found' >&2 || true; \
      rpm -qR nginx 2>/dev/null | grep -E '^lib' >&2 || true; \
      : "The Sangfor nginx RPM links against gperftools (libprofiler.so.0)." ; \
      : "Reuse a vendored copy when the base already ships one." ; \
      found="$(find /opt/sangfor /usr/local -name 'libprofiler.so.0*' 2>/dev/null | head -1 || true)"; \
      if [ -n "${found}" ]; then \
        ln -sf "${found}" /usr/lib64/libprofiler.so.0; \
      fi; \
      ldconfig || true; \
      : "Otherwise pull the runtime dependency from the same repo." ; \
      for pkg in gperftools-libs gperftools google-perftools libprofiler libunwind; do \
        if nginx -v >/dev/null 2>&1; then break; fi; \
        echo "trying ${pkg} for the missing nginx libraries" >&2; \
        if [ -x /opt/sangfor/scripts/install_rpm.sh ]; then \
          /opt/sangfor/scripts/install_rpm.sh "${pkg}" >/dev/null 2>&1 || true; \
        elif command -v dnf >/dev/null 2>&1; then \
          dnf install -y "${pkg}" >/dev/null 2>&1 || true; \
        elif command -v yum >/dev/null 2>&1; then \
          yum install -y "${pkg}" >/dev/null 2>&1 || true; \
        fi; \
      done; \
    fi; \
    nginx -v || { \
      echo "nginx still cannot start; rebuild with NGINX_BUILD_MODE=copy and a" >&2; \
      echo "glibc-compatible NGINX_SOURCE_IMAGE (for example the portal-nginx image)" >&2; \
      exit 1; \
    }

RUN set -eux; \
    if ! getent passwd nginx >/dev/null 2>&1; then \
      echo 'nginx:x:10001:10001::/nonexistent:/bin/false' >> /etc/passwd; \
    fi; \
    if ! getent group nginx >/dev/null 2>&1; then \
      echo 'nginx:x:10001:' >> /etc/group; \
    fi; \
    mkdir -p /var/log/nginx /var/cache/nginx /tmp/nginx/client_temp /tmp/nginx/proxy_temp /tmp/nginx/fastcgi_temp /tmp/nginx/uwsgi_temp /tmp/nginx/scgi_temp; \
    chown -R 10001:10001 /var/log/nginx /var/cache/nginx /tmp/nginx

COPY nginx.conf /etc/nginx/nginx.conf

RUN set -eux; \
    sed -e 's#server muad-console-platform:8000#server 127.0.0.1:8000#' \
        -e 's#server muad-im-gateway:8003#server 127.0.0.1:8003#' \
        /etc/nginx/nginx.conf > /tmp/nginx-test.conf; \
    nginx -t -c /tmp/nginx-test.conf; \
    rm -f /tmp/nginx-test.conf /tmp/nginx.pid /var/log/nginx/error.log /var/log/nginx/access.log; \
    chown -R 10001:10001 /var/log/nginx /var/cache/nginx /tmp/nginx

USER 10001:10001
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]

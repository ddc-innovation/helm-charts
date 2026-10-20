# Ranger candidate with the reviewed foreground launcher and vendor setup init.
ARG RANGER_BASE_IMAGE=rstevens198238/mdaca-policy-manager@sha256:777c25b7736c26d62dc322b88c85f2453067bc3fd5e4b0acb9514db8222920ad
FROM ${RANGER_BASE_IMAGE}
USER root
COPY --chmod=0755 ranger-init.py /opt/bdv-ranger/bin/ranger-init
RUN set -eu; \
    target=/opt/bdv-policy-manager/ews/webapp/WEB-INF/lib/mysql-connector-j-9.7.0.jar; \
    if test -e "$target" || test -L "$target"; then rm -f "$target"; fi; \
    test ! -e "$target" && test ! -L "$target"; \
    python3 -m py_compile /opt/bdv-ranger/bin/ranger-init \
 && test -x /opt/bdv-policy-manager/setup.sh \
 && test -f /opt/mysql-connector-j-9.7.0.jar
# Explicit configuration is required before selecting the foreground command.
ENTRYPOINT ["java"]
CMD ["-version"]

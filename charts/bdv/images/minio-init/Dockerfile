FROM rstevens198238/pme-minio@sha256:eacd3dc98e269e30b88ed10df48560b8b9e7484465912724bedaecf8d5c9936a
USER root
COPY --chmod=0755 minio-init.sh /usr/local/bin/bdv-minio-init
USER minio
ENTRYPOINT ["/usr/local/bin/bdv-minio-init"]

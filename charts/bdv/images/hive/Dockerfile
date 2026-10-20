FROM rstevens198238/mdaca-metastore@sha256:fa0af47ce86878d907906e397441cb587b49cce4bfdfd73f9995ca44dfd469e9

USER root

COPY --chmod=0644 mysql-connector-j-9.7.0.jar /opt/hive/lib/mysql-connector-j-9.7.0.jar
COPY --chmod=0755 bdv-hive /usr/local/bin/bdv-hive

RUN printf '%s  %s\n' \
      '0353648eaa1c91e0f4020c959abf756bc866ffd583df22ae6b6f6e0cbd43eb44' \
      '/opt/hive/lib/mysql-connector-j-9.7.0.jar' | sha256sum -c - \
 && test -x /usr/local/bin/bdv-hive \
 && test -f /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-3.1.0.jar \
 && test -f /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-1.11.271.jar \
 && command -v timeout >/dev/null \
 && ln -s /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-3.1.0.jar \
      /opt/hive/lib/hadoop-aws-3.1.0.jar \
 && ln -s /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-1.11.271.jar \
      /opt/hive/lib/aws-java-sdk-bundle-1.11.271.jar \
 && groupadd --gid 10001 bdv-hive \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp \
      --shell /usr/sbin/nologin bdv-hive

ENV HOME=/tmp \
    HIVE_CONF_DIR=/work/hive-conf \
    HADOOP_CLASSPATH=/opt/hadoop/share/hadoop/tools/lib/hadoop-aws-3.1.0.jar:/opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-1.11.271.jar \
    HIVE_LOG_DIR=/tmp/hive \
    HADOOP_LOG_DIR=/tmp/hadoop

USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/bdv-hive"]
CMD ["run"]

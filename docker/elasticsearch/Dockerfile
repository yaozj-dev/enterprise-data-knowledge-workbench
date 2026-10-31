FROM elasticsearch:8.19.10

USER root

ARG IK_PLUGIN_VERSION=8.19.10
ARG IK_PLUGIN_URL=https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.19.10.zip

RUN curl -fsSL "${IK_PLUGIN_URL}" -o "/tmp/elasticsearch-analysis-ik-${IK_PLUGIN_VERSION}.zip" && /usr/share/elasticsearch/bin/elasticsearch-plugin install --batch "file:///tmp/elasticsearch-analysis-ik-${IK_PLUGIN_VERSION}.zip" && rm -f "/tmp/elasticsearch-analysis-ik-${IK_PLUGIN_VERSION}.zip"

RUN chown -R elasticsearch:elasticsearch /usr/share/elasticsearch/plugins

USER elasticsearch

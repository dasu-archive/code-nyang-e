# code-nyang-e Jenkins controller.
# Builds run on this controller, so it carries the tools the pipeline needs:
#   JDK 21 (from the base image; rehab's Gradle toolchain is 21), git (base image),
#   Python 3 + jsonschema + PyYAML (report scripts), jq, Claude Code CLI.
# JENKINS_HOME (/var/jenkins_home) is a volume, so nothing is installed under it.

FROM jenkins/jenkins:2.580.3-lts-jdk21

USER root

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-jsonschema python3-yaml jq gnupg \
 && rm -rf /var/lib/apt/lists/*

# Claude Code CLI from Anthropic's signed apt repository (stable channel).
# The key fingerprint is the one published in the Claude Code setup docs.
# Set CLAUDE_CODE_VERSION to pin a package version; empty installs the current stable.
ARG CLAUDE_CODE_KEY_FPR=31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE
ARG CLAUDE_CODE_VERSION=
RUN install -d -m 0755 /etc/apt/keyrings \
 && curl -fsSL https://downloads.claude.ai/keys/claude-code.asc -o /etc/apt/keyrings/claude-code.asc \
 && fpr="$(gpg --show-keys --with-colons /etc/apt/keyrings/claude-code.asc | awk -F: '/^fpr:/ { print $10; exit }')" \
 && if [ "$fpr" != "$CLAUDE_CODE_KEY_FPR" ]; then echo "claude-code key fingerprint mismatch: $fpr" >&2; exit 1; fi \
 && echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
      > /etc/apt/sources.list.d/claude-code.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends "claude-code${CLAUDE_CODE_VERSION:+=$CLAUDE_CODE_VERSION}" \
 && rm -rf /var/lib/apt/lists/* /root/.gnupg

# The image is the only update channel: no self-updates, no non-essential traffic
# (telemetry, error reports, release notes). Auth comes from CLAUDE_CODE_OAUTH_TOKEN,
# bound from the Jenkins credential at run time. Never use --bare (it ignores that token).
ENV DISABLE_UPDATES=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

USER jenkins

# Plugins. The get.jenkins.io redirector sometimes picks a mirror that does not answer
# from this network, so downloads go to one fixed mirror. Metadata stays on updates.jenkins.io.
ARG JENKINS_UC_DOWNLOAD_URL=https://ftp.yz.yamagata-u.ac.jp/pub/misc/jenkins/plugins
COPY --chown=jenkins:jenkins plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --verbose -f /usr/share/jenkins/ref/plugins.txt

# Jobs. Copied into JENKINS_HOME on start only if the job does not exist yet.
COPY --chown=jenkins:jenkins jobs/ /usr/share/jenkins/ref/jobs/

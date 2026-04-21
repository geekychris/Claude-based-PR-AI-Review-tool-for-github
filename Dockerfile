# Stage 1: Build code_graph_search
FROM eclipse-temurin:21-jdk AS graph-builder
WORKDIR /build
# Clone and build code_graph_search
RUN apt-get update && apt-get install -y git maven && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/geekychris/code_graph_search.git .
RUN chmod +x build.sh && ./build.sh

# Stage 2: Runtime
FROM eclipse-temurin:21-jre-noble AS runtime

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    curl git tini \
    gnupg software-properties-common \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install gh CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Copy code_graph_search jar
COPY --from=graph-builder /build/app/target/code-graph-search.jar /opt/code-graph-search/code-graph-search.jar

# Install review-tool
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/

# Create venv and install
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir .

# Default config
COPY config.example.json /app/config.example.json

# Environment variables (override at runtime)
ENV GH_TOKEN=""
ENV ANTHROPIC_API_KEY=""

# code_graph_search jar location for config
ENV CGS_JAR_PATH="/opt/code-graph-search/code-graph-search.jar"

# Mount points:
#   /root/.claude  — Claude Code auth credentials (mount from host ~/.claude)
#   /config        — review_tool.json
#   /repos         — cached repo checkouts
VOLUME ["/root/.claude", "/config", "/repos"]

ENTRYPOINT ["tini", "--"]
CMD ["review-tool", "--help"]

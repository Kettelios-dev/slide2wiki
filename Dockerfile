FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pypdf==6.21.4 pymupdf==2.1 requests==2.34.2
# Claude Code (binaire natif) sorti du HOME de root : le conteneur peut tourner sous n'importe quel uid (PUID)
RUN curl -fsSL https://claude.ai/install.sh | bash \
 && cp -L /root/.local/bin/claude /usr/local/bin/claude && rm -rf /root/.local /root/.claude /root/.claude.json \
 && claude --version
# dépôt du wiki monté depuis l'hôte, d'un autre propriétaire que l'uid du conteneur
RUN git config --system --add safe.directory '*'
ENV HOME=/tmp COURS_ROOT=/data DOCLING_URL=http://docling:5001 DISABLE_AUTOUPDATER=1 PYTHONUNBUFFERED=1
COPY app/ /app/
COPY regles.md /app/regles.md
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh  # clone sous Windows : fins de ligne CRLF
WORKDIR /app
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]

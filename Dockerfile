# ChessMind OS backend for a Hugging Face Docker Space.
#
# The Space listens on 7860 (README.md frontmatter: app_port). Config is read
# from env; set DATABASE_URL, JWT_SECRET and CORS_ORIGINS as Space secrets /
# variables. At start, scripts/hf_start.sh migrates, seeds, then runs uvicorn.
FROM python:3.12-slim

# Debian's stockfish package installs the engine at /usr/games/stockfish.
RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish \
    && rm -rf /var/lib/apt/lists/* \
    && printf 'uci\nquit\n' | /usr/games/stockfish | grep -q '^uciok'

# Spaces run containers as uid 1000.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENVIRONMENT=production \
    STOCKFISH_PATH=/usr/games/stockfish \
    PORT=7860

WORKDIR /home/user/app

USER user
COPY --chown=user backend backend
COPY --chown=user scripts scripts
RUN pip install --user ./backend

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/health'%os.environ.get('PORT','7860'),timeout=4)"

CMD ["bash", "scripts/hf_start.sh"]

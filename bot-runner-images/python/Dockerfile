# Minimal Python bot-runner image.  Build with:
#   docker build -t pyowa/bot-runner-python:<VER> \
#     --build-arg PY_VERSION=<VER> bot-runner-images/python/
ARG PY_VERSION=3.14
FROM python:${PY_VERSION}-slim
COPY turn_server.py /turn_server.py
EXPOSE 8080
CMD ["python", "/turn_server.py"]

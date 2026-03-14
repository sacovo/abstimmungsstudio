FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    DEBUG=False

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
  && apt-get -y install --no-install-recommends \
  postgresql-client \
  python3-pip \
  netcat-traditional \
  gettext git


RUN --mount=type=cache,target=/root/.cache pip install --upgrade uv

RUN useradd -ms /sbin/nologin -c "App user" app

ENV HOME=/home/app
ENV APP_HOME=/home/app/web

RUN mkdir -p $APP_HOME $APP_HOME/static
RUN chown -R app:app $APP_HOME 

WORKDIR $APP_HOME
# change to the app user
USER app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev
ENV PATH="/home/app/web/.venv/bin:$PATH"

# copy project
COPY --chown=app:app . $APP_HOME

RUN python manage.py collectstatic

ENTRYPOINT ["/home/app/web/docker/entrypoint.sh"]

CMD ["gunicorn", "-w", "2", "core.wsgi:application", "--bind", "0.0.0.0:8000"]

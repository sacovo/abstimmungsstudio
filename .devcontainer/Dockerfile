FROM mcr.microsoft.com/devcontainers/python:3-3.12-trixie

ENV PYTHONUNBUFFERED 1

# [Optional] If your requirements rarely change, uncomment this section to add them to the image.
# COPY requirements.txt /tmp/pip-tmp/
# RUN pip3 --disable-pip-version-check --no-cache-dir install -r /tmp/pip-tmp/requirements.txt \
#    && rm -rf /tmp/pip-tmp

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
    r-base \
    r-base-dev \
    gfortran \
    libblas-dev \
    liblapack-dev \
    && rm -rf /var/lib/apt/lists/*

RUN R -e "install.packages('lphom', repos='https://cloud.r-project.org/')"




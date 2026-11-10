#!/usr/bin/env bash
# Виконує shell-команду в контейнері з мережею хоста ВМ Docker, де localhost:8080 — це
# опублікований порт kind (NodePort web). На самому Mac 127.0.0.1:8080 зайнятий іншим процесом.
exec docker run --rm -i --network host curlimages/curl:8.11.1 sh -c "$1"

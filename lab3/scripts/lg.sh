#!/usr/bin/env bash
# Генератор у контейнері з мережею хоста ВМ: шлях запиту — перенаправлення порту Docker → kube-proxy → под.
exec docker run --rm --network host -v "$PWD/results":/results telemed3-loadgen:lab3 "$@"

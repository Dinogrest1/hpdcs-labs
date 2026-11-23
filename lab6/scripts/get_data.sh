#!/usr/bin/env bash
# Завантаження BloodMNIST (MedMNIST v3.0, Zenodo) без пакета medmnist.
set -eu
cd "$(dirname "$0")/.."
mkdir -p data
URL="https://zenodo.org/records/10519652/files"
[ -s data/bloodmnist.npz ] || curl -L -o data/bloodmnist.npz "$URL/bloodmnist.npz?download=1"
ls -l data/bloodmnist.npz

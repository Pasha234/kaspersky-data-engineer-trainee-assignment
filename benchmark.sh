#!/bin/bash

set -euo pipefail

usage() {
    printf 'Usage: bash %s [--secrets-file PATH] [--host HOST] [--port PORT] [--image IMAGE]\n' "${0##*/}"
    printf 'Defaults: --host 127.0.0.1 --port 8000 --image vllm-cpu:latest\n'
    printf 'API key: VLLM_API_KEY from k8s/base/.env.secrets next to this script\n'
    printf 'Environment: INPUT_LEN=128 OUTPUT_LEN=32 CONCURRENCY=4 NUM_PROMPTS=20 NUM_WARMUPS=2 SEED=42\n'
}

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 2
}

# Do not forward previously exported credentials to the Docker client.
unset OPENAI_API_KEY VLLM_API_KEY
api_key=""
export -n api_key
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
secrets_file="$script_dir/k8s/base/.env.secrets"
host="127.0.0.1"
port="8000"
image="vllm-cpu:latest"
input_len="${INPUT_LEN:-128}"
output_len="${OUTPUT_LEN:-32}"
concurrency="${CONCURRENCY:-4}"
num_prompts="${NUM_PROMPTS:-20}"
num_warmups="${NUM_WARMUPS:-2}"
seed="${SEED:-42}"

while (($#)); do
    case "$1" in
        --secrets-file|--host|--port|--image)
            [[ $# -ge 2 && "$2" != --* && -n "$2" ]] || fail "Missing value for $1"
            case "$1" in
                --secrets-file) secrets_file="$2" ;;
                --host) host="$2" ;;
                --port) port="$2" ;;
                --image) image="$2" ;;
            esac
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) fail "Unknown argument; see --help" ;;
    esac
done

for name in input_len output_len concurrency num_prompts; do
    [[ "${!name}" =~ ^[1-9][0-9]*$ ]] || fail "$name must be a positive integer"
done
[[ "$num_warmups" =~ ^[0-9]+$ ]] || fail "NUM_WARMUPS must be a nonnegative integer"
[[ "$seed" =~ ^[0-9]+$ ]] || fail "SEED must be a nonnegative integer"
[[ "$port" =~ ^[0-9]{1,5}$ ]] || fail "Port must be an integer from 1 to 65535"
((10#$port >= 1 && 10#$port <= 65535)) || fail "Port must be an integer from 1 to 65535"
port="$((10#$port))"
[[ "$host" != *[[:space:]/\?\#@]* ]] || fail "Host must be a hostname or IP address, without a URL scheme or path"
if [[ "$host" == *:* && "$host" != \[*\] ]]; then
    host="[$host]"
fi

[[ -f "$secrets_file" && -r "$secrets_file" ]] || fail "Secrets file is missing or unreadable; see --secrets-file"
key_found=false
line=""
while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    if [[ "$line" == VLLM_API_KEY=* ]]; then
        [[ "$key_found" == false ]] || fail "Secrets file contains duplicate VLLM_API_KEY entries"
        api_key="${line#VLLM_API_KEY=}"
        key_found=true
    fi
done < "$secrets_file"
[[ -n "$api_key" ]] || fail "Secrets file must contain a nonempty VLLM_API_KEY=value entry"

for file in tokenizer.json tokenizer_config.json config.json; do
    [[ -r "$script_dir/tokenizer/$file" ]] || fail "Missing tokenizer/$file; see README.md"
done
mkdir -p "$script_dir/bench-results"
[[ -w "$script_dir/bench-results" ]] || fail "bench-results must be writable by the current user"
result_file="$(date -u +%Y%m%dT%H%M%S%NZ)-in${input_len}-out${output_len}-c${concurrency}-n${num_prompts}.json"

# Send only the API key over stdin. Never source the file, export the key on the
# host, pass it in command arguments, or include the HF token in the container.
printf '%s\n' "$api_key" | docker run \
    --rm \
    --interactive \
    --network host \
    --user "$(id -u):$(id -g)" \
    --entrypoint /opt/venv/bin/python \
    -e VLLM_LOGGING_CONFIG_PATH= \
    -e LD_PRELOAD= \
    -e HOME=/tmp \
    -e XDG_CACHE_HOME=/tmp/benchmark-cache \
    -e HF_HOME=/tmp/hf-cache \
    -e VLLM_CACHE_ROOT=/tmp/vllm-cache \
    -e VLLM_CONFIG_ROOT=/tmp/vllm-config \
    -v "$script_dir/bench-results:/results" \
    -v "$script_dir/tokenizer:/tokenizer:ro" \
    "$image" -c '
import os
import sys

key = sys.stdin.buffer.readline().rstrip(b"\r\n")
if not key or any(byte < 33 or byte > 126 for byte in key):
    raise SystemExit("API key must contain non-whitespace printable ASCII characters")
os.environ["OPENAI_API_KEY"] = key.decode("ascii")
os.execv("/opt/venv/bin/vllm", ["vllm", *sys.argv[1:]])
' bench serve \
        --backend openai-chat \
        --base-url "http://$host:$port" \
        --endpoint /v1/chat/completions \
        --model local-instruct \
        --tokenizer /tokenizer \
        --dataset-name random \
        --random-input-len "$input_len" \
        --random-output-len "$output_len" \
        --random-range-ratio 0 \
        --num-prompts "$num_prompts" \
        --num-warmups "$num_warmups" \
        --seed "$seed" \
        --request-rate inf \
        --max-concurrency "$concurrency" \
        --ignore-eos \
        --percentile-metrics ttft,tpot,e2el,client_queue_time \
        --metric-percentiles 50,95 \
        --save-result \
        --result-dir /results \
        --result-filename "$result_file"

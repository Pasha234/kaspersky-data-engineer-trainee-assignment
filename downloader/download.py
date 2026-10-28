import os
from pathlib import Path
from huggingface_hub import snapshot_download

token_path = Path(os.getenv("HF_TOKEN_PATH", "/run/secrets/hf_token"))
hf_token = token_path.read_text().strip() if token_path.is_file() else os.getenv("HF_TOKEN")
if not hf_token:
    hf_token = None

repo_id = os.environ["MODEL_ID"]
revision = os.environ["MODEL_REVISION"]
model_dir = Path("/models/current")
marker = model_dir / ".download-complete"

# Для этого примера фиксируем версию модели
# конкретным commit hash.
if marker.exists() and marker.read_text().strip() == revision:
    print("Model already downloaded")
    raise SystemExit(0)

model_dir.mkdir(parents=True, exist_ok=True)

# Удаляем устаревший маркер, если он существует.
marker.unlink(missing_ok=True)

print(f"Downloading {repo_id}@{revision}")

snapshot_download(
    repo_id=repo_id,
    revision=revision,
    local_dir=str(model_dir),
    token=hf_token,
)

# Маркер создаётся только после успешной загрузки.
temporary_marker = model_dir / ".download-complete.tmp"
temporary_marker.write_text(revision)
os.replace(temporary_marker, marker)

print("Model is ready")
#!/usr/bin/env python3
"""Upload the IQ4_NL GGUF to Hugging Face."""
import sys, os
sys.path.insert(0, os.path.expanduser("~/turboquant-work/llama-cpp-turboquant/gguf-py"))

from huggingface_hub import HfApi, create_repo

MODEL_FILE = os.path.expanduser("~/turboquant/quantzhai/var/models/qwen3.5b-24b-a10b-IQ4_NL.gguf")
README = os.path.join(os.path.dirname(__file__), "README.md")
REPO_ID = "h4rm0n1c/qwen3.5-24b-a10b-IQ4_NL-GGUF"

api = HfApi()

# Create the repo (no-op if exists)
try:
    create_repo(REPO_ID, repo_type="model", exist_ok=True, private=False)
    print(f"Repo {REPO_ID} ready")
except Exception as e:
    print(f"Repo error: {e}")
    sys.exit(1)

# Upload README
print("Uploading README.md...")
api.upload_file(
    path_or_fileobj=README,
    path_in_repo="README.md",
    repo_id=REPO_ID,
    repo_type="model",
)

# Upload model (large file, shows progress)
print("Uploading model (13.9 GB, may take a while)...")
api.upload_file(
    path_or_fileobj=MODEL_FILE,
    path_in_repo="qwen3.5b-24b-a10b-IQ4_NL.gguf",
    repo_id=REPO_ID,
    repo_type="model",
)

print(f"Done! https://huggingface.co/{REPO_ID}")

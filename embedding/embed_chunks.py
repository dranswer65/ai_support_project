import json
from openai import OpenAI
from pathlib import Path

client = OpenAI()

# Project root directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Correct path to chunks.json (inside rag/)
chunks_path = BASE_DIR / "rag" / "chunks.json"

with open(chunks_path, "r", encoding="utf-8") as f:
    chunks = json.load(f)

embedded_chunks = []

for i, chunk in enumerate(chunks):
    text = chunk["text"]
    metadata = chunk.get("metadata", {})

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )

    embedding = response.data[0].embedding

    embedded_chunks.append({
        "id": chunk.get("id", f"chunk_{i}"),
        "text": text,
        "embedding": embedding,
        "metadata": metadata
    })

    print(f"Embedded chunk {i + 1}/{len(chunks)}")

# Save embeddings.json inside embedding/
output_path = Path(__file__).resolve().parent / "embeddings.json"

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(embedded_chunks, f)

print("✅ All chunks embedded and saved to embedding/embeddings.json")


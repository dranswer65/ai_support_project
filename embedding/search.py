import json
import math
from openai import OpenAI
from pathlib import Path

client = OpenAI()

BASE_DIR = Path(__file__).resolve().parent

# Load stored embeddings
with open(BASE_DIR / "embeddings.json", "r", encoding="utf-8") as f:
    data = json.load(f)

def cosine_similarity(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x*x for x in a))
    norm_b = math.sqrt(sum(y*y for y in b))
    return dot / (norm_a * norm_b)

def search(query, top_k=3):
    query_embedding = client.embeddings.create(
        model="text-embedding-3-small",
        input=query
    ).data[0].embedding

    scored = []

    for item in data:
        score = cosine_similarity(query_embedding, item["embedding"])
        scored.append((score, item))

    scored.sort(reverse=True, key=lambda x: x[0])

    return scored[:top_k]

if __name__ == "__main__":
    question = input("Ask a question: ")

    results = search(question)

    print("\nTop relevant knowledge:\n")
    for score, item in results:
        print(f"Score: {score:.4f}")
        print(item["text"])
        print("-" * 40)

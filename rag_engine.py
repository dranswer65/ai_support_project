import json
import math
from pathlib import Path
from openai import OpenAI
from datetime import datetime


# ----------------------------
# Cost Settings
# ----------------------------

TOKEN_PRICE_PER_1K = 0.002


# ----------------------------
# OpenAI Client
# ----------------------------

client = OpenAI()


# ----------------------------
# Load Client Config
# ----------------------------

def load_client_config(client_name):

    base = Path(__file__).resolve().parent

    path = base / "clients" / client_name / "config" / "settings.json"

    if not path.exists():
        raise FileNotFoundError(f"No config for client: {client_name}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------
# Load Client API Key
# ----------------------------

def load_client_key(client_name):

    base = Path(__file__).resolve().parent

    path = base / "clients" / client_name / "config" / "api_key.json"

    if not path.exists():
        raise FileNotFoundError(f"No API key for client: {client_name}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data.get("api_key")


# ----------------------------
# Load Client Embeddings
# ----------------------------

def load_client_embeddings(client_name):

    base = Path(__file__).resolve().parent

    path = base / "clients" / client_name / "knowledge" / "embeddings.json"

    if not path.exists():
        raise FileNotFoundError(f"No data for client: {client_name}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------
# Usage Logger
# ----------------------------

def log_usage(client_name, tokens, cost):

    usage_file = Path("usage/usage_log.json")

    if not usage_file.exists():
        usage_file.parent.mkdir(exist_ok=True)
        usage_file.write_text("[]")

    with open(usage_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    record = {
        "client": client_name,
        "tokens": tokens,
        "cost": round(cost, 6),
        "date": datetime.now().strftime("%Y-%m-%d")
    }

    data.append(record)

    with open(usage_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ----------------------------
# Chat Logger
# ----------------------------

def log_chat(question, answer, tone):

    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    log_file = logs_dir / "chat_log.txt"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with open(log_file, "a", encoding="utf-8") as f:

        f.write(f"\n[{timestamp}]\n")
        f.write(f"Tone: {tone}\n")
        f.write(f"Q: {question}\n")
        f.write(f"A: {answer}\n")
        f.write("-" * 50 + "\n")


# ----------------------------
# Similarity Function
# ----------------------------

def cosine_similarity(a, b):

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    return dot / (norm_a * norm_b)


# ----------------------------
# Vector Search
# ----------------------------

def search_knowledge(query, client_data, top_k=3):

    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=query
    )

    query_vector = response.data[0].embedding

    scores = []

    for item in client_data:

        score = cosine_similarity(query_vector, item["embedding"])
        scores.append((score, item))

    scores.sort(reverse=True, key=lambda x: x[0])

    return scores[:top_k]


# ----------------------------
# AI Generator
# ----------------------------

def generate_answer(system_prompt, user_prompt):

    response = client.chat.completions.create(

        model="gpt-4o-mini",

        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],

        temperature=0.2
    )

    answer = response.choices[0].message.content
    tokens = response.usage.total_tokens

    return answer, tokens


# ----------------------------
# Quality Check
# ----------------------------

def validate_answer(answer):

    bad_phrases = [
        "i think",
        "maybe",
        "not sure",
        "probably",
        "guess"
    ]

    for phrase in bad_phrases:
        if phrase in answer.lower():
            return False

    return True


# ----------------------------
# Prompt Builder
# ----------------------------

def build_prompt(question, results, tone, client_config):

    if tone == "friendly":
        style = "Use a warm, friendly, and supportive tone."
    elif tone == "premium":
        style = "Use a luxury, VIP-style, highly respectful tone."
    else:
        style = "Use a formal, professional corporate tone."


    context = ""

    for score, item in results:

        context += f"- {item['text']}\n\n"


    system_prompt = f"""
You are a professional AI customer support assistant for an e-commerce company in the UAE.

Style:
{style}

Rules:
1. Use ONLY the information provided.
2. Do NOT guess.
3. Be polite and professional.

Company Policies:
{context}

Legal Notice:
{client_config.get("legal_notice", "")}
"""

    return system_prompt, question


# ----------------------------
# MAIN
# ----------------------------

def run():

    print("\n=== AI Support System Login ===\n")

    client_name = input("Enter client name: ").strip().lower()
    client_key = input("Enter API key: ").strip()


    # Load Files
    try:

        client_config = load_client_config(client_name)

        if not client_config.get("active", True):
           print("❌ This client account is suspended.")
           return

        client_data = load_client_embeddings(client_name)
        saved_key = load_client_key(client_name)

    except Exception as e:

        print(f"\n❌ {e}")
        return


    # Verify Key
    if client_key != saved_key:

        print("\n🚫 Invalid API Key. Access Denied.")
        return


    print("\n✅ Access Granted\n")


    # Tone
    tone = input("Select tone (formal / friendly / premium) [Enter=default]: ").strip().lower()

    if not tone:
        tone = client_config.get("default_tone", "formal")


    question = input("\nAsk customer question: ").strip()

    if not question:
        print("❌ Invalid question.")
        return


    # Search
    results = search_knowledge(question, client_data)


    # Threshold
    MIN_SCORE = client_config.get("escalation_threshold", 0.38)

    filtered = [
        (s, i) for s, i in results if s >= MIN_SCORE
    ]


    if not filtered:

        print("\n⚠️ Escalated to Human Support.\n")

        print("Your request will be reviewed by our support team.")
        return


    # Prompt
    system_prompt, user_prompt = build_prompt(question, filtered, tone, client_config)


    # AI
    answer, tokens = generate_answer(system_prompt, user_prompt)


    # Cost
    cost = (tokens / 1000) * TOKEN_PRICE_PER_1K

    log_usage(client_name, tokens, cost)


    # Quality
    if not validate_answer(answer):

        print("\n⚠️ Manual Review Required.\n")
        return


    # Output
    print("\n===== AI RESPONSE =====\n")
    print(answer)


    # Log
    log_chat(question, answer, tone)



# ----------------------------
# ENTRY
# ----------------------------

if __name__ == "__main__":

    run()

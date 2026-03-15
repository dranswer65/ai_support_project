import json

# Load prompts
with open("prompts/system_prompt.txt") as f:
    system_prompt = f.read()

with open("prompts/rag_prompt.txt") as f:
    rag_prompt = f.read()

# Load chunks
with open("rag/chunks.json") as f:
    chunks = json.load(f)

# -------------------------------
# STEP 6: Escalation Check (HERE)
# -------------------------------

needs_human = any(c["requires_human"] for c in chunks)

if needs_human:
    print("⚠️ Escalation required. Sending to human agent.")
    exit()

# -------------------------------
# Build context (after check)
# -------------------------------

context = "\n\n".join([c["text"] for c in chunks])

# Sample question
question = "When will you deliver my order?"

# Build final prompt
final_prompt = f"""
SYSTEM:
{system_prompt}

USER TASK:
{rag_prompt.replace("{{context}}", context).replace("{{question}}", question)}
"""

print("\n===== FINAL PROMPT =====\n")
print(final_prompt)

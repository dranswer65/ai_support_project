from openai import OpenAI

client = OpenAI()

response = client.embeddings.create(
    model="text-embedding-3-small",
    input="Hello world"
)

print(len(response.data[0].embedding))

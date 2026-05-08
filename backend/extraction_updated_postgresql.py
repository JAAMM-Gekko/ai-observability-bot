import os
import uuid
import asyncpg
import pandas as pd
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

DB_URL = "postgresql://chatbot:chatbot_password@localhost:5432/chatbot_db"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TENANT_ID = "704bd8d9-2791-4f6b-ba69-7f7cf065ba88"
EXCEL_PATH = "/home/ubuntu/ai-observability-bot/Cannabis FAQ_expanded_claude_rewritten.xlsx"
EMBEDDING_MODEL = "text-embedding-3-small"

client = OpenAI(api_key=OPENAI_API_KEY)

def load_faqs(excel_path):
    df = pd.read_excel(excel_path)
    df["Question"] = df["Question"].fillna("").astype(str).str.strip()
    df["Answer"] = df["Answer"].fillna("").astype(str).str.strip()
    faqs = []
    for i, row in df.iterrows():
        q, a = row["Question"], row["Answer"]
        if q and a and q.lower() != "nan" and a.lower() != "nan":
            faqs.append({"question": q, "answer": a})
        else:
            print(f"Skipping row {i+2}: missing question or answer")
    return faqs

def get_embedding(text):
    response = client.embeddings.create(input=text, model=EMBEDDING_MODEL)
    return response.data[0].embedding

async def upload_faqs(faqs):
    conn = await asyncpg.connect(DB_URL)

    document_id = uuid.uuid4()
    await conn.execute("""
        INSERT INTO knowledge_base_document
            (document_id, tenant_id, source_name)
        VALUES ($1, $2, $3)
    """, document_id, uuid.UUID(TENANT_ID), "FAQ Excel Upload")

    print(f"Created document record: {document_id}")

    for i, faq in enumerate(faqs):
        chunk_id = uuid.uuid4()
        embedding_id = uuid.uuid4()
        chunk_text = f"Question: {faq['question']}\nAnswer: {faq['answer']}"

        await conn.execute("""
            INSERT INTO knowledge_base_chunk
                (document_chunk_id, document_id, tenant_id, chunk_index, chunk_text)
            VALUES ($1, $2, $3, $4, $5)
        """, chunk_id, document_id, uuid.UUID(TENANT_ID), i, chunk_text)

        vector = get_embedding(chunk_text)
        await conn.execute("""
            INSERT INTO embedding
                (embedding_id, document_chunk_id, document_id, tenant_id, embedding)
            VALUES ($1, $2, $3, $4, $5)
        """, embedding_id, chunk_id, document_id, uuid.UUID(TENANT_ID), vector)

        if (i + 1) % 50 == 0:
            print(f"Processed {i+1}/{len(faqs)} FAQs...")

    await conn.close()
    print(f"Done. {len(faqs)} FAQs uploaded for tenant {TENANT_ID}.")

if __name__ == "__main__":
    import asyncio
    print("--- Starting FAQ Upload ---")
    faqs = load_faqs(EXCEL_PATH)
    if faqs:
        asyncio.run(upload_faqs(faqs))
    else:
        print("No valid FAQs found. Aborting.")

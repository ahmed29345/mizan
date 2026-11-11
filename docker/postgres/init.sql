-- Runs once, the first time the database volume is created.
-- pgvector adds the `vector` column type and similarity search (used for RAG in week 3).
CREATE EXTENSION IF NOT EXISTS vector;

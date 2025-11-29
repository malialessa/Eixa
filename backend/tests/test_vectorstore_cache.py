import pytest
import asyncio
from vectorstore_utils import get_embedding, _embedding_cache

class DummyEmbedding:
    def __init__(self, values):
        self.values = values

class DummyModel:
    def __init__(self, counter):
        self.counter = counter
    def get_embeddings(self, texts):
        self.counter[0] += 1
        return [DummyEmbedding([0.1]*768)]

@pytest.mark.asyncio
async def test_embedding_cache(monkeypatch):
    counter = [0]
    def fake_from_pretrained(name):
        return DummyModel(counter)
    import vertexai.language_models
    monkeypatch.setattr(vertexai.language_models.TextEmbeddingModel, 'from_pretrained', staticmethod(fake_from_pretrained))

    text = "Olá mundo"
    emb1 = await get_embedding(text, project_id="proj", location="us-east1")
    emb2 = await get_embedding(text, project_id="proj", location="us-east1")

    assert emb1 == emb2
    assert counter[0] == 1  # apenas uma chamada ao modelo
    assert len(emb1) == 768

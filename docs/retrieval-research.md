# What the best-benchmarked RAG systems do, and what to adopt

Research notes, November 2026. Where Sentinel Vault stands against the open-source RAG systems
and papers with the strongest published results, and what is worth copying.

## Where we are

- Hybrid search: dense vectors (Qdrant) plus keyword search (Postgres full-text), fused, then
  reranked by Qwen3-Reranker-0.6B. At most 2 passages per document, scoped to the knowledge
  sources the user picked, and every passage re-checked against the user's permissions.
- Answers cite passages; citations are range-checked, and every cited sentence and diagram box
  is judged against its passage (the claim check, `app/grounding.py`, with Bespoke-MiniCheck
  for English claims).
- GitLab handbook eval (39 questions, Qwen reranker): right document first 81.8%, in the top 8
  100%, correct answers 84.8–87.9%, faithfulness 0.91 (answer-model judge) / 0.96 (MiniCheck).

So recall is solved for this set; what's left is ranking (the right document first) and exact
details in answers (the misses are a false refusal and answers that leave out "24" or
"4 weeks").

## Findings

### 1. Context added to chunks, then hybrid search and reranking (Anthropic, Contextual Retrieval)

Anthropic has an LLM write a short, chunk-specific context and prepends it to each chunk
before both embedding and keyword indexing. Measured as the share of questions whose relevant
chunk is missing from the top 20, failures fell from 5.7% to 3.7% with contextual embeddings,
to 2.9% adding contextual BM25, and to 1.9% adding a reranker (a 67% reduction overall).

We already have the second and third steps (keyword search and a reranker). The missing piece
is the context added to each chunk. A 2025 comparison (Merola and Singh, ECIR workshop) found
contextual retrieval beats late chunking when combined with rank fusion and reranking, but at
a high compute cost.

### 2. BM25 matters more as the corpus grows ("BM25 Wins at Scale", arXiv 2607.26497)

A July 2026 study held the reader model and questions fixed and grew an enterprise corpus
(about 512,000 documents) through 28 sizes. An LLM agent browsing the files led at the
smallest sizes; from about 10 million corpus tokens, BM25 led at every larger size, by close
to 20 points at full scale. Dense retrieval was cheap but less accurate. Graph-based RAG
(GraphRAG, LightRAG, HippoRAG 2) hit construction-cost limits before reaching realistic
corpus sizes. Giving the agent a BM25 search tool raised it from 36.9 to 69.4 on the same
questions: an agent works best searching a ranked index, not replacing it.

The handbook is about 52,800 chunks of up to 400 tokens, so it is probably near or past the
10M-token crossover. Our keyword side is Postgres `ts_rank`, which is not BM25.

### 3. Search again and extract evidence (PaperQA2, FutureHouse)

PaperQA2 tops the science part of RAG-QA Arena, 12.4% above the next system. Two things set
it apart:
- An agent that can search again, or rewrite the answer, after judging a draft answer.
- "Re-ranking and contextual summarization": an LLM summarizes each top passage with respect
  to the question, and only summaries judged relevant feed the answer, not the raw chunks.
  FutureHouse notes this step is expensive.

### 4. Frameworks don't add accuracy

A 2026 benchmark of six RAG frameworks found a 47-line plain Python pipeline matched all of them
on answer correctness and latency. LangChain, LlamaIndex, Haystack or RAGFlow would not make
answers better. RAGFlow's strength is parsing complex PDFs (tables, layouts); we use Docling.

## What to adopt, in order

| | Change | Why | Cost |
|---|---|---|---|
| A | Real BM25 instead of Postgres full-text ranking | We are near the size where BM25 leads | Medium; Qdrant's native BM25 sparse vectors (see README, "Where it can be better") |
| B | Context headers on chunks: title, section path and a one-line document summary, in both indexes | The cheap form of Contextual Retrieval: one LLM call per document (~4,200), not per chunk (~52,800) | A full re-index, about an hour or two of GPU time |
| C | One extra search when an answer refuses or the claim check flags claims: rewrite the question, search again | Targets our real misses (false refusals, missing details) | Small; adds latency only to failing answers |

Not adopting, for now:
- Knowledge graphs (GraphRAG, LightRAG): the scaling study's construction-cost findings, and our
  questions are mostly single-fact lookups.
- PaperQA2's per-passage summaries: eight extra 9B-model calls per answer on a 16 GB GPU.
- A RAG framework: no accuracy gain, more dependencies.

Measure each change separately with `vault source eval gitlab-handbook --rerankers qwen
--answers`, watching hit@1, correct, false refusals and faithfulness.

## Sources

- Anthropic, [Introducing Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) (figures as summarized in the [memx glossary](https://memx.app/glossary/contextual-retrieval/))
- Merola and Singh, [Reconstructing Context: Evaluating Advanced Chunking Strategies for RAG](https://arxiv.org/abs/2504.19754) (2025)
- Wang, Xu et al., [BM25 Wins at Scale: A Scaling Study of RAG Paradigms](https://arxiv.org/pdf/2607.26497) (July 2026)
- FutureHouse, [PaperQA2 achieves SOTA performance on RAG-QA Arena's science benchmark](https://futurehouse.org/news/paperqa2-achieves-sota-performance-on-rag-qa-arena-science-benchmark)
- ScrapingBee, [What Are the Best RAG Frameworks in 2026?](https://www.scrapingbee.com/blog/best-rag-frameworks/)

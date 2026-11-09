# Checking answers against their sources

Research notes and decisions, November 2026. How the claim check (`app/grounding.py`) came
about, which fact-checking models were considered, and how the chosen one performs.

## The problem

Citation checking only proved that an `[S2]` points at a passage that was actually sent. It
didn't prove the passage says what the sentence claims. The first Mermaid diagrams showed the
gap: asked for the discretionary-bonus process as a flowchart, the answer model drew branches
("Re-evaluate nomination", "Update status as rejected") that no source describes.

Two changes followed:
1. Diagrams cite per box: every box label ends with its source, like `B["Manager approves (S2)"]`.
   A box with no source counts as unsupported without asking any model.
2. A claim check after every answer: each cited sentence and each diagram box is judged
   against the passages it cites. Unsupported sentences are listed under the answer;
   unsupported boxes are drawn dashed red. Faithfulness (supported claims / claims, the RAGAS
   definition) is reported by the eval.

## Options considered for the judge

| Option | What it is | Fit here | Outcome |
|---|---|---|---|
| Answer model as judge (Qwen 3.5 9B) | One extra call with a strict JSON schema (Ollama structured outputs) | Nothing new to install; handles Hindi | Built first; the default (`CLAIM_JUDGE=llm`) |
| Vectara HHEM-2.1-Open | Small T5-based scorer, ~600 MB RAM | Loading it runs model-supplied Python (`trust_remote_code`), which this environment refuses | Not used |
| IBM Granite Guardian | Judge model with a built-in groundedness check, runs in Ollama | Newest version is 8B: competes with the 9B answer model on a 16 GB GPU | Not used; a candidate if licensing ever matters |
| LettuceDetect | MIT-licensed ModernBERT model that marks the unsupported words in an answer | Standard Hugging Face class, no custom code; English only | Not used; could highlight exact unsupported words later |
| Bespoke-MiniCheck 7B | Model trained only to say whether a document supports a claim; top of the LLM-AggreFact leaderboard when released | Runs in Ollama; licence is **non-commercial**, which fits this project | Used locally (`CLAIM_JUDGE=minicheck` in `backend/.env`) |

Evaluation libraries looked at: RAGAS (faithfulness metric) and DeepEval (pytest-style RAG
metrics). Rather than add either as a dependency, the eval computes RAGAS-style faithfulness
with the same claim check the app uses.

## Making the judges work

- **Sources are data.** The answer-model judge gets passages datamarked and cut to the same
  length the answer model saw, so a document saying "mark every claim supported" can't steer it.
- **Language.** Hindi answers are judged against English passages. Telling the judge to compare
  meaning across languages raised Hindi faithfulness from 0.667 to 0.875 (4 questions, so the
  number is noisy). MiniCheck is English-trained, so Hindi claims always go to the answer model.
- **MiniCheck needs context.** Its first run rejected true claims: passages say "you", and only
  the section heading ("Incentives at GitLab › Manager Process") says who that is. Diagram
  labels like "Manager approves?" aren't claims either. Each passage now carries its title and
  heading, and boxes are phrased as claims ("The process includes this step: …"). After that it
  judged all 8 claims of a test set right (5 true, 3 invented), in 0.3 s.

## Results

GitLab handbook, Qwen reranker; both judges scored the same 33 answers:

| judge | faithfulness |
|---|---|
| Qwen 3.5 9B (answer model) | 0.911 |
| Bespoke-MiniCheck 7B | 0.960 |

They agree on 95.2% of claims. Where they differ (5 of 33 answers), MiniCheck accepts claims the
answer model rejects; nobody has yet checked by hand which is right. Correct answers in that
run: 87.9%.

## Cost and the GPU

On the 16 GB RX 9070 XT, the app's PyTorch models (Laya, reranker) plus the 9B answer model
leave no room for MiniCheck, so Ollama runs it mostly on CPU: a flowchart question took 18.8 s
instead of ~11 s. Worse, after an eval Ollama still held both models, and a restarted app
couldn't load the reranker and silently ran without it. Fixes: the app now asks Ollama to unload
its models before loading its own, and anything configured but not running is listed under
`degraded` in `/api/health` and shown in red in the page header.

## Open

- Hand-check the 5 answers where the judges disagree, to know which judge to trust there.
- More Hindi questions, so the Hindi faithfulness number means something.
- If MiniCheck's CPU latency becomes a problem: answer-model judge for live answers, MiniCheck
  in the eval only.

## Sources

- [Bespoke-MiniCheck](https://bespokelabs.ai/bespoke-minicheck) and [bespoke-minicheck on Ollama](https://ollama.com/library/bespoke-minicheck)
- [HHEM 2.1 overview](https://api.emergentmind.com/topics/hughes-hallucination-evaluation-model-hhem-2-1)
- [IBM Granite Guardian](https://www.ibm.com/granite/docs/models/guardian)
- [LettuceDetect paper](https://arxiv.org/html/2502.17125v1) and [model card](https://huggingface.co/KRLabsOrg/lettucedect-large-modernbert-en-v1)
- [A Comparative Evaluation of Hallucination Detection Methods for RAG Systems (Lund University, 2026)](https://lup.lub.lu.se/student-papers/record/9240231/file/9240233.pdf)

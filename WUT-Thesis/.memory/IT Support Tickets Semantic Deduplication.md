# Semantic Deduplication of IT Support Tickets: Research Notes

Thesis context: architectural design of an intelligent IT gateway for mass semantic data deduplication in cloud storage (e.g., AWS S3). Main use case: deduplicating IT support tickets and bug reports, and their heavy attachments (memory dumps, logs, videos), based on how similar their text descriptions are in meaning.

Compiled: 3 October 2026

---

## 1. Embedding models for IT text on CPU

Three open-source Hugging Face models cover different trade-offs between speed and quality. Modern models such as BGE-M3 and Nomic are general-purpose retrieval and embedding models, not classic STS-only SBERT models. They work well for semantic similarity once the threshold is calibrated.

### 1.1 `sentence-transformers/all-MiniLM-L6-v2`: ultra-light baseline

| Property | Value |
|---|---|
| Parameters | 22.7M [1] |
| Embedding dimensions | 384 [1] |
| Max sequence length | 256 word pieces (longer input is truncated) [1] |
| Base model | `nreimers/MiniLM-L6-H384-uncased` [1] |
| Language | English [1] |
| CPU embedding latency | ~28 ms per report (8 vCPU AMD EPYC, 16 GB RAM, via Ollama) [4] |
| MTEB (ArguAna) | 50.17 [1] |

- The SBERT documentation says it is about five times faster than `all-mpnet-base-v2` while still offering good quality [2].
- It is the default embedding model in NVIDIA NeMo Curator's semantic deduplication pipeline [3].
- Hardware: any modern CPU. The model weights are about 100 MB. No GPU needed.
- Limitation: in a bug-report benchmark, 76 of 650 reports were too long for its 256-token window, so 60 pairs were dropped [4]. This matters for tickets that contain stack traces or log excerpts.

### 1.2 `nomic-ai/nomic-embed-text-v1.5`: long-context, flexible dimensions

| Property | Value |
|---|---|
| Parameters | ~137M (0.1B) [4][5] |
| Embedding dimensions | 768, with Matryoshka truncation to 512 / 256 / 128 / 64 [5] |
| Max sequence length | 8,192 tokens [5] |
| Architecture | `nomic_bert`, a long-context BERT [5] |
| Language | English [5] |
| CPU embedding latency | ~82 ms per report [4] |
| MTEB score | 62.28 at 768 dims; 61.04 at 256; 59.34 at 128 [5] |

- Matryoshka Representation Learning (MRL) lets you shorten the vectors to save storage. On synthetic bug pairs, F1 was 0.972 at 128 dims and 0.981 at the full 768 dims [4]. MRL needs v1.5 or later [4].
- Hardware: about 550 MB of weights in F16. Runs on a standard CPU.
- Note: on real Mozilla Bugzilla data it scored lowest of the CPU-feasible models (F1 0.894) [4] (see Section 4.1).

### 1.3 `BAAI/bge-m3`: multilingual, high capability

| Property | Value |
|---|---|
| Parameters | ~568M [4] |
| Embedding dimensions | 1,024 [6] |
| Max sequence length | 8,192 tokens [6] |
| Architecture | XLM-RoBERTa, extended with RetroMAE pretraining [6] |
| Languages | 100+ [6] |
| CPU embedding latency | ~268 ms per report [4] |
| MTEB (ArguAna) | 54.04 [6] |

- Its 1,024 dimensions fit pgvector's HNSW index directly (the index limit is 2,000 dimensions) [4].
- Setting `use_fp16=True` speeds up computation with a slight loss of quality [6].
- Hardware: about 1.1 GB of weights in F16. Runs on CPU, but it is about 3× slower than Nomic and about 10× slower than MiniLM [4].
- Good for multilingual enterprise tickets, for example a Polish description with English error messages.

---

## 2. Cosine similarity threshold for rejecting duplicates

### 2.1 There is no universal threshold

- The commonly cited 0.9 threshold misses 42–78% of real duplicates across six models on synthetic bug reports. On Mozilla Bugzilla it misses almost every duplicate [4].
- Thresholds do not carry over between models or datasets. Using a threshold tuned on synthetic data cost every model 20–30 F1 points on Bugzilla [4].
- General literature reports typical SBERT thresholds of 0.80–0.85, and calibrated embedding filters around 0.80–0.82. In every case the value is chosen per dataset by maximising F1 [7].
- NVIDIA NeMo Curator expresses its threshold as cosine dissimilarity (`eps_to_extract`, e.g. 0.01–0.1), where lower values are stricter [3].
- Practitioners warn that tickets such as "Dashboard is slow" and "dashboard is broken" can embed close together despite having unrelated causes. Wrongly merging them can cost more than missing a duplicate [8].

### 2.2 Empirical thresholds from a bug-report benchmark [4]

| Model | Best threshold (synthetic) | Best threshold (Mozilla Bugzilla) | Suggested start | Tuning range |
|---|---|---|---|---|
| all-MiniLM | 0.62 (0.64 in another section) | 0.27 | 0.65 | 0.55–0.75 |
| snowflake-arctic-embed | 0.65 | 0.51 | – | – |
| bge-m3 | 0.69 | 0.49 | 0.68 | 0.60–0.75 |
| mxbai-embed-large | 0.73 | 0.53 | 0.73 | 0.65–0.80 |
| nomic-embed-text v1.5 | 0.73 | 0.62 | – | – |
| qwen3-embedding (7.6B) | 0.73 (0.74) | 0.49 | 0.74 | 0.68–0.82 |

### 2.3 Calibration procedure

1. L2-normalise the embeddings so that cosine similarity equals the dot product [2].
2. Label 50–100 ticket pairs from your own data as duplicate or not duplicate [4].
3. Sweep the threshold from 0.50 to 0.90 (or 0.99) in steps of 0.01 [4].
4. Pick the threshold with the highest F1. If false merges are costly, pick a higher threshold that favours precision [4][8].
5. Recalibrate from time to time as products and vocabulary change.

### 2.4 Is there a paper backing 0.72?

No peer-reviewed paper sets 0.72, or any fixed value, as the right threshold for ticket deduplication. What exists:

- Budanov: best thresholds of 0.69–0.73 on synthetic data, but 0.27–0.62 on real Mozilla data [4]. A "0.72" most likely comes from this synthetic result.
- Laney et al. (2025), on BugHub Eclipse with `paraphrase-MiniLM-L6-v2`: started at 85% and also tested 90% and 95%, with no theoretical justification for those values. The stricter thresholds worked better on their data [27].
- Surveys: 0.80–0.85, tuned per dataset [7].

Cosine scores are specific to each model, because each model spreads its scores differently. A score of 0.72 in mxbai does not mean the same as 0.72 in MiniLM. The defensible method is empirical calibration on a time-ordered validation split.

Suggested wording for the thesis: "The threshold θ = 0.xx was determined empirically as the F1-maximising value on the validation set, because cosine scores are not comparable across models or datasets [4][27]."

### 2.5 Gateway pipeline sketch

```
Ticket text -> Embedding model (CPU) -> L2-normalised vector -> Vector DB (pgvector / Milvus)
                                                                      |
                                         cosine similarity query -> threshold gate -> duplicate / not duplicate
```

- Run the embedding model as a stateless inference microservice, for example an Ollama `/api/embed` endpoint [4] or a `sentence-transformers` service [2].
- Filter by metadata first (product, component, time window) to shrink the search space.

---

## 3. Datasets for the proof of concept

### 3.1 Datasets with duplicate labels

| Dataset | Content | Size | Format / access | How duplicates are marked |
|---|---|---|---|---|
| GitBugs [9][10][11][12] | Bugzilla, Jira and GitHub bugs: Firefox, Mozilla Core, Thunderbird, VS Code, Spark, Hadoop, HBase, Cassandra, Seamonkey | Over 150,000 reports; Mozilla Core alone has 85,673 with 17,899 duplicates [9][12] | Files per project; Hugging Face shows them in Parquet; Kaggle download is 513.91 MB; CC BY 4.0 [9][10] | Mapping files from `Issue id` to `Duplicate id`, plus ready train/test splits [9][12] |
| IssueDupesLite [13] | GitHub issue pairs | About 10K duplicate pairs | Open; shown as Parquet; no license stated | One pair per row: `primary_title/body` and `duplicate_title/body` |
| IssueDupes (full) [14] | GitHub issues from 25,000 repositories | Over 1.2M issues, over 750K pairs, 2.8 GB | Access by request only | Same pair layout, plus the closing comment that flagged the duplicate |
| BugHub / BugRepo [15] | Mozilla Core, Firefox, Thunderbird, Eclipse Platform, JDT, and Apache Jira projects (Spark, Hadoop, HDFS, MapReduce, HBase, Cassandra, Mesos) | e.g. Mozilla Core 205,069 issues (21.8% duplicates); Firefox 115,814 (30.9%); Eclipse Platform 85,156 (16.9%) | Free for research; the README does not describe the file format | Duplicate labels with a time-ordered 80/20 train/test split |
| Lazar et al., MSR'14 [16][17][18][19] | Eclipse, Mozilla, NetBeans, OpenOffice: every bug ever submitted to their Bugzilla trackers | Hundreds of thousands of bugs | MongoDB dump; scripts in [19] produce JSON plus split files | Bugzilla-style duplicate links. The classic academic benchmark |
| Public Jira Dataset [20] | 16 public Jira instances, 1,822 projects, 2.7M issues, 32M changes, 9M comments, 1M issue links | 5.8 GB ZIP | MongoDB dump; CC BY 4.0 | Issue links. The Zenodo page does not list link types, so filter for Jira's "Duplicate" link type yourself |

### 3.2 Ticket-style text without duplicate labels

Useful as realistic helpdesk text, for testing false positives, or as a base for synthetic duplicates (e.g., paraphrasing with an LLM):

- Tobi-Bueck/customer-support-tickets [21]: 61.8K synthetic email tickets in English and German, CSV, with fields `subject`, `body`, `answer`, `type`, `queue`, `priority`. License CC BY-NC 4.0 (non-commercial).
- mindweave/help-desk-tickets [22]: 10,000 synthetic tickets over 18 months, with comment threads, SLA tracking and escalations.
- Kaggle IT Service Ticket Classification [23]: categories such as Hardware, Access, Storage.
- Kaggle Synthetic IT Support Tickets [24]: 100,000 synthetic IT/SaaS tickets.

### 3.3 Building pairs for F1 evaluation

The pair datasets only contain positive pairs, so you need to add negative pairs yourself:

- Easy negatives: random tickets from the same project.
- Hard negatives: tickets from the same component that score high on BM25 but are not marked as duplicates.
- Sweep the threshold on validation data and report F1 on a test set split by time, as BugHub [15] and GitBugs [12] already do.

```python
from datasets import load_dataset
pairs = load_dataset("primordic/IssueDupesLite", split="train")
# positives: (primary_title + primary_body, duplicate_title + duplicate_body, label=1)
# negatives: shuffle duplicate_* within the same repo, label=0
```

The Mozilla/Bugzilla data is the most widely cited, with the most published work to compare against [15][16][25]. GitBugs and IssueDupes are newer and add modern GitHub and VS Code issues [12][13].

---

## 4. Best model for BugHub (Mozilla Core / Eclipse)

### 4.1 Evidence on real Mozilla Bugzilla data [4]

| Method | F1 on Mozilla Bugzilla | F1 on synthetic data (cross-validated) | CPU time per report |
|---|---|---|---|
| qwen3-embedding (7.6B) | 0.966 | 0.990 | ~2.7 s, too slow for the gateway |
| mxbai-embed-large | 0.962 | 0.984 | 224 ms |
| BM25 (keyword search, no embeddings) | 0.954 | 0.951 | under 1 ms |
| all-MiniLM | 0.952 | 0.978 | 28 ms (76 reports too long to embed) |
| bge-m3 | 0.948 | 0.986 | 268 ms |
| snowflake-arctic-embed | 0.872 | 0.977 | 220 ms |
| nomic-embed-text | 0.894 | 0.973 | 82 ms |

### 4.2 Recommendation

Use `mixedbread-ai/mxbai-embed-large-v1` (335M parameters, 1,024 dimensions, 512-token input) [4], combined with BM25.

- Mozilla and Eclipse reports are English-only, so BGE-M3's main advantage (100+ languages) does not apply.
- The benchmark author names mxbai the best quality-to-speed option for English [4].
- 512 tokens is enough for the title plus the start of the description. MiniLM's 256 tokens is not [4].
- BM25 is a strong baseline. It beat 4 of the 6 embedding models on Bugzilla [4]. A peer-reviewed study, Zhang et al. (ACM TOSEM), found that simpler techniques beat more sophisticated, newer ones on most projects [25][26].
- The strongest design is a hybrid: combine BM25 and embedding results (e.g. Reciprocal Rank Fusion), then apply the calibrated threshold.
- Caveat: the Budanov benchmark [4] is a blog post, not a peer-reviewed paper, and used 407 Mozilla bugs. Cite it as supporting evidence and repeat the comparison on BugHub [15] yourself.

### 4.3 Related literature

- Zhang et al., "Duplicate Bug Report Detection: How Far Are We?", ACM TOSEM [25]. Replication package [26] covers REP, SABD, Siamese Pairs, DC-CNN, HINDBR and full-text search.
- Laney, Barovic, Moin, "Automated Duplicate Bug Report Detection in Large Open Bug Repositories" (arXiv 2025) [27]. BugHub Eclipse data; cosine thresholds of 0.85, 0.90 and 0.95 with `paraphrase-MiniLM-L6-v2`; a GPT-summary variant reached F1 0.96.
- GitBugs paper [12]: semantic embeddings reached Recall@10 = 0.61 on its duplicate-retrieval task.

---

## 5. Simulating attachments (hybrid dummy-data simulation)

The original proposal: when the test code loads a ticket from CSV/JSON, it attaches a "virtual attachment" (`wirtualny_rozmiar_mb = random.randint(10, 100)`). When the algorithm rejects a duplicate, that size is added to `zaoszczedzone_MB` (saved MB). This is sound and standard practice. Three improvements follow.

### 5.1 Use a skewed size distribution, not a uniform one

Real attachment sizes are mostly small with a few very large files. Sample per attachment type from a log-normal distribution. These parameters are assumptions and should be labelled as such in the thesis:

| Type | Share of tickets | Median | Upper range |
|---|---|---|---|
| none | 40% | 0 | – |
| screenshot (PNG) | 25% | 0.5 MB | 5 MB |
| log (.log/.txt) | 20% | 5 MB | 200 MB |
| video (MP4) | 10% | 30 MB | 500 MB |
| memory dump (.dmp) | 5% | 300 MB | 4 GB |

The dump range is grounded: Mozilla's documentation notes that a full minidump can be several GB, depending on the size of the Firefox process [28].

### 5.2 Hybrid approach: real metadata plus synthetic sizes

- For the bug IDs in BugHub, fetch real attachment metadata with the Bugzilla REST API: `GET /rest/bug/<bug_id>/attachment`. It returns `size` (in bytes) and `content_type` [29].
- Use real sizes where they exist. Sample synthetic sizes only where there is no attachment, or to model the heavy enterprise case. Bugzilla attachments are mostly small (patches, screenshots), which is why heavy dumps and videos have to be synthetic.

### 5.3 Count losses as well as savings

If the gateway wrongly rejects a ticket (a false positive), its attachment is lost. Track this alongside the savings.

```python
import numpy as np
rng = np.random.default_rng(42)  # fixed seed = reproducible results

PROFILE = {  # type: (probability, median_MB, sigma)
    "brak": (0.40, 0, 0), "screenshot": (0.25, 0.5, 0.8), "log": (0.20, 5, 1.2),
    "video": (0.10, 30, 1.0), "dump": (0.05, 300, 0.9),
}

def wirtualny_zalacznik():
    typy, p = list(PROFILE), [v[0] for v in PROFILE.values()]
    t = rng.choice(typy, p=p)
    _, med, s = PROFILE[t]
    return t, 0.0 if med == 0 else float(rng.lognormal(np.log(med), s))

zaoszczedzone_MB = utracone_MB = 0.0
for ticket in test_set:
    typ, mb = ticket.get("real_size_mb") and ("real", ticket["real_size_mb"]) or wirtualny_zalacznik()
    if gateway.is_duplicate(ticket):           # cosine >= theta
        if ticket["is_true_duplicate"]:
            zaoszczedzone_MB += mb              # true positive: storage saved
        else:
            utracone_MB += mb                   # false positive: data lost
```

### 5.4 Strengthening the evaluation

- Repeat the run many times: 100 runs with different seeds, reporting savings as a mean with a 95% confidence interval.
- Optionally move real bytes: generate files with `os.urandom` and upload them to MinIO or LocalStack (local S3 emulators) to measure the gateway's real latency and bandwidth. Avoid zero-filled files: they compress or deduplicate to almost nothing, which makes the results look better than they are.
- Convert MB into money: multiply saved GB by the current S3 price per GB-month, kept as a parameter in the script.
- Consider linking instead of deleting: a duplicate's attachment can contain new evidence, such as a different crash dump. The gateway could keep a reference to the original ticket and move the attachment to a cheaper storage tier. This trade-off works well as a discussion section.

---

## References

[1] sentence-transformers/all-MiniLM-L6-v2, Hugging Face model card. https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

[2] Sentence Transformers documentation, Pretrained Models. https://www.sbert.net/docs/sentence_transformer/pretrained_models.html

[3] NVIDIA NeMo Curator, Semantic Deduplication. https://docs.nvidia.com/nemo/curator/0.25.7/curate-text/process-data/deduplication/semdedup.html

[4] A. Budanov, "How I Chose an Embedding Model for Bug Report Deduplication". https://budanov.me/posts/embedding-benchmark/

[5] nomic-ai/nomic-embed-text-v1.5, Hugging Face model card. https://huggingface.co/nomic-ai/nomic-embed-text-v1.5

[6] BAAI/bge-m3, Hugging Face model card. https://huggingface.co/BAAI/bge-m3

[7] Emergent Mind, "Semantic Duplicates: Detection & Applications". https://www.emergentmind.com/topics/semantic-duplicates

[8] Kushagra, "How do you dedupe support tickets that don't share any words?", DEV Community, 2026. https://dev.to/kushagra-devs/how-do-you-dedupe-support-tickets-that-dont-share-any-words-heres-our-messy-attempt-d5g

[9] av9ash/GitBugs, Hugging Face dataset. https://huggingface.co/datasets/av9ash/GitBugs

[10] GitBugs, Kaggle dataset. https://www.kaggle.com/datasets/av9ash/gitbugs

[11] GitBugs, GitHub repository. https://github.com/av9ash/gitbugs/

[12] Patil et al., "GitBugs: Bug Reports for Duplicate Detection, Retrieval Augmented Generation, and More", arXiv:2504.09651. https://ar5iv.labs.arxiv.org/html/2504.09651

[13] primordic/IssueDupesLite, Hugging Face dataset. https://huggingface.co/datasets/primordic/IssueDupesLite

[14] primordic/IssueDupes, Hugging Face dataset. https://huggingface.co/datasets/primordic/IssueDupes

[15] logpai/bughub (BugRepo), GitHub repository. https://github.com/logpai/bughub

[16] A. Lazar et al., MSR'14 duplicate bug datasets (original download page). http://alazar.people.ysu.edu/msr14data/

[17] tqtg/DuplicateBugFinder, GitHub repository. https://github.com/tqtg/DuplicateBugFinder

[18] MSR 2014 proceedings, "Generating Duplicate Bug Datasets" abstract. https://www.conference-publishing.com/toc/MSR14/abs

[19] I. Muller, soft_alignment_model_bug_deduplication, GitHub repository. https://github.com/irving-muller/soft_alignment_model_bug_deduplication

[20] Montgomery et al., The Public Jira Dataset, Zenodo. https://zenodo.org/records/15719919

[21] Tobi-Bueck/customer-support-tickets, Hugging Face dataset. https://huggingface.co/datasets/Tobi-Bueck/customer-support-tickets

[22] mindweave/help-desk-tickets, Hugging Face dataset. https://huggingface.co/datasets/mindweave/help-desk-tickets

[23] IT Service Ticket Classification Dataset, Kaggle. https://www.kaggle.com/datasets/adisongoh/it-service-ticket-classification-dataset

[24] IT Support Tickets (synthetic), Kaggle. https://www.kaggle.com/datasets/ahsanneural/synthetic-it-support-tickets

[25] T. Zhang et al., "Duplicate Bug Report Detection: How Far Are We?", ACM Transactions on Software Engineering and Methodology. https://dl.acm.org/doi/10.1145/3576042

[26] soarsmu/TOSEM-DBRD, replication package for [25], GitHub. https://github.com/soarsmu/TOSEM-DBRD

[27] C. E. Laney, A. Barovic, A. Moin, "Automated Duplicate Bug Report Detection in Large Open Bug Repositories", arXiv:2504.14797. https://arxiv.org/html/2504.14797v1

[28] Firefox Source Docs, "Capturing a minidump". https://firefox-source-docs.mozilla.org/contributing/debugging/capturing_minidump.html

[29] Bugzilla REST API documentation, Attachments. https://bugzilla.readthedocs.io/en/latest/api/core/v1/attachment.html

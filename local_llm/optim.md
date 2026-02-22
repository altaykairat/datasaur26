# Optimization Strategy for Local LLM + VLM Deployment

## Objective

Define a production-grade optimization roadmap for a local deployment of:

* 1 Text LLM (tickets + chatbot) -> **Qwen2.5:14b-Instruct**
* 1 Vision-Language Model (attachments only) -> **MiniCPM-V:8b**

Hardware:

* RTX 5000 (32GB VRAM)
* 50+ GB RAM

---

# Phase 1 — Baseline Stabilization (Mandatory First Step)

Before optimization:

## 1. Functional Validation

* Structured JSON output correctness
* Multilingual stability (RU/KZ/EN)
* Image extraction reliability
* Schema validation enforcement

## 2. Metrics Collection

Measure:

* Average latency
* P95 latency
* GPU utilization
* VRAM usage
* Tokens/sec
* Concurrent request behavior

Do not optimize before measuring.

---

# Phase 2 — Quantization (High ROI)

## Text LLM

Recommended:

* 4-bit quantization (GPTQ / AWQ)
* 8-bit if maximum stability required

Verify:

* JSON structure integrity
* Classification consistency

## Vision-Language Model

Recommended:

* 4-bit for UI screenshot extraction
* 8-bit if reasoning quality drops

Expected Benefits:

* Lower VRAM consumption
* Higher throughput
* More headroom for batching

---

# Phase 3 — Concurrency & Throughput Control

## 1. Semaphore-Based Concurrency Control

Separate pools:

* Text LLM workers
* Vision LLM workers

Example policy:

* Max 4 concurrent text jobs
* Max 2 concurrent vision jobs

Prevents GPU overload and tail latency spikes.

---

## 2. Dynamic Batching

Use when:

* Multiple managers active
* Burst ticket ingestion

Configuration:

* max_batch_size
* max_wait_ms (e.g., 20–40ms)

Tradeoff:

* Slight per-request latency increase
* Significant throughput gain

---

## 3. Async I/O + Worker Queues

Architecture:

* Non-blocking API layer
* Background workers for VLM
* Priority queue for fraud/claims

Improves:

* UI responsiveness
* System stability

---

# Phase 4 — Memory & Cache Optimization

## 1. KV Cache Optimization

Important for:

* Chatbot sessions
* Long ticket threads

Reduces repeated computation.

---

## 2. Hash-Based Caching

Cache by:

* ticket_text_hash
* image_hash

If identical input seen before:

* Return cached structured result
* Skip inference entirely

Major cost and latency reduction.

---

If identical input seen before:

* Return cached structured result
* Skip inference entirely

Major cost and latency reduction.

---

# Phase 5 — Production Target (vLLM & Continuous Batching)

While `Ollama` is used for rapid local prototyping and MVP deployment, the final production architecture must migrate to **vLLM** to maximize the RTX 5000 ROI.

## 1. Migrating to vLLM

Use vLLM when:

* Traffic exceeds 30–50 tickets/min.
* Strict latency SLA (<200ms TTFT) is required.

**Key vLLM Advantages for Production:**
* **Continuous Batching:** Unlike Ollama's static queue, vLLM dynamically batches incoming requests at the token level, increasing throughput by 3x-4x.
* **PagedAttention:** Effectively manages KV cache memory, preventing OOM errors during concurrent large-context queries (e.g., long email threads).
* **AWQ / GPTQ:** Native support for Activation-aware Weight Quantization (AWQ), which preserves model quality better than standard GGUF formats during 4-bit compression.

**Deployment Plan (Linux/WSL2):**
```bash
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-14B-Instruct-AWQ \
  --quantization awq \
  --gpu-memory-utilization 0.85
```

## 2. Kernel Acceleration

Enable within vLLM:
* **FlashAttention-2**: For maximum throughput on Ada/Turing architectures.
* **CUDA Graphs**: To minimize CPU overhead during model forward passes.

> Note: The `IntelligenceEngine` in python is already built and tested to seamlessly switch to vLLM via the `mode="vllm"` argument.

## 3. Optimization Codebase

Scripts for advanced optimization are provided in the `local_llm/optimization_scripts/` directory:
- [quantize_awq.py](file:///d:/Altay/hackathon/datasaur26/local_llm/optimization_scripts/quantize_awq.py): Automated 4-bit AWQ quantization.
- [benchmark_llm.py](file:///d:/Altay/hackathon/datasaur26/local_llm/optimization_scripts/benchmark_llm.py): Latency and Tokens/sec measurement.
- [trt_blueprint.py](file:///d:/Altay/hackathon/datasaur26/local_llm/optimization_scripts/trt_blueprint.py): TensorRT-LLM compilation logic.

---

## Speculative Decoding

Useful for:

* Chatbot latency reduction

Less important for:

* Structured extraction tasks

---

# Phase 6 — Intelligent Routing (Highest Impact Optimization)

## Do NOT call VLM unnecessarily

Routing logic:

* No image → Text LLM only
* Image present → Text LLM first → VLM for attachment only
* Known template match → Skip VLM
* Validation failure → Component-specific retry

This reduces GPU load more than low-level optimization.

---

# Performance Expectations (Balanced Setup)

| Task                  | Latency    |
| --------------------- | ---------- |
| Ticket classification | 300–600 ms |
| Chatbot intent        | 200–500 ms |
| Screenshot analysis   | 2–3 s      |

Supports:

* 5–10 concurrent managers
* Stable internal production use

---

# Optimization Priority Order

1. Correctness
2. Measurement
3. Quantization
4. Concurrency control
5. Dynamic batching
6. Caching
7. Advanced engine acceleration (if required)

Do not invert this order.

---

# Key Engineering Principle

Most performance problems in production are architectural, not kernel-level.

Smart routing + deterministic validation + concurrency isolation

provide more stability than aggressive low-level GPU tuning.

---

End of Document.

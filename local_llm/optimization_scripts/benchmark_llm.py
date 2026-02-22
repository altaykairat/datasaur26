import time
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

def benchmark(model_id, prompt, max_tokens=100):
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.float16, device_map="auto")
    
    inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
    
    start_time = time.time()
    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=max_tokens, use_cache=True)
    end_time = time.time()
    
    total_time = end_time - start_time
    tokens_generated = len(output[0]) - len(inputs.input_ids[0])
    tokens_per_sec = tokens_generated / total_time
    
    print(f"Model: {model_id}")
    print(f"Tokens generated: {tokens_generated}")
    print(f"Total time: {total_time:.2f}s")
    print(f"Tokens per second: {tokens_per_sec:.2f}")

if __name__ == "__main__":
    test_prompt = "Explain the importance of local LLM deployment for financial data privacy."
    benchmark("qwen2.5-14b-awq", test_prompt)

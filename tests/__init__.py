from pipeline.stages import llm_http as _llm_http
_llm_http.WAITS = (0.0, 0.0)      # tests never sleep between LLM retries

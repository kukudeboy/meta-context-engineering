# Llama 3.1 tool calling template

`tool_chat_template_llama3.1_json.jinja` is copied unchanged from
[vLLM v0.8.5](https://github.com/vllm-project/vllm/blob/v0.8.5/examples/tool_chat_template_llama3.1_json.jinja).
It is distributed under the upstream Apache License 2.0 (see `VLLM_LICENSE`).

Start the LLaMA server with these additional flags, using the absolute path to
the template in the uploaded repository:

```bash
--enable-auto-tool-choice \
--tool-call-parser llama3_json \
--chat-template /path/to/mce/assets/tool_chat_template_llama3.1_json.jinja
```
